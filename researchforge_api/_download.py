import os
from typing import Optional
from researchforge_api import _config
from gui import paths

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
        output_dir = paths.output_root(_config)

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
    return paths.output_root(_config)


def _sanitize_session_name(name: str) -> str:
    """Sanitise a session name for use as a folder segment. Delegates to
    ``paths.session_segment`` so the API and GUI agree exactly."""
    return paths.session_segment(name)


def _topic_dir(session_name: str, output_folder: str) -> str:
    """Per-paper download folder, matching the GUI's layout exactly:
    ``output_root / <sanitised session name> / <output_folder (query name)> /``.
    Delegates to ``paths.topic_downloads_dir`` so there is one source of truth
    for the download path contract."""
    return paths.topic_downloads_dir(_config, session_name, output_folder)


def _resolve_session_for_listing(session_id: str, output_dir: str) -> str:
    """Resolve the directory that list_downloads/list_download_tree should
    scan. With ``session_id`` it's the session's downloads root (one folder per
    query topic underneath); without it, it's the explicit ``output_dir`` or
    the global downloads root (legacy flat behaviour)."""
    if output_dir:
        return output_dir
    if session_id:
        from researchforge_api import _sessions
        s = _sessions.load_session(session_id)
        sn = (s.get("name") if s else "") or ""
        if not sn:
            return ""
        return paths.session_downloads_root(_config, sn)
    return _output_root()


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

        # Patents don't download as a PDF the way papers do — fetch the EPO
        # original document + write the metadata sidecar into the same folder.
        if r.get("doc_type") == "patent":
            from researchforge_api import _patents
            res = _patents.download_patent(r, out_dir)
            path = res.get("pdf") or res.get("json") or ""
            if path:
                r["file_exists"] = bool(res.get("pdf_ok"))
                r["file_path"] = res.get("pdf") or ""
                r["meta_path"] = res.get("json") or ""
                r["file_size_mb"] = res.get("size_mb", 0.0)
                summary["ok"].append({"id": r.get("id"), "title": r.get("title", ""),
                                      "path": path, "pdf": res.get("pdf_ok", False)})
            else:
                r["file_exists"] = False
                summary["failed"].append({"id": r.get("id"), "title": r.get("title", "")})
            continue

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
        output_dir = paths.output_root(_config)
    if not os.path.isdir(output_dir):
        return False
    registry = Registry(os.path.join(output_dir, "downloads_registry.db"))
    return registry.is_downloaded(paper_id)


def get_download_path(paper_id: str, output_dir: str = "") -> Optional[str]:
    if not output_dir:
        output_dir = paths.output_root(_config)
    registry = Registry(os.path.join(output_dir, "downloads_registry.db"))
    return registry.get_filepath(paper_id)


def list_downloads(output_dir: str = "", *, session_id: str = "") -> list[dict]:
    """List all downloaded PDFs.

    Session-aware (recommended): pass ``session_id`` to list only that
    session's PDFs — exactly what the GUI's Downloaded view shows. Without
    ``session_id`` it walks the whole ``output_root`` (legacy flat behaviour).
    """
    root = _resolve_session_for_listing(session_id, output_dir)
    if not root or not os.path.isdir(root):
        return []
    results = []
    for r, _dirs, files in os.walk(root):
        for fname in files:
            if fname.lower().endswith(".pdf"):
                fpath = os.path.join(r, fname)
                results.append({"name": fname, "path": fpath, "size_mb": os.path.getsize(fpath) / (1024**2)})
    return results


def list_download_tree(output_dir: str = "", *, session_id: str = "") -> dict:
    """List the download directory as a topic → papers tree.

    Session-aware (recommended): with ``session_id`` the tree is rooted at that
    session's downloads folder, so its top-level entries are the query topics
    (matching the GUI's Downloaded tab). Without ``session_id`` it scans the
    flat ``output_root`` — useful for a global overview, but note that with the
    GUI's session-scoped layout the top-level entries there are session names,
    not topics.
    """
    root = _resolve_session_for_listing(session_id, output_dir)
    if not root or not os.path.isdir(root):
        return {"topics": []}
    topics = []
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        if entry.is_dir():
            pdfs = [f.name for f in os.scandir(entry.path) if f.is_file() and f.name.lower().endswith(".pdf")]
            if pdfs:
                topics.append({"name": entry.name, "path": entry.path, "pdfs": sorted(pdfs), "count": len(pdfs)})
    return {"topics": topics, "dir": root}
