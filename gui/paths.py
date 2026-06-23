"""Canonical path resolution for ResearchForge — the single source of truth.

Both the desktop GUI (PySide6) and the headless API/MCP layer import from this
module so that downloads, summaries, and audit outputs always land where the
other layer expects to find them. Every path the application persists is built
here; no other module should construct these paths inline.

Layout (the contract both layers rely on):

  Downloads:  <output_root>/<session>/<query>/                    one registry per query
              └── downloads_registry.db                            (sqlite, per query folder)

  Summaries:  <summary_output_dir>/<session>/<model>/             per session + per model
              ├── <topic>_SUMMARY.md
              ├── GLOBAL_SUMMARY.md  ·  RELATED_WORK.md  ·  INTRODUCTION.md
              ├── _relevance_scores.json
              └── detailed_topic_reviews/<topic>/
                  ├── MASTER_REPORT.md
                  └── _cache/<pdf>.md   (+ <pdf>.skipped markers)

  Audit:      <audit_output_dir>/                                 flat (not session-scoped)
              ├── <save_name>.json   (reloadable bundle — schema researchforge.audit/1)
              └── <save_name>.md     (human-readable copy)

Segment sanitisers (see ``session_segment`` / ``model_segment``):
  * Session names strip Windows-forbidden chars but KEEP dots, so "Dr. Foo's
    Study (rPPG)" becomes "Dr. Foo_ Study (rPPG)" — readable on disk.
  * Model identifiers additionally strip dots, so "gpt-3.5-turbo" becomes
    "gpt-3_5-turbo" — model versions never produce dotted folder names, which
    confuse some tooling and shell autocompletion.

All functions are pure: they take a ``cfg``-like object (anything implementing
``.get(key, default)`` — both ``gui.config_manager.ConfigManager`` and the
API's ``researchforge_api._config`` wrapper satisfy this) and return strings.
No filesystem mutation, no Qt imports, no global state. This keeps them
trivially testable and safe to call from any thread.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

# Pre-compiled sanitiser character classes.
# The session class omits "." (titles like "Vol.1" should survive as "Vol.1").
# The model class includes "." (model versions like "1.5b" should not create
# dotted folders — "1_5b" is unambiguous and shell-safe).
_SESSION_BAD_CHARS = re.compile(r'[\\/*?:"<>|]')
_MODEL_BAD_CHARS = re.compile(r'[\\/*?:"<>|.]')


# ─── segment sanitisers ──────────────────────────────────────────────────────

def session_segment(name: str) -> str:
    """Sanitise a session name for use as a single folder-name segment.

    Strips Windows-forbidden characters (``\\ / * ? : " < > |``); KEEPS dots,
    spaces, parentheses, etc., so human-readable titles survive. Idempotent:
    passing an already-sanitised name (e.g. read back from settings) yields the
    same value.
    """
    return _SESSION_BAD_CHARS.sub("_", name or "")


def model_segment(model: str) -> str:
    """Sanitise a model identifier for use as a single folder-name segment.

    Like ``session_segment`` but ALSO strips dots. ``deepseek-chat`` stays
    ``deepseek-chat``; ``gpt-3.5-turbo`` becomes ``gpt-3_5-turbo``; empty input
    becomes the literal ``"default"`` so the layout never produces a blank
    folder segment.
    """
    seg = _MODEL_BAD_CHARS.sub("_", model or "")
    return seg if seg else "default"


# ─── cfg accessor ────────────────────────────────────────────────────────────

def _cfg_get(cfg: Any, key: str, default: str = "") -> str:
    """Read a string value from a cfg-like object; None-safe."""
    if cfg is None:
        return default
    v = cfg.get(key, default)
    return v if v is not None else default


# ─── downloads ───────────────────────────────────────────────────────────────

def output_root(cfg: Any) -> str:
    """Top-level downloads root. Falls back to ``~/.ResearchForge/downloads``."""
    base = _cfg_get(cfg, "output_root", "")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".ResearchForge", "downloads")
    return base


def session_downloads_root(cfg: Any, session_name: str) -> str:
    """``<output_root>/<sanitised session>/`` — the folder containing one
    session's per-query download subfolders. If ``session_name`` is empty,
    returns the bare ``output_root`` (legacy/unsaved-session behaviour)."""
    seg = session_segment(session_name)
    return os.path.join(output_root(cfg), seg) if seg else output_root(cfg)


def topic_downloads_dir(cfg: Any, session_name: str, output_folder: str) -> str:
    """``<output_root>/<session>/<query>/`` — one folder per query, holding its
    PDFs and its own ``downloads_registry.db``. Empty ``output_folder`` falls
    back to the session root (rare; for PDFs not bound to a query)."""
    root = session_downloads_root(cfg, session_name)
    return os.path.join(root, output_folder) if output_folder else root


def registry_db(topic_dir: str) -> str:
    """The per-query download registry. Every query folder owns its own sqlite
    db so concurrent sessions never collide."""
    return os.path.join(topic_dir, "downloads_registry.db")


# ─── summaries ───────────────────────────────────────────────────────────────

def summary_root(cfg: Any) -> str:
    """Top-level summary root. Falls back to ``~/.ResearchForge/summaries``."""
    base = _cfg_get(cfg, "summary_output_dir", "").strip()
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".ResearchForge", "summaries")
    return base


def session_summary_root(cfg: Any, session_name: str) -> str:
    """``<summary_output_dir>/<sanitised session>/`` — the Check Summaries tab
    opens here and lists each model subfolder underneath."""
    return os.path.join(summary_root(cfg), session_segment(session_name))


def model_output_root(cfg: Any, session_name: str, model: str = "") -> str:
    """``<summary>/<session>/<model>/`` — where ALL synthesis outputs for one
    session+model combination live. This is the directory the LLM worker writes
    into (``output_path`` on ``_LLMWorker``) and the API's ``synthesize_*``
    functions target when given a ``session_id``."""
    if not model:
        model = _cfg_get(cfg, "llm_model", "default")
    return os.path.join(session_summary_root(cfg, session_name), model_segment(model))


# Per-topic layout under a model root.
def topic_reviews_parent(model_root: str) -> str:
    """``<model_root>/detailed_topic_reviews/`` — the parent of all per-topic
    review folders. Use when iterating topic subfolders; for a single topic's
    folder use ``topic_reviews_dir`` instead."""
    return os.path.join(model_root, "detailed_topic_reviews")


def topic_reviews_dir(model_root: str, topic_name: str) -> str:
    """``<model_root>/detailed_topic_reviews/<topic>/`` — one subfolder per
    query topic, holding that topic's MASTER_REPORT and per-paper cache."""
    return os.path.join(topic_reviews_parent(model_root), topic_name)


def topic_cache_dir(model_root: str, topic_name: str) -> str:
    """``<model_root>/detailed_topic_reviews/<topic>/_cache/`` — per-paper
    analysis cache (``<pdf>.md`` for done, ``<pdf>.skipped`` for rejected)."""
    return os.path.join(topic_reviews_dir(model_root, topic_name), "_cache")


def topic_master_report(model_root: str, topic_name: str) -> str:
    return os.path.join(topic_reviews_dir(model_root, topic_name), "MASTER_REPORT.md")


def topic_summary_file(model_root: str, topic_name: str) -> str:
    """``<model_root>/<topic>_SUMMARY.md`` — the topic-level synthesis, sitting
    at the model root (NOT inside ``detailed_topic_reviews/``) so global
    synthesis can enumerate it via a single glob."""
    return os.path.join(model_root, f"{topic_name}_SUMMARY.md")


# Top-level synthesis artefacts at the model root.
def global_summary_file(model_root: str) -> str:
    return os.path.join(model_root, "GLOBAL_SUMMARY.md")


def related_work_file(model_root: str) -> str:
    return os.path.join(model_root, "RELATED_WORK.md")


def introduction_file(model_root: str) -> str:
    return os.path.join(model_root, "INTRODUCTION.md")


def relevance_scores_file(model_root: str) -> str:
    """``<model_root>/_relevance_scores.json`` — map of ``<pdf> -> score``,
    written incrementally by the per-paper worker."""
    return os.path.join(model_root, "_relevance_scores.json")


# ─── audit ───────────────────────────────────────────────────────────────────

def audit_root(cfg: Any) -> str:
    """Audit output root. Flat (NOT session-scoped) by design — audits are
    standalone documents. Falls back to ``~/.ResearchForge/audit_results``."""
    d = _cfg_get(cfg, "audit_output_dir", "").strip()
    return d if d else os.path.join(os.path.expanduser("~"), ".ResearchForge", "audit_results")


# ─── audit filename helpers (shared by GUI save dialog and API) ──────────────
# Moved here so the headless API produces filenames identical to the GUI's,
# letting API-saved bundles appear in the GUI's "Load Audit" dialog.

def title_to_slug(title: str, max_words: int = 6, min_words: int = 4,
                  max_chars: int = 55) -> str:
    """Convert a paper title into an underscore-separated filename slug.

    Keeps at least ``min_words`` words even if they exceed ``max_chars``; after
    that threshold, stops at ``max_chars``. Drops words of length <= 1.
    Mirrors the GUI's historical ``_title_to_slug`` exactly so existing saved
    files remain consistent with new ones.
    """
    cleaned = re.sub(r"[^\w\s-]", " ", title or "", flags=re.UNICODE)
    words = [w for w in cleaned.split() if len(w) > 1]
    slug = ""
    for i, word in enumerate(words[:max_words]):
        candidate = (slug + "_" + word) if slug else word
        if i >= min_words and len(candidate) > max_chars:
            break
        slug = candidate
    return slug


def audit_filename(title: str, source_type: str = "pdf", now: datetime | None = None) -> str:
    """Build the default audit save name:

    ``audit_report_<title_slug>_<pdf|latex>_<DayName>_<DD>_<Mon>_<YYYY>_<HHMMSS>``

    Example: ``audit_report_Spatial_Artifact_Coherence_latex_Saturday_30_May_2026_071140``

    Pass ``now`` for deterministic output in tests.
    """
    ts = (now or datetime.now()).strftime("%A_%d_%b_%Y_%H%M%S")
    slug = title_to_slug(title) if title else ""
    parts = ["audit_report"]
    if slug:
        parts.append(slug)
    parts.append(source_type)
    parts.append(ts)
    return "_".join(parts)


__all__ = [
    # sanitisers
    "session_segment", "model_segment",
    # downloads
    "output_root", "session_downloads_root", "topic_downloads_dir", "registry_db",
    # summaries
    "summary_root", "session_summary_root", "model_output_root",
    "topic_reviews_dir", "topic_reviews_parent", "topic_cache_dir", "topic_master_report",
    "topic_summary_file", "global_summary_file", "related_work_file",
    "introduction_file", "relevance_scores_file",
    # audit
    "audit_root", "title_to_slug", "audit_filename",
]
