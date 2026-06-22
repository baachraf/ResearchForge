import re
import time
from datetime import datetime
from typing import Optional
from researchforge_api import _config

from research_downloader.sources.arxiv_source import ArxivSource
from research_downloader.sources.semantic_scholar_source import SemanticScholarSource
from research_downloader.sources.web_source import WebSource
from research_downloader.sources.brave_source import BraveSource
from research_downloader.sources.pubmed_source import PubMedSource
from research_downloader.sources.openalex_source import OpenAlexSource
from research_downloader.sources.crossref_source import CrossRefSource
from research_downloader.sources.europe_pmc_source import EuropePmcSource
from research_downloader.sources.core_source import CoreSource
from gui.discovery import discover as _discover_endpoints
from difflib import SequenceMatcher


def _clean_query(query: str) -> str:
    cleaned = re.sub(r'[()]', ' ', query)
    cleaned = re.sub(r'\b(AND|OR|NOT)\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _sanitize_date(date_str: str) -> Optional[str]:
    if not date_str or not date_str.strip():
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if fmt == "%Y-%m-%d":
                return dt.strftime("%Y-%m-%d")
            elif fmt == "%Y-%m":
                return dt.strftime("%Y-%m-01")
            else:
                return dt.strftime("%Y-01-01")
        except ValueError:
            continue
    return None


def search_papers(
    query: str,
    sources: Optional[list[str]] = None,
    max_results: int = 20,
    after_date: str = "",
    must_contain: Optional[list[str]] = None,
    force_plus: bool = False,
    search_mode: str = "academic",
    progress_callback=None,
) -> dict:
    """Search across configured sources. Returns dict with 'results' and per-source stats."""
    if sources is None:
        sources = _config.get_list("default_sources", ["arxiv", "semantic_scholar", "web", "brave", "pubmed"])
    elif isinstance(sources, str):
        # An agent may pass sources as a string instead of a JSON array.
        sources = [s.strip() for s in sources.strip("[]").replace('"', "").replace("'", "").split(",") if s.strip()]

    search_term = _clean_query(query)
    if must_contain:
        missing = [kw for kw in must_contain if kw.lower() not in search_term.lower()]
        if missing:
            search_term = search_term + " " + " ".join(missing)

    after_date = _sanitize_date(after_date)

    available_sources = {"web": WebSource()}

    brave_key = _config.get("brave_api_key", "")
    if brave_key:
        available_sources["brave"] = BraveSource(credentials={"api_key": brave_key, "enabled": True})
    available_sources["arxiv"] = ArxivSource()
    available_sources["semantic_scholar"] = SemanticScholarSource(
        credentials={"api_key": _config.get("semantic_scholar_api_key", "")})
    pubmed_key = _config.get("pubmed_api_key", "")
    available_sources["pubmed"] = PubMedSource(credentials={"api_key": pubmed_key, "enabled": True})

    email = _config.get("contact_email", "")
    available_sources["openalex"] = OpenAlexSource(credentials={"email": email})
    available_sources["crossref"] = CrossRefSource(credentials={"email": email})
    available_sources["europe_pmc"] = EuropePmcSource(credentials={"email": email})
    core_key = _config.get("core_api_key", "")
    if core_key:
        available_sources["core"] = CoreSource(credentials={"api_key": core_key})

    all_results = []
    source_stats = {}

    for source_name, source_obj in available_sources.items():
        if source_name not in sources:
            continue
        if progress_callback:
            progress_callback(f"Searching {source_name}...")
        try:
            kwargs = dict(query=search_term, max_results=max_results, after_date=after_date,
                          force_plus=force_plus)
            if source_name == "web":
                kwargs["mode"] = search_mode
            results = source_obj.search(**kwargs)
            for r in results:
                r["query_source"] = source_name
            all_results.extend(results)
            source_stats[source_name] = len(results)
        except Exception as e:
            source_stats[source_name] = f"error: {e}"

        if source_name == "arxiv":
            time.sleep(3.0)
        elif source_name == "semantic_scholar":
            time.sleep(1.5)
        elif source_name in ("openalex", "crossref", "europe_pmc", "core"):
            time.sleep(0.4)

    return {"results": all_results, "sources": source_stats, "total": len(all_results)}


def discover_endpoints(timeout: float = 3.0) -> list[dict]:
    """Scan localhost for running LM Studio and Ollama endpoints.
    Returns list of dicts with base_url, provider, models, v1_endpoint."""
    return _discover_endpoints(timeout)


def lookup_by_title(
    titles: list[str],
    sources: Optional[list[str]] = None,
    progress_callback=None,
) -> dict:
    """Look up papers by exact/fuzzy title across sources.
    Returns dict with found papers and not_found titles."""
    if sources is None:
        sources = ["arxiv", "brave", "web"]

    found = {}
    not_found = []

    available = {}
    available["arxiv"] = ArxivSource()
    brave_key = _config.get("brave_api_key", "")
    if brave_key:
        available["brave"] = BraveSource(credentials={"api_key": brave_key, "enabled": True})
    available["web"] = WebSource()

    for title in titles:
        matched = None
        for sn in sources:
            if sn not in available:
                continue
            src = available[sn]
            try:
                if sn == "arxiv":
                    results = src.search(title, max_results=5)
                    for r in results:
                        if r.get("title") and SequenceMatcher(None, r["title"].lower(), title.lower()).ratio() >= 0.85:
                            r["query_source"] = sn
                            matched = r
                            break
                else:
                    results = src.search(title, max_results=5)
                    for r in results:
                        rt = r.get("title", "")
                        if rt and SequenceMatcher(None, rt.lower(), title.lower()).ratio() >= 0.85:
                            r["query_source"] = sn
                            matched = r
                            break
            except Exception:
                continue
            if matched:
                break
        if matched:
            found[title] = matched
        else:
            not_found.append(title)

        if progress_callback:
            progress_callback(f"Looked up: {title[:60]}")

    return {
        "found": list(found.values()),
        "not_found": not_found,
        "total": len(titles),
        "matched": len(found),
    }


def filter_papers(
    papers: list[dict],
    title_filter: str = "",
    score_threshold: int = 0,
    must_contain: Optional[list[str]] = None,
    title_ok_only: bool = False,
) -> list[dict]:
    """Filter a list of paper results by title keyword, score, and content.
    Mimics the filter logic from the Search & Download tab."""
    filtered = []
    for p in papers:
        title = p.get("title", "")
        score = p.get("relevance_score", -1)

        if title_filter and title_filter.lower() not in title.lower():
            continue

        if score_threshold > 0 and 0 <= score < score_threshold:
            continue

        if title_ok_only:
            match_val = p.get("match", p.get("title_match", ""))
            if match_val != "OK":
                continue

        if must_contain:
            if not all(kw.lower() in title.lower() for kw in must_contain):
                continue

        filtered.append(p)
    return filtered
