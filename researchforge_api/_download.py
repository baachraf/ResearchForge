import os
import re
from typing import Optional
from researchforge_api import _config

from research_downloader.downloader import Downloader
from research_downloader.registry import Registry
from research_downloader.pdf_resolver import resolve_pdf_url, is_direct_pdf
from research_downloader.relevance_filter import passes_content_filter


def download_paper(
    paper: dict,
    output_dir: str = "",
    max_size_mb: float = 100.0,
    skip_content_filter: bool = False,
) -> Optional[str]:
    """Download a single paper PDF. Returns filepath or None on failure."""
    url = paper.get("url", "")
    title = paper.get("title", "Untitled")
    paper_id = paper.get("id", paper.get("url", ""))
    source = paper.get("source", paper.get("query_source", "unknown"))
    year = paper.get("year")

    if not url:
        return None

    if not output_dir:
        output_dir = _config.get("output_root", os.path.join(_config.get_app_data_dir(), "downloads"))

    os.makedirs(output_dir, exist_ok=True)
    registry = Registry(os.path.join(output_dir, "downloads_registry.db"))

    if registry.is_downloaded(paper_id):
        fpath = registry.get_filepath(paper_id)
        if fpath and os.path.exists(fpath):
            return fpath

    downloader = Downloader(registry=registry, max_size_mb=max_size_mb)
    filepath = downloader.download(
        paper_id=paper_id, title=title, url=url, source=source,
        target_dir=output_dir, year=year,
    )

    if filepath and isinstance(filepath, str):
        must_contain = paper.get("must_contain", [])
        relevance_threshold = paper.get("relevance_threshold", 2)
        if must_contain and not skip_content_filter:
            if not passes_content_filter(filepath, must_contain, relevance_threshold):
                os.remove(filepath)
                registry.remove_download(paper_id)
                return None
        return filepath
    return None


def download_papers(
    papers: list[dict],
    output_dir: str = "",
    max_size_mb: float = 100.0,
    skip_content_filter: bool = False,
    progress_callback=None,
) -> dict:
    """Download multiple papers. Returns dict with ok/failed/skipped counts and paths."""
    results = {"ok": [], "failed": [], "total": len(papers)}
    for i, paper in enumerate(papers):
        if progress_callback:
            progress_callback(f"Downloading {i+1}/{len(papers)}: {paper.get('title', 'Untitled')[:60]}")
        path = download_paper(paper, output_dir, max_size_mb, skip_content_filter)
        if path:
            results["ok"].append({"title": paper.get("title", ""), "path": path})
        else:
            results["failed"].append({"title": paper.get("title", ""), "reason": "download failed or content rejected"})
    return results


def _output_root() -> str:
    return _config.get("output_root", os.path.join(_config.get_app_data_dir(), "downloads"))


def _sanitize_session_name(name: str) -> str:
    """Same sanitisation the GUI applies to derive session_download_name
    (gui/search_tab.py)."""
    return re.sub(r'[\\/*?:"<>|]', '_', name or "")


def _topic_dir(session_name: str, output_folder: str) -> str:
    """Per-paper download folder, matching the GUI's layout exactly:
    output_root / <sanitised session name> / <output_folder (query name)> / .
    The GUI's _session_root() inserts the session-name segment, and DownloadWorker
    appends the query's output_folder; this must match so the GUI finds the files."""
    parts = [_output_root()]
    sn = _sanitize_session_name(session_name)
    if sn:
        parts.append(sn)
    if output_folder:
        parts.append(output_folder)
    return os.path.join(*parts)


def download_session(session_id: str,
                     paper_ids: Optional[list] = None,
                     max_size_mb: float = 100.0,
                     skip_content_filter: bool = False,
                     progress_callback=None) -> dict:
    """Download a session's results into the GUI folder convention and persist state.

    Each paper goes to ``output_root/<output_folder>/`` (output_folder = its query
    name), then the matching session result is updated in place with
    ``file_exists``/``file_path``/``file_size_mb`` and the session is saved — so a
    later session reload sees exactly what is downloaded and where.
    """
    from researchforge_api import _sessions
    session = _sessions.load_session(session_id)
    if not session:
        return {"error": f"Session '{session_id}' not found"}
    session = _sessions.ensure_full_schema(session)

    session_name = session.get("name", session_id)
    results = session.get("results", [])
    targets = [r for r in results
               if paper_ids is None or r.get("id") in paper_ids]
    summary = {"ok": [], "failed": [], "total": len(targets), "session_id": session_id}

    for i, r in enumerate(targets):
        if progress_callback:
            progress_callback(f"Downloading {i+1}/{len(targets)}: {r.get('title', 'Untitled')[:60]}")
        out_dir = _topic_dir(session_name, r.get("output_folder") or r.get("query_key") or "")
        path = download_paper(r, output_dir=out_dir, max_size_mb=max_size_mb,
                              skip_content_filter=skip_content_filter)
        if path:
            r["file_exists"] = True
            r["file_path"] = path
            try:
                r["file_size_mb"] = os.path.getsize(path) / (1024 ** 2)
            except OSError:
                pass
            summary["ok"].append({"id": r.get("id"), "title": r.get("title", ""), "path": path})
        else:
            r["file_exists"] = False
            summary["failed"].append({"id": r.get("id"), "title": r.get("title", "")})

    _sessions.save_session(session_id, session)
    return summary


