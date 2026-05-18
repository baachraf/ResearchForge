import hashlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from ddgs import DDGS
from .base_source import DocumentSource
from ..relevance_filter import extract_year
from ..pdf_resolver import ACADEMIC_DOMAINS, is_direct_pdf


def _is_academic_domain(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return any(domain == d or domain.endswith("." + d) for d in ACADEMIC_DOMAINS)
    except Exception:
        return False

NON_ACADEMIC_DOMAINS = {
    "dealereprocess.org", "cdn.dealereprocess.org",
    "ownersmanuals.com", "manualsnet.com", "manualslib.com",
    "servicemanuals.net", "carmanual.org", "carmanualsonline.info",
    "carmanual.com", "allcarmanuals.com", "justgivemethedamnmanual.com",
    "manualzz.com", "manualsonline.com", "usermanual.wiki",
    "fccid.io", "fcc.gov",
    "cdn.shopify.com", "static.shopify.com",
    "bedienungsanleitu.ng",
}
OBJECT_KEYWORDS = {
    "manual", "guide", "catalog", "brochure", "specification sheet",
    "spec sheet", "datasheet", "owners manual", "user manual",
    "service manual", "repair manual", "installation manual",
    "quick start", "quick guide", "reference guide",
    "parts list", "assembly instructions", "operator manual",
    "maintenance manual", "troubleshooting guide",
    "product manual", "instruction manual",
}


def _ddg_search(query, max_results):
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append(r)
    return results


class WebSource(DocumentSource):
    def __init__(self, credentials: Dict[str, Any] = None):
        super().__init__(credentials)

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False,
               mode: str = "academic") -> List[Dict[str, Any]]:
        results = []

        ddg_query_base = query.replace(' ', '+') if force_plus else query
        ddg_query = f"{ddg_query_base} filetype:pdf"

        try:
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(_ddg_search, ddg_query, max_results * 2)
            try:
                ddgs_results = future.result(timeout=20)
            except FutureTimeout:
                print("Web search timed out after 20s")
                executor.shutdown(wait=False)
                return results
            executor.shutdown(wait=False)

            for r in ddgs_results:
                href = r.get("href", "")
                title = r.get("title", "Untitled Web Document")

                if not href:
                    continue

                domain = urlparse(href).netloc.lower().lstrip("www.")

                # Always skip non-academic manual/product domains
                if domain in NON_ACADEMIC_DOMAINS:
                    continue

                is_pdf = is_direct_pdf(href)
                is_academic = _is_academic_domain(href)

                if mode == "academic":
                    # In academic mode: accept direct PDFs or academic landing pages;
                    # reject obvious non-academic titles
                    if not is_pdf and not is_academic:
                        continue
                    title_lower = title.lower()
                    if any(kw in title_lower for kw in OBJECT_KEYWORDS):
                        continue
                else:
                    # In general mode: accept direct PDFs only (web sources)
                    if not is_pdf:
                        continue

                year = extract_year([href, title, r.get("body", "")])
                url_hash = hashlib.md5(href.encode()).hexdigest()[:12]
                results.append({
                    "id": f"web_{url_hash}",
                    "title": title,
                    "url": href,
                    "source": "DuckDuckGo",
                    "year": year,
                    "abstract": r.get("body", "")[:500],
                    "authors": [],
                    "needs_pdf_resolve": not is_pdf,
                })

                if len(results) >= max_results:
                    break

        except Exception as e:
            print(f"Error checking Web Search (DuckDuckGo): {e}")

        return results
