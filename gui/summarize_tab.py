"""
Summarize Tab: view downloaded PDFs organized by topic, run LLM processing.
"""
import os
import shutil
import re
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QFileDialog,
    QLineEdit, QLabel, QTextEdit, QProgressBar, QCheckBox,
    QMessageBox, QHeaderView, QDialog, QSizePolicy, QSplitter,
)
from PySide6.QtCore import Qt, Signal, QThread

from gui.workers import LLMProcessWorker
from gui.llm_provider import (
    create_llm_client, get_provider_api_key, check_provider_connection,
    ensure_llm_available,
)
from gui import paths


class SummarizeTab(QWidget):
    """Generate Reports tab: run LLM summarization pipeline on downloaded PDFs.

    Shows a tree of downloaded papers organized by topic. User selects papers
    and triggers per-paper analysis, per-topic synthesis, global synthesis, or
    related-work generation. Uses LLMProcessWorker for the 3-pass pipeline.

    Signals:
      log(str)                — Forwarded to Logs tab
      session_save_requested() — Emitted when analysis lense toggles change
    """
    log = Signal(str)
    session_save_requested = Signal()

    def __init__(self, config_manager, log_signal, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.log = log_signal
        self._llm_worker = None
        self._total_papers = 0
        self._done_papers = 0
        self._setup_ui()

    def showEvent(self, event):
        super().showEvent(event)
        self.lbl_model.setText(f"Model: {self.cfg.get('llm_model', 'none')}")
        ctx = self.cfg.get("our_work_context", "")
        if ctx and ctx != self.our_work.toPlainText().strip():
            self.our_work.blockSignals(True)
            self.our_work.setText(ctx)
            self.our_work.blockSignals(False)
        intent = self.cfg.get("session_intent", "")
        if intent and intent != self.intent_text.toPlainText().strip():
            self.intent_text.blockSignals(True)
            self.intent_text.setText(intent)
            self.intent_text.blockSignals(False)
        for chk, key, default in [
            (self.chk_similarity,  "summ_chk_similarity",  True),
            (self.chk_novelty,     "summ_chk_novelty",     True),
            (self.chk_methodology, "summ_chk_methodology", False),
            (self.chk_gaps,        "summ_chk_gaps",        False),
        ]:
            chk.blockSignals(True)
            chk.setChecked(self.cfg.get(key, default))
            chk.blockSignals(False)
        saved_mode = self.cfg.get("summ_selected_mode", "per_paper")
        if saved_mode != self._selected_mode:
            self._select_mode(saved_mode, persist=False)
        self._refresh_tree()

    @staticmethod
    def _collapse_group(grp, expanded):
        for child in grp.findChildren(QWidget):
            if child is not grp:
                child.setVisible(expanded)
        splitter = grp.parentWidget()
        if not expanded:
            grp.setMinimumHeight(30)
            grp.setMaximumHeight(30)
        else:
            grp.setMinimumHeight(80)
            grp.setMaximumHeight(16777215)
        grp.updateGeometry()
        if splitter and isinstance(splitter, QSplitter):
            splitter.refresh()
            splitter.update()
        else:
            pw = grp.parentWidget()
            if pw:
                layout = pw.layout()
                if layout:
                    layout.invalidate()
                    layout.activate()
                pw.updateGeometry()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self.grp_ctx = QGroupBox("Research Context")
        self.grp_ctx.setCheckable(True)
        self.grp_ctx.setChecked(True)
        self.grp_ctx.setToolTip(
            "Describe YOUR research here. This text is included in every LLM prompt:\n\n"
            "• Per-Paper Analysis — Section 6 compares each paper against your work,\n"
            "  Section 10 mines citations from relevant papers\n"
            "• Rate Relevance — primary filter for scoring papers 0–100\n"
            "• Topic Synthesis — identifies papers closest to your approach\n"
            "• Global Synthesis — positions your contribution against the field\n"
            "• Related Work — generates a related-work section tailored to your paper\n\n"
            "The more specific you are (methods, datasets, problem definition),\n"
            "the more targeted the analysis will be."
        )
        cl = QHBoxLayout(self.grp_ctx)
        cl.setSpacing(4)
        cl.setContentsMargins(4, 4, 4, 4)

        self.our_work = QTextEdit()
        self.our_work.setPlaceholderText("Describe your research")
        self.our_work.setText(self.cfg.get("our_work_context", ""))
        self.our_work.textChanged.connect(lambda: self.cfg.set("our_work_context", self.our_work.toPlainText().strip()))
        cl.addWidget(self.our_work)
        self.grp_ctx.toggled.connect(lambda on: self._collapse_group(self.grp_ctx, on))

        self.grp_intent = QGroupBox("What do you want to achieve?")
        self.grp_intent.setCheckable(True)
        self.grp_intent.setChecked(True)
        self.grp_intent.setToolTip(
            "Tell the LLM what you want from the analysis.\n\n"
            "HOW TO FILL:\n"
            "  • Auto-fill: Use 'Analyze My Paper' in the session creator — objectives\n"
            "    are extracted automatically and copied here on session creation.\n"
            "  • Manual: Describe what you want to compare, prove, or discover.\n\n"
            "WHY IT MATTERS:\n"
            "Without this, the LLM does not know your research goal. Summaries become\n"
            "generic — every paper gets equal treatment instead of being filtered and\n"
            "compared against what you actually need.\n\n"
            "USED IN ALL STAGES:\n"
            "  • Per-Paper — shapes how relevance and comparison are judged\n"
            "  • Topic Synthesis — focuses the narrative on what matters to you\n"
            "  • Global Synthesis — structures the overview around your angle\n"
            "  • Related Work — writes the section to support your contribution\n"
            "  • Introduction — positions your work in the field\n\n"
            "EXAMPLES:\n"
            "  • 'Compare my rPPG method against SOTA on benchmark X'\n"
            "  • 'Find gaps in ICA-based approaches for heart rate extraction'\n"
            "  • 'Map the evolution of deep learning methods in remote PPG'\n"
            "  • 'Identify which papers I must cite for my Related Work section'"
        )
        il = QHBoxLayout(self.grp_intent)
        il.setSpacing(4)
        il.setContentsMargins(4, 4, 4, 4)
        self.intent_text = QTextEdit()
        self.intent_text.setPlaceholderText(
            "What do you want from this literature review? (e.g. compare against SOTA, find gaps...)\n\n"
            "Tip: Use 'Analyze My Paper' in session creation to auto-fill this.\n"
            "Leaving this blank produces generic analysis — the LLM won't know your goal."
        )
        self.intent_text.textChanged.connect(lambda: self.cfg.set("session_intent", self.intent_text.toPlainText().strip()))
        il.addWidget(self.intent_text, 3)

        chk_ctr = QVBoxLayout()
        chk_ctr.setSpacing(2)
        chk_ctr.setContentsMargins(0, 0, 0, 0)
        self.btn_enhance = QPushButton("✨ Enhance")
        self.btn_enhance.setObjectName("btn_enhance")
        self.btn_enhance.clicked.connect(self._on_enhance_context)
        chk_ctr.addWidget(self.btn_enhance)
        self.chk_similarity = QCheckBox("Similarity")
        self.chk_similarity.setChecked(self.cfg.get("summ_chk_similarity", True))
        chk_ctr.addWidget(self.chk_similarity)
        self.chk_novelty = QCheckBox("Novelty")
        self.chk_novelty.setChecked(self.cfg.get("summ_chk_novelty", True))
        chk_ctr.addWidget(self.chk_novelty)
        self.chk_methodology = QCheckBox("Methodology")
        self.chk_methodology.setChecked(self.cfg.get("summ_chk_methodology", False))
        chk_ctr.addWidget(self.chk_methodology)
        self.chk_gaps = QCheckBox("Gaps")
        self.chk_gaps.setChecked(self.cfg.get("summ_chk_gaps", False))
        chk_ctr.addWidget(self.chk_gaps)

        self.chk_similarity.stateChanged.connect(lambda v: (self.cfg.set("summ_chk_similarity", bool(v)), self.session_save_requested.emit()))
        self.chk_novelty.stateChanged.connect(lambda v: (self.cfg.set("summ_chk_novelty", bool(v)), self.session_save_requested.emit()))
        self.chk_methodology.stateChanged.connect(lambda v: (self.cfg.set("summ_chk_methodology", bool(v)), self.session_save_requested.emit()))
        self.chk_gaps.stateChanged.connect(lambda v: (self.cfg.set("summ_chk_gaps", bool(v)), self.session_save_requested.emit()))
        chk_ctr.addStretch()
        il.addLayout(chk_ctr)
        self.grp_intent.toggled.connect(lambda on: self._collapse_group(self.grp_intent, on))

        self.grp_tree = QGroupBox("Papers by Topic")
        self.grp_tree.setCheckable(True)
        self.grp_tree.setChecked(True)
        self.grp_tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.grp_tree.setMinimumHeight(200)
        tl = QVBoxLayout(self.grp_tree)
        tl.setSpacing(2)
        tl.setContentsMargins(2, 4, 2, 2)

        hb_tree_btns = QHBoxLayout()
        hb_tree_btns.setSpacing(4)
        self.btn_refresh_tree = QPushButton("⟳ Refresh")
        self.btn_refresh_tree.setObjectName("btn_refresh_tree")
        self.btn_refresh_tree.clicked.connect(self._refresh_tree)
        hb_tree_btns.addWidget(self.btn_refresh_tree)
        self.lbl_model = QLabel(f"Model: {self.cfg.get('llm_model', 'none')}")
        hb_tree_btns.addWidget(self.lbl_model)
        hb_tree_btns.addStretch()
        self.lbl_progress = QLabel("")
        hb_tree_btns.addWidget(self.lbl_progress)

        self._selected_mode = "per_paper"

        self.btn_per_paper = QPushButton("Per-Paper")
        self.btn_per_paper.clicked.connect(lambda: self._select_mode("per_paper"))
        hb_tree_btns.addWidget(self.btn_per_paper)
        self.btn_topic = QPushButton("Topic")
        self.btn_topic.clicked.connect(lambda: self._select_mode("topic"))
        hb_tree_btns.addWidget(self.btn_topic)
        self.btn_global = QPushButton("Global")
        self.btn_global.clicked.connect(lambda: self._select_mode("global"))
        hb_tree_btns.addWidget(self.btn_global)
        self.btn_related = QPushButton("Related Work")
        self.btn_related.clicked.connect(lambda: self._select_mode("related_work"))
        hb_tree_btns.addWidget(self.btn_related)
        self.btn_introduction = QPushButton("Introduction")
        self.btn_introduction.clicked.connect(lambda: self._select_mode("introduction"))
        hb_tree_btns.addWidget(self.btn_introduction)
        self.btn_patent = QPushButton("Patent Landscape")
        self.btn_patent.setToolTip(
            "Analyse every patent in this session and write PATENT_LANDSCAPE.md. "
            "Requires patent results (PatentsView / EPO OPS / PQAI sources).")
        self.btn_patent.clicked.connect(lambda: self._select_mode("patent_landscape"))
        hb_tree_btns.addWidget(self.btn_patent)
        self.btn_all = QPushButton("All")
        self.btn_all.clicked.connect(lambda: self._select_mode("all"))
        hb_tree_btns.addWidget(self.btn_all)

        # Single source of truth for the mode buttons. _select_mode used to carry
        # its own hardcoded copy of this list and btn_patent was missing from it,
        # so clicking Patent Landscape set the mode internally but highlighted
        # nothing — the button looked unselectable.
        self._mode_buttons = [
            (self.btn_per_paper, "per_paper"),
            (self.btn_topic, "topic"),
            (self.btn_global, "global"),
            (self.btn_related, "related_work"),
            (self.btn_introduction, "introduction"),
            (self.btn_patent, "patent_landscape"),
            (self.btn_all, "all"),
        ]
        self.btn_start_stop = QPushButton("Start")
        self.btn_start_stop.setObjectName("btn_search")
        self.btn_start_stop.clicked.connect(self._on_start_stop)
        hb_tree_btns.addWidget(self.btn_start_stop)
        tl.addLayout(hb_tree_btns)

        self.paper_tree = QTreeWidget()
        self.paper_tree.setHeaderLabels(["Topic / Paper", "Status"])
        self.paper_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.paper_tree.header().setSectionResizeMode(1, QHeaderView.Interactive)
        self.paper_tree.setAlternatingRowColors(True)
        tl.addWidget(self.paper_tree)

        self.overall_progress = QProgressBar()
        self.overall_progress.setVisible(False)
        self.overall_progress.setMaximumHeight(14)
        tl.addWidget(self.overall_progress)

        self.grp_tree.toggled.connect(lambda on: self._collapse_group(self.grp_tree, on))

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.grp_ctx)
        splitter.addWidget(self.grp_intent)
        splitter.addWidget(self.grp_tree)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([120, 120, 500])
        layout.addWidget(splitter, 1)
        self._select_mode("per_paper", persist=False)

    def _on_enhance_context(self):
        text = self.our_work.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Empty", "Write something about your research first.")
            return
        if not ensure_llm_available(self.cfg, self):
            return
        provider_name = self.cfg.get("llm_provider", "LM Studio")
        api_key = get_provider_api_key(provider_name, self.cfg)

        prompt = self.cfg.load_prompt("enhance_research_prompt")
        if not prompt:
            prompt = "Reformulate this research description into academic language. Output only the text."

        dlg = QDialog(self)
        dlg.setWindowTitle("Enhance Research Context")
        dlg.setMinimumSize(620, 380)
        dl = QVBoxLayout(dlg)
        dl.addWidget(QLabel("AI-enhanced research context:"))
        preview = QTextEdit()
        preview.setReadOnly(True)
        dl.addWidget(preview, 1)
        btn_hb = QHBoxLayout()
        btn_regenerate = QPushButton("Regenerate")
        btn_cancel = QPushButton("Cancel")
        btn_accept = QPushButton("Accept")
        btn_accept.setObjectName("btn_save")
        btn_hb.addWidget(btn_regenerate)
        btn_hb.addStretch()
        btn_hb.addWidget(btn_cancel)
        btn_hb.addWidget(btn_accept)
        dl.addLayout(btn_hb)

        self._enhance_worker = None

        def _run_enhance():
            from openai import OpenAI
            from PySide6.QtCore import QThread
            from PySide6.QtCore import Signal as QSignal

            class EWorker(QThread):
                result = QSignal(str)
                fail = QSignal(str)

                def __init__(self, ep, md, txt, pr, pn="", ak="not-needed", parent=None):
                    super().__init__(parent)
                    self.ep = ep; self.md = md; self.txt = txt; self.pr = pr
                    self.pn = pn; self.ak = ak

                def run(self):
                    try:
                        client = create_llm_client(self.ep, self.ak, self.pn, timeout=60.0)
                        res = client.chat.completions.create(
                            model=self.md,
                            messages=[{"role": "system", "content": self.pr}, {"role": "user", "content": self.txt}],
                            temperature=0.4,
                        )
                        self.result.emit(res.choices[0].message.content.strip())
                    except Exception as e:
                        self.fail.emit(str(e))

            self._enhance_worker = EWorker(endpoint, model, text, prompt, provider_name, api_key, dlg)

            def _ok(enhanced):
                preview.setText(enhanced)
                btn_regenerate.setEnabled(True)
                btn_accept.setEnabled(True)
                btn_cancel.setEnabled(True)

            def _fail(msg):
                preview.setText(f"Error: {msg}")
                btn_regenerate.setEnabled(True)
                QMessageBox.warning(dlg, "LLM Error", msg)

            btn_regenerate.setEnabled(False)
            btn_accept.setEnabled(False)
            btn_cancel.setEnabled(False)
            preview.setText("Enhancing...")
            self._enhance_worker.result.connect(_ok)
            self._enhance_worker.fail.connect(_fail)
            self._enhance_worker.finished.connect(self._enhance_worker.deleteLater)
            self._enhance_worker.start()

        btn_accept.clicked.connect(lambda: (self.our_work.setPlainText(preview.toPlainText()), dlg.accept()))
        btn_regenerate.clicked.connect(_run_enhance)
        btn_cancel.clicked.connect(dlg.reject)
        _run_enhance()
        dlg.exec()

    def _get_research_context(self):
        parts = []
        our_text = self.our_work.toPlainText().strip()
        if not our_text:
            return ""
        parts.append(f"OUR RESEARCH:\n{our_text}")
        intent = self.intent_text.toPlainText().strip()
        if intent:
            parts.append(f"\nRESEARCH INTENT (what the researcher wants to achieve):\n{intent}")
        lenses = []
        if self.chk_similarity.isChecked():
            lenses.append("SIMILARITY — include only if the paper shares methods, goals, or domain with our work")
        if self.chk_novelty.isChecked():
            lenses.append("NOVELTY — highlight what is genuinely new vs incremental in each paper")
        if self.chk_methodology.isChecked():
            lenses.append("METHODOLOGY — compare methods, datasets, strengths and weaknesses against ours")
        if self.chk_gaps.isChecked():
            lenses.append("GAPS — identify open problems or limitations our work could address")
        if lenses:
            parts.append("\nINCLUDE ONLY papers relevant through these lenses:")
            for l in lenses:
                parts.append(f"- {l}")
            parts.append("Skip any paper that does not match at least one of these lenses.")
        return "\n".join(parts)

    def _effective_session_name(self) -> str:
        """The session-name segment used for path construction, with a legacy-
        field fallback. Reads ``session_download_name`` (set when a session is
        opened in the Search tab); if empty, tries the older
        ``summary_session_folder`` field and strips its timestamp suffix.
        Returns "" when no session is active."""
        name = self.cfg.get("session_download_name", "")
        if not name:
            legacy = self.cfg.get("summary_session_folder", "")
            if legacy:
                name = re.sub(r'_\d{8}_\d{6}$', '', legacy)
        return name

    def _refresh_tree(self):
        self.paper_tree.clear()
        download_name = self._effective_session_name()
        if not download_name:
            self.log.emit("No session loaded. Open a session in Search tab first.")
            return
        if not self.cfg.get("summary_output_dir", "").strip():
            self.log.emit("Summary output directory not set.")
            return
        in_dir = paths.session_downloads_root(self.cfg, download_name)
        if not os.path.isdir(in_dir):
            self.log.emit(f"Session folder not found: {in_dir}")
            return
        subfolders = sorted([f.path for f in os.scandir(in_dir) if f.is_dir()])
        if not subfolders:
            self.log.emit("No topic subfolders found.")
            return
        model = self.cfg.get("llm_model", "default")
        base_path = paths.model_output_root(self.cfg, download_name, model)
        self.log.emit(f"Active model: {model} → {base_path}")
        total_pdfs = 0
        total_cached = 0
        topics_with_summary = 0

        from researchforge_api import _patents
        for folder_path in subfolders:
            folder_name = os.path.basename(folder_path)
            pdf_files = sorted(_patents.paper_pdfs(folder_path))  # papers only, skip patents
            if not pdf_files:
                continue

            cache_base = paths.topic_cache_dir(base_path, folder_name)
            topic_summary_path = paths.topic_summary_file(base_path, folder_name)
            has_summary = os.path.isfile(topic_summary_path)
            if has_summary:
                topics_with_summary += 1

            topic_cached = 0
            for pdf in pdf_files:
                cache_file = os.path.join(cache_base, os.path.splitext(pdf)[0] + ".md")
                if os.path.exists(cache_file):
                    topic_cached += 1

            summary_mark = "✓" if has_summary else "—"
            topic_label = f"{folder_name}  ({len(pdf_files)} papers)"
            topic_item = QTreeWidgetItem(self.paper_tree)
            topic_item.setText(0, topic_label)
            topic_item.setText(1, f"{topic_cached}/{len(pdf_files)} papers  |  summary {summary_mark}")
            topic_item.setData(0, Qt.UserRole + 1, topic_summary_path)
            topic_item.setExpanded(True)

            if topic_cached == len(pdf_files) and has_summary:
                topic_item.setForeground(1, Qt.darkGreen)
            elif topic_cached == len(pdf_files):
                topic_item.setForeground(1, Qt.darkYellow)

            for pdf in pdf_files:
                pdf_item = QTreeWidgetItem(topic_item)
                pdf_item.setText(0, pdf)
                cache_file = os.path.join(cache_base, os.path.splitext(pdf)[0] + ".md")
                if os.path.exists(cache_file):
                    pdf_item.setText(1, "Done")
                    pdf_item.setForeground(1, Qt.darkGreen)
                elif os.path.exists(os.path.join(cache_base, os.path.splitext(pdf)[0] + ".skipped")):
                    pdf_item.setText(1, "Skipped")
                    pdf_item.setForeground(1, Qt.darkYellow)
                else:
                    pdf_item.setText(1, "Pending")
                    pdf_item.setForeground(1, Qt.gray)
                pdf_item.setData(0, Qt.UserRole, os.path.join(folder_path, pdf))
                pdf_item.setData(0, Qt.UserRole + 1, cache_file)

            total_pdfs += len(pdf_files)
            total_cached += topic_cached

        summary_text = f"{total_pdfs} papers, {total_cached} analyzed  |  {topics_with_summary}/{len(subfolders)} topics synthesized"
        self.lbl_progress.setText(summary_text)

        global_path = paths.global_summary_file(base_path)
        if os.path.isfile(global_path):
            item = QTreeWidgetItem(self.paper_tree)
            font = item.font(0); font.setBold(True); item.setFont(0, font)
            item.setText(0, "GLOBAL SUMMARY")
            item.setText(1, "Done")
            item.setForeground(1, Qt.darkGreen)

        rw_path = paths.related_work_file(base_path)
        if os.path.isfile(rw_path):
            item = QTreeWidgetItem(self.paper_tree)
            font = item.font(0); font.setBold(True); item.setFont(0, font)
            item.setText(0, "RELATED WORK")
            item.setText(1, "Done")
            item.setForeground(1, Qt.darkGreen)

        self.log.emit("Paper tree refreshed.")

    def _pipeline_status(self, model_output_root):
        lines = ["── Pipeline Status ──"]

        in_dir = paths.session_downloads_root(self.cfg, self._effective_session_name())

        total_papers = 0
        cached_papers = 0
        skipped_papers = 0
        topics_with_summary = 0
        topics_count = 0

        if os.path.isdir(in_dir):
            topics_count = len([f for f in os.listdir(in_dir) if os.path.isdir(os.path.join(in_dir, f))])

        for sf in (os.listdir(in_dir) if os.path.isdir(in_dir) else []):
            sf_path = os.path.join(in_dir, sf)
            if not os.path.isdir(sf_path):
                continue
            from researchforge_api import _patents
            total_papers += len(_patents.paper_pdfs(sf_path))  # papers only, skip patents
            cache_path = paths.topic_cache_dir(model_output_root, sf)
            if os.path.isdir(cache_path):
                for cf in os.listdir(cache_path):
                    if cf.endswith(".md"):
                        cached_papers += 1
                    elif cf.endswith(".skipped"):
                        skipped_papers += 1
            summary_path = paths.topic_summary_file(model_output_root, sf)
            if os.path.isfile(summary_path):
                topics_with_summary += 1

        pending_papers = total_papers - cached_papers - skipped_papers
        lines.append(f"Papers: {total_papers} total | {cached_papers} analyzed | {skipped_papers} skipped | {pending_papers} pending")
        lines.append(f"Topic summaries: {topics_with_summary}/{topics_count} done")
        gs = paths.global_summary_file(model_output_root)
        lines.append(f"Global summary: {'Done' if os.path.isfile(gs) else 'Not yet'}")
        rw = paths.related_work_file(model_output_root)
        lines.append(f"Related work: {'Done' if os.path.isfile(rw) else 'Not yet'}")
        intro = paths.introduction_file(model_output_root)
        lines.append(f"Introduction: {'Done' if os.path.isfile(intro) else 'Not yet'}")
        lines.append("──")
        return "\n".join(lines)

    def _on_process(self, mode):
        self.cfg.set("our_work_context", self.our_work.toPlainText().strip())
        intent = self.intent_text.toPlainText().strip()
        if intent:
            self.cfg.set("session_intent", intent)

        if not ensure_llm_available(self.cfg, self):
            return
        provider_name = self.cfg.get("llm_provider", "LM Studio")
        api_key = get_provider_api_key(provider_name, self.cfg)
        endpoint = self.cfg.get("llm_endpoint", "")

        download_name = self._effective_session_name()
        if not self.cfg.get("output_root", "").strip() or not download_name:
            QMessageBox.warning(self, "Missing Input", "Set Output directory and load a session in the Search tab first.")
            return
        in_dir = paths.session_downloads_root(self.cfg, download_name)
        if not os.path.isdir(in_dir):
            QMessageBox.warning(self, "Missing Input", f"Session folder not found:\n{in_dir}")
            return
        if not self.cfg.get("summary_output_dir", "").strip():
            QMessageBox.warning(self, "Missing Input", "Set Summary Output directory in Settings first.")
            return
        model = self.cfg.get("llm_model", "default")
        model_output_root = paths.model_output_root(self.cfg, download_name, model)
        cache_root = paths.topic_reviews_parent(model_output_root)

        existing = 0
        if os.path.isdir(cache_root):
            for fld in os.listdir(cache_root):
                cp = paths.topic_cache_dir(model_output_root, fld)
                if os.path.isdir(cp):
                    existing += len([f for f in os.listdir(cp) if f.endswith(".md")])

        status = self._pipeline_status(model_output_root)
        self.log.emit(status)

        mode_labels = {
            "per_paper": ("Per-Paper Analysis", "re-analyze every paper from scratch"),
            "topic": ("Topic Synthesis", "re-synthesize all topics"),
            "global": ("Global Synthesis", "regenerate the global summary"),
            "related_work": ("Related Work", "regenerate the Related Work section"),
            "introduction": ("Introduction", "regenerate the Introduction section"),
            "patent_landscape": ("Patent Landscape", "re-analyse every patent and regenerate the report"),
            "all": ("All", "re-process everything from scratch"),
        }
        ml = mode_labels.get(mode, ("", ""))
        has_existing = False
        if mode in ("related_work", "introduction", "patent_landscape"):
            out_paths = {"related_work": "RELATED_WORK.md", "introduction": "INTRODUCTION.md",
                         "patent_landscape": "PATENT_LANDSCAPE.md"}
            has_existing = os.path.isfile(os.path.join(model_output_root, out_paths.get(mode, "")))
        else:
            has_existing = existing > 0

        force_rerun = False
        msg = QMessageBox(self)
        msg.setWindowTitle(f"Start {mode.replace('_', ' ').title()}")
        msg.setText(f"Pipeline status:\n{status}")
        if has_existing:
            msg.setInformativeText(f"Cached results found for {ml[0]}.\nOverwrite will {ml[1]}.")
        btn_start = msg.addButton("Start", QMessageBox.AcceptRole)
        if has_existing:
            btn_overwrite = msg.addButton(f"Overwrite {ml[0]}", QMessageBox.DestructiveRole)
        msg.addButton("Cancel", QMessageBox.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if has_existing and clicked == btn_overwrite:
            force_rerun = True
        elif clicked != btn_start:
            return

        if mode == "related_work":
            self._on_related_work(endpoint, model, provider_name, api_key, force_rerun)
            return
        if mode == "introduction":
            self._on_introduction(endpoint, model, provider_name, api_key, force_rerun)
            return
        if mode == "patent_landscape":
            self._on_patent_landscape(force_rerun)
            return

        prompt_path = self.cfg.get("per_paper_prompt", "")
        topic_prompt = ""
        tp_path = self.cfg.get("topic_synthesis_prompt", "")
        if os.path.isfile(tp_path):
            with open(tp_path, "r", encoding="utf-8") as f:
                topic_prompt = f.read()
        global_prompt = ""
        gp_path = self.cfg.get("global_synthesis_prompt", "")
        if os.path.isfile(gp_path):
            with open(gp_path, "r", encoding="utf-8") as f:
                global_prompt = f.read()

        research_context = self._get_research_context()
        os.makedirs(cache_root, exist_ok=True)
        self._count_total_papers(in_dir)

        total_steps = self._total_papers
        if mode in ("topic", "global", "all"):
            total_steps += sum(1 for f in os.scandir(in_dir) if f.is_dir())
        if mode in ("global", "all"):
            total_steps += 1

        self._disable_buttons()
        self.overall_progress.setVisible(True)
        self.overall_progress.setRange(0, total_steps)
        self.overall_progress.setValue(0)
        self._done_papers = 0
        self.log.emit(f"Starting {mode}: {model}")
        self.log.emit(f"Output: {cache_root}")

        if self._llm_worker:
            try:
                self._llm_worker.progress.disconnect()
                self._llm_worker.paper_processed.disconnect()
                self._llm_worker.topic_summary_done.disconnect()
                self._llm_worker.global_summary_done.disconnect()
                self._llm_worker.finished.disconnect()
                self._llm_worker.error.disconnect()
            except Exception:
                pass
            if self._llm_worker.isRunning():
                self._llm_worker.stop()
                self._llm_worker.wait(3000)
            self._llm_worker.deleteLater()
            self._llm_worker = None

        self._llm_worker = LLMProcessWorker(
            endpoint=endpoint, model_id=model, prompt_path=prompt_path,
            input_path=in_dir, output_path=model_output_root,
            topic_synthesis_prompt=topic_prompt, global_synthesis_prompt=global_prompt,
            research_context=research_context, run_mode=mode,
            max_pages=self.cfg.get("per_paper_max_pages", 30),
            max_chars=self.cfg.get("per_paper_max_chars", 60000),
            force_rerun=force_rerun,
            provider_name=provider_name, api_key=api_key,
        )
        self._llm_worker.progress.connect(self._on_llm_progress)
        self._llm_worker.paper_processed.connect(self._on_paper_processed)
        self._llm_worker.topic_summary_done.connect(self._on_topic_summary_done)
        self._llm_worker.global_summary_done.connect(self._on_global_summary_done)
        self._llm_worker.finished.connect(self._on_llm_finished)
        self._llm_worker.error.connect(self._on_llm_error)
        self._llm_worker.start()

    def _count_total_papers(self, in_dir):
        from researchforge_api import _patents
        self._total_papers = 0
        for f in os.scandir(in_dir):
            if f.is_dir():
                self._total_papers += len(_patents.paper_pdfs(f.path))  # skip patents

    def _on_llm_progress(self, msg):
        self.log.emit(msg)
        short = msg.strip()
        if short.startswith("  "):
            short = short.strip()
        if len(short) > 80:
            short = short[:77] + "..."
        spin_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        idx = getattr(self, '_spin_idx', 0)
        spin = spin_chars[idx % len(spin_chars)]
        self._spin_idx = idx + 1
        done = self._done_papers
        total = self._total_papers
        if total > 0:
            self.lbl_progress.setText(f"{spin} {done}/{total} — {short}")
        else:
            self.lbl_progress.setText(f"{spin} {short}")

    def _on_paper_processed(self, pdf_name, topic_name):
        self._done_papers += 1
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._on_paper_processed_deferred())

    def _on_paper_processed_deferred(self):
        self.overall_progress.setValue(self._done_papers)
        pct = int(100 * self._done_papers / self._total_papers) if self._total_papers else 0
        self.lbl_progress.setText(f"✓ {self._done_papers}/{self._total_papers} papers done ({pct}%)")

    def _on_paper_failed(self, pdf_name, topic_name, reason):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.log.emit(f"Failed: {pdf_name} - {reason}"))

    def _on_topic_summary_done(self, topic_name, summary_path):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._on_topic_summary_done_deferred(topic_name))

    def _on_topic_summary_done_deferred(self, topic_name):
        self.overall_progress.setValue(self.overall_progress.value() + 1)
        self.log.emit(f"Topic synthesis done: {topic_name}")

    def _on_global_summary_done(self, global_path):
        label = "RELATED WORK" if "RELATED_WORK" in global_path else "GLOBAL SUMMARY"
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.log.emit(f"{label} done"))

    def _on_llm_finished(self):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._on_llm_finished_deferred)

    def _on_llm_finished_deferred(self):
        self.overall_progress.setVisible(False)
        self.btn_per_paper.setEnabled(True)
        self.btn_topic.setEnabled(True)
        self.btn_global.setEnabled(True)
        self.btn_related.setEnabled(True)
        self.btn_patent.setEnabled(True)
        self.btn_introduction.setEnabled(True)
        self.btn_all.setEnabled(True)
        self.btn_start_stop.setText("Start")
        self.btn_start_stop.setObjectName("btn_search")
        self.btn_start_stop.style().unpolish(self.btn_start_stop); self.btn_start_stop.style().polish(self.btn_start_stop)
        self.lbl_progress.setText("Refreshing tree...")

        from PySide6.QtCore import QTimer
        mode = getattr(self, '_selected_mode', 'per_paper')

        launching_next = False
        download_name = self._effective_session_name()
        model_root = paths.model_output_root(self.cfg, download_name, self.cfg.get("llm_model", "default"))
        intro_path = paths.introduction_file(model_root)
        endpoint = self.cfg.get("llm_endpoint", "")
        model = self.cfg.get("llm_model", "")
        _pn = self.cfg.get("llm_provider", "LM Studio")
        _ak = get_provider_api_key(_pn, self.cfg)
        if mode == "all":
            rw_path = paths.related_work_file(model_root)
            if not os.path.isfile(rw_path) and endpoint and model:
                launching_next = True
                self._selected_mode = "related_work"
                QTimer.singleShot(150, lambda: self._on_related_work(endpoint, model, _pn, _ak))
            elif not os.path.isfile(intro_path) and endpoint and model:
                launching_next = True
                self._selected_mode = "introduction"
                QTimer.singleShot(150, lambda: self._on_introduction(endpoint, model, _pn, _ak))
        elif mode == "related_work":
            if not os.path.isfile(intro_path) and endpoint and model:
                launching_next = True
                self._selected_mode = "introduction"
                QTimer.singleShot(150, lambda: self._on_introduction(endpoint, model, _pn, _ak))

        QTimer.singleShot(250, self._refresh_tree)

        if launching_next:
            next_name = "Related Work" if self._selected_mode == "related_work" else "Introduction"
            self.lbl_progress.setText(f"Launching {next_name}...")
            self.log.emit(f"Launching {next_name}...")
        else:
            self.log.emit("LLM processing complete.")
            out_dir = self.cfg.get("summary_output_dir", "").strip()
            download_name = self.cfg.get("session_download_name", "")
            safe_model = re.sub(r'[\\/*?:"<>|.]', '_', self.cfg.get("llm_model", "default"))
            end_status = self._pipeline_status(os.path.join(out_dir, download_name, safe_model))
            self.log.emit(end_status)
            self.lbl_progress.setText("Done")
            QMessageBox.information(self, "Processing Complete", end_status)

    def _on_llm_error(self, msg):
        self.btn_per_paper.setEnabled(True)
        self.btn_topic.setEnabled(True)
        self.btn_global.setEnabled(True)
        self.btn_related.setEnabled(True)
        self.btn_patent.setEnabled(True)
        self.btn_introduction.setEnabled(True)
        self.btn_all.setEnabled(True)
        self.btn_start_stop.setText("Start")
        self.btn_start_stop.setObjectName("btn_search")
        self.btn_start_stop.style().unpolish(self.btn_start_stop); self.btn_start_stop.style().polish(self.btn_start_stop)
        self.overall_progress.setVisible(False)
        self.lbl_progress.setText("Error")
        self.log.emit(f"LLM ERROR: {msg}")
        QMessageBox.critical(self, "LLM Error", msg)

    def _disable_buttons(self):
        self.btn_start_stop.setText("Stop")
        self.btn_start_stop.setObjectName("")
        self.btn_start_stop.setObjectName("btn_stop")
        self.btn_start_stop.style().unpolish(self.btn_start_stop); self.btn_start_stop.style().polish(self.btn_start_stop)
        self.btn_per_paper.setEnabled(False)
        self.btn_topic.setEnabled(False)
        self.btn_global.setEnabled(False)
        self.btn_related.setEnabled(False)
        self.btn_introduction.setEnabled(False)
        self.btn_all.setEnabled(False)
        self.lbl_progress.setText("Starting...")

    def _on_related_work(self, endpoint, model, provider_name="", api_key="not-needed", force_rerun=False):
        if not endpoint or not model:
            QMessageBox.warning(
                self, "No LLM Model Available",
                "No LLM model is configured.\n\n"
                "Set the provider, endpoint and model in the Settings tab.")
            return
        download_name = self._effective_session_name()
        if not download_name:
            QMessageBox.warning(self, "Missing Input", "Load a session in the Search tab first.")
            return
        in_dir = paths.session_downloads_root(self.cfg, download_name)
        if not self.cfg.get("summary_output_dir", "").strip():
            QMessageBox.warning(self, "Missing Input", "Set Summary Output directory in Settings first.")
            return
        model = self.cfg.get("llm_model", "default")
        model_output_root = paths.model_output_root(self.cfg, download_name, model)

        # Patents live outside the paper tree, so they are appended explicitly via
        # the shared helper. A patents-only session is valid — Related Work can be
        # built from patents alone.
        patent_text = paths.read_patent_analyses(model_output_root)

        global_path = paths.global_summary_file(model_output_root)
        source_text = ""
        if os.path.isfile(global_path):
            with open(global_path, "r", encoding="utf-8") as f:
                source_text = f.read()
        else:
            summaries_dir = paths.topic_reviews_parent(model_output_root)
            parts = []
            if os.path.isdir(summaries_dir):
                for folder in sorted(os.listdir(summaries_dir)):
                    cache_path = paths.topic_cache_dir(model_output_root, folder)
                    if os.path.isdir(cache_path):
                        md_files = sorted([f for f in os.listdir(cache_path) if f.endswith(".md")])
                        for mf in md_files:
                            with open(os.path.join(cache_path, mf), "r", encoding="utf-8") as f:
                                parts.append(f.read())
            if not parts and not patent_text:
                QMessageBox.warning(
                    self, "No Summaries",
                    "No paper summaries or patent analyses found.\n"
                    "Run Per-Paper first, or Patent Landscape if this is a patent session.")
                return
            source_text = "\n\n---\n\n".join(parts)

        if patent_text:
            source_text = (source_text + "\n\n" + patent_text) if source_text else patent_text

        prompt = self.cfg.load_prompt("related_work_prompt")
        if not prompt:
            prompt = "Generate a Related Work section based on these paper summaries."
        context = self._get_research_context()
        intent = self.intent_text.toPlainText().strip()
        if not context:
            context = self.our_work.toPlainText().strip()
        full_prompt = prompt.format(
            context=context, intent=intent if intent else "Not specified",
            topic_summaries=source_text,
        )

        self._disable_buttons()
        self.lbl_progress.setText("Generating Related Work...")
        self.log.emit("Starting Related Work...")

        if self._llm_worker:
            try:
                self._llm_worker.progress.disconnect()
                self._llm_worker.global_summary_done.disconnect()
                self._llm_worker.finished.disconnect()
                self._llm_worker.error.disconnect()
            except Exception:
                pass
            if self._llm_worker.isRunning():
                self._llm_worker.stop()
                self._llm_worker.wait(5000)
            self._llm_worker.deleteLater()
            self._llm_worker = None

        self._llm_worker = LLMProcessWorker(
            endpoint=endpoint, model_id=model, prompt_path="",
            input_path=in_dir, output_path=model_output_root,
            topic_synthesis_prompt="", global_synthesis_prompt=full_prompt,
            research_context="", run_mode="related_work_custom",
            provider_name=provider_name, api_key=api_key,
            force_rerun=force_rerun,
        )
        self._llm_worker.progress.connect(self._on_llm_progress)
        self._llm_worker.global_summary_done.connect(self._on_global_summary_done)
        self._llm_worker.finished.connect(self._on_llm_finished)
        self._llm_worker.error.connect(self._on_llm_error)
        self._llm_worker.start()

    def _on_introduction(self, endpoint, model, provider_name="", api_key="not-needed", force_rerun=False):
        if not endpoint or not model:
            QMessageBox.warning(
                self, "No LLM Model Available",
                "No LLM model is configured.\n\n"
                "Set the provider, endpoint and model in the Settings tab.")
            return
        download_name = self._effective_session_name()
        if not download_name:
            QMessageBox.warning(self, "Missing Input", "Load a session in the Search tab first.")
            return
        in_dir = paths.session_downloads_root(self.cfg, download_name)
        if not self.cfg.get("summary_output_dir", "").strip():
            QMessageBox.warning(self, "Missing Input", "Set Summary Output directory in Settings first.")
            return
        model = self.cfg.get("llm_model", "default")
        model_output_root = paths.model_output_root(self.cfg, download_name, model)

        parts = []
        summaries_dir = paths.topic_reviews_parent(model_output_root)
        if os.path.isdir(summaries_dir):
            for folder in sorted(os.listdir(summaries_dir)):
                cache_path = paths.topic_cache_dir(model_output_root, folder)
                if os.path.isdir(cache_path):
                    for mf in sorted(os.listdir(cache_path)):
                        if mf.endswith(".md"):
                            with open(os.path.join(cache_path, mf), "r", encoding="utf-8") as f:
                                parts.append(f.read())
        patent_text = paths.read_patent_analyses(model_output_root)
        if not parts and not patent_text:
            QMessageBox.warning(
                self, "No Summaries",
                "No paper summaries or patent analyses found.\n"
                "Run Per-Paper first, or Patent Landscape if this is a patent session.")
            return
        paper_analyses = "\n\n---\n\n".join(parts)
        if patent_text:
            paper_analyses = (paper_analyses + "\n\n" + patent_text) if paper_analyses else patent_text

        prompt = self.cfg.load_prompt("introduction_prompt")
        if not prompt:
            prompt = "Generate an Introduction section based on these paper analyses."
        context = self._get_research_context()
        intent = self.intent_text.toPlainText().strip()
        if not context:
            context = self.our_work.toPlainText().strip()
        full_prompt = prompt.format(
            context=context, intent=intent if intent else "Not specified",
            paper_analyses=paper_analyses,
        )

        self._disable_buttons()
        self.lbl_progress.setText("Generating Introduction...")
        self.log.emit("Starting Introduction...")

        if self._llm_worker:
            try:
                self._llm_worker.progress.disconnect()
                self._llm_worker.global_summary_done.disconnect()
                self._llm_worker.finished.disconnect()
                self._llm_worker.error.disconnect()
            except Exception:
                pass
            if self._llm_worker.isRunning():
                self._llm_worker.stop()
                self._llm_worker.wait(5000)
            self._llm_worker.deleteLater()
            self._llm_worker = None

        self._llm_worker = LLMProcessWorker(
            endpoint=endpoint, model_id=model, prompt_path="",
            input_path=in_dir, output_path=model_output_root,
            topic_synthesis_prompt="", global_synthesis_prompt=full_prompt,
            research_context="", run_mode="introduction_custom",
            provider_name=provider_name, api_key=api_key,
            force_rerun=force_rerun,
        )
        self._llm_worker.progress.connect(self._on_llm_progress)
        self._llm_worker.global_summary_done.connect(self._on_global_summary_done)
        self._llm_worker.finished.connect(self._on_llm_finished)
        self._llm_worker.error.connect(self._on_llm_error)
        self._llm_worker.start()

    def _select_mode(self, mode, persist: bool = True):
        """Select a report mode and highlight its button.

        `persist=False` for programmatic selection (initial default, restoring a
        saved mode). Only a real user click should write the setting — _setup_ui
        ended with a persisting _select_mode("per_paper"), which overwrote the
        saved mode at construction, so showEvent always read back "per_paper"
        and the restore never fired for any mode.
        """
        self._selected_mode = mode
        if persist:
            self.cfg.set("summ_selected_mode", mode)
        for btn, m in self._mode_buttons:
            if m == mode:
                btn.setObjectName("btn_done")
                btn.style().unpolish(btn); btn.style().polish(btn)
            else:
                btn.setObjectName("")
                btn.style().unpolish(btn); btn.style().polish(btn)

    def _on_start_stop(self):
        if self._llm_worker and self._llm_worker.isRunning():
            try:
                self._llm_worker.progress.disconnect()
                self._llm_worker.paper_processed.disconnect()
                self._llm_worker.topic_summary_done.disconnect()
                self._llm_worker.global_summary_done.disconnect()
                self._llm_worker.finished.disconnect()
                self._llm_worker.error.disconnect()
            except Exception:
                pass
            self._llm_worker.stop()
            if not self._llm_worker.wait(5000):
                self._llm_worker.terminate()
                self._llm_worker.wait(2000)
            self._llm_worker = None
            self.log.emit("Stopping LLM processing...")
            self.btn_per_paper.setEnabled(True)
            self.btn_topic.setEnabled(True)
            self.btn_global.setEnabled(True)
            self.btn_related.setEnabled(True)
            self.btn_patent.setEnabled(True)
            self.btn_introduction.setEnabled(True)
            self.btn_all.setEnabled(True)
            self.btn_start_stop.setText("Start")
            self.btn_start_stop.setObjectName("btn_search")
            self.btn_start_stop.style().unpolish(self.btn_start_stop); self.btn_start_stop.style().polish(self.btn_start_stop)
            self.overall_progress.setVisible(False)
            self.lbl_progress.setText("Stopped")
            return
        if not hasattr(self, '_selected_mode'):
            self._selected_mode = "per_paper"
        self._on_process(self._selected_mode)


