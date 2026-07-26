"""Session layer — delegates to gui.session_manager.SessionManager (PySide6-free).

Uses the EXACT same JSON format and directory as the GUI. The canonical schema
mirrors gui/search_tab.py::_session_save and gui/search_tab.py::_normalize_query
so sessions created/edited through the headless API load identically in the GUI.
"""
import json
import os
import re
from datetime import datetime
from typing import Optional
from gui.session_manager import SessionManager
from researchforge_api import _config

_mgr = None

# Canonical source-filter map (matches the GUI's QMenu checkable actions).
_DEFAULT_SOURCE_FILTERS = {
    "arXiv": True, "OpenAlex": True, "Crossref": True, "Europe PMC": True,
    "Sem. Scholar": True, "PubMed": True, "CORE": False, "Brave": True,
    "DuckGo": True, "Local": True,
}


def _get_mgr() -> SessionManager:
    global _mgr
    if _mgr is None:
        _mgr = SessionManager()
    return _mgr


# ── canonical schema helpers ────────────────────────────────────────────────

def _slugify(text: str, max_words: int = 6) -> str:
    """snake_case name from query text — mirrors the GUI's query naming."""
    words = re.sub(r"[^0-9A-Za-z\s]", " ", text or "").split()
    slug = "_".join(words[:max_words]).lower()
    return slug or "query"


def normalize_query(qd: dict) -> dict:
    """Fill a query dict with every field the GUI expects.

    Mirrors gui/search_tab.py::_normalize_query — most importantly it guarantees
    a ``name`` (the label the GUI shows for each search-list checkbox).
    """
    qd = dict(qd or {})
    query_text = qd.get("query", "")
    qd.setdefault("name", _slugify(query_text))
    if not qd.get("name"):
        qd["name"] = _slugify(query_text)
    qd.setdefault("intent", "discovery")
    qd.setdefault("must_contain", [])
    qd.setdefault("must_not", [])
    qd.setdefault("max_results", _config.get("default_max_results", 100))
    qd.setdefault("max_size_mb", _config.get("default_max_size_mb", 50.0))
    qd.setdefault("relevance_threshold", _config.get("default_relevance_threshold", 1))
    qd.setdefault("force_plus", _config.get("default_force_plus", False))
    qd.setdefault("after_date", _config.get("default_after_date", ""))
    qd.setdefault("output_folder", qd.get("name", ""))
    qd.setdefault("and_terms", query_text.split() if query_text else [])
    qd.setdefault("or_terms", [])
    qd.setdefault("language", "")
    src = qd.get("sources") or _config.get("default_sources", ["arxiv", "semantic_scholar", "web", "brave", "pubmed"])
    if isinstance(src, str):
        try:
            parsed = json.loads(src)
            src = parsed if isinstance(parsed, list) else None
        except (ValueError, TypeError):
            src = None
        if src is None:
            src = [t.strip() for t in str(qd.get("sources", "")).strip("[]").replace('"', "").replace("'", "").split(",") if t.strip()]
    qd["sources"] = src
    return qd


def normalize_result(r: dict, query_key: str = "") -> dict:
    """Fill a raw search hit with every field the GUI results table reads."""
    r = dict(r or {})
    qk = r.get("query_key") or query_key
    r["query_key"] = qk
    r.setdefault("output_folder", qk)
    r.setdefault("must_contain", [])
    r.setdefault("must_not", [])
    r.setdefault("relevance_threshold", 1)
    r.setdefault("filter_passed", True)
    r.setdefault("file_exists", False)
    r.setdefault("file_path", "")
    r.setdefault("title_filter_ok", True)
    r.setdefault("relevance_score", r.get("relevance_score", 0))
    r.setdefault("score_reason", "")
    r.setdefault("file_size_mb", 0.0)
    # Patents. Defaults keep every pre-existing result a "paper" with no migration.
    r.setdefault("doc_type", "paper")
    r.setdefault("patent_meta", {})
    return r


def blank_session(name: str, context: str = "", intent: str = "",
                  focus_keywords: str = "", avoid_topics: str = "") -> dict:
    """A complete, GUI-loadable session skeleton (all canonical keys present)."""
    return {
        "name": name,
        "context": context,
        "intent": intent,
        "focus_keywords": focus_keywords,
        "avoid_topics": avoid_topics,
        "queries": [],
        "results": [],
        "paper_data": {},
        "paper_path": "",
        "paper_pages": 0,
        "paper_size_mb": 0.0,
        "paper_titles": [],
        "paper_topic_name": "",
        "summ_chk_similarity": _config.get("summ_chk_similarity", True),
        "summ_chk_novelty": _config.get("summ_chk_novelty", True),
        "summ_chk_methodology": _config.get("summ_chk_methodology", False),
        "summ_chk_gaps": _config.get("summ_chk_gaps", False),
        "summ_selected_mode": _config.get("summ_selected_mode", "per_paper"),
        "score_threshold": _config.get("score_threshold", 30),
        "scoring_depth": _config.get("scoring_depth", 1),
        "title_ok_only": False,
        "title_filter_text": "",
        "title_filter_enabled": False,
        "source_filters": dict(_DEFAULT_SOURCE_FILTERS),
        "agentic_log": "",
    }


