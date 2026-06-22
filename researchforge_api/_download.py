import os
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