class _PatentLandscapeWorker(QThread):
    """Runs the patent landscape pipeline off the UI thread.

    Delegates to ``researchforge_api.generate_patent_landscape`` rather than
    reimplementing it, so the GUI and MCP produce byte-identical output in the
    same location. The API layer has no Qt dependency, so this is safe.
    """
    progress = Signal(str)
    tick = Signal(int, int, str)   # (done, total, label) for the visible bar
    done = Signal(dict)

    def __init__(self, session_id: str, force: bool = False, parent=None):
        super().__init__(parent)
        self.session_id = session_id
        self.force = force

    def run(self):
        try:
            self.progress.emit("Analysing patents...")
            import researchforge_api as rf

            def _cb(done, total, label):
                self.tick.emit(done, total, label)
                self.progress.emit(f"Patent {done}/{total}: {label}")

            res = rf.generate_patent_landscape(
                session_id=self.session_id, on_progress=_cb)
            self.done.emit(res if isinstance(res, dict) else {"error": "unexpected result"})
        except Exception as e:
            self.done.emit({"error": str(e)})


def _on_patent_landscape(self, force_rerun: bool = False):
    """Patent Landscape button handler. Bound onto SummarizeTab below."""
    # Two different things, previously conflated: `session_name` is the sanitised
    # path segment the summary layout is built from, `session_id` is the session
    # JSON's filename stem. They are independent — a GUI session is saved as
    # session_20260727_114039.json while its name is "rPPG systems". Passing the
    # name as the id made the API fail to load the session, which surfaced as
    # "synthesize_* needs ... a valid session_id".
    session_name = self._effective_session_name()
    session_id = self.cfg.get("last_session", "") or session_name
    if not session_name and not session_id:
        QMessageBox.warning(self, "No session", "Load or create a session first.")
        return

    if force_rerun:
        # Overwrite means re-analyse: drop the per-patent cache so every patent
        # is sent to the LLM again.
        try:
            root = paths.model_output_root(self.cfg, session_name, self.cfg.get("llm_model", ""))
            cache = paths.patent_cache_dir(root)
            if os.path.isdir(cache):
                shutil.rmtree(cache)
        except Exception as e:
            self.log.emit(f"Could not clear patent cache: {e}")

    self.btn_patent.setEnabled(False)
    self.overall_progress.setVisible(True)
    self.overall_progress.setRange(0, 0)   # busy until the first tick sets a range
    self.lbl_progress.setText("Analysing patents...")
    self._patent_worker = _PatentLandscapeWorker(session_id, force_rerun, self)
    self._patent_worker.progress.connect(self.log.emit)
    self._patent_worker.tick.connect(self._on_patent_tick)
    self._patent_worker.done.connect(self._on_patent_landscape_done)
    self._patent_worker.start()


