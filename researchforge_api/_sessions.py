"""Session layer — delegates to gui.session_manager.SessionManager (PySide6-free).

Uses the EXACT same JSON format and directory as the GUI.
"""
import json
import os
from datetime import datetime
from typing import Optional
from gui.session_manager import SessionManager
from researchforge_api import _config

_mgr = None


def _get_mgr() -> SessionManager:
    global _mgr
    if _mgr is None:
        _mgr = SessionManager()
    return _mgr


def list_sessions() -> list[dict]:
    return _get_mgr().list_sessions()


def load_session(session_id: str) -> Optional[dict]:
    return _get_mgr().load(session_id)


def save_session(session_id: str, data: dict) -> str:
    return _get_mgr().save(session_id, data)


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
                          topic: str = "General") -> dict:
    session = load_session(session_id)
    if not session:
        return {"error": f"Session '{session_id}' not found"}

    queries = session.get("queries", [])
    queries.append({
        "query": query_text,
        "sources": sources or _config.get("default_sources", ["arxiv", "semantic_scholar"]),
        "must_contain": must_contain or [],
        "must_not": must_not or [],
        "max_results": max_results,
        "after_date": after_date or _config.get("default_after_date", ""),
        "topic": topic,
        "language": "",
    })
    session["queries"] = queries
    save_session(session_id, session)
    return session


def remove_query_from_session(session_id: str, query_index: int) -> Optional[dict]:
    session = load_session(session_id)
    if not session:
        return None
    queries = session.get("queries", [])
    if 0 <= query_index < len(queries):
        queries.pop(query_index)
        session["queries"] = queries
        save_session(session_id, session)
    return session


def set_session_results(session_id: str, results: list[dict]) -> dict:
    session = load_session(session_id)
    if not session:
        return {"error": f"Session '{session_id}' not found"}
    session["results"] = results
    save_session(session_id, session)
    return session
