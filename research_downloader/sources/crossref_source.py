import re
import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource

_TAG_RE = re.compile(r"<[^>]+>")


class CrossRefSource(DocumentSource):
    """Crossref — ~150M DOI records, no key (optional contact email = polite pool)."""

    SEARCH_URL = "https://api.crossref.org/works"

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        results = []
        email = self.credentials.get("email")
        ua = f"ResearchForge/0.1 (mailto:{email})" if email else "ResearchForge/0.1"
        headers = {"User-Agent": ua}
        params = {"query": query.replace("+", " ").strip(), "rows": max_results}
        if after_date:
            params["filter"] = f"from-pub-date:{after_date}"
        if email:
            params["mailto"] = email

        try:
            resp = requests.get(self.SEARCH_URL, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            items = (resp.json().get("message") or {}).get("items", [])
            for it in items:
                titles = it.get("title") or []
                title = titles[0] if titles else ""
                if not title:
                    continue
                doi = it.get("DOI", "")
                parts = (it.get("issued") or {}).get("date-parts") or [[None]]
                year = parts[0][0] if parts and parts[0] else None
                authors = [
                    f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in it.get("author", [])
                ]
                abstract = _TAG_RE.sub("", it.get("abstract", "")).strip()

                pdf_url = ""
                needs_resolve = True
                for link in it.get("link", []):
                    if link.get("content-type") == "application/pdf" and link.get("URL"):
                        pdf_url = link["URL"]
                        needs_resolve = False
                        break
                if not pdf_url:
                    pdf_url = f"https://doi.org/{doi}" if doi else it.get("URL", "")
                    needs_resolve = True
                if not pdf_url:
                    continue

                results.append({
                    "id": doi or it.get("URL", ""),
                    "title": title,
                    "url": pdf_url,
                    "source": "Crossref",
                    "year": year,
                    "abstract": abstract,
                    "authors": [a for a in authors if a],
                    "doi": doi,
                    "needs_pdf_resolve": needs_resolve,
                })
        except Exception as e:
            print(f"Error checking Crossref: {e}")
        return results
