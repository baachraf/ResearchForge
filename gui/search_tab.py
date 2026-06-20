"""
Search & Download Tab with query builder, session management, and review mode.
"""
import os
import re
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QLabel, QTextEdit, QMessageBox, QCheckBox,
    QFileDialog, QDialog, QDialogButtonBox, QProgressBar, QSpinBox,
    QAbstractItemView, QSizePolicy, QSplitter, QFrame, QFormLayout, QDoubleSpinBox,
    QListWidget, QListWidgetItem, QMenu, QApplication, QComboBox, QInputDialog,
    QTabWidget, QTreeWidget, QTreeWidgetItem,
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtGui import QIcon, QColor

from gui.app_info import icon
from gui.workers import SearchWorker, DownloadWorker, RelevanceScoringWorker
from gui.llm_provider import get_provider_api_key, ensure_llm_available
from gui.session_manager import SessionManager
from gui.session_creator import SessionCreatorDialog
from gui.folder_import import FolderImportDialog, extract_pdf_metadata, copy_pdf_to_topic_root


class _PDFImportWorker(QThread):
    """Background worker for PDF metadata extraction + file copying."""
    progress = Signal(str)
    paper_ready = Signal(dict)
    finished = Signal()
    error = Signal(str)

    def __init__(self, paths: list, output_root: str, start_id: int):
        super().__init__()
        self.paths = paths  # list of (src_path, topic) tuples
        self.output_root = output_root
        self.start_id = start_id
        self._stop = False
        self.added = 0
        self.skipped = 0

    def stop(self):
        self._stop = True

    def run(self):
        try:
            total = len(self.paths)
            added = 0
            skipped = 0
            for i, item in enumerate(self.paths):
                if self._stop:
                    break
                if isinstance(item, (list, tuple)):
                    src_path, topic = item
                else:
                    src_path = item
                    topic = "Local PDFs"

                src_path = os.path.normpath(src_path)
                if not os.path.exists(src_path) or not src_path.lower().endswith(".pdf"):
                    self.progress.emit(f"Skipping {os.path.basename(src_path)}")
                    skipped += 1
                    continue

                fname = os.path.basename(src_path)
                pct = (i + 1) * 100 // total
                self.progress.emit(f"\U0001f4c4 {pct}%  ({i+1}/{total}) Processing: {fname}")

                meta = extract_pdf_metadata(src_path)
                target_dir = os.path.join(self.output_root, topic)
                dest = copy_pdf_to_topic_root(src_path, target_dir, fname)
                paper = {
                    "id": f"local_{self.start_id + added}",
                    "title": meta["title"],
                    "abstract": meta["abstract"],
                    "source": "Local",
                    "url": dest,
                    "year": "",
                    "authors": [],
                    "query_key": "Local",
                    "output_folder": topic,
                    "must_contain": [],
                    "must_not": [],
                    "relevance_threshold": 2,
                    "filter_passed": True,
                    "file_exists": True,
                    "needs_pdf_resolve": False,
                    "title_filter_ok": True,
                }
                self.paper_ready.emit(paper)
                added += 1

            self.added = added
            self.skipped = skipped
        except Exception as e:
            self.error.emit(str(e))
        self.finished.emit()


class QueryBuilderDialog(QDialog):
    """Advanced query builder with AND/OR, must-contain, must-NOT, and source/dates."""
    def __init__(self, query_data=None, parent=None, config_manager=None):
        super().__init__(parent)
        self._cfg = config_manager
        self._mode = self._cfg.get("search_mode", "academic") if self._cfg else "academic"
        self.setWindowTitle("Query Builder")
        self.setMinimumWidth(620)
        self.setMinimumHeight(520)
        self._setup_ui()
        if query_data:
            self.load(query_data)

    def _setup_ui(self):
        layout = QFormLayout(self)
        layout.setSpacing(4)

        self.query_name = QLineEdit()
        self.query_name.setPlaceholderText("e.g., rppg_ica_blind_source")
        layout.addRow("Name:", self.query_name)

        self.output_folder = QLineEdit()
        self.output_folder.setPlaceholderText("Auto: uses query name")
        layout.addRow("Output Folder:", self.output_folder)

        # --- Search terms with AND / OR ---
        grp_terms = QGroupBox("Search Terms")
        tl = QVBoxLayout(grp_terms)

        hb_and = QHBoxLayout()
        hb_and.addWidget(QLabel("All of these (AND):"))
        self.and_terms = QLineEdit()
        self.and_terms.setPlaceholderText("term1 term2 term3  (all must appear)")
        hb_and.addWidget(self.and_terms, 1)
        tl.addLayout(hb_and)

        hb_or = QHBoxLayout()
        hb_or.addWidget(QLabel("Any of these (OR):"))
        self.or_terms = QLineEdit()
        self.or_terms.setPlaceholderText("term1, term2, term3  (at least one)")
        hb_or.addWidget(self.or_terms, 1)
        tl.addLayout(hb_or)

        layout.addRow(grp_terms)

        # --- Keyword filters ---
        grp_kw = QGroupBox("Keyword Filters")
        kl = QVBoxLayout(grp_kw)

        hb_must = QHBoxLayout()
        hb_must.addWidget(QLabel("Must contain:"))
        self.must_contain = QLineEdit()
        self.must_contain.setPlaceholderText("rPPG, heart rate, waveform (comma-separated)")
        hb_must.addWidget(self.must_contain, 1)
        kl.addLayout(hb_must)

        hb_not = QHBoxLayout()
        hb_not.addWidget(QLabel("Must NOT contain:"))
        self.must_not = QLineEdit()
        self.must_not.setPlaceholderText("review, survey (comma-separated)")
        hb_not.addWidget(self.must_not, 1)
        kl.addLayout(hb_not)

        layout.addRow(grp_kw)
        if self._mode != "academic":
            grp_kw.setVisible(False)

        # --- Sources ---
        cfg_sources = self._cfg.get("default_sources", ["arxiv", "semantic_scholar", "duckduckgo", "brave"]) if self._cfg else ["arxiv", "semantic_scholar", "duckduckgo", "brave"]
        has_brave_key = bool(self._cfg.get("brave_api_key", "")) if self._cfg else False
        grp_src = QGroupBox("Sources")
        sl = QVBoxLayout(grp_src)
        hb_src = QHBoxLayout()
        self.src_arxiv = QCheckBox("arXiv")
        self.src_arxiv.setChecked("arxiv" in cfg_sources)
        self.src_s2 = QCheckBox("Semantic Scholar")
        self.src_s2.setChecked("semantic_scholar" in cfg_sources)
        self.src_duckduckgo = QCheckBox("DuckDuckGo")
        self.src_duckduckgo.setChecked("duckduckgo" in cfg_sources)
        self.src_brave = QCheckBox("Brave Search")
        self.src_brave.setChecked("brave" in cfg_sources and has_brave_key)
        self.src_brave.setEnabled(has_brave_key)
        self.src_pubmed = QCheckBox("PubMed")
        self.src_pubmed.setChecked("pubmed" in cfg_sources)
        if self._mode != "academic":
            self.src_arxiv.setChecked(False)
            self.src_s2.setChecked(False)
            self.src_brave.setChecked(False)
            self.src_pubmed.setChecked(False)
            self.src_duckduckgo.setChecked(True)
        for w in [self.src_arxiv, self.src_s2, self.src_duckduckgo, self.src_brave, self.src_pubmed]:
            hb_src.addWidget(w)
        sl.addLayout(hb_src)
        layout.addRow(grp_src)

        # --- Dates & Limits ---
        cfg_after = (self._cfg.get("default_after_date", "") or "2020-01-01") if self._cfg else "2020-01-01"
        cfg_max_res = self._cfg.get("default_max_results", 100) if self._cfg else 100
        cfg_max_size = self._cfg.get("default_max_size_mb", 100.0) if self._cfg else 100.0

        grp_limits = QGroupBox("Limits")
        ll = QHBoxLayout(grp_limits)

        ll.addWidget(QLabel("After:"))
        self.after_date = QLineEdit()
        self.after_date.setPlaceholderText("YYYY-MM-DD")
        self.after_date.setMaximumWidth(110)
        self.after_date.setText(cfg_after)
        ll.addWidget(self.after_date)

        ll.addWidget(QLabel("Max Results:"))
        self.max_q_results = QSpinBox()
        self.max_q_results.setRange(1, 200)
        self.max_q_results.setValue(cfg_max_res)
        ll.addWidget(self.max_q_results)

        ll.addWidget(QLabel("Max PDF (MB):"))
        self.max_pdf_size = QDoubleSpinBox()
        self.max_pdf_size.setRange(1, 500)
        self.max_pdf_size.setValue(cfg_max_size)
        self.max_pdf_size.setSuffix(" MB")
        ll.addWidget(self.max_pdf_size)
        layout.addRow(grp_limits)

        # --- Relevance ---
        cfg_rel = self._cfg.get("default_relevance_threshold", 2) if self._cfg else 2
        grp_rel = QGroupBox("Title Filter")
        rl = QHBoxLayout(grp_rel)
        rl.addWidget(QLabel("Min keyword matches in title:"))
        self.relevance_threshold = QSpinBox()
        self.relevance_threshold.setRange(1, 20)
        self.relevance_threshold.setValue(cfg_rel)
        self.relevance_threshold.setFixedWidth(50)
        self.relevance_threshold.setToolTip("Paper title must contain at least this many 'must contain' keywords")
        rl.addWidget(self.relevance_threshold)

        cfg_fp = self._cfg.get("default_force_plus", False) if self._cfg else False
        self.force_plus = QCheckBox("Force AND in web search")
        self.force_plus.setChecked(cfg_fp)
        rl.addWidget(self.force_plus)
        rl.addStretch()
        layout.addRow(grp_rel)
        if self._mode != "academic":
            grp_rel.setVisible(False)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def load(self, data: dict):
        self.query_name.setText(data.get("name", ""))
        self.output_folder.setText(data.get("output_folder", ""))
        self.and_terms.setText(" ".join(data.get("and_terms", [])))
        self.or_terms.setText(", ".join(data.get("or_terms", [])))
        self.must_contain.setText(", ".join(data.get("must_contain", [])))
        self.must_not.setText(", ".join(data.get("must_not", [])))
        sources = data.get("sources", [])
        if not sources and self._cfg:
            sources = self._cfg.get("default_sources", ["arxiv", "semantic_scholar", "duckduckgo", "brave"])
        self.src_arxiv.setChecked("arxiv" in sources)
        self.src_s2.setChecked("semantic_scholar" in sources)
        self.src_duckduckgo.setChecked("web" in sources or "duckduckgo" in sources)
        self.src_brave.setChecked("brave" in sources)
        self.src_pubmed.setChecked("pubmed" in sources)
        self.after_date.setText(data.get("after_date", "") or (self._cfg.get("default_after_date", "") if self._cfg else ""))
        self.max_q_results.setValue(data.get("max_results") or (self._cfg.get("default_max_results", 100) if self._cfg else 100))
        self.max_pdf_size.setValue(data.get("max_size_mb") or (self._cfg.get("default_max_size_mb", 100.0) if self._cfg else 100.0))
        self.relevance_threshold.setValue(data.get("relevance_threshold") or (self._cfg.get("default_relevance_threshold", 2) if self._cfg else 2))
        fp = data.get("force_plus")
        if fp is None and self._cfg:
            fp = self._cfg.get("default_force_plus", False)
        self.force_plus.setChecked(fp if fp is not None else False)

    def get_data(self) -> dict:
        sources = []
        if self.src_arxiv.isChecked(): sources.append("arxiv")
        if self.src_s2.isChecked(): sources.append("semantic_scholar")
        if self.src_duckduckgo.isChecked(): sources.append("web")
        if self.src_brave.isChecked(): sources.append("brave")
        if self.src_pubmed.isChecked(): sources.append("pubmed")

        and_terms = [t.strip() for t in self.and_terms.text().split() if t.strip()]
        or_terms = [t.strip() for t in self.or_terms.text().replace(",", " ").split() if t.strip()]
        must_contain = [kw.strip() for kw in self.must_contain.text().split(",") if kw.strip()]
        must_not = [kw.strip() for kw in self.must_not.text().split(",") if kw.strip()]

        query_parts = []
        if and_terms:
            query_parts.append(" ".join(and_terms))
        if or_terms:
            query_parts.append("(" + " OR ".join(or_terms) + ")")
        query = " ".join(query_parts)

        return {
            "name": self.query_name.text().strip(),
            "query": query,
            "output_folder": self.output_folder.text().strip() or self.query_name.text().strip(),
            "sources": sources,
            "and_terms": and_terms,
            "or_terms": or_terms,
            "after_date": self.after_date.text().strip(),
            "max_results": self.max_q_results.value(),
            "max_size_mb": self.max_pdf_size.value(),
            "must_contain": must_contain,
            "must_not": must_not,
            "relevance_threshold": self.relevance_threshold.value(),
            "force_plus": self.force_plus.isChecked(),
            "language": "en",
        }


def _score_fail_display(reason: str):
    """Return (short_label, tooltip, QColor) for a score failure reason code."""
    from PySide6.QtGui import QColor
    r = reason.lower()
    if r == "no_url":
        return "no url", "No URL — this paper has no link to fetch content from.", QColor("#9e9e9e")
    if r == "no_pdf_found":
        return "no pdf", "Landing page found but the PDF download link could not be discovered automatically.", QColor("#e67e22")
    if r == "no_text":
        return "no text", "PDF downloaded but no text could be extracted (scanned image or DRM-protected).", QColor("#e67e22")
    if r == "timeout":
        return "timeout", "Download timed out — server is slow or unreachable.", QColor("#e67e22")
    if r == "conn_failed":
        return "no conn", "Connection failed — server may be offline or URL is broken.", QColor("#c0392b")
    if r.startswith("http 403"):
        return "403", "HTTP 403 Forbidden — access is blocked (paywall / login required).", QColor("#c0392b")
    if r.startswith("http 404"):
        return "404", "HTTP 404 Not Found — the PDF link is dead or the paper was removed.", QColor("#c0392b")
    if r.startswith("http 401"):
        return "401", "HTTP 401 Unauthorized — authentication required.", QColor("#c0392b")
    if r.startswith("http 4"):
        code = reason.split()[1] if len(reason.split()) > 1 else "4xx"
        return code, f"HTTP {code} client error — access denied or invalid request.", QColor("#c0392b")
    if r.startswith("http 5"):
        code = reason.split()[1] if len(reason.split()) > 1 else "5xx"
        return code, f"HTTP {code} server error — publisher server issue.", QColor("#e67e22")
    if r.startswith("not_pdf"):
        return "not pdf", f"URL returned non-PDF content ({reason}) — may be a login page or redirect.", QColor("#e67e22")
    if r.startswith("llm error"):
        return "llm err", f"LLM error: {reason[10:]}", QColor("#9c27b0")
    if "did not return" in r or r.startswith("llm"):
        return "no score", f"LLM returned no valid score number. Raw response could not be parsed.", QColor("#9c27b0")
    if r == "no_content":
        return "no data", "No content available to score — no abstract and download produced nothing.", QColor("#9e9e9e")
    if r == "duplicate":
        return "dup", "Duplicate paper — score copied from first occurrence.", QColor("#9e9e9e")
    if not r or r == "unknown":
        return "—", "Score unavailable — reason not recorded (try re-running rating).", QColor("#9e9e9e")
    # Generic / unknown
    short = reason[:8] if len(reason) > 8 else reason
    return short, f"Scoring failed: {reason}", QColor("#9e9e9e")


