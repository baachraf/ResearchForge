"""
Session Creator dialog: single source of truth for session metadata and search queries.
"""
import json
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QLineEdit, QGroupBox, QWidget,
    QAbstractItemView, QDialogButtonBox, QMenu, QTabWidget,
    QFileDialog, QComboBox, QListWidget, QListWidgetItem, QCheckBox,
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QSize
from PySide6.QtGui import QIcon

from gui.app_info import icon
from gui.workers import _extract_paper_for_analysis
from research_downloader.sources.arxiv_source import ArxivSource
from research_downloader.sources.web_source import WebSource
from research_downloader.sources.brave_source import BraveSource
from gui.llm_provider import (
    PROVIDER_NAMES, PROVIDER_URLS, create_llm_client, fetch_models_for_provider,
    get_provider_api_key, provider_index_from_endpoint,
    ensure_llm_available, check_provider_connection,
)


def _load_prompt(cfg, key: str, fallback: str) -> str:
    content = ""
    if cfg:
        content = cfg.load_prompt(key)
    return content.strip() if content.strip() else fallback

FALLBACK_ENHANCE_RESEARCH = """\
You are a senior academic researcher helping a colleague structure their research description for a literature search system.

Read the researcher's description. Produce exactly two sections in the output — nothing else.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — mandatory, no exceptions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONTRIBUTION:
[3–6 sentences. The researcher's own coined terms, novel framing, specific claims, and named contributions. Preserve their vocabulary exactly. Expand abbreviations in parentheses. If FOCUS KEYWORDS are provided, every one must appear here.]

PROBLEM SPACE:
[3–6 sentences. The same research restated using the vocabulary that prior work uses to describe this problem — established field terms, classical problem names, standard methods and benchmarks in this area. Do NOT use the researcher's coined terms unless they are already standard in the field.]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FOCUS KEYWORDS — when provided
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
If the researcher's message contains a "FOCUS KEYWORDS:" line:
- Every focus keyword MUST appear in the CONTRIBUTION section.
- In PROBLEM SPACE, use their established-literature synonyms instead.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ABSOLUTE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- First line of output MUST be "CONTRIBUTION:" — no preamble, no "Here is...", no markdown fences.
- Never say "I need more information" — always produce both sections.
- If the input already contains "CONTRIBUTION:" and "PROBLEM SPACE:" headers, refine each section independently without collapsing them.
- If the input is too short or ambiguous for a clean split: CONTRIBUTION gets the user's terms verbatim, PROBLEM SPACE gets a best-effort restatement.
- Do NOT introduce research topics the user did not mention.
- No bullet points, no headers beyond the two section labels, no markdown."""

FALLBACK_ENHANCE_INTENT = """\
You are a senior academic researcher helping a colleague clarify their research goals.

Reformulate the following into a clear, structured statement of what the researcher wants to achieve:
- What comparisons they want to make
- What questions they want to answer
- What gaps they want to find
- What the end goal of this literature review is

This will be used by a summarizer LLM to focus its analysis, so be specific and actionable.
Output ONLY the reformulated text — no commentary, no markdown, no "Here is..."."""

FALLBACK_QUERY_GENERATOR = """\
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



FALLBACK_ANALYZE_OWN_PAPER = """\
You are a senior academic researcher analyzing a paper written by the user. Your goal: extract every meaningful detail about this paper's contributions, claims, results, and comparisons so the researcher can find related and competing work in the literature.

Follow both steps in order.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — DEEP PAPER ANALYSIS  (write this before the JSON)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Read the paper carefully, then write:

