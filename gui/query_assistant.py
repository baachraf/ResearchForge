"""
Query Assistant: uses local LLM to generate search queries from a research description.
"""
import json
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QMessageBox, QSplitter, QWidget, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal, QThread

from gui.llm_provider import create_llm_client, get_provider_api_key, ensure_llm_available


_FALLBACK_QUERY_GENERATION = """\
You are a senior academic research librarian. Your task: build precise, high-recall scientific search queries from a researcher's description. Follow all steps in order.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 0 — STRUCTURED INPUT DETECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Check whether the input contains "CONTRIBUTION:" and "PROBLEM SPACE:" section headers.

If both are present:
  CONTRIBUTION TEXT: [everything between "CONTRIBUTION:" and "PROBLEM SPACE:"]
  PROBLEM SPACE TEXT: [everything after "PROBLEM SPACE:"]

If not present:
  CONTRIBUTION TEXT: (empty — leave CONTRIBUTION TEXT empty)
  PROBLEM SPACE TEXT: [the entire input]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — RESEARCH BREAKDOWN  (write this before the JSON)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Using PROBLEM SPACE TEXT as the primary source (and CONTRIBUTION TEXT for coined-term anchors), write:

CORE PROBLEM: [one sentence — exactly what is being investigated?]
KEY CONCEPTS: [every technical object, signal, method, dataset explicitly mentioned]
PRIMARY vs AUXILIARY:
  PRIMARY (P) = the domain, phenomenon, or problem being studied (what the research IS about)
  AUXILIARY (A) = techniques, algorithms, or general tools used to study that problem (what the research USES)
  For each KEY CONCEPT, label it P or A.
  Example: "We use PCA to study EMG fatigue patterns" → EMG(P), fatigue(P), PCA(A)
SYNONYM MAP: [for each concept, list its alternative names as found in literature]
DISCIPLINES: [every field this research touches — be exhaustive, minimum one per concept]
SEARCH ANGLES: [one distinct search angle per discipline, covering every concept above]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — JSON QUERIES  (output immediately after STEP 1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Generate a JSON array. Two classes of queries, distinguished by the "intent" field:

CLASS 1 — "prior_art" queries (generate 3–5):
  Source: CONTRIBUTION TEXT.
  Goal: find papers that use, compare against, or directly precede the researcher's specific framing.
  Rule: MUST use the coined terms and specific claims from CONTRIBUTION TEXT as anchors.
  Generate ONLY if CONTRIBUTION TEXT is non-empty. If empty, skip this class entirely.

CLASS 2 — "discovery" queries (generate 4–6):
  Source: PROBLEM SPACE TEXT.
  Goal: find papers addressing the same underlying problem using established field vocabulary.
  Rule: MUST NOT use the researcher's coined terms — use only established synonyms and classical framings from SYNONYM MAP.

ABSOLUTE RULES — violating any is a critical failure:

1. FAITHFULNESS — Every query must trace directly to a concept the researcher stated.
   Do NOT add topics not mentioned or clearly implied by the description.

2. SYNONYMS — Each query string must include alternative terminology to maximize recall.
   Bad:  "heart rate facial video"
   Good: "rPPG remote photoplethysmography contactless heart rate facial video camera"

3. COVERAGE — Every discipline from STEP 1 must appear in at least one query.
   Before outputting: if any discipline is missing, add a query for it.

4. must_contain — Fill with 1–2 core terms that uniquely define this angle.
   Leave empty [] only when no single term reliably distinguishes this angle.

5. must_not — Always [].

6. NON-OVERLAP — Each query targets a different concept or methodology.
   No two queries should return substantially the same papers.

7. after_date — Always "" unless the researcher explicitly stated a time range.

8. sources — Choose databases appropriate to the discipline:
   - arxiv: CS, physics, engineering, mathematics, signal processing, ML
   - semantic_scholar: all fields — always include for breadth
   - pubmed: biomedical, physiology, clinical medicine, biology
   - web: grey literature, preprints, technical reports

9. TECHNIQUE ANCHORING — for every AUXILIARY (A) concept from STEP 1:
   - NEVER generate a query that targets the technique in isolation.
     Bad:  "PCA principal component analysis dimensionality reduction"
   - Always combine with at least one PRIMARY concept.
     Good: "PCA principal component analysis EMG fatigue detection"

SELF-CHECK before writing the JSON:
- Every concept from STEP 1 covered by at least one query?
- Queries non-overlapping?
- Every query string includes synonyms?
- must_contain filled where applicable?
- No AUXILIARY (A) concept as standalone query?
- prior_art queries only use CONTRIBUTION TEXT terms?
- discovery queries do NOT use CONTRIBUTION TEXT coined terms?

OUTPUT FORMAT — a valid JSON array immediately after STEP 1:
[
  {
    "name": "short_snake_case_name",
    "intent": "prior_art",
    "query": "coined terms and their synonyms",
    "must_contain": ["coined_term"],
    "must_not": [],
    "sources": ["arxiv", "semantic_scholar"],
    "after_date": ""
  },
  {
    "name": "short_snake_case_name",
    "intent": "discovery",
    "query": "established field vocabulary synonyms classical framing",
    "must_contain": ["field_term"],
    "must_not": [],
    "sources": ["arxiv", "semantic_scholar"],
    "after_date": ""
  }
]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FOCUS KEYWORDS — when the researcher provides them
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
If the researcher's message contains a "FOCUS KEYWORDS:" line, those terms are priority anchors:
- Each focus keyword MUST appear in at least one prior_art query string (include it and its synonyms).
- Add the focus keyword to must_contain for the most relevant prior_art query targeting it.
- In STEP 1, list focus keywords first in SYNONYM MAP and SEARCH ANGLES.
- After generating all queries, verify every focus keyword is covered — if any is missing, add a dedicated prior_art query for it.
- If CONTRIBUTION TEXT is empty (no prior_art class generated), include focus keywords in the most relevant discovery query instead."""


