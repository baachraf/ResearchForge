import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource


class CoreSource(DocumentSource):
    """CORE — ~290M open-access papers with full-text PDF links. Requires a free API key."""

    SEARCH_URL = "https://api.core.ac.uk/v3/search/works"

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        api_key = self.credentials.get("api_key")
        if not api_key:
            return []
        results = []
        q = query.replace("+", " ").strip()
        if after_date:
            q = f"({q}) AND yearPublished>={after_date[:4]}"
        params = {"q": q, "limit": min(max_results, 100)}
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            resp = requests.get(self.SEARCH_URL, params=params, headers=headers, timeout=20)
            resp.raise_for_status()
            for it in resp.json().get("results", []):
                title = it.get("title")
                if not title:
                    continue
                doi = it.get("doi", "") or ""
                authors = [a.get("name", "") for a in it.get("authors", [])]
                pdf_url = it.get("downloadUrl", "") or ""
                needs_resolve = not bool(pdf_url)
                if not pdf_url and doi:
                    pdf_url = f"https://doi.org/{doi}"
                    needs_resolve = True
                if not pdf_url:
                    continue
                results.append({
                    "id": str(it.get("id", "")) or doi,
                    "title": title,
                    "url": pdf_url,
                    "source": "CORE",
                    "year": it.get("yearPublished"),
                    "abstract": it.get("abstract", "") or "",
                    "authors": [a for a in authors if a],
                    "doi": doi,
                    "needs_pdf_resolve": needs_resolve,
                })
        except Exception as e:
            print(f"Error checking CORE: {e}")
        return results