PROBLEM STATEMENT: [One precise sentence]
CORE CLAIMS: [Every assertion the paper makes as novel or true]
CONTRIBUTIONS: [Every methodological, theoretical, dataset, or systems contribution]
KEY RESULTS: [Quantitative findings with exact numbers, datasets, and baseline names]
COMPARISONS: [Prior methods compared against — method name, metric, their score, your score, gain]
RESEARCH CONTEXT: [Field, sub-field, application domain, datasets, evaluation protocols]
FOCUS KEYWORDS: [10–15 core technical terms — include abbreviations AND full forms]
SEARCH ANGLES: [6–8 angles to find papers that compete with, build on, or compare against this work]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — JSON OUTPUT  (immediately after STEP 1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{
  "description": "Research context paragraph (150–250 words). Cover: problem domain, core approach, key methods, datasets, and what makes this work distinct. Fluent academic prose.",
  "objectives": "What the researcher wants to achieve or prove (3–5 sentences). State goals, desired comparisons, gaps addressed. Specific and actionable.",
  "claims": ["claim 1", "..."],
  "contributions": ["contribution 1", "..."],
  "results": ["Metric: X on Dataset Y (vs Baseline Z: +N%)", "..."],
  "comparisons": [{"baseline": "method name", "metric": "metric name", "ours": "value", "theirs": "value", "gain": "+N%"}],
  "keywords": "keyword1, keyword2, keyword3, ...",
  "suggested_queries": [
    {
      "name": "short_snake_case_name",
      "query": "primary terms plus synonyms for maximum recall",
      "must_contain": ["core_term"],
      "must_not": [],
      "sources": ["arxiv", "semantic_scholar"],
      "after_date": ""
    }
  ]
}

RULES: description/objectives in prose. 6–8 queries. Include synonyms. Never invent data. Output only the JSON — no markdown fences."""


class _LLMWorker(QThread):
    """Generic single-turn LLM call worker. Used by "Enhance" button in session creator.

    Signals:
      progress(str) — Status messages
      finished(str) — LLM response text (empty on failure)
      error(str)    — Error message
    """
    progress = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, endpoint, model_id, system_prompt, user_content,
                 provider_name="", api_key="not-needed"):
        super().__init__()
        self.endpoint = endpoint
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.user_content = user_content
        self.provider_name = provider_name
        self.api_key = api_key
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            if self._stop:
                return
            from openai import OpenAI
            client = create_llm_client(self.endpoint, self.api_key, self.provider_name, timeout=120.0)
            self.progress.emit("Connecting to LLM...")
            res = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": self.user_content},
                ],
                temperature=0.3,
            )
            text = res.choices[0].message.content
            if not text or not text.strip():
                self.error.emit("LLM returned empty response.")
                self.finished.emit("")
                return
            self.finished.emit(text.strip())
        except Exception as e:
            self.error.emit(str(e))

class _ModelFetchWorker(QThread):
    result = Signal(list)

    def __init__(self, provider_name, endpoint_url, api_key):
        super().__init__()
        self.provider_name = provider_name
        self.endpoint_url = endpoint_url
        self.api_key = api_key

    def run(self):
        models = fetch_models_for_provider(self.provider_name, self.endpoint_url, self.api_key)
        self.result.emit(models or [])


class _QueryGenWorker(QThread):
    """Generates structured search queries from a natural-language description via LLM.

    Used by the "Find Queries" flow in the session creator. Sends the user's
    research description with query_generation_prompt as system prompt. Parses
    the LLM's JSON response into a list of query dicts.

    Signals:
      progress(str)  — Status messages
      finished(list) — List of query dicts (name, query, intent, sources, ...)
      error(str)     — Error message
    """
    progress = Signal(str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, endpoint, model_id, description, system_prompt,
                 provider_name="", api_key="not-needed"):
        super().__init__()
        self.endpoint = endpoint
        self.model_id = model_id
        self.description = description
        self.system_prompt = system_prompt
        self.provider_name = provider_name
        self.api_key = api_key

    def run(self):
        try:
            from openai import OpenAI
            client = create_llm_client(self.endpoint, self.api_key, self.provider_name, timeout=120.0)
            self.progress.emit("Connecting to LLM...")
            res = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"My research project:\n\n{self.description}"},
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
            self.finished.emit(queries)
        except Exception as e:
            self.error.emit(str(e))

    def _parse(self, text):
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


class _PaperAnalysisWorker(QThread):
    """Extracts text from a PDF and sends it to LLM for structured analysis.

    Used by "Analyze My Paper" in session creator. Extracts up to 20 pages,
    truncates to 24000 chars, sends with analyze_own_paper_prompt. Parses
    the JSON response into a dict with claims, results, comparisons, citation.

    Signals:
      progress(str)  — Status messages
      finished(dict) — Parsed analysis dict (claims, results, comparisons, citation_key, ...)
      error(str)     — Error message
    """
    progress = Signal(str)
    finished = Signal(object)
    error    = Signal(str)

    def __init__(self, pdf_path, endpoint, model_id, system_prompt, user_context="", focus_instructions="",
                 provider_name="", api_key="not-needed"):
        super().__init__()
        self.pdf_path = pdf_path
        self.endpoint = endpoint
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.user_context = user_context
        self.focus_instructions = focus_instructions
        self.provider_name = provider_name
        self.api_key = api_key
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            if self._stop:
                return
            self.progress.emit("Extracting PDF text...")
            text = self._extract_pdf()
            if self._stop:
                return
            if not text.strip():
                self.error.emit("Could not extract text from PDF. The file may be scanned/image-based.")
                return
            self.progress.emit(f"Extracted {len(text):,} characters. Sending to LLM...")
            parts = []
            if self.user_context:
                parts.append(f"RESEARCH CONTEXT (my work — use this to understand what I'm looking for):\n{self.user_context}")
            parts.append(f"Analyze this paper:\n\n{text}")
            if self.focus_instructions:
                parts.append(f"FOCUS INSTRUCTIONS: {self.focus_instructions}")
            user_msg = "\n\n".join(parts)
            from openai import OpenAI
            client = create_llm_client(self.endpoint, self.api_key, self.provider_name, timeout=180.0)
            res = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
            )
            raw = res.choices[0].message.content
            if not raw or not raw.strip():
                self.error.emit("LLM returned empty response.")
                return
            self.progress.emit("Parsing analysis...")
            result = self._parse(raw.strip())
            if not result:
                self.error.emit("Could not parse JSON from LLM response.")
                return
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _extract_pdf(self) -> str:
        return _extract_paper_for_analysis(self.pdf_path, max_pages=30, max_chars=40000)

    def _parse(self, text: str) -> dict:
        # strip markdown fences
        if "```" in text:
            text = re.sub(r'```\w*\n?', '', text)
        # find the JSON object (may have chain-of-thought before it)
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}


class _TitleLookupWorker(QThread):
    """Look up a list of paper titles via arXiv → Semantic Scholar → Brave.

    Tries each source sequentially with rate-limiting. Results streamed
    per-title via paper_found / title_not_found signals.

    Signals:
      paper_found(int, dict)      — (list_index, paper_dict)
      title_not_found(int, str)   — (list_index, title)
      progress(str)               — Status messages
      finished()                  — All titles processed
      error(str)                  — Error message
    """
    paper_found = Signal(int, dict)   # (list_index, paper_dict)
    title_not_found = Signal(int, str)  # (list_index, title)
    progress = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, titles: list, credentials: dict):
        super().__init__()
        self.titles = titles
        self.credentials = credentials
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            from difflib import SequenceMatcher
            arxiv_creds = self.credentials.get("arxiv", {})
            brave_creds = self.credentials.get("brave_search", {})
            brave_key = brave_creds.get("api_key", "")

            for i, title in enumerate(self.titles):
                if self._stop:
                    break

                found = None

                self.progress.emit(f"Looking up: {title[:70]}...")

                # DDG first — free, fast
                try:
                    src = WebSource()
                    results = src.search(query=title, max_results=5, mode="academic")
                    self.progress.emit(f"  DDG → {len(results)} results")
                    title_lower = title.lower()
                    for r in results:
                        rt = r.get("title", "").lower()
                        if rt and SequenceMatcher(None, rt, title_lower).ratio() >= 0.85:
                            found = r
                            found["source"] = "DuckDuckGo"
                            break
                except Exception as e:
                    self.progress.emit(f"  DDG error: {e}")

                # arXiv
                if found is None and arxiv_creds.get("enabled", True):
                    try:
                        src = ArxivSource(credentials=arxiv_creds)
                        found = src.lookup_by_title(title)
                        self.progress.emit(f"  arXiv → {'found' if found else 'not found'}")
                    except Exception as e:
                        self.progress.emit(f"  arXiv error: {e}")

                # Brave
                if found is None and brave_key and brave_creds.get("enabled", True):
                    try:
                        src = BraveSource(credentials=brave_creds)
                        results = src.search(query=f'"{title}"', max_results=3)
                        self.progress.emit(f"  Brave → {len(results)} results")
                        title_lower = title.lower()
                        for r in results:
                            rt = r.get("title", "").lower()
                            if rt and SequenceMatcher(None, rt, title_lower).ratio() >= 0.85:
                                found = r
                                found["source"] = "Brave"
                                break
                    except Exception as e:
                        self.progress.emit(f"  Brave error: {e}")

                if found is not None:
                    self.paper_found.emit(i, found)
                else:
                    self.title_not_found.emit(i, title)

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class SessionCreatorDialog(QDialog):
    """Dialog for creating/editing a research session with AI-enhanced workflows.

    Layout (left panel = text inputs, right panel = actions):
      ┌─ Session name, Research description, Keywords, Avoid topics ─┐
      ├─ Provider + Model row [with refresh]                          ┤
      ├─ [Enhance] [Generate Queries] [Analyze My Paper] ────────────┤
      ├─ Paper preview area                                           ┤
      ├─ Suggested queries table  →→  Search queries table            ┤
      └─ [Create Session] [Cancel]                                   ┘

    Key flows:
      - "Enhance": _LLMWorker → splits description into CONTRIBUTION/PROBLEM SPACE
      - "Generate Queries": _QueryGenWorker → generates structured search queries from description
      - "Analyze My Paper": _PaperAnalysisWorker → extract PDF → LLM → fill description

    Saves session state to config on "Create Session" click.
    """
    def __init__(self, config_manager, parent=None, existing=None):
        super().__init__(parent)
        self.cfg = config_manager
        self._worker = None
        self._paper_worker = None
        self._suggested_queries = []
        self._search_queries = []
        self._objectives = ""
        self._paper_path = ""
        self._paper_data = {}
        self._paper_pages = 0
        self._paper_size_mb = 0.0
        self._paper_titles = []       # manually added paper titles
        self._paper_topic_name = ""   # topic name for manual paper titles
        self._direct_papers = []      # papers resolved by exact title lookup
        self._lookup_worker = None    # _TitleLookupWorker
        self._query_gen_worker = None # _QueryGenWorker
        self._closing = False
        self._mode = config_manager.get("search_mode", "academic") if config_manager else "academic"
        self.setWindowTitle("Create New Session" if not existing else "Edit Session")
        self.setMinimumSize(960, 700)
        self._setup_ui()
        QTimer.singleShot(150, self._fetch_session_models)
        QTimer.singleShot(200, self._fetch_paper_models)
        if existing:
            self.session_name.setText(existing.get("name", ""))
            self.research_text.setPlainText(existing.get("context", ""))
            self.keywords.setText(existing.get("focus_keywords", ""))
            self.avoid_kw.setText(existing.get("avoid_topics", ""))
            self._objectives = existing.get("intent", "")
            self._search_queries = existing.get("queries", [])
            self._populate_search_table()
            self.btn_create.setText("Save Changes")
            # Restore paper analysis state
            paper_data = existing.get("paper_data", {})
            if paper_data:
                self._paper_data = paper_data
                self._paper_path = existing.get("paper_path", "")
                self._paper_pages = existing.get("paper_pages", 0)
                self._paper_size_mb = existing.get("paper_size_mb", 0.0)
                import os
                fname = os.path.basename(self._paper_path) if self._paper_path else "Paper"
                self.lbl_paper_file.setText(fname)
                self.btn_analyze.setEnabled(True)
                self._update_paper_info()
                self.paper_preview.setPlainText(self._format_paper_preview(paper_data))
                self.btn_apply_paper.setEnabled(True)
                self.lbl_paper_status.setText("Restored from saved session.")
            # Restore paper titles
            saved_titles = existing.get("paper_titles", [])
            if saved_titles:
                self._paper_titles = list(saved_titles)
                self._refresh_find_list()
            saved_topic = existing.get("paper_topic_name", "")
            if saved_topic:
                self._paper_topic_name = saved_topic
                self.find_topic_name.setText(saved_topic)
            saved_direct = existing.get("direct_papers", [])
            if saved_direct:
                self._direct_papers = list(saved_direct)
            found_map = {p.get("title", "").strip().lower(): p for p in saved_direct if p.get("title")}
            if saved_titles or saved_direct:
                self.find_list.clear()
                for title in self._paper_titles:
                    key = title.strip().lower()
                    if key in found_map:
                        p = found_map[key]
                        authors = p.get("authors", [])
                        first_author = authors[0].split()[-1] if authors else "?"
                        year = p.get("year", "")
                        self.find_list.addItem(f"✅ {p['title']} ({first_author}, {year})")
                    else:
                        self.find_list.addItem(f"⚠ {title}  [not found — will search as query]")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Session Name (outside tabs) ──
        nl = QHBoxLayout()
        nl.addWidget(QLabel("Session Name:"))
        self.session_name = QLineEdit()
        self.session_name.setPlaceholderText("e.g., electricity_price_forecasting")
        nl.addWidget(self.session_name, 1)
        layout.addLayout(nl)

        # ── Tab widget ──
        self._tab_widget = QTabWidget()
        layout.addWidget(self._tab_widget, 1)

        # Tab 1: Manual
        manual_w = QWidget()
        self._setup_manual_tab(manual_w)
        self._tab_widget.addTab(manual_w, "Manual")

        # Tab 2: Analyze My Paper (academic mode only)
        paper_w = QWidget()
        self._setup_paper_tab(paper_w)
        if self._mode == "academic":
            self._tab_widget.addTab(paper_w, "Analyze My Paper")

        # Tab 3: Find Papers
        find_w = QWidget()
        self._setup_find_papers_tab(find_w)
        self._tab_widget.addTab(find_w, "Find Papers")

        # ── Bottom buttons (outside tabs) ──
        btns = QDialogButtonBox()
        btns.addButton(QDialogButtonBox.Cancel)
        btns.rejected.connect(self.reject)
        self.btn_create = QPushButton("Create Session && Search")
        self.btn_create.setObjectName("btn_search")
        self.btn_create.clicked.connect(self.accept)
        btns.addButton(self.btn_create, QDialogButtonBox.AcceptRole)
        layout.addWidget(btns)


    def _setup_manual_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setSpacing(2)
        layout.setContentsMargins(4, 4, 4, 4)

        # ── Research Context ──
        grp_ctx = QGroupBox("What are you working on?")
        cl = QHBoxLayout(grp_ctx)
        cl.setSpacing(4)
        cl.setContentsMargins(4, 0, 4, 4)
        cl.setAlignment(Qt.AlignTop)

        self.research_text = QTextEdit()
        self.research_text.setObjectName("sc_research_area")
        self.research_text.setPlaceholderText(
            "Describe your research in two parts:\n\n"
            "1. CONTRIBUTION — what your paper proposes, your coined terms, novel framing, specific claims.\n\n"
            "2. PROBLEM SPACE — what underlying problem it addresses, in plain established field vocabulary.\n\n"
            "Enhance will structure this for you if you write it naturally."
        )
        self.research_text.setMinimumHeight(150)
        cl.addWidget(self.research_text, 5)

        btns_col = QVBoxLayout()
        btns_col.setSpacing(3)

        # ── Provider + Model + Refresh (compact single row) ──
        prov_row = QHBoxLayout()
        prov_row.setSpacing(2)
        _endpoint = self.cfg.get("llm_endpoint", "") if self.cfg else ""
        self.session_provider_combo = QComboBox()
        self.session_provider_combo.addItems(PROVIDER_NAMES)
        self.session_provider_combo.setToolTip("LLM provider")
        self.session_provider_combo.setCurrentIndex(provider_index_from_endpoint(_endpoint))
        prov_row.addWidget(self.session_provider_combo, 1)

        self.session_model_combo = QComboBox()
        self.session_model_combo.setEditable(False)
        self.session_model_combo.setToolTip("LLM model")
        _saved_model = self.cfg.get("llm_model", "") if self.cfg else ""
        if _saved_model:
            self.session_model_combo.addItem(_saved_model)
        prov_row.addWidget(self.session_model_combo, 2)

        btn_refresh_models = QPushButton()
        btn_refresh_models.setObjectName("btn_refresh_models")
        btn_refresh_models.setIcon(QIcon(icon("refresh.png")))
        btn_h = self.session_model_combo.sizeHint().height()
        btn_refresh_models.setIconSize(QSize(btn_h - 4, btn_h - 4))
        btn_refresh_models.setFixedSize(btn_h, btn_h)
        btn_refresh_models.setFlat(True)
        btn_refresh_models.setCursor(Qt.PointingHandCursor)
        btn_refresh_models.setToolTip("Fetch available models from the selected provider")
        btn_refresh_models.clicked.connect(self._fetch_session_models)
        prov_row.addWidget(btn_refresh_models, 0, Qt.AlignVCenter)
        btns_col.addLayout(prov_row)

        self.lbl_model_status = QLabel("")
        self.lbl_model_status.setObjectName("sc_model_status")
        btns_col.addWidget(self.lbl_model_status)

        self.session_provider_combo.currentIndexChanged.connect(
            lambda _: self._fetch_session_models()
        )
        self.session_model_combo.currentIndexChanged.connect(self._on_session_model_changed)

        self.btn_enhance = QPushButton("Enhance")
        self.btn_enhance.setObjectName("sc_btn_enhance")
        self.btn_enhance.setToolTip(
            "Split your description into CONTRIBUTION (your coined terms and claims) "
            "and PROBLEM SPACE (the established field vocabulary for the same problem)."
        )
        self.btn_enhance.clicked.connect(self._toggle_enhance)
        btns_col.addWidget(self.btn_enhance)

        self.keywords = QLineEdit()
        self.keywords.setPlaceholderText("LLM Focus Keywords (e.g. rPPG, codec, heart rate)")
        self.keywords.setToolTip("Keywords the LLM must anchor its query generation to.")
        btns_col.addWidget(self.keywords)

        self.avoid_kw = QLineEdit()
        self.avoid_kw.setPlaceholderText("Avoid topics (e.g. deep learning, clinical trials)")
        self.avoid_kw.setToolTip("Topics the LLM should avoid when generating queries.")
        btns_col.addWidget(self.avoid_kw)

        # Session-level default for the per-query "must contain" title filter.
        # Generated queries inherit this; each can still be toggled afterward in
        # the search query panel (double-click the query).
        self.chk_must_contain_default = QCheckBox("Filter results by 'must contain' keywords")
        self.chk_must_contain_default.setChecked(
            self.cfg.get("default_must_contain_enabled", True) if self.cfg else True)
        self.chk_must_contain_default.setToolTip(
            "Default for new queries in this session. When off, searches are not "
            "gated by must-contain keywords. Per-query overrides live in the "
            "search query panel.")
        self.chk_must_contain_default.toggled.connect(
            lambda on: self.cfg and self.cfg.set("default_must_contain_enabled", bool(on)))
        btns_col.addWidget(self.chk_must_contain_default)

        btns_col.addStretch()

        self.btn_gen_queries = QPushButton("Generate Queries")
        self.btn_gen_queries.setObjectName("sc_btn_find_queries")
        self.btn_gen_queries.setToolTip(
            "Generate structured search queries from your research description using LLM."
        )
        self.btn_gen_queries.clicked.connect(self._toggle_gen_queries)
        btns_col.addWidget(self.btn_gen_queries)

        cl.addLayout(btns_col, 1)
        layout.addWidget(grp_ctx)

        self.lbl_ctx_status = QLabel("")
        self.lbl_ctx_status.setObjectName("sc_ctx_status")
        layout.addWidget(self.lbl_ctx_status)

        # ── Direct query input (general mode) ──
        grp_direct = QGroupBox("What are you looking for?")
        gl = QVBoxLayout(grp_direct)
        gl.setSpacing(3)
        gl.setContentsMargins(4, 0, 4, 4)
        hb_dir = QHBoxLayout()
        hb_dir.setSpacing(3)
        self.direct_queries = QTextEdit()
        self.direct_queries.setPlaceholderText("Enter search terms, one per line:\nToyota RAV4 2023 service manual\nhybrid battery replacement guide\n...")
        self.direct_queries.setMaximumHeight(70)
        hb_dir.addWidget(self.direct_queries, 1)
        btn_direct_add = QPushButton("Add Queries")
        btn_direct_add.setObjectName("btn_search")
        btn_direct_add.clicked.connect(self._add_direct_queries)
        hb_dir.addWidget(btn_direct_add)
        gl.addLayout(hb_dir)
        layout.addWidget(grp_direct)

        # ── Three tables side by side ──
        qh = QHBoxLayout()
        qh.setSpacing(2)

        # Suggested Queries (left)
        self._left_panel = QWidget()
        left_box = QVBoxLayout(self._left_panel)
        left_box.setSpacing(1)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_hdr = QHBoxLayout()
        left_hdr.setContentsMargins(0, 0, 0, 0)
        left_lbl = QLabel("Suggested")
        left_lbl.setObjectName("sc_bold_label")
        left_hdr.addWidget(left_lbl)
        left_hdr.addStretch()
        btn_clear_suggested = QPushButton("Clear All")
        btn_clear_suggested.setFixedHeight(20)
        btn_clear_suggested.setObjectName("btn_clear_small")
        btn_clear_suggested.clicked.connect(self._clear_suggested)
        left_hdr.addWidget(btn_clear_suggested)
        left_box.addLayout(left_hdr)
        self.suggested_table = QTableWidget(0, 2)
        self.suggested_table.setObjectName("sc_suggested_table")
        self.suggested_table.setHorizontalHeaderLabels(["", "Name / Terms"])
        self.suggested_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.suggested_table.setColumnWidth(0, 24)
        self.suggested_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.suggested_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.suggested_table.setAlternatingRowColors(True)
        self.suggested_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.suggested_table.customContextMenuRequested.connect(self._suggested_context_menu)
        left_box.addWidget(self.suggested_table)
        btn_copy_raw = QPushButton("Copy Raw Queries")
        btn_copy_raw.setFixedHeight(22)
        btn_copy_raw.setToolTip("Copy all suggested queries as JSON to clipboard for external editing")
        btn_copy_raw.clicked.connect(self._copy_raw_queries)
        left_box.addWidget(btn_copy_raw)
        qh.addWidget(self._left_panel, 1)

        # Transfer buttons (center)
        self._transfer_panel = QWidget()
        self._transfer_panel.setFixedWidth(36)
        ctr = QVBoxLayout(self._transfer_panel)
        ctr.setSpacing(1)
        ctr.setContentsMargins(0, 0, 0, 0)
        ctr.addStretch()
        self.btn_add_selected = QPushButton(">>")
        self.btn_add_selected.setToolTip("Add selected queries to search list")
        self.btn_add_selected.setMaximumWidth(36)
        self.btn_add_selected.clicked.connect(self._add_selected_queries)
        ctr.addWidget(self.btn_add_selected)
        self.btn_add_all = QPushButton(">>")
        self.btn_add_all.setToolTip("Add all suggested queries to search list")
        self.btn_add_all.setMaximumWidth(36)
        self.btn_add_all.clicked.connect(self._add_all_queries)
        ctr.addWidget(self.btn_add_all)
        self.btn_remove_selected = QPushButton("<<")
        self.btn_remove_selected.setToolTip("Remove selected queries from search list")
        self.btn_remove_selected.setMaximumWidth(36)
        self.btn_remove_selected.clicked.connect(self._remove_selected_queries)
        ctr.addWidget(self.btn_remove_selected)
        ctr.addStretch()
        qh.addWidget(self._transfer_panel)

        # Search Queries (center)
        self._mid_panel = QWidget()
        mid_box = QVBoxLayout(self._mid_panel)
        mid_box.setSpacing(1)
        mid_box.setContentsMargins(0, 0, 0, 0)
        mid_hdr = QHBoxLayout()
        mid_hdr.setContentsMargins(0, 0, 0, 0)
        mid_lbl = QLabel("Search Queries")
        mid_lbl.setObjectName("sc_bold_label")
        mid_hdr.addWidget(mid_lbl)
        mid_hdr.addStretch()
        self.btn_add_manual = QPushButton("+ Manual")
        self.btn_add_manual.setFixedHeight(20)
        self.btn_add_manual.setFlat(True)
        self.btn_add_manual.setCursor(Qt.PointingHandCursor)
        self.btn_add_manual.setToolTip("Open full query builder to add a query manually")
        self.btn_add_manual.clicked.connect(self._add_manual_query)
        mid_hdr.addWidget(self.btn_add_manual)
        mid_box.addLayout(mid_hdr)
        self.search_table = QTableWidget(0, 2)
        self.search_table.setObjectName("sc_search_table")
        self.search_table.setHorizontalHeaderLabels(["", "Name / Terms"])
        self.search_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.search_table.setColumnWidth(0, 24)
        self.search_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.search_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.search_table.setAlternatingRowColors(True)
        self.search_table.itemSelectionChanged.connect(self._show_query_detail)
        mid_box.addWidget(self.search_table)
        qh.addWidget(self._mid_panel, 1)

        spacer = QWidget()
        spacer.setFixedWidth(36)
        qh.addWidget(spacer)

        # Query Detail (right)
        self._right_panel = QWidget()
        detail_box = QVBoxLayout(self._right_panel)
        detail_box.setSpacing(1)
        detail_box.setContentsMargins(0, 0, 0, 0)
        detail_hdr = QHBoxLayout()
        detail_hdr.setContentsMargins(0, 0, 0, 0)
        detail_lbl = QLabel("Query Detail")
        detail_lbl.setObjectName("sc_bold_label")
        detail_hdr.addWidget(detail_lbl)
        detail_hdr.addStretch()
        detail_box.addLayout(detail_hdr)
        self.detail_table = QTableWidget(0, 2)
        self.detail_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.detail_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.detail_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.detail_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.detail_table.verticalHeader().setVisible(False)
        self.detail_table.cellChanged.connect(self._on_detail_cell_changed)
        detail_box.addWidget(self.detail_table)
        qh.addWidget(self._right_panel, 1)

        layout.addLayout(qh, 1)

        if self._mode != "academic":
            grp_ctx.setVisible(False)
            self._left_panel.setVisible(False)
            self._transfer_panel.setVisible(False)
            self._mid_panel.setVisible(False)
            self._right_panel.setVisible(False)
        else:
            grp_direct.setVisible(False)

    def _setup_paper_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Compact control bar (one row) ──
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)

        ctrl.addWidget(QLabel("Provider:"))
        _ep = self.cfg.get("llm_endpoint", "") if self.cfg else ""
        self.paper_provider_combo = QComboBox()

        self.paper_provider_combo.addItems(PROVIDER_NAMES)

        self.paper_provider_combo.setCurrentIndex(provider_index_from_endpoint(_ep))
        ctrl.addWidget(self.paper_provider_combo, 2)

        ctrl.addWidget(QLabel("Model:"))
        self.paper_model_combo = QComboBox()
        self.paper_model_combo.setEditable(False)
        _m = self.cfg.get("llm_model", "") if self.cfg else ""
        if _m:
            self.paper_model_combo.addItem(_m)
        ctrl.addWidget(self.paper_model_combo, 5)

        btn_paper_refresh = QPushButton("Reload")
        btn_paper_refresh.setToolTip("Fetch available models from the selected provider")
        btn_paper_refresh.clicked.connect(self._fetch_paper_models)
        ctrl.addWidget(btn_paper_refresh)

        self.lbl_paper_llm_status = QLabel("")
        self.lbl_paper_llm_status.setObjectName("sc_paper_llm_status")
        ctrl.addWidget(self.lbl_paper_llm_status, 2)

        ctrl.addSpacing(12)

        self.btn_browse = QPushButton("Browse PDF…")
        self.btn_browse.clicked.connect(self._browse_paper)
        ctrl.addWidget(self.btn_browse)

        self.lbl_paper_file = QLabel("No file selected")
        self.lbl_paper_file.setObjectName("sc_paper_file")
        ctrl.addWidget(self.lbl_paper_file, 2)

        self.btn_analyze = QPushButton("Analyze Paper")
        self.btn_analyze.setObjectName("sc_btn_analyze")
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.clicked.connect(self._toggle_analyze)
        ctrl.addWidget(self.btn_analyze)

        self.paper_provider_combo.currentIndexChanged.connect(lambda _: self._fetch_paper_models())
        self.paper_model_combo.currentIndexChanged.connect(self._on_paper_model_changed)

        layout.addLayout(ctrl)

        info_row = QHBoxLayout()
        self.lbl_paper_info = QLabel("")
        self.lbl_paper_info.setObjectName("sc_paper_info")
        info_row.addWidget(self.lbl_paper_info)
        self.lbl_paper_status = QLabel("")
        self.lbl_paper_status.setObjectName("sc_paper_status")
        info_row.addWidget(self.lbl_paper_status, 1)
        layout.addLayout(info_row)

        # ── Focus + Preview ──
        hb_focus = QHBoxLayout()
        hb_focus.addWidget(QLabel("Focus (optional):"))
        self.paper_focus = QLineEdit()
        self.paper_focus.setPlaceholderText("e.g., focus on methodology and benchmark comparisons, ignore clinical sections")
        hb_focus.addWidget(self.paper_focus, 1)
        layout.addLayout(hb_focus)

        grp_preview = QGroupBox("Extracted Information")
        pl = QVBoxLayout(grp_preview)

        self.paper_preview = QTextEdit()
        self.paper_preview.setReadOnly(True)
        self.paper_preview.setPlaceholderText(
            "Analysis results will appear here after clicking Analyze Paper.\n\n"
            "The LLM will extract:\n"
            "  • Research description & objectives\n"
            "  • Claims & contributions\n"
            "  • Key results with exact numbers\n"
            "  • Comparisons against baselines\n"
            "  • Focus keywords\n"
            "  • Suggested search queries"
        )
        pl.addWidget(self.paper_preview)
        layout.addWidget(grp_preview, 1)

        # ── Apply button ──
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self.btn_apply_paper = QPushButton("Apply to Session →")
        self.btn_apply_paper.setObjectName("btn_search")
        self.btn_apply_paper.setEnabled(False)
        self.btn_apply_paper.setToolTip(
            "Populate the Manual tab with extracted description, keywords, objectives, and queries"
        )
        self.btn_apply_paper.clicked.connect(self._apply_paper_analysis)
        apply_row.addWidget(self.btn_apply_paper)
        layout.addLayout(apply_row)

    def _setup_find_papers_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        grp = QGroupBox("Find Specific Papers")
        gl = QVBoxLayout(grp)
        gl.setSpacing(4)
        gl.setContentsMargins(4, 0, 4, 4)

        hl1 = QHBoxLayout()
        lb_topic = QLabel("Topic Name:"); lb_topic.setObjectName("sc_bold_label")
        hl1.addWidget(lb_topic)
        self.find_topic_name = QLineEdit()
        self.find_topic_name.setPlaceholderText("e.g., My Papers")
        hl1.addWidget(self.find_topic_name, 1)
        gl.addLayout(hl1)

        hl2 = QHBoxLayout()
        lb_title = QLabel("Paper Title:"); lb_title.setObjectName("sc_bold_label")
        hl2.addWidget(lb_title)
        self.find_title_input = QLineEdit()
        self.find_title_input.setPlaceholderText("Type a paper title to search and download...")
        self.find_title_input.returnPressed.connect(self._add_find_title)
        hl2.addWidget(self.find_title_input, 1)
        btn_add = QPushButton("+ Add")
        btn_add.setObjectName("btn_search")
        btn_add.clicked.connect(self._add_find_title)
        hl2.addWidget(btn_add)
        gl.addLayout(hl2)

        self.find_list = QListWidget()
        self.find_list.setAlternatingRowColors(True)
        self.find_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        gl.addWidget(self.find_list, 1)

        rm_row = QHBoxLayout()
        btn_remove = QPushButton("× Remove Selected")
        btn_remove.clicked.connect(self._remove_find_titles)
        rm_row.addWidget(btn_remove)
        rm_row.addStretch()
        gl.addLayout(rm_row)

        layout.addWidget(grp, 1)

        apply_row = QHBoxLayout()
        self.btn_lookup = QPushButton("🔍 Lookup Papers")
        self.btn_lookup.setObjectName("btn_search")
        self.btn_lookup.setToolTip(
            "Look up each title via Semantic Scholar and arXiv.\n"
            "Found papers are added directly to search results (no query needed).\n"
            "Not-found titles fall back to quoted search queries."
        )
        self.btn_lookup.clicked.connect(self._toggle_title_lookup)
        apply_row.addWidget(self.btn_lookup)
        apply_row.addStretch()
        layout.addLayout(apply_row)

        self.lbl_lookup_status = QLabel("")
        self.lbl_lookup_status.setObjectName("sc_ctx_status")
        layout.addWidget(self.lbl_lookup_status)

        lbl_hint = QLabel(
            "Enter paper titles one by one. Each title is searched as an exact phrase.\n"
            "All papers will download into a subfolder named by the topic above."
        )
        lbl_hint.setObjectName("sc_hint")
        layout.addWidget(lbl_hint)

    def _add_find_title(self):
        title = self.find_title_input.text().strip()
        if not title:
            return
        self._paper_titles.append(title)
        self._refresh_find_list()
        self.find_title_input.clear()
        self.find_title_input.setFocus()

    def _remove_find_titles(self):
        selected = [i.row() for i in self.find_list.selectedIndexes()]
        if not selected:
            return
        for idx in sorted(selected, reverse=True):
            if idx < len(self._paper_titles):
                del self._paper_titles[idx]
        self._refresh_find_list()

    def _refresh_find_list(self):
        self.find_list.clear()
        for title in self._paper_titles:
            self.find_list.addItem(title)

    def _apply_find_to_queries(self, silent=False):
        if not self._paper_titles:
            if not silent:
                QMessageBox.warning(self, "Empty", "Add at least one paper title first.")
            return 0
        topic = self.find_topic_name.text().strip() or "My Papers"
        self._paper_topic_name = topic
        sources = self.cfg.get("default_sources", ["arxiv", "semantic_scholar", "web", "brave", "pubmed"])
        found_titles = {p.get("title", "").strip().lower() for p in self._direct_papers}
        existing_queries = {q.get("name", "").strip().lower() for q in self._search_queries}
        added = 0
        for title in self._paper_titles:
            if title.strip().lower() in found_titles:
                continue
            if title.strip().lower() in existing_queries:
                continue
            q = {"name": title, "query": f'"{title}"', "output_folder": topic,
                 "sources": list(sources), "must_contain": [], "must_not": []}
            self._search_queries.append(q)
            existing_queries.add(title.strip().lower())
            added += 1
        self._populate_search_table()
        if not silent:
            self._tab_widget.setCurrentIndex(0)
            self.lbl_ctx_status.setText(
                f"Added {added} paper title(s) to search queries under '{topic}'."
            )
        return added

    def _toggle_title_lookup(self):
        if self._lookup_worker and self._lookup_worker.isRunning():
            self._stop_lookup_worker()
        else:
            self._start_title_lookup()

    def _start_title_lookup(self):
        if not self._paper_titles:
            QMessageBox.warning(self, "Empty", "Add at least one paper title first.")
            return
        # Preserve papers already found in a previous lookup run — only drop
        # entries whose title is no longer in the current list.
        current_titles_lower = {t.strip().lower() for t in self._paper_titles}
        self._direct_papers = [
            p for p in self._direct_papers
            if p.get("title", "").strip().lower() in current_titles_lower
        ]
        already_found_lower = {p.get("title", "").strip().lower() for p in self._direct_papers}
        self.find_list.clear()
        for title in self._paper_titles:
            if title.strip().lower() in already_found_lower:
                self.find_list.addItem(f"✓ {title}  [already found]")
            else:
                self.find_list.addItem(f"🔍 {title}")

        self.btn_lookup.setText("⏹ Stop")
        self.btn_lookup.setStyleSheet("background: #c0392b; color: #fff;")
        self.btn_create.setEnabled(False)
        self.lbl_lookup_status.setText("Looking up papers...")

        creds = self.cfg.get("credentials", {}) if self.cfg else {}
        titles_to_lookup = [
            t for t in self._paper_titles
            if t.strip().lower() not in already_found_lower
        ]
        self._lookup_worker = _TitleLookupWorker(titles_to_lookup, creds)
        self._lookup_worker.progress.connect(lambda msg: self.lbl_lookup_status.setText(msg))
        self._lookup_worker.paper_found.connect(self._on_lookup_found)
        self._lookup_worker.title_not_found.connect(self._on_lookup_not_found)
        self._lookup_worker.finished.connect(self._on_lookup_finished)
        self._lookup_worker.error.connect(lambda e: self.lbl_lookup_status.setText(f"Error: {e}"))
        self._lookup_worker.start()

    def _on_lookup_found(self, index: int, paper: dict):
        self._direct_papers.append(paper)
        authors = paper.get("authors", [])
        first_author = authors[0].split()[-1] if authors else "?"
        year = paper.get("year", "")
        display = f"✅ {paper['title']} ({first_author}, {year})"
        item = self.find_list.item(index)
        if item:
            item.setText(display)

    def _on_lookup_not_found(self, index: int, title: str):
        item = self.find_list.item(index)
        if item:
            item.setText(f"⚠ {title}  [not found — will search as query]")

    def _on_lookup_finished(self):
        found = len(self._direct_papers)
        self.btn_lookup.setText("\U0001f50d Lookup Papers")
        self.btn_lookup.setStyleSheet("")
        self.btn_create.setEnabled(True)
        self._lookup_worker = None
        added = self._apply_find_to_queries(silent=True)
        msg = f"Lookup done: {found} found directly"
        if added:
            msg += f", {added} added as search queries"
        self.lbl_lookup_status.setText(msg)

    def _stop_lookup_worker(self):
        if self._lookup_worker and self._lookup_worker.isRunning():
            self._lookup_worker.stop()
            try:
                self._lookup_worker.progress.disconnect()
                self._lookup_worker.paper_found.disconnect()
                self._lookup_worker.title_not_found.disconnect()
                self._lookup_worker.finished.disconnect()
            except Exception:
                pass
            if not self._lookup_worker.wait(2000):
                self._lookup_worker.terminate()
                self._lookup_worker.wait(1000)
            self._lookup_worker = None
        self.btn_lookup.setText("🔍 Lookup Papers")
        self.btn_lookup.setStyleSheet("")
        self.btn_create.setEnabled(True)

    # ── Paper analysis handlers ──

    def _browse_paper(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Your Paper (PDF)", "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        import os
        self._paper_path = path
        self.lbl_paper_file.setText(os.path.basename(path))
        self.btn_analyze.setEnabled(True)
        self.lbl_paper_status.setText("")
        # Extract quick metadata without full text (fast)
        try:
            import pypdf
            self._paper_size_mb = os.path.getsize(path) / (1024 * 1024)
            with open(path, "rb") as f:
                reader = pypdf.PdfReader(f)
                self._paper_pages = len(reader.pages)
        except Exception:
            self._paper_pages = 0
            self._paper_size_mb = 0.0
        self._update_paper_info()

    def _update_paper_info(self):
        parts = []
        if self._paper_pages:
            parts.append(f"Pages: {self._paper_pages}")
        if self._paper_size_mb:
            parts.append(f"Size: {self._paper_size_mb:.1f} MB")
        if self._paper_data:
            n_claims = len(self._paper_data.get("claims", []))
            n_contribs = len(self._paper_data.get("contributions", []))
            n_queries = len(self._paper_data.get("suggested_queries", []))
            if n_claims:
                parts.append(f"Claims: {n_claims}")
            if n_contribs:
                parts.append(f"Contributions: {n_contribs}")
            if n_queries:
                parts.append(f"Queries: {n_queries}")
        self.lbl_paper_info.setText("   |   ".join(parts) if parts else "")

    def _analyze_paper(self):
        if not self._paper_path:
            return
        endpoint = PROVIDER_URLS.get(self.paper_provider_combo.currentIndex(), "")
        model = self.paper_model_combo.currentText().strip() or self.cfg.get("llm_model", "")
        provider_name = self.paper_provider_combo.currentText()
        api_key = get_provider_api_key(provider_name, self.cfg) if self.cfg else "not-needed"
        if not model or not endpoint:
            QMessageBox.warning(
                self, "No LLM Model Available",
                "No LLM model is configured.\n\n"
                "Set the provider and model, then click \U0001f504 to load models."
            )
            return
        try:
            ok, msg = check_provider_connection(provider_name, endpoint, api_key, model)
        except Exception:
            ok, msg = False, "Check failed."
        if not ok:
            QMessageBox.warning(
                self, "LLM Not Reachable",
                f"Cannot connect to {provider_name}.\n\n{msg}"
            )
            return
        prompt = _load_prompt(self.cfg, "analyze_own_paper_prompt", FALLBACK_ANALYZE_OWN_PAPER)
        user_context = self.cfg.get("our_work_context", "") if self.cfg else ""
        focus = self.paper_focus.text().strip()
        self.btn_analyze.setText("\u23f9 Stop")
        self.btn_analyze.setStyleSheet("background: #c0392b; color: #fff;")
        self.btn_browse.setEnabled(False)
        self.btn_apply_paper.setEnabled(False)
        self.lbl_paper_status.setText("Starting analysis...")
        self._paper_worker = _PaperAnalysisWorker(
            self._paper_path, endpoint, model, prompt, user_context, focus,
            provider_name, api_key)
        self._paper_worker.progress.connect(self.lbl_paper_status.setText)
        self._paper_worker.finished.connect(self._on_paper_analyzed)
        self._paper_worker.error.connect(self._on_paper_error)
        self._paper_worker.start()

    def _on_paper_analyzed(self, data: dict):
        self._paper_data = data
        self.lbl_paper_status.setText("Analysis complete.")
        self._restore_analyze_button()
        self.btn_apply_paper.setEnabled(True)
        self._update_paper_info()
        self.paper_preview.setPlainText(self._format_paper_preview(data))
        self._paper_worker = None

    def _on_paper_error(self, err: str):
        self.lbl_paper_status.setText(f"Error: {err}")
        self._restore_analyze_button()
        self._paper_worker = None
        QMessageBox.critical(self, "Paper Analysis Error", err)

    def _format_paper_preview(self, data: dict) -> str:
        lines = []

        def section(title, content):
            if content:
                lines.append(f"{'─' * 60}")
                lines.append(f"  {title}")
                lines.append(f"{'─' * 60}")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            lines.append("  • " + "  |  ".join(f"{k}: {v}" for k, v in item.items()))
                        else:
                            lines.append(f"  • {item}")
                else:
                    lines.append(f"  {content}")
                lines.append("")

        section("DESCRIPTION", data.get("description", ""))
        section("OBJECTIVES", data.get("objectives", ""))
        section("CLAIMS", data.get("claims", []))
        section("CONTRIBUTIONS", data.get("contributions", []))
        section("KEY RESULTS", data.get("results", []))
        section("COMPARISONS", data.get("comparisons", []))
        section("KEYWORDS", data.get("keywords", ""))
        queries = data.get("suggested_queries", [])
        if queries:
            lines.append(f"{'─' * 60}")
            lines.append(f"  SUGGESTED QUERIES ({len(queries)})")
            lines.append(f"{'─' * 60}")
            for q in queries:
                lines.append(f"  [{q.get('name', '?')}]  {q.get('query', '')}")
            lines.append("")
        return "\n".join(lines)

    def _apply_paper_analysis(self):
        data = self._paper_data
        if not data:
            return

        # Populate Manual tab fields
        if data.get("description"):
            self.research_text.setPlainText(data["description"])
        if data.get("keywords"):
            self.keywords.setText(data["keywords"])
        if data.get("objectives"):
            self._objectives = data["objectives"]

        # Add suggested queries to the suggested table
        queries = data.get("suggested_queries", [])
        fixed = []
        for i, q in enumerate(queries):
            if isinstance(q, str):
                q = {"name": f"Query {i+1}", "query": q,
                     "sources": ["arxiv", "semantic_scholar", "web"]}
            q.setdefault("must_not", [])
            q.setdefault("must_contain", [])
            q.setdefault("name", q.get("query", f"Query {i+1}"))
            fixed.append(q)
        if fixed:
            self._suggested_queries = fixed
            self._populate_suggested_table()

        # Switch to Manual tab
        self._tab_widget.setCurrentIndex(0)
        self.lbl_ctx_status.setText(
            f"Applied from paper: {len(fixed)} queries, description and keywords filled."
        )

    # ── Provider / Model helpers ──

    _PROVIDER_URLS = PROVIDER_URLS

    def _do_fetch_models(self, provider_combo: QComboBox, model_combo: QComboBox, status_label: QLabel):
        idx = provider_combo.currentIndex()
        endpoint_url = PROVIDER_URLS.get(idx, "")
        provider_name = provider_combo.currentText()
        api_key = get_provider_api_key(provider_name, self.cfg) if self.cfg else ""
        status_label.setText("Loading\u2026")

        self._model_fetch_id = getattr(self, '_model_fetch_id', 0) + 1
        fetch_id = self._model_fetch_id
        worker = _ModelFetchWorker(provider_name, endpoint_url, api_key)

        def on_models_fetched(models):
            if self._closing or getattr(self, '_model_fetch_id', 0) != fetch_id:
                return
            if models:
                saved = self.cfg.get("llm_model", "") if self.cfg else ""
                model_combo.blockSignals(True)
                model_combo.clear()
                for m in models:
                    model_combo.addItem(m)
                if saved:
                    i = model_combo.findText(saved)
                    if i >= 0:
                        model_combo.setCurrentIndex(i)
                elif models:
                    model_combo.setCurrentIndex(0)
                model_combo.blockSignals(False)
                chosen = model_combo.currentText().strip()
                if chosen and self.cfg:
                    self.cfg.set("llm_model", chosen)
                    self.cfg.set("llm_endpoint", endpoint_url)
                status_label.setText(f"{provider_name} \u2014 {len(models)} model(s)")
            else:
                status_label.setText("No models \u2014 is server running?")
            worker.deleteLater()

        worker.result.connect(on_models_fetched)
        worker.start()

    def _fetch_session_models(self):
        self._do_fetch_models(self.session_provider_combo, self.session_model_combo, self.lbl_model_status)

    def _fetch_paper_models(self):
        self._do_fetch_models(self.paper_provider_combo, self.paper_model_combo, self.lbl_paper_llm_status)

    def _on_session_model_changed(self, _index: int):
        text = self.session_model_combo.currentText()
        if not text or not self.cfg:
            return
        idx = self.session_provider_combo.currentIndex()
        self.cfg.set("llm_model", text)
        self.cfg.set("llm_endpoint", PROVIDER_URLS.get(idx, ""))
        self.cfg.set("llm_provider", self.session_provider_combo.currentText())

    def _on_paper_model_changed(self, _index: int):
        text = self.paper_model_combo.currentText()
        if not text or not self.cfg:
            return
        idx = self.paper_provider_combo.currentIndex()
        self.cfg.set("llm_model", text)
        self.cfg.set("llm_endpoint", PROVIDER_URLS.get(idx, ""))
        self.cfg.set("llm_provider", self.paper_provider_combo.currentText())

    # ── LLM calls ──

    def _toggle_enhance(self):
        if self._worker and self._worker.isRunning():
            self._call_llm("__stop__")
        else:
            self._call_llm("enhance")

    def _toggle_analyze(self):
        if self._paper_worker and self._paper_worker.isRunning():
            self._stop_paper_worker()
        else:
            self._analyze_paper()

    def _stop_llm_worker(self):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            try:
                self._worker.finished.disconnect()
                self._worker.error.disconnect()
            except Exception:
                pass
            if not self._worker.wait(2000):
                self._worker.terminate()
                self._worker.wait(1000)
            self._worker = None
        self._restore_buttons()

    def _stop_paper_worker(self):
        if self._paper_worker and self._paper_worker.isRunning():
            self._paper_worker.stop()
            try:
                self._paper_worker.progress.disconnect()
                self._paper_worker.finished.disconnect()
                self._paper_worker.error.disconnect()
            except Exception:
                pass
            if not self._paper_worker.wait(2000):
                self._paper_worker.terminate()
                self._paper_worker.wait(1000)
            self._paper_worker = None
        self._restore_analyze_button()

    def _set_running_buttons(self, action: str):
        if action == "enhance":
            self.btn_enhance.setText("⏹ Stop")
            self.btn_enhance.setStyleSheet("background: #c0392b; color: #fff;")
            self.btn_gen_queries.setEnabled(False)
        else:  # gen_queries
            self.btn_gen_queries.setText("⏹ Stop")
            self.btn_gen_queries.setStyleSheet("background: #c0392b; color: #fff;")
            self.btn_enhance.setEnabled(False)

    def _restore_buttons(self):
        self.btn_enhance.setText("Enhance")
        self.btn_enhance.setStyleSheet("")
        self.btn_gen_queries.setText("Generate Queries")
        self.btn_gen_queries.setStyleSheet("")
        self.btn_enhance.setEnabled(True)
        self.btn_gen_queries.setEnabled(True)
        self.keywords.setEnabled(True)
        self.avoid_kw.setEnabled(True)

    def _toggle_gen_queries(self):
        if self._query_gen_worker and self._query_gen_worker.isRunning():
            self._stop_query_gen_worker()
        else:
            self._start_gen_queries()

    def _start_gen_queries(self):
        research_text = self.research_text.toPlainText().strip()
        if not research_text:
            QMessageBox.warning(self, "Empty", "Write your research description first.")
            return

        if not ensure_llm_available(self.cfg, self):
            return

        idx = self.session_provider_combo.currentIndex()
        endpoint = PROVIDER_URLS.get(idx, "")
        model = self.session_model_combo.currentText().strip()
        provider_name = self.session_provider_combo.currentText()
        api_key = get_provider_api_key(provider_name, self.cfg) if self.cfg else "not-needed"

        if not model or not endpoint:
            QMessageBox.warning(
                self, "No LLM Model Available",
                "No LLM model is configured.\n\n"
                "Set the provider and model, then click Reload."
            )
            return

        prompt = _load_prompt(self.cfg, "query_generation_prompt", FALLBACK_QUERY_GENERATOR)
        self.lbl_ctx_status.setText("Generating queries...")
        self._set_running_buttons("gen_queries")

        self._query_gen_worker = _QueryGenWorker(
            endpoint, model, research_text, prompt, provider_name, api_key
        )
        self._query_gen_worker.progress.connect(lambda msg: self.lbl_ctx_status.setText(msg))
        self._query_gen_worker.finished.connect(self._on_queries_generated)
        self._query_gen_worker.error.connect(self._on_query_gen_error)
        self._query_gen_worker.start()

    def _stop_query_gen_worker(self):
        if self._query_gen_worker and self._query_gen_worker.isRunning():
            try:
                self._query_gen_worker.progress.disconnect()
                self._query_gen_worker.finished.disconnect()
                self._query_gen_worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass
            if not self._query_gen_worker.wait(2000):
                self._query_gen_worker.terminate()
                self._query_gen_worker.wait(1000)
            self._query_gen_worker = None
        self._restore_buttons()

    def _on_queries_generated(self, queries):
        fixed = []
        for i, q in enumerate(queries):
            if isinstance(q, str):
                q = {"name": f"Query {i+1}", "query": q,
                     "sources": ["arxiv", "semantic_scholar", "web", "brave", "pubmed"]}
            q.setdefault("must_not", [])
            q.setdefault("must_contain", [])
            q.setdefault("name", q.get("query", f"Query {i+1}"))
            fixed.append(q)
        self._suggested_queries = fixed
        self._populate_suggested_table()
        self.lbl_ctx_status.setText(f"Generated {len(fixed)} queries — select and transfer to Search Queries.")
        self._restore_buttons()
        self._query_gen_worker = None

    def _on_query_gen_error(self, err):
        self.lbl_ctx_status.setText(f"Error: {err}")
        self._restore_buttons()
        self._query_gen_worker = None
        QMessageBox.critical(self, "Query Generation Error", err)

    def reject(self):
        if getattr(self, '_closing_async', False):
            super().reject()
            return
        self._closing = True
        self._closing_async = True

        running = []
        for worker, attr_name, disconnect in [
            (self._lookup_worker, '_lookup_worker', self._disconnect_lookup),
            (self._worker, '_worker', self._disconnect_llm),
            (self._paper_worker, '_paper_worker', self._disconnect_paper),
            (self._query_gen_worker, '_query_gen_worker', self._disconnect_query_gen),
        ]:
            if worker and worker.isRunning():
                try:
                    if hasattr(worker, 'stop'):
                        worker.stop()
                    disconnect()
                except Exception:
                    pass
                running.append((worker, attr_name))

        if not running:
            super().reject()
            return

        import time as _time
        self._close_deadline = _time.time() + 5.0
        self._close_workers = running
        self._close_timer = QTimer()
        self._close_timer.timeout.connect(self._poll_reject)
        self._close_timer.start(100)

    def _disconnect_lookup(self):
        try:
            self._lookup_worker.progress.disconnect()
            self._lookup_worker.paper_found.disconnect()
            self._lookup_worker.title_not_found.disconnect()
            self._lookup_worker.finished.disconnect()
        except (TypeError, RuntimeError):
            pass

    def _disconnect_llm(self):
        try:
            self._worker.finished.disconnect()
            self._worker.error.disconnect()
        except (TypeError, RuntimeError):
            pass

    def _disconnect_paper(self):
        try:
            self._paper_worker.progress.disconnect()
            self._paper_worker.finished.disconnect()
            self._paper_worker.error.disconnect()
        except (TypeError, RuntimeError):
            pass

    def _disconnect_query_gen(self):
        try:
            self._query_gen_worker.progress.disconnect()
            self._query_gen_worker.finished.disconnect()
            self._query_gen_worker.error.disconnect()
        except (TypeError, RuntimeError):
            pass

    def _poll_reject(self):
        import time as _time
        still_running = []
        for worker, attr_name in self._close_workers:
            if worker.isRunning():
                still_running.append((worker, attr_name))
            else:
                setattr(self, attr_name, None)
        if not still_running or _time.time() >= self._close_deadline:
            self._close_timer.stop()
            for worker, _ in still_running:
                try:
                    worker.terminate()
                    worker.wait(1000)
                except Exception:
                    pass
            super().reject()

    def _restore_analyze_button(self):
        self.btn_analyze.setText("Analyze Paper")
        self.btn_analyze.setStyleSheet("")
        self.btn_analyze.setEnabled(True)
        self.btn_browse.setEnabled(True)

    def _call_llm(self, action):
        if action == "__stop__":
            self._stop_llm_worker()
            return
        if not ensure_llm_available(self.cfg, self):
            return
        provider_name = self.cfg.get("llm_provider", "LM Studio")
        api_key = get_provider_api_key(provider_name, self.cfg)
        endpoint = self.cfg.get("llm_endpoint", "")
        model = self.cfg.get("llm_model", "")

        if action == "enhance":
            text = self.research_text.toPlainText().strip()
            if not text:
                QMessageBox.warning(self, "Empty", "Write your research description first.")
                return
            kws = self.keywords.text().strip()
            if kws:
                text = f"{text}\n\nFOCUS KEYWORDS: {kws}"
            self.lbl_ctx_status.setText("Enhancing...")
            self._set_running_buttons("enhance")
            self.keywords.setEnabled(False)
            self.avoid_kw.setEnabled(False)
            prompt = _load_prompt(self.cfg, "enhance_research_prompt", FALLBACK_ENHANCE_RESEARCH)
            self._worker = _LLMWorker(endpoint, model, prompt, text, provider_name, api_key)
            self._worker.finished.connect(self._on_enhance_done)
            self._worker.error.connect(self._on_llm_error)
            self._worker.start()

    def _on_enhance_done(self, text):
        self.research_text.document().setPlainText(text)
        QTimer.singleShot(0, self.research_text.repaint)
        self.lbl_ctx_status.setText("Done.")
        self._restore_buttons()
        self._worker = None

    def _on_enhance_intent_done(self, text):
        self.lbl_ctx_status.setText("Done.")
        self._restore_buttons()
        self._worker = None

    def _on_queries_found(self, queries):
        fixed = []
        for i, q in enumerate(queries):
            if isinstance(q, str):
                q = {"name": f"Query {i+1}", "query": q, "sources": ["arxiv", "semantic_scholar", "web", "brave", "pubmed"]}
            q.setdefault("must_not", [])
            q.setdefault("must_contain", [])
            q.setdefault("name", q.get("query", f"Query {i+1}"))
            fixed.append(q)
        self._suggested_queries = fixed
        self._populate_suggested_table()
        self.lbl_ctx_status.setText(f"Found {len(fixed)} suggested queries.")
        self._restore_buttons()

    def _on_llm_error(self, err):
        self.lbl_ctx_status.setText(f"Error: {err}")
        self._restore_buttons()
        self._worker = None
        QMessageBox.critical(self, "LLM Error", err)

    # ── Table population ──

    def _populate_suggested_table(self):
        t = self.suggested_table
        t.setRowCount(0)
        for i, q in enumerate(self._suggested_queries):
            t.insertRow(i)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked)
            t.setItem(i, 0, chk)
            display = q.get("name", q.get("query", "?"))
            item = QTableWidgetItem(f"{display}\n{q.get('query', '')}")
            intent = q.get("intent", "")
            intent_line = f"Intent: {intent}\n" if intent else ""
            item.setToolTip(f"{intent_line}"
                           f"Must contain: {', '.join(q.get('must_contain', []))}\n"
                           f"Must NOT: {', '.join(q.get('must_not', []))}\n"
                           f"Sources: {', '.join(q.get('sources', []))}")
            t.setItem(i, 1, item)

    def _populate_search_table(self):
        t = self.search_table
        t.setRowCount(0)
        for i, q in enumerate(self._search_queries):
            t.insertRow(i)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked)
            t.setItem(i, 0, chk)
            display = q.get("name", q.get("query", "?"))
            item = QTableWidgetItem(f"{display}\n{q.get('query', '')}")
            intent = q.get("intent", "")
            intent_line = f"Intent: {intent}\n" if intent else ""
            item.setToolTip(f"{intent_line}"
                           f"Must contain: {', '.join(q.get('must_contain', []))}\n"
                           f"Must NOT: {', '.join(q.get('must_not', []))}\n"
                           f"Sources: {', '.join(q.get('sources', []))}")
            t.setItem(i, 1, item)

    # ── Transfer actions ──

    def _add_direct_queries(self):
        text = self.direct_queries.toPlainText().strip()
        if not text:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            self._search_queries.append({
                "name": line[:50],
                "query": line,
                "must_contain": [],
                "must_not": [],
                "sources": ["web"],
                "language": "en",
                "relevance_threshold": 1,
                "force_plus": False,
            })
        self._populate_search_table()
        self.direct_queries.clear()

    def _existing_query_strings(self) -> set:
        return {q.get("query", "").strip().lower() for q in self._search_queries}

    def _add_selected_queries(self):
        to_add = []
        to_keep = []
        for i, q in enumerate(self._suggested_queries):
            item = self.suggested_table.item(i, 0)
            if item and item.checkState() == Qt.Checked:
                to_add.append(q)
            else:
                to_keep.append(q)
        if not to_add:
            return
        existing = self._existing_query_strings()
        added, skipped = [], []
        for q in to_add:
            if q.get("query", "").strip().lower() in existing:
                skipped.append(q)
            else:
                added.append(q)
                existing.add(q.get("query", "").strip().lower())
        self._search_queries.extend(added)
        # put skipped back in suggested so user can see them
        self._suggested_queries = skipped + to_keep
        self._populate_suggested_table()
        self._populate_search_table()
        if skipped:
            self.lbl_ctx_status.setText(
                f"Added {len(added)}, skipped {len(skipped)} duplicate(s)."
            )

    def _add_all_queries(self):
        existing = self._existing_query_strings()
        added, skipped = [], []
        for q in self._suggested_queries:
            if q.get("query", "").strip().lower() in existing:
                skipped.append(q)
            else:
                added.append(q)
                existing.add(q.get("query", "").strip().lower())
        self._search_queries.extend(added)
        self._suggested_queries = skipped
        self._populate_suggested_table()
        self._populate_search_table()
        if skipped:
            self.lbl_ctx_status.setText(
                f"Added {len(added)}, skipped {len(skipped)} duplicate(s)."
            )

    def _remove_selected_queries(self):
        to_remove = []
        to_keep = []
        for i, q in enumerate(self._search_queries):
            item = self.search_table.item(i, 0)
            if item and item.checkState() == Qt.Checked:
                to_remove.append(q)
            else:
                to_keep.append(q)
        if not to_remove:
            return
        self._suggested_queries.extend(to_remove)
        self._search_queries = to_keep
        self._populate_suggested_table()
        self._populate_search_table()

    def _suggested_context_menu(self, pos):
        menu = QMenu()
        menu.addAction("Remove Checked", self._remove_suggested_queries)
        menu.exec(self.suggested_table.viewport().mapToGlobal(pos))

    def _remove_suggested_queries(self):
        to_keep = []
        for i, q in enumerate(self._suggested_queries):
            item = self.suggested_table.item(i, 0)
            if not (item and item.checkState() == Qt.Checked):
                to_keep.append(q)
        self._suggested_queries = to_keep
        self._populate_suggested_table()

    def _clear_suggested(self):
        self._suggested_queries.clear()
        self._populate_suggested_table()

    def _copy_raw_queries(self):
        from PySide6.QtWidgets import QApplication
        if not self._suggested_queries:
            QMessageBox.information(self, "Empty", "No suggested queries to copy.")
            return
        raw = json.dumps(self._suggested_queries, indent=2, ensure_ascii=False)
        QApplication.clipboard().setText(raw)
        self.lbl_ctx_status.setText(f"Copied {len(self._suggested_queries)} raw queries to clipboard.")

    def _add_manual_query(self):
        from gui.search_tab import QueryBuilderDialog
        dlg = QueryBuilderDialog(parent=self, config_manager=self.cfg)
        if dlg.exec() == QDialog.Accepted:
            qd = dlg.get_data()
            self._search_queries.append(qd)
            self._populate_search_table()

    def _show_query_detail(self):
        self.detail_table.blockSignals(True)
        self.detail_table.setRowCount(0)
        indexes = self.search_table.selectedIndexes()
        if not indexes:
            self.detail_table.blockSignals(False)
            return
        row = indexes[0].row()
        if row < 0 or row >= len(self._search_queries):
            self.detail_table.blockSignals(False)
            return
        self._detail_row = row
        q = self._search_queries[row]

        FIELDS = ["name", "query", "must_contain", "must_not", "sources",
                   "after_date", "max_results", "max_size_mb",
                   "relevance_threshold", "content_filter_enabled", "force_plus"]
        LABELS = ["Name", "Search Terms", "Must Contain", "Must NOT", "Sources",
                  "After Date", "Max Results", "Max PDF (MB)",
                  "Min Keywords", "Content Filter", "Force AND"]

        for i, (key, label) in enumerate(zip(FIELDS, LABELS)):
            self.detail_table.insertRow(i)
            self.detail_table.setItem(i, 0, QTableWidgetItem(label))
            val = q.get(key, "")
            if isinstance(val, list):
                val = ", ".join(val)
            elif isinstance(val, bool):
                val = "ON" if val else "OFF"
            elif val is None:
                val = ""
            item = QTableWidgetItem(str(val))
            self.detail_table.setItem(i, 1, item)
        self.detail_table.blockSignals(False)

    def _on_detail_cell_changed(self, row, col):
        if col != 1:
            return
        if not hasattr(self, '_detail_row') or self._detail_row >= len(self._search_queries):
            return
        FIELDS = ["name", "query", "must_contain", "must_not", "sources",
                   "after_date", "max_results", "max_size_mb",
                   "relevance_threshold", "content_filter_enabled", "force_plus"]
        if row >= len(FIELDS):
            return
        key = FIELDS[row]
        item = self.detail_table.item(row, 1)
        if not item:
            return
        val = item.text().strip()
        q = self._search_queries[self._detail_row]

        if key in ("name", "query", "after_date"):
            q[key] = val
        elif key in ("must_contain", "must_not", "sources"):
            q[key] = [v.strip() for v in val.split(",") if v.strip()] if val else []
        elif key == "max_results":
            try: q[key] = int(val)
            except: pass
        elif key == "max_size_mb":
            try: q[key] = float(val)
            except: pass
        elif key == "relevance_threshold":
            try: q[key] = int(val)
            except: pass
        elif key == "content_filter_enabled":
            q[key] = val.lower() in ("on", "true", "1", "yes")
        elif key == "force_plus":
            q[key] = val.lower() in ("on", "true", "1", "yes")

        r = self._detail_row
        if r < self.search_table.rowCount():
            display = q.get("name", q.get("query", "?"))
            self.search_table.item(r, 1).setText(f"{display}\n{q.get('query', '')}")
            intent = q.get("intent", "")
            intent_line = f"Intent: {intent}\n" if intent else ""
            self.search_table.item(r, 1).setToolTip(
                f"{intent_line}"
                f"Must contain: {', '.join(q.get('must_contain', []))}\n"
                f"Must NOT: {', '.join(q.get('must_not', []))}\n"
                f"Sources: {', '.join(q.get('sources', []))}")

    # ── Result ──

    def get_result(self) -> dict:
        return {
            "name": self.session_name.text().strip() or "Research Session",
            "context": self.research_text.toPlainText().strip(),
            "focus_keywords": self.keywords.text().strip(),
            "avoid_topics": self.avoid_kw.text().strip(),
            "intent": self._objectives,
            "queries": self._search_queries,
            "paper_path": self._paper_path,
            "paper_data": self._paper_data,
            "paper_pages": self._paper_pages,
            "paper_size_mb": self._paper_size_mb,
            "paper_titles": self._paper_titles,
            "paper_topic_name": self._paper_topic_name,
            "direct_papers": self._direct_papers,
        }
