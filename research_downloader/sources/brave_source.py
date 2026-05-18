import re
import hashlib
import requests
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource
from ..relevance_filter import extract_year
from ..pdf_resolver import ACADEMIC_DOMAINS, is_direct_pdf


def _is_academic_domain(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return any(domain == d or domain.endswith("." + d) for d in ACADEMIC_DOMAINS)
    except Exception:
        return False


class BraveSource(DocumentSource):
    SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, credentials: Dict[str, Any] = None):
        super().__init__(credentials)
        self.api_key = self.credentials.get("api_key", "")

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None, after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        results = []

        if not self.api_key:
            print("Warning: No Brave API key provided. Skipping Brave Search.")
            return results

        # Build query with filetype hint
        brave_query = query.replace(' ', '+') if force_plus else query
        brave_query = f"{brave_query} filetype:pdf"

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key
        }

        params = {
            "q": brave_query,
            "count": min(max_results * 2, 20),  # Brave max is 20 per request
            "search_lang": language if language else "en",
            "result_filter": "web"
        }

        # Add freshness for date filtering
        if after_date:
            try:
                year = after_date.split("-")[0]
                params["freshness"] = f"p{2025 - int(year)}y"  # e.g., past 5 years
            except Exception:
                pass

        try:
            response = requests.get(self.SEARCH_URL, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            for item in data.get("web", {}).get("results", []):
                href = item.get("url", "")
                title = item.get("title", "Untitled")

                if not href:
                    continue

                is_pdf = is_direct_pdf(href)
                is_academic = _is_academic_domain(href)

                # Accept direct PDFs and academic landing pages; skip everything else
                if not is_pdf and not is_academic:
                    continue

                paper_id = hashlib.md5(href.encode("utf-8")).hexdigest()[:12]

                year = extract_year([href, title, item.get("extra_snippets", [])])

                results.append({
                    "id": f"brave_{paper_id}",
                    "title": title,
                    "url": href,
                    "source": "Brave",
                    "year": year,
                    "abstract": item.get("description", ""),
                    "authors": [],
                    "needs_pdf_resolve": not is_pdf,
                })

                if len(results) >= max_results:
                    break

        except Exception as e:
            print(f"Error checking Brave Search: {e}")

        return results