def _progress_bar(done: int, total: int, width: int = 16) -> str:
    """Mini ascii progress bar: '[#####.......]'."""
    if total <= 0:
        return ""
    pct = min(done, total) / total
    filled = max(0, min(width, round(pct * width)))
    return f"[{'#' * filled}{'.' * (width - filled)}]"


class SearchDownloadTab(QWidget):
    """Core tab: query management, paper search, download queue, and relevance scoring.

    Layout: left panel (session + queries) | right panel (results table)

    Owns SearchWorker, DownloadWorker, and RelevanceScoringWorker. Manages session
    persistence (save/restore). Results table with checkbox, title (green if downloaded),
    open button, source, year, title filter, match score, and LLM relevance reason.

    Signals:
      log(str)            — Forwarded to Logs tab
      session_save_requested() — Emitted when session state changes
    """
    log = Signal(str)

    def __init__(self, config_manager, log_signal, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.log = log_signal
        self._queries: list[dict] = []
        self._search_results: list[dict] = []
        self._search_creds = {}
        self._current_dl_worker = None
        self._current_query_idx = 0
        self._pending_queries: list = []
        self._search_active = False
        self._audit_tab = None
        self._session_manager = SessionManager()
        self._session_id = None
        self._session_context = ""
        self._session_intent = ""
        self._session_focus_keywords = ""
        self._session_avoid_topics = ""
        self._session_paper_data = {}
        self._session_paper_path = ""
        self._session_paper_pages = 0
        self._session_paper_size_mb = 0.0
        self._session_agentic_log = ""
        self._download_done = 0
        self._download_total = 0
        self._scoring_worker = None
        self._closing = False
        self._filter_timer = QTimer()
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._apply_filters)
        self._setup_ui()

    def set_audit_tab(self, tab):
        self._audit_tab = tab

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(4, 4, 4, 4)

        # ── Session + Queries ──
        grp_queries = QGroupBox("Queries")
        grp_queries.setCheckable(True)
        grp_queries.setChecked(True)
        grp_queries.toggled.connect(lambda on: self._collapse_group(grp_queries, on))
        ql = QVBoxLayout(grp_queries)
        ql.setSpacing(1)
        ql.setContentsMargins(0, 0, 0, 0)

        hb_title = QHBoxLayout()
        self.lbl_session = QLabel("Session: [None]")
        self.lbl_session.setObjectName("lbl_session")
        hb_title.addWidget(self.lbl_session)
        hb_title.addStretch()
        ql.addLayout(hb_title)

        ql_inner_top = QHBoxLayout()
        ql_inner_top.setSpacing(2)

        self.query_table = QTableWidget(0, 6)
        self.query_table.setHorizontalHeaderLabels(["", "Name", "Search Terms", "Must Contain", "Must NOT", "Sources"])
        self.query_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.query_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.query_table.horizontalHeader().setStretchLastSection(True)
        self.query_table.horizontalHeader().setMinimumSectionSize(60)
        self.query_table.setColumnWidth(0, 28)
        self.query_table.setColumnWidth(1, 130)
        self.query_table.setColumnWidth(2, 260)
        self.query_table.setColumnWidth(3, 130)
        self.query_table.setColumnWidth(4, 110)
        self.query_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.query_table.horizontalHeader().sectionResized.connect(
            self._make_resize_guard(self.query_table, 5, 100))
        self.query_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.query_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.query_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.query_table.customContextMenuRequested.connect(self._query_context_menu)
        self.query_table.doubleClicked.connect(self._edit_query)
        ql_inner_top.addWidget(self.query_table, 1)

        self._mode = "academic"

        vb_btns = QVBoxLayout()
        vb_btns.setSpacing(3)

        self.btn_session_new = QPushButton("+ New")
        self.btn_session_new.setObjectName("btn_session_new")
        self.btn_session_new.clicked.connect(self._session_new)
        self.btn_session_load = QPushButton("📂 Load")
        self.btn_session_load.setObjectName("btn_session_load")
        self.btn_session_load.clicked.connect(self._session_load)
        self.btn_session_edit = QPushButton("✎ Edit")
        self.btn_session_edit.setObjectName("btn_session_edit")
        self.btn_session_edit.clicked.connect(self._session_edit)
        self.btn_sel_all = QPushButton("Deselect All")
        self.btn_sel_all.clicked.connect(self._select_all_queries)
        btn_remove_query = QPushButton("✕ Remove")
        btn_remove_query.setObjectName("btn_delete")  # filled red
        btn_remove_query.clicked.connect(self._remove_query)
        self.btn_search = QPushButton("▶ Search")
        self.btn_search.setObjectName("btn_search")
        self.btn_search.clicked.connect(self._on_search_or_stop)

        for _btn in [self.btn_session_new, self.btn_session_load, self.btn_session_edit,
                     self.btn_sel_all, btn_remove_query, self.btn_search]:
            _btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            vb_btns.addWidget(_btn)

        ql_inner_top.addLayout(vb_btns)

        ql.addLayout(ql_inner_top)

        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.NoFrame)
        status_frame.setFixedHeight(22)
        sf = QHBoxLayout(status_frame)
        sf.setContentsMargins(0, 0, 0, 0)
        self.search_status = QLabel("")
        self.search_status.setObjectName("search_status")
        self.search_status.setFixedWidth(320)
        sf.addWidget(self.search_status)
        sf.addStretch()
        self.lbl_search_progress = QLabel("")
        self.lbl_search_progress.setTextFormat(Qt.PlainText)
        self.lbl_search_progress.setFixedWidth(600)
        sf.addWidget(self.lbl_search_progress)
        ql.addWidget(status_frame)

        self.search_progress = QProgressBar()
        self.search_progress.setVisible(False)
        self.search_progress.setMaximumHeight(14)
        ql.addWidget(self.search_progress)

        # ── Results ──
        grp_results = QGroupBox("Search Results")
        grp_results.setCheckable(True)
        grp_results.setChecked(True)
        grp_results.toggled.connect(lambda on: self._collapse_group(grp_results, on))
        rl = QVBoxLayout(grp_results)
        rl.setSpacing(1)
        rl.setContentsMargins(2, 2, 2, 2)

        hb_result_bar = QHBoxLayout()
        hb_result_bar.setSpacing(4)
        self.lbl_result_count = QLabel("No search results")
        hb_result_bar.addWidget(self.lbl_result_count)

        self.title_filter_lbl = QCheckBox("Filter by Title")
        self.title_filter_lbl.setChecked(True)
        self.title_filter_lbl.toggled.connect(lambda _: self._filter_timer.start())
        hb_result_bar.addWidget(self.title_filter_lbl)
        self.title_filter = QLineEdit()
        self.title_filter.setPlaceholderText("type to filter table rows...")
        self.title_filter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.title_filter.textChanged.connect(self._filter_timer.start)
        hb_result_bar.addWidget(self.title_filter, 1)

        self.chk_title_pass = QCheckBox("Title OK only")
        self.chk_title_pass.setChecked(False)
        self.chk_title_pass.setToolTip("Show only papers that passed the title filter")
        self.chk_title_pass.toggled.connect(lambda _: self._filter_timer.start())
        hb_result_bar.addWidget(self.chk_title_pass)

        self._source_filters = {}
        self._source_menu = QMenu()
        self._source_menu.aboutToShow.connect(self._sync_source_menu)
        has_brave = bool(self.cfg.get("brave_api_key", ""))
        has_core = bool(self.cfg.get("core_api_key", ""))
        _key_gated = {"Brave": has_brave, "CORE": has_core}
        for src_name in ["arXiv", "OpenAlex", "Crossref", "Europe PMC",
                         "Sem. Scholar", "PubMed", "CORE", "Brave", "DuckGo", "Local"]:
            action = self._source_menu.addAction(src_name)
            action.setCheckable(True)
            if src_name in _key_gated and not _key_gated[src_name]:
                action.setChecked(False)
                action.setEnabled(False)
                action.setToolTip(f"{src_name} API key not set — configure in the Settings tab")
            else:
                action.setChecked(True)
            action.toggled.connect(lambda _, a=src_name: self._on_source_toggle(a))
            self._source_filters[src_name] = action
        self._source_menu.addSeparator()
        sel_all = self._source_menu.addAction("Select All")
        sel_all.triggered.connect(lambda: self._set_all_sources(True))
        desel_all = self._source_menu.addAction("Deselect All")
        desel_all.triggered.connect(lambda: self._set_all_sources(False))
        self.btn_source_filter = QPushButton("Sources  ▼")
        self.btn_source_filter.setMenu(self._source_menu)
        self.btn_source_filter.setToolTip("Filter results by source")
        hb_result_bar.addWidget(self.btn_source_filter)

        self.btn_toggle_all = QPushButton("Select All")
        self.btn_toggle_all.clicked.connect(self._toggle_all_checked)
        hb_result_bar.addWidget(self.btn_toggle_all)
        self.btn_invert = QPushButton("Invert")
        self.btn_invert.clicked.connect(self._invert_selection)
        hb_result_bar.addWidget(self.btn_invert)

        self.btn_refresh_table = QPushButton()
        self.btn_refresh_table.setObjectName("btn_refresh_table")
        self.btn_refresh_table.setFixedSize(28, 28)
        self.btn_refresh_table.setIcon(QIcon(icon("refresh.png")))
        self.btn_refresh_table.setIconSize(self.btn_refresh_table.size())
        self.btn_refresh_table.setToolTip("Refresh table display")
        self.btn_refresh_table.clicked.connect(self._on_refresh_table)
        hb_result_bar.addWidget(self.btn_refresh_table)

        rl.addLayout(hb_result_bar)

        self.results_table = QTableWidget(0, 8)
        self.results_table.setHorizontalHeaderLabels(["", "Title", "Open", "Source", "Year", "Filter", "Match", "Relevance"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.results_table.setColumnWidth(0, 44)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.results_table.setColumnWidth(1, 400)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.results_table.setColumnWidth(2, 50)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        self.results_table.setColumnWidth(3, 100)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.results_table.setColumnWidth(4, 45)
        self.results_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self.results_table.setColumnWidth(5, 55)
        self.results_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Fixed)
        self.results_table.setColumnWidth(6, 55)
        self.results_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSortIndicatorShown(True)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.horizontalHeader().setMinimumSectionSize(40)
        self.results_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.results_table.horizontalHeader().sectionResized.connect(
            self._make_resize_guard(self.results_table, 7, 60,
                                    {3: 80, 4: 40, 5: 42, 6: 42}))
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(self._result_context_menu)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.verticalHeader().setDefaultSectionSize(36)
        self.results_table.horizontalHeader().sectionClicked.connect(self._on_result_header_clicked)
        self._sort_col = -1
        self._sort_desc = True
        self._abstract_timer = QTimer()
        self._abstract_timer.setSingleShot(True)
        self._abstract_timer.setInterval(100)
        self._abstract_timer.timeout.connect(self._show_abstract)
        self.results_table.itemSelectionChanged.connect(self._abstract_timer.start)
        self.results_table.cellClicked.connect(self._on_result_cell_clicked)

        self.abstract_panel = QLabel("")
        self.abstract_panel.setWordWrap(True)
        self.abstract_panel.setMaximumHeight(60)
        self.abstract_panel.setVisible(False)
        self.abstract_panel.setObjectName("abstract_panel")
        self.btn_close_abstract = QPushButton("\u2715")
        self.btn_close_abstract.setFixedSize(20, 20)
        self.btn_close_abstract.setToolTip("Dismiss abstract")
        self.btn_close_abstract.setObjectName("btn_close_abstract")
        self.btn_close_abstract.setStyleSheet("QPushButton { border: none; font-size: 12px; padding: 0; }")
        self.btn_close_abstract.clicked.connect(lambda: self.abstract_container.setVisible(False))
        self.btn_close_abstract.setVisible(False)
        self.btn_close_abstract.setCursor(Qt.PointingHandCursor)
        self.abstract_container = QWidget()
        self.abstract_container.setMaximumHeight(60)
        self.abstract_container.setVisible(False)
        self.abstract_container.setObjectName("abstract_container")
        al = QHBoxLayout(self.abstract_container)
        al.setContentsMargins(0, 0, 2, 0)
        al.setSpacing(0)
        al.addWidget(self.abstract_panel, 1)
        al.addWidget(self.btn_close_abstract, 0, Qt.AlignTop)

        # --- Search Results tab container ---
        search_tab_widget = QWidget()
        stl = QVBoxLayout(search_tab_widget)
        stl.setContentsMargins(0, 0, 0, 0)
        stl.setSpacing(2)
        stl.addWidget(self.results_table)
        stl.addWidget(self.abstract_container)

        # --- Downloaded Papers tab ---
        downloaded_widget = QWidget()
        dvl = QVBoxLayout(downloaded_widget)
        dvl.setContentsMargins(0, 4, 0, 0)
        dvl.setSpacing(4)

        dl_header = QHBoxLayout()
        self.lbl_dl_tab_count = QLabel("No session loaded")
        self.lbl_dl_tab_count.setStyleSheet("color: #888; font-size: 11px;")
        dl_header.addWidget(self.lbl_dl_tab_count)
        dl_header.addStretch()
        self.btn_refresh_dl = QPushButton("\u21bb Refresh")
        self.btn_refresh_dl.setFixedHeight(24)
        self.btn_refresh_dl.setToolTip("Rescan the session download folder")
        self.btn_refresh_dl.clicked.connect(self._refresh_downloaded_tab)
        dl_header.addWidget(self.btn_refresh_dl)
        dvl.addLayout(dl_header)

        self.downloaded_tree = QTreeWidget()
        self.downloaded_tree.setHeaderLabels(["File / Title", "Size", "Modified"])
        self.downloaded_tree.setColumnWidth(0, 440)
        self.downloaded_tree.setColumnWidth(1, 70)
        self.downloaded_tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.downloaded_tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.downloaded_tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.downloaded_tree.setAlternatingRowColors(True)
        self.downloaded_tree.setUniformRowHeights(True)
        self.downloaded_tree.setRootIsDecorated(True)
        self.downloaded_tree.itemDoubleClicked.connect(self._on_dl_item_open)
        self.downloaded_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.downloaded_tree.customContextMenuRequested.connect(self._on_dl_context_menu)
        dvl.addWidget(self.downloaded_tree)

        # --- Tab widget wrapping both ---
        self.results_tabs = QTabWidget()
        self.results_tabs.addTab(search_tab_widget, "Search Results")
        self.results_tabs.addTab(downloaded_widget, "Downloaded")
        self.results_tabs.currentChanged.connect(self._on_results_tab_changed)
        rl.addWidget(self.results_tabs)

        hb_dl_btns = QHBoxLayout()
        hb_dl_btns.setSpacing(2)
        self.btn_download_selected = QPushButton("\u2b07 Selected")
        self.btn_download_selected.setObjectName("btn_download")
        self.btn_download_selected.setToolTip("Download only the papers you checked in the table")
        self.btn_download_selected.clicked.connect(lambda: self._on_download(selected_only=True))
        self.btn_delete_selected = QPushButton("\U0001f5d1 Delete")
        self.btn_delete_selected.setObjectName("btn_delete")  # filled red
        self.btn_delete_selected.setToolTip("Remove the checked papers from the search results (does not delete downloaded files)")
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)
        self.btn_download_by_score = QPushButton("\u2b07 Score")
        self.btn_download_by_score.setObjectName("btn_download")
        self.btn_download_by_score.setToolTip("Download all papers above Min Score regardless of title filter")
        self.btn_download_by_score.clicked.connect(lambda: self._on_download(by_score=True))
        self.btn_download_all = QPushButton("\u2b07 All")
        self.btn_download_all.setObjectName("btn_download")
        self.btn_download_all.setToolTip("Download all visible papers in the table")
        self.btn_download_all.clicked.connect(lambda: self._on_download(selected_only=False))

        self.btn_rate = QPushButton("\u2605 Relevance")
        self.btn_rate.setObjectName("btn_rate")
        self.btn_rate.setToolTip(
            "LLM scores each paper's relevance to your research (0-100)\n\n"
            "90-100: Same core problem, same domain, similar methods\n"
            "70-89: Same core problem, same domain, different approach\n"
            "50-69: Related sub-problem in our domain\n"
            "25-49: Same broad domain, different specific problem\n"
            "0-24: Different problem entirely\n\n"
            "Depth 2-3: multi-turn analysis with reasoning"
        )
        self.btn_rate.clicked.connect(self._on_rate_relevance)

        self.lbl_depth = QLabel("Depth:")
        self.spin_depth = QSpinBox()
        self.spin_depth.setRange(1, 3)
        self.spin_depth.setValue(1)
        self.spin_depth.setFixedWidth(40)
        self.spin_depth.setToolTip(
            "Scoring depth:\n"
            "1 — single LLM call (fast)\n"
            "2 — compare then score (recommended)\n"
            "3 — analyze, compare, then score (most thorough)"
        )
        self.spin_depth.valueChanged.connect(lambda v: self.cfg.set("scoring_depth", v))

        self.btn_stop_action = QPushButton("\u23f9 Stop")
        self.btn_stop_action.setObjectName("btn_stop")
        self.btn_stop_action.setVisible(False)
        self.btn_stop_action.clicked.connect(self._on_stop_action)

        self.score_threshold = QSpinBox()
        self.score_threshold.setObjectName("score_threshold")
        self.score_threshold.setRange(0, 100)
        self.score_threshold.setValue(self.cfg.get("score_threshold", 50))
        self.score_threshold.setSuffix("%")
        self.score_threshold.setFixedWidth(58)
        self.score_threshold.setToolTip(
            "Minimum relevance score — papers below this are hidden when 'Show \u2265' is checked\n\n"
            "90–100: Same core problem, same domain, similar methods\n"
            "70–89: Same core problem, same domain, different approach\n"
            "50–69: Related sub-problem in our domain\n"
            "25–49: Same broad domain, different specific problem\n"
            "0–24: Different problem entirely"
        )
        self.score_threshold.valueChanged.connect(self._filter_timer.start)
        self.score_threshold.valueChanged.connect(self._update_score_button)
        self.score_threshold.valueChanged.connect(lambda v: self.cfg.set("score_threshold", v))

        self.dl_progress = QProgressBar()
        self.dl_progress.setObjectName("dl_progress")
        self.dl_progress.setVisible(False)
        self.dl_progress.setMaximumHeight(18)
        self.dl_status = QLabel("")
        self.dl_status.setObjectName("dl_status")

        hb_dl_btns.addWidget(self.btn_download_selected)
        hb_dl_btns.addWidget(self.btn_delete_selected)
        hb_dl_btns.addWidget(self.btn_download_by_score)
        hb_dl_btns.addWidget(self.btn_download_all)

        self.btn_add_pdfs = QPushButton("+ Add PDF(s)")
        self.btn_add_pdfs.setObjectName("btn_secondary")
        self.btn_add_pdfs.setStyleSheet("QPushButton { color: #27ae60; font-weight: bold; }")
        self.btn_add_pdfs.setToolTip("Select one or more local PDF files to inject into results")
        self.btn_add_pdfs.clicked.connect(self._on_add_pdfs)
        hb_dl_btns.addWidget(self.btn_add_pdfs)

        self.btn_add_folder = QPushButton("+ Add Folder")
        self.btn_add_folder.setObjectName("btn_secondary")
        self.btn_add_folder.setStyleSheet("QPushButton { color: #2980b9; font-weight: bold; }")
        self.btn_add_folder.setToolTip("Import all PDFs from a folder, preserving topic structure where possible")
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        hb_dl_btns.addWidget(self.btn_add_folder)

        hb_dl_btns.addWidget(self.btn_rate)

        depth_grp = QHBoxLayout()
        depth_grp.setSpacing(1)
        depth_grp.setContentsMargins(0, 0, 0, 0)
        depth_grp.addWidget(self.lbl_depth)
        depth_grp.addWidget(self.spin_depth)
        hb_dl_btns.addLayout(depth_grp)

        hb_dl_btns.addWidget(self.btn_stop_action)
        sc_grp = QHBoxLayout()
        sc_grp.setSpacing(2)
        sc_grp.setContentsMargins(0,0,0,0)
        lb_score = QLabel("Min:")
        lb_score.setObjectName("sc_bold_label")
        lb_score.setToolTip("Minimum relevance score — papers below are hidden when 'Show \u2265' is checked")
        sc_grp.addWidget(lb_score)
        sc_grp.addWidget(self.score_threshold)
        self.chk_score_filter = QCheckBox("Show \u2265")
        self.chk_score_filter.setObjectName("chk_score_filter")
        self.chk_score_filter.setToolTip("Display only papers with relevance score at or above the minimum")
        self.chk_score_filter.toggled.connect(lambda _: self._filter_timer.start())
        sc_grp.addWidget(self.chk_score_filter)
        hb_dl_btns.addLayout(sc_grp)
        hb_dl_btns.addWidget(self.dl_progress, 1)
        hb_dl_btns.addWidget(self.dl_status)
        rl.addLayout(hb_dl_btns)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(grp_queries)
        splitter.addWidget(grp_results)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

    def _make_resize_guard(self, table, last_col, min_last, col_mins=None):
        """Return a sectionResized handler that enforces per-column minimums
        and prevents the last column from being squeezed below min_last."""
        def guard(logical_index, old_size, new_size):
            header = table.horizontalHeader()
            if col_mins and logical_index in col_mins:
                if new_size < col_mins[logical_index]:
                    header.blockSignals(True)
                    header.resizeSection(logical_index, col_mins[logical_index])
                    header.blockSignals(False)
                    return
            if logical_index == last_col:
                return
            total_other = sum(
                header.sectionSize(i)
                for i in range(header.count())
                if i != last_col
            )
            available = table.viewport().width() - min_last
            if total_other > available:
                excess = total_other - available
                floor = (col_mins or {}).get(logical_index, header.minimumSectionSize())
                clamped = max(int(new_size - excess), floor)
                header.blockSignals(True)
                header.resizeSection(logical_index, clamped)
                header.blockSignals(False)
        return guard

    # ── Session ──

    def _session_new(self):
        dlg = SessionCreatorDialog(self.cfg, self)
        if dlg.exec() != QDialog.Accepted:
            return

        result = dlg.get_result()
        self._session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._session_context = result["context"]
        self._session_intent = result["intent"]
        self._session_focus_keywords = result.get("focus_keywords", "")
        self._session_avoid_topics = result.get("avoid_topics", "")
        self._session_paper_data = result.get("paper_data", {})
        self._session_paper_path = result.get("paper_path", "")
        self._session_paper_pages = result.get("paper_pages", 0)
        self._session_paper_size_mb = result.get("paper_size_mb", 0.0)
        self._session_paper_titles = result.get("paper_titles", [])
        self._session_paper_topic_name = result.get("paper_topic_name", "")
        self._session_agentic_log = result.get("agentic_log", "")
        self.cfg.set("our_work_context", self._session_context)
        self.cfg.set("session_intent", self._session_intent)
        session_folder = re.sub(r'[\\/*?:"<>|]', '_', result["name"]) + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        self.cfg.set("summary_session_folder", session_folder)
        self.cfg.set("session_download_name", re.sub(r'[\\/*?:"<>|]', '_', result["name"]))
        self._queries.clear()
        self._search_results.clear()
        self.query_table.setRowCount(0)
        self.results_table.setRowCount(0)

        for qd in result["queries"]:
            self._normalize_query(qd)
            self._queries.append(qd)
            self._add_query_to_table(qd)

        direct_papers = result.get("direct_papers", [])
        if direct_papers:
            topic = result.get("paper_topic_name", "My Papers") or "My Papers"
            for paper in direct_papers:
                paper["query_key"] = "Direct Lookup"
                paper["output_folder"] = topic
                paper["must_contain"] = []
                paper["must_not"] = []
                paper["relevance_threshold"] = 2
                paper["filter_passed"] = True
                paper["file_exists"] = False
                paper["pinned"] = True
                self._search_results.append(paper)
            self._populate_results_table()
            self._update_result_count()
            self._apply_filters()
            self._renumber_visible_rows()
            self.log.emit(f"Added {len(direct_papers)} directly-resolved paper(s) to results")

        # A brand-new session starts with a clean Audit tab — otherwise the
        # previous session's audit lingers and gets saved into this new one.
        if self._audit_tab is not None:
            self._audit_tab.set_session_data({})
        self._session_save()
        self.lbl_session.setText(f"Session: {result['name']}")
        self.log.emit(f"Created session: {result['name']} ({len(result['queries'])} queries)")

    def _session_edit(self):
        name = self.lbl_session.text().replace("Session: ", "").strip()
        existing = {
            "name": name if name != "[None]" else "",
            "context": self._session_context,
            "intent": self._session_intent,
            "focus_keywords": self._session_focus_keywords,
            "avoid_topics": self._session_avoid_topics,
            "queries": list(self._queries),
            "paper_data": self._session_paper_data,
            "paper_path": self._session_paper_path,
            "paper_pages": self._session_paper_pages,
            "paper_size_mb": self._session_paper_size_mb,
            "paper_titles": self._session_paper_titles,
            "paper_topic_name": self._session_paper_topic_name,
            "direct_papers": [p for p in self._search_results if p.get("query_key") == "Direct Lookup"],
            "agentic_log": getattr(self, '_session_agentic_log', ""),
        }
        dlg = SessionCreatorDialog(self.cfg, self, existing=existing)
        if dlg.exec() != QDialog.Accepted:
            return

        result = dlg.get_result()
        self._session_context = result["context"]
        self._session_intent = result["intent"]
        self._session_focus_keywords = result.get("focus_keywords", "")
        self._session_avoid_topics = result.get("avoid_topics", "")
        self._session_paper_data = result.get("paper_data", {})
        self._session_paper_path = result.get("paper_path", "")
        self._session_paper_pages = result.get("paper_pages", 0)
        self._session_paper_size_mb = result.get("paper_size_mb", 0.0)
        self._session_paper_titles = result.get("paper_titles", [])
        self._session_paper_topic_name = result.get("paper_topic_name", "")
        self._session_agentic_log = result.get("agentic_log", "")
        self.cfg.set("our_work_context", self._session_context)
        self.cfg.set("session_intent", self._session_intent)
        self.lbl_session.setText(f"Session: {result['name']}")

        self._queries.clear()
        self.query_table.setRowCount(0)
        for qd in result["queries"]:
            self._normalize_query(qd)
            self._queries.append(qd)
            self._add_query_to_table(qd)

        direct_papers = result.get("direct_papers", [])
        if direct_papers:
            topic = result.get("paper_topic_name", "My Papers") or "My Papers"
            existing_ids = {p.get("id", "") for p in self._search_results}
            for paper in direct_papers:
                if paper.get("id", "") in existing_ids:
                    continue  # avoid duplicate on re-edit
                paper["query_key"] = "Direct Lookup"
                paper["output_folder"] = topic
                paper["must_contain"] = []
                paper["must_not"] = []
                paper["relevance_threshold"] = 2
                paper["filter_passed"] = True
                paper["file_exists"] = False
                paper["pinned"] = True
                self._search_results.append(paper)
            self._populate_results_table()
            self._update_result_count()
            self._apply_filters()
            self._renumber_visible_rows()

        self._session_save()
        self.log.emit(f"Session updated: {result['name']} ({len(result['queries'])} queries)")

    def _session_save(self):
        name = self.lbl_session.text().replace("Session: ", "").strip() or "Research Session"
        cfg_context = self.cfg.get("our_work_context", "").strip()
        cfg_intent = self.cfg.get("session_intent", "").strip()
        if cfg_context:
            self._session_context = cfg_context
        if cfg_intent:
            self._session_intent = cfg_intent
        data = {
            "name": name,
            "context": self._session_context,
            "intent": self._session_intent,
            "focus_keywords": self._session_focus_keywords,
            "avoid_topics": self._session_avoid_topics,
            "queries": self._queries,
            "results": self._search_results,
            "paper_data": self._session_paper_data,
            "paper_path": self._session_paper_path,
            "paper_pages": self._session_paper_pages,
            "paper_size_mb": self._session_paper_size_mb,
            "paper_titles": self._session_paper_titles,
            "paper_topic_name": self._session_paper_topic_name,
            "summ_chk_similarity": self.cfg.get("summ_chk_similarity", True),
            "summ_chk_novelty": self.cfg.get("summ_chk_novelty", True),
            "summ_chk_methodology": self.cfg.get("summ_chk_methodology", False),
            "summ_chk_gaps": self.cfg.get("summ_chk_gaps", False),
            "summ_selected_mode": self.cfg.get("summ_selected_mode", "per_paper"),
            "score_threshold": self.score_threshold.value(),
            "scoring_depth": self.spin_depth.value(),
            "title_ok_only": self.chk_title_pass.isChecked(),
            "title_filter_text": self.title_filter.text(),
            "title_filter_enabled": self.title_filter_lbl.isChecked(),
            "source_filters": {name: act.isChecked() for name, act in self._source_filters.items()},
            "agentic_log": getattr(self, '_session_agentic_log', ""),
            "audit": self._audit_tab.get_session_data() if self._audit_tab else {},
        }
        sid = self._session_id or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._session_id = sid
        self._session_manager.save(sid, data)
        self.cfg.set("last_session", sid)
        self.cfg.set("our_work_context", self._session_context)
        self.log.emit(f"Session saved: {data['name']}")

    def _normalize_query(self, qd: dict) -> dict:
        qd.setdefault("max_results", self.cfg.get("default_max_results", 100))
        qd.setdefault("max_size_mb", self.cfg.get("default_max_size_mb", 100.0))
        qd.setdefault("relevance_threshold", self.cfg.get("default_relevance_threshold", 2))
        qd.setdefault("force_plus", self.cfg.get("default_force_plus", False))
        qd.setdefault("after_date", self.cfg.get("default_after_date", ""))
        qd.setdefault("output_folder", qd.get("name", ""))
        qd.setdefault("must_contain", [])
        qd.setdefault("must_not", [])
        qd.setdefault("and_terms", qd.get("query", "").split() if qd.get("query") else [])
        qd.setdefault("or_terms", [])
        qd.setdefault("language", "en")
        qd["sources"] = self.cfg.get("default_sources", ["arxiv", "semantic_scholar", "web", "brave", "pubmed"])
        return qd

    def _session_load(self):
        sessions = self._session_manager.list_sessions()
        if not sessions:
            QMessageBox.information(self, "No Sessions", "No saved sessions found.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Load Session")
        dlg.setMinimumWidth(550)
        dl = QVBoxLayout(dlg)

        lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.SingleSelection)
        for s in sessions:
            item_text = f"{s['name']}  —  {s['query_count']} queries, {s['result_count']} results  ({s['created']})"
            item = QListWidgetItem(item_text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, s)
            lst.addItem(item)
        lst.itemClicked.connect(lambda item: lst.setCurrentItem(item))
        dl.addWidget(lst)

        hb_btns = QHBoxLayout()
        btn_delete = QPushButton("Delete Checked")
        btn_delete.setObjectName("btn_remove")
        hb_btns.addStretch()
        hb_btns.addWidget(btn_delete)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        hb_btns.addWidget(btns)
        dl.addLayout(hb_btns)

        def _delete_checked():
            to_delete = []
            for i in range(lst.count()):
                item = lst.item(i)
                if item.checkState() == Qt.Checked:
                    to_delete.append((i, item.data(Qt.UserRole)))

            if not to_delete:
                QMessageBox.information(dlg, "None Checked", "Check the sessions you want to delete first.")
                return

            names = [d["name"] for _, d in to_delete]
            reply = QMessageBox.question(
                dlg, "Delete Sessions",
                f"Delete {len(to_delete)} session(s)?\n\n{', '.join(names)}",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

            for i, session in reversed(to_delete):
                self._session_manager.delete(session["id"])
                lst.takeItem(i)
                self.log.emit(f"Deleted session: {session['name']}")
                if self._session_id == session["id"]:
                    self._session_id = None
                    self._session_context = ""
                    self._session_intent = ""
                    self._session_focus_keywords = ""
                    self._session_avoid_topics = ""
                    self._session_paper_data = {}
                    self._session_paper_path = ""
                    self._session_paper_pages = 0
                    self._session_paper_size_mb = 0.0
                    self._session_paper_titles = []
                    self._session_paper_topic_name = ""
                    self._session_agentic_log = ""
                    self._queries.clear()
                    self._search_results.clear()
                    self.query_table.setRowCount(0)
                    self.results_table.setRowCount(0)
                    self.lbl_session.setText("Session: [None]")
                if self.cfg.get("last_session", "") == session["id"]:
                    self.cfg.set("last_session", "")

        btn_delete.clicked.connect(_delete_checked)

        if dlg.exec() == QDialog.Accepted:
            load_item = lst.currentItem()
            if not load_item:
                for i in range(lst.count()):
                    item = lst.item(i)
                    if item.checkState() == Qt.Checked:
                        load_item = item
                        break
            if load_item:
                session = load_item.data(Qt.UserRole)
                data = self._session_manager.load(session["id"])
                if data:
                    self._restore_session(data, session["id"])
                    self.cfg.set("last_session", session["id"])
                    self.log.emit(f"Loaded session: {data['name']}")

    def _load_last_session(self):
        last = self.cfg.get("last_session", "")
        if last:
            data = self._session_manager.load(last)
            if data:
                self._restore_session(data, last)

    def _restore_session(self, data: dict, session_id: str):
        self._session_id = session_id
        self._session_context = data.get("context", "")
        self._session_intent = data.get("intent", "")
        self._session_focus_keywords = data.get("focus_keywords", "")
        self._session_avoid_topics = data.get("avoid_topics", "")
        self._session_paper_data = data.get("paper_data", {})
        self._session_paper_path = data.get("paper_path", "")
        self._session_paper_pages = data.get("paper_pages", 0)
        self._session_paper_size_mb = data.get("paper_size_mb", 0.0)
        self._session_paper_titles = data.get("paper_titles", [])
        self._session_paper_topic_name = data.get("paper_topic_name", "")
        self._session_agentic_log = data.get("agentic_log", "")
        for key, default in [
            ("summ_chk_similarity", True), ("summ_chk_novelty", True),
            ("summ_chk_methodology", False), ("summ_chk_gaps", False),
            ("summ_selected_mode", "per_paper"),
            ("score_threshold", 50),
        ]:
            if key in data:
                self.cfg.set(key, data[key])
        self.score_threshold.blockSignals(True)
        self.score_threshold.setValue(data.get("score_threshold", self.cfg.get("score_threshold", 50)))
        self.score_threshold.blockSignals(False)
        self.spin_depth.blockSignals(True)
        self.spin_depth.setValue(data.get("scoring_depth", 1))
        self.spin_depth.blockSignals(False)
        self.chk_title_pass.setChecked(data.get("title_ok_only", False))
        self.title_filter.setText(data.get("title_filter_text", ""))
        self.title_filter_lbl.setChecked(data.get("title_filter_enabled", True))
        saved_src_filters = data.get("source_filters", {})
        for name, act in self._source_filters.items():
            if name in saved_src_filters:
                act.blockSignals(True)
                act.setChecked(saved_src_filters[name])
                act.blockSignals(False)
        self.btn_source_filter.setText(self._source_status_text())
        self._update_score_button()
        self.cfg.set("our_work_context", self._session_context)
        self.cfg.set("session_intent", self._session_intent)
        self.lbl_session.setText(f"Session: {data.get('name', 'Unnamed')}")

        self._queries = data.get("queries", [])
        self._search_results = []
        self.query_table.setRowCount(0)
        for q in self._queries:
            self._normalize_query(q)
            self._add_query_to_table(q)

        results = data.get("results", [])
        if results:
            self._search_results = results
            self._populate_results_table()
            self._apply_filters()
            QTimer.singleShot(0, lambda r=results: self._check_files_and_refresh(r))

        self.cfg.set("last_session", session_id)
        if not self.cfg.get("summary_session_folder", ""):
            session_folder = re.sub(r'[\\/*?:"<>|]', '_', data.get("name", "session")) + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            self.cfg.set("summary_session_folder", session_folder)
        self.cfg.set("session_download_name", re.sub(r'[\\/*?:"<>|]', '_', data.get("name", "session")))
        if self._audit_tab is not None:
            self._audit_tab.set_session_data(data.get("audit", {}))
        QTimer.singleShot(0, self._refresh_downloaded_tab)

    def _check_result_file(self, paper: dict) -> bool:
        root = self._session_root()
        if not root:
            return False
        folder = paper.get("output_folder", "")
        target_dir = os.path.join(root, folder)
        if not os.path.isdir(target_dir):
            return False
        paper_id = paper.get("id", "")
        if paper_id:
            from research_downloader.registry import Registry
            try:
                registry = Registry(os.path.join(target_dir, "downloads_registry.db"))
                if registry.is_downloaded(paper_id):
                    return True
            except Exception:
                pass
        for f in os.listdir(target_dir):
            if f.lower().endswith(".pdf"):
                return True
        return False

    def _check_result_files_batch(self, results: list):
        """Set file_exists on all results, batching filesystem access by folder.

        Reduces N SQLite opens + N listdirs to K opens + K listdirs where K = unique folders.
        """
        from collections import defaultdict
        root = self._session_root()
        if not root:
            for r in results:
                r["file_exists"] = False
            return

        folder_groups = defaultdict(list)
        for r in results:
            folder_groups[r.get("output_folder", "")].append(r)

        for folder, papers in folder_groups.items():
            target_dir = os.path.join(root, folder)
            if not os.path.isdir(target_dir):
                for p in papers:
                    p["file_exists"] = False
                continue

            try:
                pdf_files = {f for f in os.listdir(target_dir) if f.lower().endswith(".pdf")}
            except OSError:
                pdf_files = set()

            registry = None
            try:
                from research_downloader.registry import Registry
                registry = Registry(os.path.join(target_dir, "downloads_registry.db"))
            except Exception:
                pass

            for p in papers:
                pid = p.get("id", "")
                found = False
                if pid and registry:
                    try:
                        found = registry.is_downloaded(pid)
                    except Exception:
                        pass
                if not found:
                    found = bool(pdf_files)
                p["file_exists"] = found

    def _check_files_and_refresh(self, results: list):
        """Run file-existence checks after table renders, then update title colours."""
        self._check_result_files_batch(results)
        self.results_table.setUpdatesEnabled(False)
        for row, paper in enumerate(self._search_results):
            if not paper.get("file_exists"):
                continue
            title_item = self.results_table.item(row, 1)
            cb_item = self.results_table.item(row, 0)
            if title_item:
                title_item.setForeground(Qt.darkGreen)
                tip = title_item.toolTip()
                if not tip.startswith("✓"):
                    title_item.setToolTip("✓ Downloaded — " + (paper.get("title") or ""))
            if cb_item and cb_item.checkState() == Qt.Checked:
                cb_item.setCheckState(Qt.Unchecked)
        self.results_table.setUpdatesEnabled(True)

    # ── Queries ──

    def _add_query(self, data=None):
        dlg = QueryBuilderDialog(data, self, config_manager=self.cfg)
        if dlg.exec() == QDialog.Accepted:
            qd = dlg.get_data()
            self._normalize_query(qd)
            self._queries.append(qd)
            self._add_query_to_table(qd)
            self._session_save()

    def _add_query_to_table(self, qd: dict):
        row = self.query_table.rowCount()
        self.query_table.insertRow(row)
        cb = QTableWidgetItem()
        cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        cb.setCheckState(Qt.Checked)
        self.query_table.setItem(row, 0, cb)
        self.query_table.setItem(row, 1, QTableWidgetItem(qd["name"]))
        self.query_table.setItem(row, 2, QTableWidgetItem(qd["query"]))
        self.query_table.setItem(row, 3, QTableWidgetItem(", ".join(qd.get("must_contain", []))))
        self.query_table.setItem(row, 4, QTableWidgetItem(", ".join(qd.get("must_not", []))))
        self.query_table.setItem(row, 5, QTableWidgetItem(", ".join(qd["sources"])))

    def _edit_query(self, _=None):
        indexes = self.query_table.selectedIndexes()
        if not indexes:
            QMessageBox.information(self, "No Selection", "Select a query row first.")
            return
        row = indexes[0].row()
        if row < 0 or row >= len(self._queries):
            return
        dlg = QueryBuilderDialog(self._queries[row], self, config_manager=self.cfg)
        if dlg.exec() == QDialog.Accepted:
            qd = dlg.get_data()
            self._queries[row] = qd
            self.query_table.item(row, 1).setText(qd["name"])
            self.query_table.item(row, 2).setText(qd["query"])
            self.query_table.item(row, 3).setText(", ".join(qd.get("must_contain", [])))
            self.query_table.item(row, 4).setText(", ".join(qd.get("must_not", [])))
            self.query_table.item(row, 5).setText(", ".join(qd["sources"]))
            self._session_save()

    def _remove_query(self):
        rows = sorted(set(i.row() for i in self.query_table.selectedIndexes()), reverse=True)
        if not rows:
            rows = sorted(
                [r for r in range(self.query_table.rowCount())
                 if self.query_table.item(r, 0) and
                 self.query_table.item(r, 0).checkState() == Qt.Checked],
                reverse=True,
            )
        if not rows:
            QMessageBox.warning(self, "No Selection", "Select or check one or more queries to remove.")
            return
        count = len(rows)
        label = "query" if count == 1 else "queries"
        names = [self.query_table.item(r, 1).text() for r in rows if self.query_table.item(r, 1)]
        reply = QMessageBox.question(
            self, "Remove Queries",
            f"Remove {count} {label}?\n\n{chr(10).join(names)}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for row in rows:
            if row < len(self._queries):
                self.query_table.removeRow(row)
                self._queries.pop(row)
        self._session_save()

    def _select_all_queries(self):
        all_checked = True
        for row in range(self.query_table.rowCount()):
            item = self.query_table.item(row, 0)
            if item is not None and item.checkState() != Qt.Checked:
                all_checked = False
                break
        new_state = Qt.Unchecked if all_checked else Qt.Checked
        for row in range(self.query_table.rowCount()):
            item = self.query_table.item(row, 0)
            if item is None:
                continue
            if not (item.flags() & Qt.ItemIsUserCheckable):
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(new_state)
        self.btn_sel_all.setText("Deselect All" if new_state == Qt.Checked else "Select All")
        self.query_table.viewport().update()

    def _query_context_menu(self, pos):
        menu = QMenu()
        menu.addAction("Edit", self._edit_query)
        menu.addAction("Remove Selected", self._remove_query)
        menu.exec(self.query_table.viewport().mapToGlobal(pos))

    # ── Search ──

    def _on_search_or_stop(self):
        if self._search_active:
            self._search_active = False
            if self._current_search_worker and self._current_search_worker.isRunning():
                try:
                    self._current_search_worker.progress.disconnect()
                    self._current_search_worker.results_ready.disconnect()
                    self._current_search_worker.finished.disconnect()
                    self._current_search_worker.error.disconnect()
                except (TypeError, RuntimeError):
                    pass
                self._current_search_worker.stop()
                if not self._current_search_worker.wait(5000):
                    self._current_search_worker.terminate()
                    self._current_search_worker.wait(2000)
                self._current_search_worker = None
            self.search_progress.setVisible(False)
            self.btn_search.setText("Search")
            self.btn_search.setStyleSheet("")
            self.search_status.setText("Stopped")
            self._update_result_count()
            self.log.emit("\u23f9 Search stopped")
            return

        if not self._queries:
            QMessageBox.warning(self, "No Queries", "Add at least one query first.")
            return

        self.search_progress.setVisible(True)
        self.btn_search.setText("Stop")
        self.btn_search.setStyleSheet("background: #c0392b; color: #fff;")
        self.search_status.setText("Searching...")
        self.lbl_search_progress.setText("")

        creds = {
            "brave_search": {
                "api_key": self.cfg.get("brave_api_key", ""),
                "enabled": True,
            },
            "semantic_scholar": {
                "api_key": self.cfg.get("semantic_scholar_api_key", ""),
                "enabled": True,
            },
            "arxiv": {"enabled": True},
            "pubmed": {
                "api_key": self.cfg.get("pubmed_api_key", ""),
                "email": self.cfg.get("contact_email", ""),
                "enabled": True,
            },
            "core": {"api_key": self.cfg.get("core_api_key", ""), "enabled": True},
            "contact_email": self.cfg.get("contact_email", ""),
        }
        self._search_creds = creds
        live_sources = self.cfg.get("default_sources", ["arxiv", "semantic_scholar", "web", "brave", "pubmed"])
        for q in self._queries:
            q["sources"] = live_sources
        checked = []
        for row in range(self.query_table.rowCount()):
            item = self.query_table.item(row, 0)
            if item and item.checkState() == Qt.Checked and row < len(self._queries):
                checked.append(self._queries[row])
        if not checked:
            QMessageBox.warning(self, "No Queries", "Check at least one query first.")
            return
        self._pending_queries = [{**q, "search_mode": self.cfg.get("search_mode", "academic")} for q in checked]
        self._current_query_idx = 0
        self._search_active = True
        self._current_search_worker = None
        self._run_next_search()

    def _run_next_search(self):
        if self._current_query_idx >= len(self._pending_queries):
            self.search_progress.setVisible(False)
            self.btn_search.setText("Search")
            self.btn_search.setStyleSheet("")
            self._search_active = False
            self._current_search_worker = None
            self._update_result_count()
            self.search_status.setText("")
            self.lbl_search_progress.setText(f"{len(self._search_results)} papers found")
            self.log.emit(f"\U0001f50d Search complete: {len(self._search_results)} papers found")
            return

        q = self._pending_queries[self._current_query_idx]
        total = len(self._pending_queries)
        self.search_status.setText(f"Query {self._current_query_idx + 1}/{total}: {q['name']}")
        self.lbl_search_progress.setText("starting...")
        self.log.emit(f"\U0001f50d [{self._current_query_idx + 1}/{total}] {q['name']}  ({', '.join(q.get('sources',[]))})")

        self._current_search_worker = SearchWorker(q, self._search_creds, self)
        w = self._current_search_worker
        w.progress.connect(self._on_search_progress)
        w.results_ready.connect(self._on_results_ready)
        w.finished.connect(self._on_search_finished)
        err_idx = self._current_query_idx
        w.error.connect(lambda e, i=err_idx: self._on_search_error(e, i))
        w.start()

    def _on_search_progress(self, msg):
        short = msg.strip()
        if short.startswith("Searching "):
            short = short[len("Searching "):].split(":")[0] + "..."
        elif "found " in short and "papers" in short:
            short = short.strip(" -")
        self.lbl_search_progress.setText(short[:100])
        self.log.emit(msg)

    def _on_results_ready(self, query_cfg, results):
        must_contain = query_cfg.get("must_contain", [])
        must_not = query_cfg.get("must_not", [])

        existing_titles = {(r.get("title") or "").strip().lower() for r in self._search_results}
        for paper in results:
            title_key = (paper.get("title") or "").strip().lower()
            if not title_key or title_key in existing_titles:
                continue
            paper["query_key"] = query_cfg["name"]
            paper["output_folder"] = query_cfg.get("output_folder", query_cfg["name"])
            paper["must_contain"] = must_contain
            paper["must_not"] = must_not
            paper["relevance_threshold"] = query_cfg.get("relevance_threshold", 2)
            paper["filter_passed"] = True
            paper["file_exists"] = False
            self._search_results.append(paper)
            existing_titles.add(title_key)

        if self.results_table.rowCount() == 0:
            self._populate_results_table()
        else:
            self._populate_results_incremental()
        self._update_result_count()
        self._apply_filters()
        self.lbl_search_progress.setText(f"{len(self._search_results)} papers so far")
        self._session_save()

    def _on_search_finished(self):
        if not self._search_active:
            return
        self._current_query_idx += 1
        self._run_next_search()
        if not self._search_active:
            self._populate_results_incremental()

    def _on_search_error(self, msg, query_idx):
        self.lbl_search_progress.setText(f"Error: {msg}")
        self.log.emit(f"ERROR: {msg}")
        if query_idx == self._current_query_idx and self._search_active:
            self._current_query_idx += 1
            self._run_next_search()

    def _update_score_button(self):
        min_score = self.score_threshold.value()
        count = 0
        for row in range(self.results_table.rowCount()):
            if self.results_table.isRowHidden(row):
                continue
            if row < len(self._search_results):
                p = self._search_results[row]
                score = p.get("relevance_score")
                if score is not None and score >= 0 and score >= min_score:
                    count += 1
        if count:
            self.btn_download_by_score.setText(f"Download by Score ({count})")
        else:
            self.btn_download_by_score.setText("Download by Score")

    def _build_result_row(self, paper, row, seen_titles, must_contains, must_nots, thresholds,
                          preserve_checked, saved_checks):
        from research_downloader.relevance_filter import passes_title_filter

        cb = QTableWidgetItem()
        cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        title_key = (paper.get("title") or "").strip().lower()
        is_dupe = bool(title_key) and title_key in seen_titles
        if title_key:
            seen_titles[title_key] = row

        qk = paper.get("query_key", "")
        mc = must_contains.get(qk, [])
        mn = must_nots.get(qk, [])
        threshold = thresholds.get(qk, 2)

        title_ok = passes_title_filter(paper.get("title") or "", mc, threshold) if mc else True
        not_ok = True
        if mn:
            title_lower = paper["title"].lower()
            not_ok = not any(kw.lower() in title_lower for kw in mn)

        final_ok = True if paper.get("source") == "Local" else (title_ok and not_ok)
        if paper.get("source") == "Local":
            filter_text = "\u2014"
        elif is_dupe:
            filter_text = "DUP"
        elif final_ok:
            filter_text = "OK"
        elif not title_ok:
            filter_text = "FAIL"
        else:
            filter_text = "EXCL"

        paper["title_filter_ok"] = final_ok

        if preserve_checked and paper["title"] in saved_checks:
            cb.setCheckState(Qt.Checked if saved_checks[paper["title"]] else Qt.Unchecked)
        elif is_dupe:
            cb.setCheckState(Qt.Unchecked)
        elif paper.get("file_exists"):
            cb.setCheckState(Qt.Unchecked)
        elif final_ok:
            cb.setCheckState(Qt.Checked)
        else:
            cb.setCheckState(Qt.Unchecked)

        self.results_table.setItem(row, 0, cb)

        authors = paper.get("authors", [])
        author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "") if authors else ""
        title_display = paper.get("title") or "(no title)"
        if is_dupe:
            title_display = f"[DUP] {title_display}"
        if author_str:
            title_display += f"\n{author_str}"
        title_item = QTableWidgetItem(title_display)
        if is_dupe:
            title_item.setForeground(Qt.red)
        abstract = paper.get("abstract", "")
        tooltip = paper.get("title") or "(no title)"
        if paper.get("file_exists"):
            title_item.setForeground(Qt.darkGreen)
            tooltip = "✓ Downloaded — " + tooltip
            if paper.get("file_size_mb"):
                tooltip += f" ({paper['file_size_mb']:.1f} MB)"
        if authors:
            tooltip += "\n\nAuthors: " + ", ".join(authors[:8])
            if len(authors) > 8:
                tooltip += " et al."
        if abstract:
            tooltip += "\n\n" + abstract[:800]
        title_item.setToolTip(tooltip)
        self.results_table.setItem(row, 1, title_item)

        src_raw = paper.get("source", "")
        src_display = {
            "Web": "DuckGo", "SemanticScholar": "Sem. Scholar", "Local": "Local",
            "EuropePMC": "Europe PMC", "europe_pmc": "Europe PMC",
        }.get(src_raw, src_raw)
        if paper.get("pinned"):
            src_display = f"📌 {src_display}"
        src_item = QTableWidgetItem(src_display)
        src_item.setTextAlignment(Qt.AlignCenter)
        self.results_table.setItem(row, 3, src_item)
        year_item = QTableWidgetItem(str(paper.get("year", "")))
        year_item.setTextAlignment(Qt.AlignCenter)
        self.results_table.setItem(row, 4, year_item)
        filt_item = QTableWidgetItem(filter_text)
        filt_item.setTextAlignment(Qt.AlignCenter)

        if paper.get("source") == "Local":
            filt_item.setForeground(QColor("#666"))
            filt_item.setToolTip("Local PDF")
        elif is_dupe:
            filt_item.setForeground(QColor("#e67e22"))
        elif not final_ok:
            filt_item.setForeground(Qt.red)
            for col in (1, 3, 4, 6, 7):
                item = self.results_table.item(row, col)
                if item:
                    item.setForeground(Qt.darkGray)

        url = paper.get("url", "")
        needs_resolve = paper.get("needs_pdf_resolve", False)
        if url or needs_resolve:
            link_color = "#e67e22" if needs_resolve else "#4a90d9"
            link_tooltip = url + ("\n\n⚠ Landing page — PDF auto-discovered at download" if needs_resolve else "")
            open_item = QTableWidgetItem("Open")
            open_item.setTextAlignment(Qt.AlignCenter)
            open_item.setForeground(QColor(link_color))
            open_item.setToolTip(link_tooltip)
            open_item.setData(Qt.UserRole, url)
            self.results_table.setItem(row, 2, open_item)

        score = paper.get("relevance_score")
        reason = paper.get("score_reason", "")
        if score is not None and score >= 0:
            score_text = f"{score}%"
            score_item = QTableWidgetItem(score_text)
            if score >= 70:
                score_item.setForeground(Qt.darkGreen)
            elif score >= 40:
                score_item.setForeground(Qt.darkYellow)
            else:
                score_item.setForeground(Qt.red)
            score_item.setToolTip(reason if reason else f"Match: {score}%")
        elif score == -1:
            label, tooltip, color = _score_fail_display(reason or "unknown")
            score_item = QTableWidgetItem(label)
            score_item.setToolTip(tooltip)
            score_item.setForeground(color)
        else:
            score_item = QTableWidgetItem("")
        score_item.setTextAlignment(Qt.AlignCenter)
        self.results_table.setItem(row, 6, score_item)

        reason_text = reason if reason else "\u2014"
        reason_item = QTableWidgetItem(reason_text[:100])
        reason_item.setToolTip(reason if reason else "No reasoning extracted")
        self.results_table.setItem(row, 7, reason_item)

        self.results_table.setItem(row, 5, filt_item)

    def _populate_results_table(self, preserve_checked=False):
        saved_checks = {}
        if preserve_checked and self.results_table.rowCount() > 0:
            for r in range(self.results_table.rowCount()):
                item = self.results_table.item(r, 0)
                title_item = self.results_table.item(r, 1)
                if item and title_item:
                    saved_checks[title_item.text().split("\n")[0]] = item.checkState() == Qt.Checked

        self.results_table.itemSelectionChanged.disconnect(self._abstract_timer.start)
        self.results_table.setSortingEnabled(False)
        self.results_table.setUpdatesEnabled(False)
        self.results_table.setRowCount(0)
        must_contains = {q["name"]: q.get("must_contain", []) for q in self._queries}
        must_nots = {q["name"]: q.get("must_not", []) for q in self._queries}
        thresholds = {q["name"]: q.get("relevance_threshold", 2) for q in self._queries}

        seen_titles = {}
        for paper in self._search_results:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self._build_result_row(paper, row, seen_titles, must_contains, must_nots, thresholds,
                                   preserve_checked, saved_checks)

        self._seen_result_titles = seen_titles
        self.results_table.setUpdatesEnabled(True)
        self.results_table.itemSelectionChanged.connect(self._abstract_timer.start)
        self._update_score_button()

    def _populate_results_incremental(self):
        start = self.results_table.rowCount()
        if start >= len(self._search_results):
            return
        must_contains = {q["name"]: q.get("must_contain", []) for q in self._queries}
        must_nots = {q["name"]: q.get("must_not", []) for q in self._queries}
        thresholds = {q["name"]: q.get("relevance_threshold", 2) for q in self._queries}
        seen_titles = getattr(self, '_seen_result_titles', {})
        self.results_table.setSortingEnabled(False)
        self.results_table.setUpdatesEnabled(False)
        for i in range(start, len(self._search_results)):
            paper = self._search_results[i]
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self._build_result_row(paper, row, seen_titles, must_contains, must_nots, thresholds, False, {})
        self._seen_result_titles = seen_titles
        self.results_table.setUpdatesEnabled(True)

    def _show_abstract(self):
        indexes = self.results_table.selectedIndexes()
        if not indexes:
            self.abstract_container.setVisible(False)
            return
        row = indexes[0].row()
        if row < 0 or row >= len(self._search_results):
            self.abstract_container.setVisible(False)
            return
        paper = self._search_results[row]
        authors = paper.get("authors", [])
        abstract = paper.get("abstract", "")
        score = paper.get("relevance_score")
        reason = paper.get("score_reason", "")
        parts = []
        if authors:
            parts.append("Authors: " + ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else ""))
        if abstract:
            parts.append(abstract[:600])
        elif score is None:
            needs_resolve = paper.get("needs_pdf_resolve", False)
            if needs_resolve:
                parts.append("(landing page — PDF will be resolved at scoring/download time)")
            else:
                parts.append("(no abstract — PDF will be fetched for scoring)")
        elif score == -1:
            _label, tooltip, _color = _score_fail_display(reason or "unknown")
            parts.append(f"Score failed: {tooltip}")
        else:
            parts.append(f"(scored {score}% via downloaded PDF)")
        text = " | ".join(parts)
        if not text.strip():
            self.abstract_container.setVisible(False)
        else:
            self.abstract_panel.setText(text)
            self.abstract_container.setVisible(True)
            self.btn_close_abstract.setVisible(True)

    def _result_context_menu(self, pos):
        row = self.results_table.currentRow()
        if row < 0 or row >= len(self._search_results):
            return
        paper = self._search_results[row]
        menu = QMenu()
        if not paper.get("title_filter_ok", True):
            menu.addAction("Override Filter — Mark for Download", lambda: self._override_filter(row))
        else:
            menu.addAction("Flag as Irrelevant", lambda: self._flag_paper(row))
        menu.exec(self.results_table.viewport().mapToGlobal(pos))

    def _override_filter(self, row):
        paper = self._search_results[row]
        paper["title_filter_ok"] = True
        paper["force_download"] = True
        self.results_table.item(row, 0).setCheckState(Qt.Checked)
        self.results_table.item(row, 5).setText("OVERRIDE")
        self.results_table.item(row, 5).setForeground(Qt.darkGreen)
        for col in (1, 3, 4, 6, 7):
            item = self.results_table.item(row, col)
            if item:
                item.setForeground(Qt.black)

    def _flag_paper(self, row):
        self.results_table.item(row, 0).setCheckState(Qt.Unchecked)
        self._search_results[row]["title_filter_ok"] = False
        self.results_table.item(row, 5).setText("FAIL")
        self.results_table.item(row, 5).setForeground(Qt.red)
        for col in (1, 3, 4, 6, 7):
            item = self.results_table.item(row, col)
            if item:
                item.setForeground(Qt.darkGray)

    def _set_all_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        self.btn_toggle_all.setText("Deselect All" if checked else "Select All")
        for row in range(self.results_table.rowCount()):
            if not self.results_table.isRowHidden(row):
                self.results_table.item(row, 0).setCheckState(state)

    def _toggle_all_checked(self):
        all_checked = True
        for row in range(self.results_table.rowCount()):
            if not self.results_table.isRowHidden(row):
                if self.results_table.item(row, 0).checkState() != Qt.Checked:
                    all_checked = False
                    break
        self._set_all_checked(not all_checked)

    @staticmethod
    def _open_url(url: str):
        import webbrowser
        webbrowser.open(url)

    def _on_result_cell_clicked(self, row, col):
        if col != 2:
            return
        item = self.results_table.item(row, 2)
        if item:
            url = item.data(Qt.UserRole)
            if url:
                self._open_url(url)

    @staticmethod
    def _collapse_group(grp, expanded):
        for child in grp.findChildren(QWidget):
            if child is not grp:
                child.setVisible(expanded)
        if not expanded:
            grp.setMinimumHeight(0)
            grp.setMaximumHeight(30)
            grp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            grp.setMinimumHeight(80)
            grp.setMaximumHeight(16777215)
            grp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grp.updateGeometry()
        pw = grp.parentWidget()
        if pw:
            layout = pw.layout()
            if layout:
                layout.invalidate()
                layout.activate()
            pw.updateGeometry()
            window = pw.window()
            if window:
                window.update()

    def _invert_selection(self):
        self.results_table.setUpdatesEnabled(False)
        for row in range(self.results_table.rowCount()):
            if not self.results_table.isRowHidden(row):
                item = self.results_table.item(row, 0)
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        self.results_table.setUpdatesEnabled(True)

    def _on_refresh_table(self):
        if hasattr(self, '_scoring_worker') and self._scoring_worker and self._scoring_worker.isRunning():
            self._apply_filters()
            self.results_table.viewport().repaint()
        else:
            self._populate_results_table(preserve_checked=True)
            self._apply_filters()

    def _on_result_header_clicked(self, col):
        if col in (0, 2, 5):
            return
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = True
        key = {1: "title", 3: "source", 4: "year", 6: "relevance_score"}.get(col)
        if not key:
            return
        if key == "year":
            self._search_results.sort(key=lambda p: str(p.get(key, "")), reverse=self._sort_desc)
        elif key == "relevance_score":
            self._search_results.sort(key=lambda p: p.get(key, -1) if p.get(key, -1) >= 0 else -1, reverse=self._sort_desc)
        else:
            self._search_results.sort(key=lambda p: str(p.get(key, "")).lower(), reverse=self._sort_desc)
        self._populate_results_table(preserve_checked=True)
        self._update_result_count()

        order = Qt.DescendingOrder if self._sort_desc else Qt.AscendingOrder
        self.results_table.horizontalHeader().setSortIndicator(col, order)

        self._apply_filters()

    def _on_source_toggle(self, src_name):
        self._filter_timer.start()

    def _set_all_sources(self, checked):
        for action in self._source_filters.values():
            if action.isEnabled():
                action.setChecked(checked)

    def _sync_source_menu(self):
        self.btn_source_filter.setText(self._source_status_text())

    def _source_status_text(self):
        checked = sum(1 for a in self._source_filters.values() if a.isChecked())
        total = sum(1 for a in self._source_filters.values() if a.isEnabled())
        if checked == total:
            return "Sources  ▼"
        elif checked == 0:
            return "Sources [0] ▼"
        else:
            return f"Sources [{checked}/{total}] ▼"

    def _apply_filters(self):
        self.results_table.setUpdatesEnabled(False)
        active = set()
        for name, act in self._source_filters.items():
            if act.isChecked():
                active.add(name)
        title_text = self.title_filter.text().strip().lower()
        title_filter_on = self.title_filter_lbl.isChecked()
        score_filter_on = self.chk_score_filter.isChecked()
        score_min = self.score_threshold.value()
        title_pass_only = self.chk_title_pass.isChecked()
        for row in range(self.results_table.rowCount()):
            src_item = self.results_table.item(row, 3)
            title_item = self.results_table.item(row, 1)
            score_item = self.results_table.item(row, 6)
            filt_item = self.results_table.item(row, 5)
            src_match = True
            title_match = True
            score_match = True
            filt_match = True
            dup_match = True
            is_local = filt_item and filt_item.text() == "\u2014"
            if src_item:
                src_match = src_item.text() in active
            if filt_item and filt_item.text() == "DUP":
                dup_match = False
            if not is_local:
                if title_filter_on and title_text and title_item:
                    title_match = title_text in title_item.text().lower()
                if title_pass_only and filt_item:
                    filt_match = filt_item.text() == "OK"
            if score_filter_on and score_item:
                try:
                    val = int(score_item.text().replace("%", ""))
                    score_match = val >= score_min
                except ValueError:
                    score_match = False
            self.results_table.setRowHidden(row, not (src_match and title_match and score_match and filt_match and dup_match))
        self._update_visible_count()
        self._renumber_visible_rows()
        self.results_table.setUpdatesEnabled(True)

    def _renumber_visible_rows(self):
        idx = 0
        for row in range(self.results_table.rowCount()):
            item = self.results_table.item(row, 0)
            if item is None:
                continue
            if not self.results_table.isRowHidden(row):
                idx += 1
                item.setText(f" {idx}/{row + 1} ")
            else:
                item.setText("")

    def _update_visible_count(self):
        self._update_result_info()

    def _update_result_count(self):
        self._update_result_info()

    def _update_result_info(self):
        total = len(self._search_results)
        row_count = self.results_table.rowCount()
        if not total:
            self.lbl_result_count.setText("No search results")
            self.lbl_result_count.setToolTip("")
            return

        visible = sum(1 for r in range(row_count) if not self.results_table.isRowHidden(r))
        selected = 0
        dup_count = 0
        title_hidden = 0
        source_hidden = 0
        score_hidden = 0
        filt_hidden = 0

        title_filter_on = self.title_filter_lbl.isChecked()
        title_pass_only = self.chk_title_pass.isChecked()
        score_filter_on = self.chk_score_filter.isChecked()
        title_text = self.title_filter.text().strip().lower()
        active_sources = {n for n, a in self._source_filters.items() if a.isChecked()}

        for r in range(row_count):
            is_hidden = self.results_table.isRowHidden(r)
            chk_item = self.results_table.item(r, 0)
            filt_item = self.results_table.item(r, 5)
            src_item = self.results_table.item(r, 3)
            title_item = self.results_table.item(r, 1)
            score_item = self.results_table.item(r, 6)

            if chk_item and chk_item.checkState() == Qt.Checked and not is_hidden:
                selected += 1

            if not is_hidden:
                continue

            if filt_item and filt_item.text() == "DUP":
                dup_count += 1
                continue

            match_hidden = False
            if src_item and src_item.text() not in active_sources:
                source_hidden += 1
                match_hidden = True
            if not match_hidden and title_filter_on and title_text and title_item:
                if title_text not in title_item.text().lower():
                    title_hidden += 1
                    match_hidden = True
            if not match_hidden and title_pass_only and filt_item:
                if filt_item.text() not in ("OK", "\u2014"):
                    filt_hidden += 1
                    match_hidden = True
            if match_hidden:
                continue
            if score_filter_on and score_item:
                try:
                    val = int(score_item.text().replace("%", ""))
                    if val < self.score_threshold.value():
                        score_hidden += 1
                except ValueError:
                    pass

        ok = sum(1 for p in self._search_results if p.get("title_filter_ok", True))
        flagged = total - ok

        from collections import Counter
        M = {"Web": "DuckGo", "SemanticScholar": "Sem. Scholar"}
        src_counts = Counter(M.get(p.get("source", ""), p.get("source", "?")) for p in self._search_results)
        src_parts = []
        for src in ["arXiv", "DuckGo", "Sem. Scholar", "Brave", "PubMed", "Local"]:
            c = src_counts.get(src, 0)
            if c:
                src_parts.append(f"{src}: {c}")

        from gui.theme_manager import ThemeManager
        tm = ThemeManager()
        is_dark = tm.name == "Dark"
        c_shown = tm.get("success", "#27ae60") if is_dark else "#27ae60"
        c_select = tm.get("primary", "#2980b9") if is_dark else "#2980b9"
        c_title_h = tm.get("action_amber", "#e67e22") if is_dark else "#e67e22"
        c_source_h = tm.get("action_purple", "#8e44ad") if is_dark else "#8e44ad"
        c_filter_h = tm.get("action_teal", "#16a085") if is_dark else "#16a085"
        c_score_h = tm.get("danger", "#c0392b") if is_dark else "#c0392b"
        c_dup = tm.get("text_muted", "#7f8c8d") if is_dark else "#7f8c8d"

        html_parts = [f"<span>{total} total</span>"]
        if visible < total:
            html_parts.append(f'<span style="color:{c_shown}">{visible} shown</span>')
        if selected:
            html_parts.append(f'<span style="color:{c_select}">&#10003; {selected} selected</span>')

        hidden_items = []
        hidden_html = []
        if title_hidden:
            hidden_items.append(f"title {title_hidden}")
            hidden_html.append(f'<span style="color:{c_title_h}">&minus; title {title_hidden}</span>')
        if source_hidden:
            hidden_items.append(f"source {source_hidden}")
            hidden_html.append(f'<span style="color:{c_source_h}">&minus; source {source_hidden}</span>')
        if filt_hidden:
            hidden_items.append(f"filter {filt_hidden}")
            hidden_html.append(f'<span style="color:{c_filter_h}">&minus; filter {filt_hidden}</span>')
        if score_hidden:
            hidden_items.append(f"score {score_hidden}")
            hidden_html.append(f'<span style="color:{c_score_h}">&minus; score {score_hidden}</span>')
        if dup_count:
            hidden_items.append(f"dup {dup_count}")
            hidden_html.append(f'<span style="color:{c_dup}">&minus; dup {dup_count}</span>')

        text = "  |  ".join(html_parts)
        if hidden_html:
            text += "  |  " + " &middot; ".join(hidden_html)

        legend = "Colors: total=default  shown=green  selected=blue  title=orange  source=purple  filter=teal  score=red  dup=gray"
        tooltip = (
            f"{total} total  ({ok} OK, {flagged} flagged)\n"
            f"Showing: {visible}  |  Selected: {selected}\n"
            f"Sources: {', '.join(src_parts) if src_parts else 'none'}"
        )
        if hidden_items:
            tooltip += "\nHidden: " + " · ".join(hidden_items)
        tooltip += f"\n\n{legend}"

        self.lbl_result_count.setText(text)
        self.lbl_result_count.setTextFormat(Qt.RichText)
        self.lbl_result_count.setToolTip(tooltip)

    # ── Download ──

    def _session_root(self) -> str:
        base = self.cfg.get("output_root", "")
        if not base:
            base = os.path.join(os.path.expanduser("~"), ".ResearchForge", "downloads")
        session_name = self.cfg.get("session_download_name", "")
        if session_name:
            return os.path.join(base, session_name)
        return base

    def _on_results_tab_changed(self, index: int):
        if index == 1:
            self._refresh_downloaded_tab()

    def _refresh_downloaded_tab(self):
        self.downloaded_tree.clear()
        root = self._session_root()
        if not root or not os.path.isdir(root):
            self.lbl_dl_tab_count.setText("Session folder not found")
            return

        total = 0
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            self.lbl_dl_tab_count.setText("Cannot read session folder")
            return

        for topic in entries:
            topic_path = os.path.join(root, topic)
            if not os.path.isdir(topic_path):
                continue
            try:
                pdfs = sorted(
                    f for f in os.listdir(topic_path) if f.lower().endswith(".pdf")
                )
            except OSError:
                continue
            if not pdfs:
                continue

            topic_item = QTreeWidgetItem([f"📁  {topic}", "", ""])
            font = topic_item.font(0)
            font.setBold(True)
            topic_item.setFont(0, font)
            topic_item.setExpanded(True)

            for fname in pdfs:
                fpath = os.path.join(topic_path, fname)
                try:
                    size_mb = os.path.getsize(fpath) / (1024 * 1024)
                    mtime = datetime.fromtimestamp(
                        os.path.getmtime(fpath)
                    ).strftime("%Y-%m-%d  %H:%M")
                    size_str = f"{size_mb:.1f} MB"
                except OSError:
                    size_str = "—"
                    mtime = "—"
                child = QTreeWidgetItem([fname, size_str, mtime])
                child.setToolTip(0, f"Double-click to open\n{fpath}")
                child.setData(0, Qt.UserRole, fpath)
                topic_item.addChild(child)
                total += 1

            self.downloaded_tree.addTopLevelItem(topic_item)

        noun = "file" if total == 1 else "files"
        self.lbl_dl_tab_count.setText(f"{total} {noun} on disk")

    def _on_dl_item_open(self, item, column):
        fpath = item.data(0, Qt.UserRole)
        if fpath and os.path.isfile(fpath):
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(fpath))

    def _on_dl_context_menu(self, pos):
        item = self.downloaded_tree.itemAt(pos)
        if not item:
            return
        fpath = item.data(0, Qt.UserRole)
        if not fpath or not os.path.isfile(fpath):
            return
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QDesktopServices, QGuiApplication
        from PySide6.QtCore import QUrl
        menu = QMenu(self)
        act_open = menu.addAction("Open PDF")
        act_folder = menu.addAction("Open Containing Folder")
        menu.addSeparator()
        act_copy = menu.addAction("Copy Path")
        chosen = menu.exec(self.downloaded_tree.viewport().mapToGlobal(pos))
        if chosen == act_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(fpath))
        elif chosen == act_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(fpath)))
        elif chosen == act_copy:
            QGuiApplication.clipboard().setText(fpath)

    def _get_output_root(self) -> str:
        return self._session_root()

    def _on_delete_selected(self):
        """Remove the checked papers from the search results (list only — keeps files)."""
        if hasattr(self, '_current_dl_worker') and self._current_dl_worker and self._current_dl_worker.isRunning():
            QMessageBox.information(self, "Busy", "Wait for the current download to finish.")
            return
        to_delete = []
        for row in range(self.results_table.rowCount()):
            item = self.results_table.item(row, 0)
            if item and item.checkState() == Qt.Checked and row < len(self._search_results):
                to_delete.append(self._search_results[row])
        if not to_delete:
            QMessageBox.information(self, "Nothing Selected", "Check the papers you want to remove first.")
            return
        reply = QMessageBox.question(
            self, "Remove Results",
            f"Remove {len(to_delete)} selected paper(s) from the search results?\n\n"
            "This only clears them from the list — any already-downloaded files are kept.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        doomed = {id(p) for p in to_delete}
        self._search_results = [p for p in self._search_results if id(p) not in doomed]
        self._populate_results_table()
        self._update_result_count()
        self._apply_filters()
        self._session_save()
        self.log.emit(f"Removed {len(to_delete)} paper(s) from results")

    def _on_download(self, selected_only: bool = True, by_score: bool = False):
        if hasattr(self, '_current_dl_worker') and self._current_dl_worker and self._current_dl_worker.isRunning():
            return

        to_download = []
        min_score = self.score_threshold.value()
        for row in range(self.results_table.rowCount()):
            if self.results_table.isRowHidden(row):
                continue
            if row >= len(self._search_results):
                continue
            paper = self._search_results[row]
            if by_score:
                score = paper.get("relevance_score")
                if score is not None and score >= 0 and score >= min_score:
                    to_download.append(paper)
            elif selected_only:
                if self.results_table.item(row, 0).checkState() == Qt.Checked:
                    to_download.append(paper)
            else:
                to_download.append(paper)
        if not to_download:
            QMessageBox.information(self, "Nothing Selected", "No papers selected.")
            return

        output_root = self._get_output_root()
        os.makedirs(output_root, exist_ok=True)

        self.dl_progress.setVisible(True)
        self.dl_progress.setRange(0, len(to_download))
        self.dl_progress.setValue(0)
        self.btn_search.setEnabled(False)
        self.btn_download_selected.setEnabled(False)
        self.btn_download_all.setEnabled(False)
        self.btn_download_by_score.setEnabled(False)
        self.btn_add_pdfs.setEnabled(False)
        self.btn_add_folder.setEnabled(False)
        self.btn_rate.setEnabled(False)
        self.btn_toggle_all.setEnabled(False)
        self.btn_invert.setEnabled(False)
        self.btn_refresh_table.setEnabled(False)
        self.dl_status.setText(f"0 / {len(to_download)}")
        self._download_done = 0
        self._download_total = len(to_download)
        self.btn_stop_action.setVisible(True)

        self._current_dl_worker = DownloadWorker(
            papers=to_download, target_dir=output_root,
            max_size_mb=self.cfg.get("default_max_size_mb", 50.0),
            skip_content_filter=True,
        )
        self._current_dl_worker.paper_done.connect(self._on_batch_paper_done)
        self._current_dl_worker.finished.connect(self._on_batch_all_done)
        self._current_dl_worker.error.connect(lambda e: self.log.emit(f"Download error: {e}"))
        self._current_dl_worker.start()

    def _stop_dl_worker(self):
        if self._current_dl_worker and self._current_dl_worker.isRunning():
            try:
                self._current_dl_worker.paper_done.disconnect()
                self._current_dl_worker.finished.disconnect()
                self._current_dl_worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._current_dl_worker.stop()
            import time as _time
            self._dl_stop_deadline = _time.time() + 5.0
            self._dl_stop_worker = self._current_dl_worker
            self._dl_stop_timer = QTimer(self)
            self._dl_stop_timer.setSingleShot(False)
            self._dl_stop_timer.timeout.connect(self._poll_dl_stop)
            self._dl_stop_timer.start(100)

    def _poll_dl_stop(self):
        worker = getattr(self, '_dl_stop_worker', None)
        if worker is None:
            return
        import time as _time
        worker = getattr(self, '_dl_stop_worker', None)
        deadline = getattr(self, '_dl_stop_deadline', 0)
        if not worker or not worker.isRunning() or _time.time() >= deadline:
            if hasattr(self, '_dl_stop_timer'):
                self._dl_stop_timer.stop()
            if worker and worker.isRunning():
                try:
                    worker.terminate()
                    worker.wait(1000)
                except Exception:
                    pass
            self._current_dl_worker = None
            self._dl_stop_worker = None
            self.dl_progress.setVisible(False)
            self.btn_search.setEnabled(True)
            self.btn_download_selected.setEnabled(True)
            self.btn_download_all.setEnabled(True)
            self.btn_download_by_score.setEnabled(True)
            self.btn_add_pdfs.setEnabled(True)
            self.btn_add_folder.setEnabled(True)
            self.btn_rate.setEnabled(True)
            self.btn_toggle_all.setEnabled(True)
            self.btn_invert.setEnabled(True)
            self.btn_refresh_table.setEnabled(True)
            self.btn_stop_action.setVisible(False)
            self.dl_status.setText(f"\u23f9 Stopped at {self._download_done}/{self._download_total}")

    def _on_batch_paper_done(self, title, success, size_mb=0.0):
        if success:
            self._download_done += 1
        self.dl_progress.setValue(self._download_done)
        self.dl_status.setText(f"{self._download_done} / {self._download_total}")
        bar = _progress_bar(self._download_done, self._download_total)
        status = "OK" if success else "FAIL"
        size = f"  {size_mb:.1f}MB" if size_mb > 0 else ""
        self.log.emit(f"\u2b07 [{self._download_done}/{self._download_total}] {bar} {status}{size}  {title[:80]}")

        if size_mb > 0:
            for row, paper in enumerate(self._search_results):
                if paper.get("title") == title:
                    paper["file_size_mb"] = size_mb
                    dl_item = self.results_table.item(row, 6)
                    if dl_item:
                        dl_item.setToolTip(f"Downloaded \u2014 {size_mb:.1f} MB")
                    break

    def _on_batch_all_done(self):
        self.dl_progress.setVisible(False)
        self.btn_search.setEnabled(True)
        self.btn_download_selected.setEnabled(True)
        self.btn_download_all.setEnabled(True)
        self.btn_download_by_score.setEnabled(True)
        self.btn_add_pdfs.setEnabled(True)
        self.btn_add_folder.setEnabled(True)
        self.btn_rate.setEnabled(True)
        self.btn_toggle_all.setEnabled(True)
        self.btn_invert.setEnabled(True)
        self.btn_refresh_table.setEnabled(True)
        self.btn_stop_action.setVisible(False)
        self.dl_status.setText(f"Done: {self._download_done} downloaded")
        self.log.emit(f"\u2b07  Download complete: {self._download_done}/{self._download_total} papers")
        for paper in self._search_results:
            paper["file_exists"] = self._check_result_file(paper)
            if paper["file_exists"] and not paper.get("file_size_mb"):
                root = self.cfg.get("output_root", "")
                folder = paper.get("output_folder", "")
                target_dir = os.path.join(root, folder)
                paper_id = paper.get("id", "")
                if paper_id:
                    try:
                        from research_downloader.registry import Registry
                        reg = Registry(os.path.join(target_dir, "downloads_registry.db"))
                        fpath = reg.get_filepath(paper_id)
                        if fpath and os.path.exists(fpath):
                            paper["file_size_mb"] = os.path.getsize(fpath) / (1024 * 1024)
                    except Exception:
                        pass
        for row, paper in enumerate(self._search_results):
            if row >= self.results_table.rowCount():
                continue
            title_item = self.results_table.item(row, 1)
            if title_item and paper.get("file_exists"):
                title_item.setForeground(Qt.darkGreen)
        self._apply_filters()
        self._session_save()
        self._refresh_downloaded_tab()
        if self._current_dl_worker:
            self._current_dl_worker = None

    def _on_rate_relevance(self):
        if not self._search_results:
            return
        if not ensure_llm_available(self.cfg, self):
            return
        if not self._session_context.strip():
            QMessageBox.warning(
                self, "Missing Research Description",
                "You must provide a research description before scoring relevance.\n\n"
                "Go to Session → Edit Session and fill in your research context."
            )
            return
        provider_name = self.cfg.get("llm_provider", "LM Studio")
        api_key = get_provider_api_key(provider_name, self.cfg)
        endpoint = self.cfg.get("llm_endpoint", "")
        model = self.cfg.get("llm_model", "")

        checked = []
        for row in range(self.results_table.rowCount()):
            if self.results_table.isRowHidden(row):
                continue
            item = self.results_table.item(row, 0)
            if item and item.checkState() == Qt.Checked and row < len(self._search_results):
                checked.append(self._search_results[row])
        if not checked:
            QMessageBox.information(self, "None Selected", "Check the papers you want to score first.")
            return

        already = [p for p in checked if p.get("relevance_score") is not None and p["relevance_score"] >= 0]
        if already:
            reply = QMessageBox.question(
                self, "Already Scored",
                f"{len(already)} of {len(checked)} selected papers already have scores.\n\n"
                "Yes — overwrite all scores\nNo — skip already-scored papers",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.No,
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.No:
                checked = [p for p in checked if p not in set(already)]
                if not checked:
                    QMessageBox.information(self, "Nothing", "All selected papers already scored.")
                    return
            else:
                for paper in checked:
                    paper.pop("relevance_score", None)
                    paper.pop("score_reason", None)
        else:
            for paper in checked:
                paper.pop("relevance_score", None)
                paper.pop("score_reason", None)

        self.btn_search.setEnabled(False)
        self.btn_download_selected.setEnabled(False)
        self.btn_download_all.setEnabled(False)
        self.btn_download_by_score.setEnabled(False)
        self.btn_add_pdfs.setEnabled(False)
        self.btn_add_folder.setEnabled(False)
        self.btn_rate.setEnabled(False)
        self.btn_toggle_all.setEnabled(False)
        self.btn_invert.setEnabled(False)
        self.btn_refresh_table.setEnabled(False)
        self.btn_stop_action.setVisible(True)
        self._scoring_total = len(checked)
        self._scoring_papers = checked
        self._scoring_done = 0
        self._scoring_row_map = {}
        for row, paper in enumerate(self._search_results):
            title = (paper.get("title") or "").strip().lower()
            if title:
                self._scoring_row_map[title] = row
        self.dl_status.setText(f"\u23f3 0/{self._scoring_total}")

        for paper in checked:
            self._clear_score_cell(paper)
        self.results_table.viewport().repaint()
        scoring_prompt = self.cfg.load_prompt("rate_relevance_prompt")
        scoring_depth = getattr(self, 'spin_depth', None)
        scoring_depth = scoring_depth.value() if scoring_depth else 1
        self._scoring_worker = RelevanceScoringWorker(
            endpoint, model, checked, self._session_context, scoring_prompt,
            intent=self._session_intent,
            focus_keywords=self._session_focus_keywords,
            avoid_topics=self._session_avoid_topics,
            provider_name=provider_name, api_key=api_key,
            scoring_depth=scoring_depth,
        )
        self._scoring_worker.paper_scored.connect(self._on_paper_scored)
        self._scoring_worker.finished.connect(self._on_scoring_done)
        self._scoring_worker.error.connect(lambda e: (self.log.emit(f"Scoring error: {e}"), QMessageBox.warning(self, "LLM Error", e)))
        self._scoring_worker.start()

    def _on_paper_scored(self, idx, score, reason=""):
        paper = self._scoring_papers[idx] if hasattr(self, '_scoring_papers') and idx < len(self._scoring_papers) else None
        title = paper.get("title", "") if paper else ""
        if paper:
            self._update_score_cell(paper, score, reason)
        self._scoring_done += 1
        pct = self._scoring_done * 100 // self._scoring_total
        self.dl_status.setText(f"\u23f3 {pct}%  ({self._scoring_done}/{self._scoring_total})")
        bar = _progress_bar(self._scoring_done, self._scoring_total)
        self.log.emit(f"\u2b50 [{self._scoring_done}/{self._scoring_total}] {bar} {score}%  {title[:70]}")

    def _clear_score_cell(self, paper: dict):
        title = (paper.get("title") or "").strip().lower()
        row = getattr(self, '_scoring_row_map', {}).get(title)
        if row is None:
            print(f"[UI] _clear_score_cell FAILED for: {title[:60]}", flush=True)
            return
        empty = QTableWidgetItem("")
        empty.setTextAlignment(Qt.AlignCenter)
        self.results_table.setItem(row, 6, empty)
        self.results_table.setItem(row, 7, QTableWidgetItem(""))

    def _update_score_cell(self, paper: dict, score, reason):
        title = (paper.get("title") or "").strip().lower()
        row = getattr(self, '_scoring_row_map', {}).get(title)
        if row is None:
            print(f"[UI] _update_score_cell FAILED for: {title[:60]}", flush=True)
            return
        if score is not None and score >= 0:
            score_text = f"{score}%"
            item = QTableWidgetItem(score_text)
            if score >= 70:
                item.setForeground(Qt.darkGreen)
            elif score >= 40:
                item.setForeground(Qt.darkYellow)
            else:
                item.setForeground(Qt.red)
            item.setToolTip(reason if reason else f"Match: {score}%")
            item.setTextAlignment(Qt.AlignCenter)
            self.results_table.setItem(row, 6, item)
        elif score == -1:
            label, tooltip, color = _score_fail_display(reason or "unknown")
            item = QTableWidgetItem(label)
            item.setToolTip(tooltip)
            item.setForeground(color)
            item.setTextAlignment(Qt.AlignCenter)
            self.results_table.setItem(row, 6, item)
        if reason:
            reason_item = QTableWidgetItem(reason[:100])
            reason_item.setToolTip(reason)
        else:
            reason_item = QTableWidgetItem("—")
            reason_item.setToolTip("No reasoning extracted")
        self.results_table.setItem(row, 7, reason_item)

    def _results_row_for_paper(self, paper: dict):
        title = paper.get("title", "").strip()
        for row in range(self.results_table.rowCount()):
            item = self.results_table.item(row, 1)
            if item and item.text().split("\n")[0].strip() == title:
                return row
        return None

    def _on_stop_action(self):
        if self._scoring_worker and self._scoring_worker.isRunning():
            try:
                self._scoring_worker.paper_scored.disconnect()
                self._scoring_worker.finished.disconnect()
                self._scoring_worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass
            self._scoring_worker.stop()
            import time as _time
            self._score_stop_deadline = _time.time() + 5.0
            self._score_stop_worker = self._scoring_worker
            self._score_stop_timer = QTimer(self)
            self._score_stop_timer.setSingleShot(False)
            self._score_stop_timer.timeout.connect(self._poll_scoring_stop)
            self._score_stop_timer.start(100)
            return
        if hasattr(self, '_current_dl_worker') and self._current_dl_worker and self._current_dl_worker.isRunning():
            self._stop_dl_worker()
            return

    def _poll_scoring_stop(self):
        worker = getattr(self, '_score_stop_worker', None)
        if worker is None:
            return
        import time as _time
        worker = getattr(self, '_score_stop_worker', None)
        deadline = getattr(self, '_score_stop_deadline', 0)
        if not worker or not worker.isRunning() or _time.time() >= deadline:
            if hasattr(self, '_score_stop_timer'):
                self._score_stop_timer.stop()
            if worker and worker.isRunning():
                try:
                    worker.terminate()
                    worker.wait(1000)
                except Exception:
                    pass
            self._scoring_worker = None
            self._score_stop_worker = None
            dl_running = hasattr(self, '_current_dl_worker') and self._current_dl_worker and self._current_dl_worker.isRunning()
            self.btn_search.setEnabled(True)
            self.btn_rate.setEnabled(True)
            self.btn_download_selected.setEnabled(True)
            self.btn_download_all.setEnabled(True)
            self.btn_download_by_score.setEnabled(True)
            self.btn_add_pdfs.setEnabled(True)
            self.btn_add_folder.setEnabled(True)
            self.btn_toggle_all.setEnabled(True)
            self.btn_invert.setEnabled(True)
            self.btn_refresh_table.setEnabled(True)
            if not dl_running:
                self.btn_stop_action.setVisible(False)
            self.dl_status.setText(f"\u23f9 Stopped at {self._scoring_done}/{self._scoring_total}")
            self._apply_filters()
            self._session_save()

    def _on_scoring_done(self):
        self.btn_search.setEnabled(True)
        self.btn_rate.setEnabled(True)
        self.btn_download_selected.setEnabled(True)
        self.btn_download_all.setEnabled(True)
        self.btn_download_by_score.setEnabled(True)
        self.btn_add_pdfs.setEnabled(True)
        self.btn_add_folder.setEnabled(True)
        self.btn_toggle_all.setEnabled(True)
        self.btn_invert.setEnabled(True)
        self.btn_refresh_table.setEnabled(True)
        dl_running = hasattr(self, '_current_dl_worker') and self._current_dl_worker and self._current_dl_worker.isRunning()
        if not dl_running:
            self.btn_stop_action.setVisible(False)
        self.dl_status.setText(f"\u2705 Scored {self._scoring_total} papers")
        self.log.emit(f"\u2b50 Scoring complete: {self._scoring_total} papers rated")
        self._apply_filters()
        self._session_save()
        if self._scoring_worker:
            self._scoring_worker = None

    def _on_add_pdfs(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select PDF Files", "",
            "PDF Files (*.pdf);;All Files (*)"
        )
        if not paths:
            return
        session_name = self.cfg.get("session_download_name", "")
        if not session_name:
            QMessageBox.warning(self, "No Session", "Create or load a session first.")
            return
        topic, ok = QInputDialog.getText(
            self, "Topic Name", "Topic folder for these PDFs:",
            text="Local PDFs"
        )
        if not ok or not topic.strip():
            topic = "Local PDFs"
        topic = topic.strip()

        output_root = self._session_root()
        total = len(paths)
        self.dl_progress.setVisible(True)
        self.dl_progress.setRange(0, total)
        self.dl_progress.setValue(0)
        self.btn_add_pdfs.setEnabled(False)
        self.btn_add_folder.setEnabled(False)

        paths_with_topics = [(p, topic) for p in paths]
        start_id = len(self._search_results)
        self._pdf_import_added = 0

        worker = _PDFImportWorker(paths_with_topics, output_root, start_id)
        self._pdf_import_worker = worker

        def on_paper(paper):
            paper["pinned"] = True
            self._search_results.append(paper)
            self._pdf_import_added += 1

        def on_progress(msg):
            self.dl_status.setText(msg)

        def on_finished():
            self.dl_progress.setVisible(False)
            self.dl_status.setText("")
            self.btn_add_pdfs.setEnabled(True)
            self.btn_add_folder.setEnabled(True)
            added = self._pdf_import_added
            try:
                worker.progress.disconnect(on_progress)
                worker.paper_ready.disconnect(on_paper)
                worker.finished.disconnect(on_finished)
                worker.error.disconnect(on_error)
            except (TypeError, RuntimeError):
                pass
            self._pdf_import_worker = None
            if added > 0:
                self._populate_results_table()
                self._update_result_count()
                self._apply_filters()
                self._renumber_visible_rows()
                self._session_save()
                self._refresh_downloaded_tab()
                self.log.emit(f"\U0001f4c1 Added {added} local PDF(s) -> topic '{topic}'")
                if not self._closing:
                    QMessageBox.information(
                        self, "PDFs Added",
                        f"Added {added} PDF(s) to topic:\n<b>{topic}</b>\n\n"
                        f"Copied to: {output_root}"
                    )
            else:
                if not self._closing:
                    QMessageBox.information(self, "No PDFs", "No valid PDF files found.")
            if hasattr(self, '_pdf_import_added'):
                delattr(self, '_pdf_import_added')

        def on_error(msg):
            self.log.emit(f"PDF import error: {msg}")

        worker.progress.connect(on_progress)
        worker.paper_ready.connect(on_paper)
        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        worker.start()

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if not folder:
            return
        dlg = FolderImportDialog(folder, self)
        if dlg.exec() != QDialog.Accepted:
            return
        mapping = dlg.result()
        if not mapping:
            return
        source_dir = folder
        paths_with_topics = []
        for topic, rel_dir, fname in mapping:
            src = os.path.join(source_dir, rel_dir, fname)
            paths_with_topics.append((src, topic))
        self._inject_local_papers(paths_with_topics, source_dir)

    def _inject_local_papers(self, paths, root_label):
        """Copy PDFs into session folder tree and inject into _search_results (background worker)."""
        session_name = self.cfg.get("session_download_name", "")
        if not session_name:
            QMessageBox.warning(self, "No Session", "Create or load a session first.")
            return
        output_root = self._session_root()

        total = len(paths)
        self.dl_progress.setVisible(True)
        self.dl_progress.setRange(0, total)
        self.dl_progress.setValue(0)
        self.btn_add_pdfs.setEnabled(False)
        self.btn_add_folder.setEnabled(False)

        start_id = len(self._search_results)
        self._pdf_import_added = 0
        topic_counts = {}

        worker = _PDFImportWorker(paths, output_root, start_id)
        self._pdf_import_worker = worker

        def on_paper(paper):
            paper["pinned"] = True
            self._search_results.append(paper)
            topic = paper.get("output_folder", "Local PDFs")
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
            self._pdf_import_added += 1

        def on_progress(msg):
            self.dl_status.setText(msg)

        def on_finished():
            self.dl_progress.setVisible(False)
            self.dl_status.setText("")
            self.btn_add_pdfs.setEnabled(True)
            self.btn_add_folder.setEnabled(True)
            added = self._pdf_import_added
            skipped = worker.skipped
            try:
                worker.progress.disconnect(on_progress)
                worker.paper_ready.disconnect(on_paper)
                worker.finished.disconnect(on_finished)
                worker.error.disconnect(on_error)
            except (TypeError, RuntimeError):
                pass
            self._pdf_import_worker = None
            if added > 0:
                self._populate_results_table()
                self._update_result_count()
                self._apply_filters()
                self._renumber_visible_rows()
                self._session_save()
                self._refresh_downloaded_tab()
                topics_str = ", ".join(f"'{t}' ({c})" for t, c in topic_counts.items())
                self.log.emit(f"\U0001f4c1 Added {added} local PDF(s) -> {topics_str}")
                if not self._closing:
                    QMessageBox.information(
                        self, "PDFs Added",
                        f"Added {added} PDF(s) across {len(topic_counts)} topic(s):\n"
                        + "\n".join(f"  \u2022 {t} \u2014 {c} PDF(s)" for t, c in topic_counts.items())
                    )
            else:
                msg = "No valid PDF files found."
                if skipped > 0:
                    msg += f"\n\n{skipped} file(s) were skipped. See Logs tab for details."
                if not self._closing:
                    QMessageBox.information(self, "No PDFs", msg)
            if hasattr(self, '_pdf_import_added'):
                delattr(self, '_pdf_import_added')

        def on_error(msg):
            self.log.emit(f"PDF import error: {msg}")

        worker.progress.connect(on_progress)
        worker.paper_ready.connect(on_paper)
        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        worker.start()

    def _on_download_flagged(self):
        if hasattr(self, '_current_dl_worker') and self._current_dl_worker and self._current_dl_worker.isRunning():
            return

        flagged = []
        for row in range(self.results_table.rowCount()):
            if self.results_table.isRowHidden(row):
                continue
            if row >= len(self._search_results):
                continue
            item = self.results_table.item(row, 5)
            if item and item.text() in ("FAIL", "EXCL"):
                paper = self._search_results[row]
                paper["force_download"] = True
                paper["title_filter_ok"] = True
                flagged.append(paper)
                self.results_table.item(row, 0).setCheckState(Qt.Checked)
                item.setText("OVERRIDE")
                item.setForeground(Qt.darkGreen)

        if not flagged:
            QMessageBox.information(self, "No Flagged", "No flagged papers to download.")
            return

        self.dl_progress.setVisible(True)
        self.dl_progress.setRange(0, len(flagged))
        self.dl_progress.setValue(0)
        self.btn_search.setEnabled(False)
        self.btn_download_selected.setEnabled(False)
        self.btn_download_all.setEnabled(False)
        self.btn_download_by_score.setEnabled(False)
        self.btn_add_pdfs.setEnabled(False)
        self.btn_add_folder.setEnabled(False)
        self.btn_rate.setEnabled(False)
        self.btn_toggle_all.setEnabled(False)
        self.btn_invert.setEnabled(False)
        self.btn_refresh_table.setEnabled(False)
        self.dl_status.setText(f"0 / {len(flagged)}")
        self._download_done = 0
        self._download_total = len(flagged)
        self.btn_stop_action.setVisible(True)

        self._current_dl_worker = DownloadWorker(
            papers=flagged, target_dir=self._get_output_root(),
            max_size_mb=self.cfg.get("default_max_size_mb", 50.0),
            skip_content_filter=True,
        )
        self._current_dl_worker.paper_done.connect(self._on_batch_paper_done)
        self._current_dl_worker.finished.connect(self._on_batch_all_done)
        self._current_dl_worker.error.connect(lambda e: self.log.emit(f"Download error: {e}"))
        self._current_dl_worker.start()