class QueryGenWorker(QThread):
    progress = Signal(str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, endpoint: str, model_id: str, description: str, prompt: str, keywords: str = "",
                 provider_name: str = "", api_key: str = "not-needed"):
        super().__init__()
        self.endpoint = endpoint
        self.model_id = model_id
        self.description = description
        self.prompt = prompt
        self.keywords = keywords.strip()
        self.provider_name = provider_name
        self.api_key = api_key

    def run(self):
        try:
            from openai import OpenAI
            client = create_llm_client(self.endpoint, self.api_key, self.provider_name, timeout=60.0)

            self.progress.emit("Connecting to LLM...")
            res = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": f"My research project:\n\n{self.description}"
                        + (f"\n\nFOCUS KEYWORDS: {self.keywords}" if self.keywords else "")},
                ],
                temperature=0.3,
            )
            text = res.choices[0].message.content
            if not text or not text.strip():
                self.error.emit("LLM returned empty response.")
                return

            self.progress.emit("Parsing response...")
            queries = self._parse(text)
            if not queries:
                self.error.emit("Could not parse queries from LLM response. The model may not have returned valid JSON.")
                return

            self.progress.emit(f"Generated {len(queries)} queries.")
            for i, q in enumerate(queries):
                if isinstance(q, str):
                    queries[i] = {"name": f"Query {i+1}", "query": q, "sources": ["arxiv", "semantic_scholar", "web", "brave", "pubmed"]}
            for q in queries:
                q.setdefault("must_not", [])
                q.setdefault("must_contain", [])
            self.finished.emit(queries)

        except Exception as e:
            self.error.emit(str(e))

    def _parse(self, text: str) -> list:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```\w*\n?', '', text)
            text = re.sub(r'\n?```$', '', text)
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list):
                        return v
                return [data]
            return []
        except json.JSONDecodeError:
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        return []


