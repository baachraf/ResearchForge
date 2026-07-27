"""Patent download + on-disk metadata sidecars (patents only; papers untouched).

A patent doesn't come as a downloadable PDF the way a paper does — its text
arrives with the search hit and, until now, lived only in the session JSON. This
module makes a patent behave like a paper on disk: downloading one writes the
original document PDF from EPO plus its metadata beside it, so the folder — not
the session — holds the patent's substance.

Folder layout, per patent, inside the query's download folder:

    <pubnum>.pdf     the EPO original document (page images)
    <pubnum>.json    machine-readable metadata (patent_meta)
    <pubnum>.md      the same metadata, human-readable

The PDF is a reading copy only; the summary still runs on the metadata. There is
no OCR and the PDF is never parsed for text.
"""
import json
import os
import re

from researchforge_api import _config


def _pubnum(patent: dict) -> str:
    meta = patent.get("patent_meta", {}) or {}
    return str(meta.get("publication_number") or patent.get("id") or "").strip()


def _safe(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name) or "patent"


def sidecar_paths(dest_dir: str, pub: str) -> dict:
    stem = os.path.join(dest_dir, _safe(pub))
    return {"pdf": stem + ".pdf", "json": stem + ".json", "md": stem + ".md"}


def _epo_source():
    """Build an EPO adapter from stored credentials, or None if unkeyed."""
    key = _config.get("epo_ops_key", "")
    secret = _config.get("epo_ops_secret", "")
    if not (key and secret):
        return None
    from research_downloader.sources.epo_ops_source import EpoOpsSource
    return EpoOpsSource(credentials={"consumer_key": key, "consumer_secret": secret})


def _metadata_markdown(patent: dict) -> str:
    meta = patent.get("patent_meta", {}) or {}
    pub = _pubnum(patent)
    lines = [
        f"# {patent.get('title', pub)}",
        "",
        f"- **Publication:** {pub}",
        f"- **Assignee:** {meta.get('assignee', '') or '—'}",
        f"- **Inventors:** {', '.join(meta.get('inventors', []) or []) or '—'}",
        f"- **CPC:** {', '.join(meta.get('cpc', []) or []) or '—'}",
        f"- **Priority date:** {meta.get('priority_date', '') or '—'}",
        f"- **Publication date:** {meta.get('publication_date', '') or '—'}",
        f"- **Source:** {patent.get('source', '')}",
        f"- **Link:** {patent.get('url', '')}",
        "",
        "## Abstract",
        (patent.get("abstract") or "—"),
        "",
        "## Claims (text, when EPO serves it)",
        (meta.get("claims_text") or
         "CLAIMS TEXT NOT AVAILABLE via EPO OPS — see the original document PDF "
         "beside this file (claims section)."),
    ]
    return "\n".join(lines)


def write_metadata(patent: dict, dest_dir: str) -> dict:
    """Write <pubnum>.json + <pubnum>.md into dest_dir. Returns the paths."""
    os.makedirs(dest_dir, exist_ok=True)
    pub = _pubnum(patent)
    paths = sidecar_paths(dest_dir, pub)
    record = {
        "id": patent.get("id", pub),
        "publication_number": pub,
        "title": patent.get("title", ""),
        "abstract": patent.get("abstract", ""),
        "source": patent.get("source", ""),
        "url": patent.get("url", ""),
        "year": patent.get("year", ""),
        "doc_type": "patent",
        "patent_meta": patent.get("patent_meta", {}) or {},
    }
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    with open(paths["md"], "w", encoding="utf-8") as f:
        f.write(_metadata_markdown(patent))
    return paths


def load_metadata(dest_dir: str, pub: str) -> dict:
    """Read a patent's on-disk metadata record, or {} if absent."""
    path = sidecar_paths(dest_dir, pub)["json"]
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def download_patent(patent: dict, dest_dir: str, on_page=None) -> dict:
    """Download a patent into dest_dir: original PDF (EPO) + metadata sidecars.

    Always writes the metadata (so the folder holds it even when the PDF can't be
    fetched). Only EPO OPS patents get a PDF today — the sole tested provider.
    Returns {"pdf": path|"", "json": path, "md": path, "size_mb": float,
    "pdf_ok": bool}.
    """
    pub = _pubnum(patent)
    paths = write_metadata(patent, dest_dir)
    result = {"pdf": "", "json": paths["json"], "md": paths["md"],
              "size_mb": 0.0, "pdf_ok": False}

    if patent.get("source") == "EPO OPS":
        src = _epo_source()
        if src is not None:
            try:
                if src.download_original_pdf(pub, paths["pdf"], on_page=on_page):
                    result["pdf"] = paths["pdf"]
                    result["pdf_ok"] = True
                    result["size_mb"] = os.path.getsize(paths["pdf"]) / (1024 * 1024)
            except Exception as e:
                print(f"[PATENT-DL] {pub} PDF failed: {e}")
    return result