def _on_patent_tick(self, done: int, total: int, label: str):
    # `done` is patents COMPLETED, so `done == total` is only ever the synthesis
    # step — it can't collide with the last per-patent report (done == total-1).
    if done >= total:
        self.overall_progress.setRange(0, 0)   # indeterminate: LLM is synthesising
        self.lbl_progress.setText(f"⏳ Synthesising landscape from {total} patents...")
        return
    self.overall_progress.setRange(0, total)
    self.overall_progress.setValue(done)
    self.lbl_progress.setText(f"⏳ Patent {done + 1}/{total}: {label}")


def _on_patent_landscape_done(self, res: dict):
    self.btn_patent.setEnabled(True)
    self.overall_progress.setVisible(False)
    self._patent_worker = None
    if "error" in res:
        self.log.emit(f"Patent landscape failed: {res['error']}")
        QMessageBox.warning(self, "Patent Landscape", res["error"])
        return
    total = res.get("patents", 0)
    no_claims = res.get("without_claims", 0)
    self.lbl_progress.setText(f"✓ Patent landscape: {total} patents")
    msg = f"Patent landscape written: {total} patents."
    if no_claims:
        with_claims = total - no_claims
        msg += (f"\n\nFor {no_claims} of {total}, no claims text was available "
                f"via EPO OPS. Every patent HAS claims — they are public — but "
                f"EPO's Open Patent Services carries full text mainly for EP and "
                f"WO documents; for many national publications (US, CN, KR, JP) it "
                f"returns only bibliographic data + abstract. Those {no_claims} are "
                f"described from title + abstract and left out of the claim-scope "
                f"map; read their claims at the national office or on Espacenet. "
                f"{with_claims} carry full claims (mainly EP/WO).\n\n"
                f"This is EPO coverage, not a failure. Only the EPO OPS provider "
                f"has been tested — PatentsView (US, with claims) and PQAI are "
                f"unverified.")
    self.log.emit(f"Patent landscape written: {total} patents "
                  f"({no_claims} metadata-only) -> {res.get('path', '')}")
    QMessageBox.information(self, "Patent Landscape", msg)


SummarizeTab._on_patent_landscape = _on_patent_landscape
SummarizeTab._on_patent_tick = _on_patent_tick
SummarizeTab._on_patent_landscape_done = _on_patent_landscape_done