def ensure_full_schema(session: dict) -> dict:
    """Upgrade a (possibly legacy/minimal) session dict to the canonical schema.

    Migrates the old API shape (``description``/``enhanced``) to ``context`` and
    backfills any missing canonical keys without clobbering existing data.
    """
    if not isinstance(session, dict):
        return session
    # migrate legacy keys from the old create_session_full output
    if not session.get("context") and session.get("description"):
        session["context"] = session.get("description", "")
    session.pop("description", None)
    session.pop("enhanced", None)
    session.pop("topic", None)
    template = blank_session(session.get("name", "Research Session"))
    for k, v in template.items():
        if k not in session or session[k] is None:
            session[k] = v
    # normalize nested query/result dicts
    session["queries"] = [normalize_query(q) for q in session.get("queries", [])]
    first_qkey = session["queries"][0]["name"] if session["queries"] else ""
    session["results"] = [normalize_result(r, first_qkey) for r in session.get("results", [])]
    return session


# ── public surface ──────────────────────────────────────────────────────────

def list_sessions() -> list[dict]:
    return _get_mgr().list_sessions()


def load_session(session_id: str) -> Optional[dict]:
    return _get_mgr().load(session_id)


def save_session(session_id: str, data: dict) -> str:
    return _get_mgr().save(session_id, ensure_full_schema(data))


def delete_session(session_id: str):
    _get_mgr().delete(session_id)


def get_session_queries(session_id: str) -> list[dict]:
    session = load_session(session_id)
    if not session:
        return []
    return session.get("queries", [])


def add_query_to_session(session_id: str,
                          query_text: str,
                          sources: Optional[list[str]] = None,
                          must_contain: Optional[list[str]] = None,
                          must_not: Optional[list[str]] = None,
                          max_results: int = 20,
                          after_date: str = "",
                          name: str = "",
                          topic: str = "General") -> dict:
    session = load_session(session_id)
    if not session:
        return {"error": f"Session '{session_id}' not found"}
    session = ensure_full_schema(session)

    queries = session.get("queries", [])
    queries.append(normalize_query({
        "name": name or _slugify(query_text),
        "query": query_text,
        "sources": sources or _config.get("default_sources", ["arxiv", "semantic_scholar"]),
        "must_contain": must_contain or [],
        "must_not": must_not or [],
        "max_results": max_results,
        "after_date": after_date or _config.get("default_after_date", ""),
    }))
    session["queries"] = queries
    save_session(session_id, session)
    return session


def remove_query_from_session(session_id: str, query_index: int) -> Optional[dict]:
    session = load_session(session_id)
    if not session:
        return None
    session = ensure_full_schema(session)
    queries = session.get("queries", [])
    if 0 <= query_index < len(queries):
        queries.pop(query_index)
        session["queries"] = queries
        save_session(session_id, session)
    return session


# Session-level state the GUI persists in _session_save (besides queries/results,
# which have their own dedicated tools). These are the fields an agent may update
# to keep a session's working state in sync across MCP calls.
_UPDATABLE_FIELDS = {
    "name", "context", "intent", "focus_keywords", "avoid_topics",
    "score_threshold", "scoring_depth",
    "title_ok_only", "title_filter_text", "title_filter_enabled", "source_filters",
    "summ_chk_similarity", "summ_chk_novelty", "summ_chk_methodology", "summ_chk_gaps",
    "summ_selected_mode", "agentic_log",
    "paper_data", "paper_path", "paper_pages", "paper_size_mb",
    "paper_titles", "paper_topic_name",
}


def update_session(session_id: str, fields: Optional[dict] = None,
                   append_log: str = "") -> dict:
    """Update a session's working-state fields and save (GUI-loadable).

    Persists the same per-session state the GUI's _session_save writes — context,
    intent, keywords, scoring threshold/depth, title/source filters, analysis-lens
    (summ_chk_*), own-paper analysis (paper_data), etc. Only known fields are
    applied; queries/results have their own dedicated tools. ``append_log`` adds a
    timestamped line to ``agentic_log`` for a human-readable continuity trail.
    """
    session = load_session(session_id)
    if not session:
        return {"error": f"Session '{session_id}' not found"}
    session = ensure_full_schema(session)

    applied = []
    for k, v in (fields or {}).items():
        if k in _UPDATABLE_FIELDS:
            session[k] = v
            applied.append(k)

    if append_log:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        prev = session.get("agentic_log", "")
        session["agentic_log"] = (prev + "\n" if prev else "") + f"[{stamp}] {append_log}"
        if "agentic_log" not in applied:
            applied.append("agentic_log")

    save_session(session_id, session)
    return {"session_id": session_id, "updated_fields": applied, "session": session}


def set_session_results(session_id: str, results: list[dict],
                        query_key: str = "", append: bool = False) -> dict:
    """Register search results into a session in GUI-loadable form.

    ``query_key`` links each result to a query by its ``name`` (defaults to the
    session's first query). ``append=True`` adds to existing results (dedup by id).
    """
    session = load_session(session_id)
    if not session:
        return {"error": f"Session '{session_id}' not found"}
    session = ensure_full_schema(session)

    if not query_key and session.get("queries"):
        query_key = session["queries"][0]["name"]

    normalized = [normalize_result(r, query_key) for r in (results or [])]
    if append:
        seen = {r.get("id") for r in session.get("results", []) if r.get("id")}
        merged = list(session.get("results", []))
        for r in normalized:
            if not r.get("id") or r["id"] not in seen:
                merged.append(r)
                seen.add(r.get("id"))
        session["results"] = merged
    else:
        session["results"] = normalized
    save_session(session_id, session)
    return session