def refresh_session_downloads(session_id: str) -> dict:
    """Recompute file_exists/file_path/file_size_mb for every session result from
    disk (mirrors the GUI's per-folder file-status refresh) and save. Lets a
    resuming agent re-sync a session with whatever is already on disk."""
    from researchforge_api import _sessions
    session = _sessions.load_session(session_id)
    if not session:
        return {"error": f"Session '{session_id}' not found"}
    session = _sessions.ensure_full_schema(session)

    session_name = session.get("name", session_id)
    found = 0
    # batch registry/listing access by topic folder, like the GUI
    by_folder: dict = {}
    for r in session.get("results", []):
        by_folder.setdefault(r.get("output_folder") or r.get("query_key") or "", []).append(r)

    for folder, rows in by_folder.items():
        target_dir = _topic_dir(session_name, folder)
        if not os.path.isdir(target_dir):
            for r in rows:
                r["file_exists"] = False
            continue
        registry = Registry(os.path.join(target_dir, "downloads_registry.db"))
        for r in rows:
            paper_id = r.get("id", r.get("url", ""))
            fpath = registry.get_filepath(paper_id) if registry.is_downloaded(paper_id) else None
            if fpath and os.path.exists(fpath):
                r["file_exists"] = True
                r["file_path"] = fpath
                try:
                    r["file_size_mb"] = os.path.getsize(fpath) / (1024 ** 2)
                except OSError:
                    pass
                found += 1
            else:
                r["file_exists"] = False

    _sessions.save_session(session_id, session)
    return {"session_id": session_id, "downloaded": found,
            "total": len(session.get("results", []))}


def is_downloaded(paper_id: str, output_dir: str = "") -> bool:
    if not output_dir:
        output_dir = _config.get("output_root", os.path.join(_config.get_app_data_dir(), "downloads"))
    if not os.path.isdir(output_dir):
        return False
    registry = Registry(os.path.join(output_dir, "downloads_registry.db"))
    return registry.is_downloaded(paper_id)


def get_download_path(paper_id: str, output_dir: str = "") -> Optional[str]:
    if not output_dir:
        output_dir = _config.get("output_root", os.path.join(_config.get_app_data_dir(), "downloads"))
    registry = Registry(os.path.join(output_dir, "downloads_registry.db"))
    return registry.get_filepath(paper_id)


def list_downloads(output_dir: str = "") -> list[dict]:
    """List all downloaded papers in the output directory."""
    if not output_dir:
        output_dir = _config.get("output_root", os.path.join(_config.get_app_data_dir(), "downloads"))
    if not os.path.isdir(output_dir):
        return []
    registry = Registry(os.path.join(output_dir, "downloads_registry.db"))
    results = []
    for root, dirs, files in os.walk(output_dir):
        for fname in files:
            if fname.lower().endswith(".pdf"):
                fpath = os.path.join(root, fname)
                results.append({"name": fname, "path": fpath, "size_mb": os.path.getsize(fpath) / (1024**2)})
    return results


def list_download_tree(output_dir: str = "") -> dict:
    """List download directory as a topic → papers tree for the Generate Reports tab."""
    if not output_dir:
        output_dir = _config.get("output_root", os.path.join(_config.get_app_data_dir(), "downloads"))
    if not os.path.isdir(output_dir):
        return {"topics": []}
    topics = []
    for entry in sorted(os.scandir(output_dir), key=lambda e: e.name):
        if entry.is_dir():
            pdfs = [f.name for f in os.scandir(entry.path) if f.is_file() and f.name.lower().endswith(".pdf")]
            if pdfs:
                topics.append({"name": entry.name, "path": entry.path, "pdfs": sorted(pdfs), "count": len(pdfs)})
    return {"topics": topics, "dir": output_dir}