class QueryAssistantDialog(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self._worker = None
        self._generated_queries = []
        self._mode = config_manager.get("search_mode", "academic") if config_manager else "academic"
        self.setWindowTitle("AI Query Generator")
        self.setMinimumSize(750, 600)
        self._setup_ui()
        if self._mode != "academic":
            self._setup_general_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Top: description input ──
        lbl = QLabel("Describe your research — the LLM will generate targeted search queries:")
        lbl.setObjectName("qa_title_label")
        layout.addWidget(lbl)

        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText(
            "Example:\n"
            "I am working on remote photoplethysmography (rPPG) for extracting heart rate from facial video. "
            "Specifically, I study how video codec compression (H.264, H.265, MPEG-4) degrades rPPG signal quality. "
            "I compare patch-based PCA methods against chrominance-based methods like CHROM and POS. "
            "I use UBFC-rPPG, MCD, and UBFC-PHYS datasets. I need papers on: codec artifact structures, "
            "blind source separation for rPPG, spatial decomposition methods, and benchmark comparisons."
        )
        self.desc_input.setMinimumHeight(140)
        layout.addWidget(self.desc_input)

        # ── Focus Keywords ──
        kw_row = QHBoxLayout()
        kw_lbl = QLabel("Focus Keywords:")
        kw_lbl.setObjectName("sc_bold_label")
        kw_lbl.setFixedWidth(120)
        kw_row.addWidget(kw_lbl)
        self.focus_keywords = QLineEdit()
        self.focus_keywords.setPlaceholderText(
            "Strongly recommended — comma-separated core terms the LLM must anchor every query to "
            "(e.g. rPPG, codec compression, heart rate extraction)"
        )
        kw_row.addWidget(self.focus_keywords, 1)
        layout.addLayout(kw_row)

        hb_btns = QHBoxLayout()

        self.btn_generate = QPushButton("Generate Queries")
        self.btn_generate.setObjectName("btn_search")
        self.btn_generate.clicked.connect(self._on_generate)
        hb_btns.addWidget(self.btn_generate)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setObjectName("sc_hint")
        hb_btns.addWidget(self.lbl_hint)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(16)
        hb_btns.addWidget(self.progress, 1)

        hb_btns.addStretch()
        layout.addLayout(hb_btns)

        # ── Results table ──
        self.results_table = QTableWidget(0, 6)
        self.results_table.setHorizontalHeaderLabels(["✓", "Name", "Search Terms", "Must Contain", "Must NOT", "Sources"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.results_table.setColumnWidth(0, 30)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.results_table.setColumnWidth(1, 100)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        self.results_table.setColumnWidth(3, 120)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)
        self.results_table.setColumnWidth(4, 80)
        self.results_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Interactive)
        self.results_table.setColumnWidth(5, 100)
        if self._mode != "academic":
            self.results_table.setColumnHidden(3, True)
            self.results_table.setColumnHidden(4, True)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setVisible(False)
        layout.addWidget(self.results_table)

        # ── Bottom buttons ──
        hb_bottom = QHBoxLayout()
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
        self.btn_select_all.setVisible(False)
        hb_bottom.addWidget(self.btn_select_all)

        self.btn_deselect = QPushButton("Deselect All")
        self.btn_deselect.clicked.connect(lambda: self._set_all_checked(False))
        self.btn_deselect.setVisible(False)
        hb_bottom.addWidget(self.btn_deselect)

        hb_bottom.addStretch()

        self.btn_import = QPushButton("Import Selected to Query List")
        self.btn_import.setObjectName("btn_save")
        self.btn_import.clicked.connect(self._on_import)
        self.btn_import.setVisible(False)
        hb_bottom.addWidget(self.btn_import)

        self.btn_regenerate = QPushButton("Regenerate")
        self.btn_regenerate.clicked.connect(self._on_generate)
        self.btn_regenerate.setVisible(False)
        hb_bottom.addWidget(self.btn_regenerate)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        hb_bottom.addWidget(self.btn_close)

        layout.addLayout(hb_bottom)

    def _on_generate(self):
        endpoint = self.cfg.get("llm_endpoint", "http://127.0.0.1:1234/v1")
        model = self.cfg.get("llm_model", "")
        provider_name = self.cfg.get("llm_provider", "LM Studio")
        api_key = get_provider_api_key(provider_name, self.cfg)
        desc = self.desc_input.toPlainText().strip()

        if not desc:
            QMessageBox.warning(self, "Empty", "Describe your research first.")
            return
        if not ensure_llm_available(self.cfg, self):
            return
        provider_name = self.cfg.get("llm_provider", "LM Studio")
        api_key = get_provider_api_key(provider_name, self.cfg)
        endpoint = self.cfg.get("llm_endpoint", "")
        model = self.cfg.get("llm_model", "")
        self.btn_generate.setEnabled(False)
        self.btn_regenerate.setVisible(True)
        self.btn_regenerate.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.lbl_hint.setText("Generating queries using LLM...")
        self.results_table.setVisible(False)
        self.btn_import.setVisible(False)
        self.btn_select_all.setVisible(False)
        self.btn_deselect.setVisible(False)

        keywords = self.focus_keywords.text().strip()
        prompt = self.cfg.load_prompt("query_generation_prompt") or _FALLBACK_QUERY_GENERATION
        self._worker = QueryGenWorker(endpoint, model, desc, prompt, keywords, provider_name, api_key)
        self._worker.progress.connect(lambda m: self.lbl_hint.setText(m))
        self._worker.finished.connect(self._on_queries_ready)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_queries_ready(self, queries):
        self._generated_queries = queries
        self.progress.setVisible(False)
        self.btn_generate.setEnabled(True)
        self.btn_regenerate.setEnabled(True)
        self.lbl_hint.setText(f"Generated {len(queries)} queries. Review and select which to import.")
        self._populate_table()

    def _on_error(self, msg):
        self.progress.setVisible(False)
        self.btn_generate.setEnabled(True)
        self.btn_regenerate.setEnabled(True)
        self.lbl_hint.setText(f"Error: {msg}")
        QMessageBox.critical(self, "LLM Error", msg)

    def _populate_table(self):
        self.results_table.setRowCount(0)
        self.results_table.setVisible(True)
        self.btn_import.setVisible(True)
        self.btn_select_all.setVisible(True)
        self.btn_deselect.setVisible(True)

        for q in self._generated_queries:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)

            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            cb.setCheckState(Qt.Checked)
            self.results_table.setItem(row, 0, cb)

            self.results_table.setItem(row, 1, QTableWidgetItem(q.get("name", "query")))
            self.results_table.setItem(row, 2, QTableWidgetItem(q.get("query", "")))
            self.results_table.setItem(row, 3, QTableWidgetItem(", ".join(q.get("must_contain", []))))
            self.results_table.setItem(row, 4, QTableWidgetItem(", ".join(q.get("must_not", []))))
            self.results_table.setItem(row, 5, QTableWidgetItem(", ".join(q.get("sources", ["arxiv", "semantic_scholar"]))))

    def _set_all_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.results_table.rowCount()):
            self.results_table.item(row, 0).setCheckState(state)

    def _on_import(self):
        selected = []
        for row in range(self.results_table.rowCount()):
            if self.results_table.item(row, 0).checkState() == Qt.Checked:
                q = self._generated_queries[row].copy()
                sources = q.get("sources", ["arxiv", "semantic_scholar", "web"])
                date = q.get("after_date", "").strip()
                if date and not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
                    date = ""

                selected.append({
                    "name": q.get("name", "query"),
                    "query": q.get("query", ""),
                    "output_folder": q.get("name", "query"),
                    "sources": [s for s in sources if s in ("arxiv", "semantic_scholar", "web", "brave")],
                    "and_terms": q.get("query", "").split(),
                    "or_terms": [],
                    "after_date": date,
                    "max_results": 30,
                    "max_size_mb": 50.0,
                    "must_contain": q.get("must_contain", []),
                    "must_not": q.get("must_not", []),
                    "relevance_threshold": max(1, len(q.get("must_contain", [])) // 3 + 1),
                    "force_plus": False,
                    "language": "en",
                })
        self._result = selected
        self.accept()

    def get_imported_queries(self) -> list:
        return getattr(self, '_result', [])

    def _setup_general_ui(self):
        self.setWindowTitle("Add Search Queries")
        self.desc_input.setPlaceholderText("Type your search terms — one per line:\nToyota RAV4 2023 service manual\nhybrid battery replacement")
        self.focus_keywords.setVisible(False)
        self.desc_input.setMinimumHeight(80)
        self.btn_generate.setText("Add Queries")
        self.btn_generate.setToolTip("Add these searches directly (no LLM used in document mode)")
        try:
            self.btn_generate.clicked.disconnect()
        except Exception:
            pass
        self.btn_generate.clicked.connect(self._add_direct_queries)

    def _add_direct_queries(self):
        desc = self.desc_input.toPlainText().strip()
        if not desc:
            QMessageBox.warning(self, "Empty", "Enter search terms first.")
            return
        queries = []
        for line in desc.splitlines():
            line = line.strip()
            if not line:
                continue
            queries.append({
                "name": line[:50],
                "query": line,
                "must_contain": [],
                "must_not": [],
                "sources": ["web"],
                "after_date": "",
            })
        self._generated_queries = queries
        self._populate_table()
        self.lbl_hint.setText(f"Added {len(queries)} queries.")
        self.results_table.setVisible(True)
        self.btn_import.setVisible(True)
        self.btn_select_all.setVisible(True)
        self.btn_deselect.setVisible(True)
