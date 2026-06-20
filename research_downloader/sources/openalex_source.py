import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource


def _abstract_from_inverted(inv: dict) -> str:
    """Reconstruct plain-text abstract from OpenAlex inverted index."""
    if not inv:
        return ""
    positions = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda p: p[0])
    return " ".join(w for _, w in positions)


class OpenAlexSource(DocumentSource):
    """OpenAlex — ~250M works, no key required (optional contact email = polite pool)."""

    SEARCH_URL = "https://api.openalex.org/works"

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        results = []
        params = {
            "search": query.replace("+", " ").strip(),
            "per-page": min(max_results, 200),
        }
        if after_date:
            params["filter"] = f"from_publication_date:{after_date}"
        email = self.credentials.get("email")
        if email:
            params["mailto"] = email

        try:
            resp = requests.get(self.SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for w in data.get("results", []):
                title = w.get("title") or w.get("display_name")
                if not title:
                    continue
                doi = (w.get("doi") or "").replace("https://doi.org/", "")
                best = w.get("best_oa_location") or {}
                pdf_url = best.get("pdf_url") or ""
                needs_resolve = False
                if not pdf_url:
                    oa = w.get("open_access") or {}
                    pdf_url = oa.get("oa_url") or ""
                    needs_resolve = bool(pdf_url)
                if not pdf_url:
                    if doi:
                        pdf_url = f"https://doi.org/{doi}"
                        needs_resolve = True
                    else:
                        continue
                authors = [
                    (a.get("author") or {}).get("display_name", "")
                    for a in w.get("authorships", [])
                ]
                results.append({
                    "id": w.get("id", ""),
                    "title": title,
                    "url": pdf_url,
                    "source": "OpenAlex",
                    "year": w.get("publication_year"),
                    "abstract": _abstract_from_inverted(w.get("abstract_inverted_index")),
                    "authors": [a for a in authors if a],
                    "doi": doi,
                    "needs_pdf_resolve": needs_resolve,
                })
        except Exception as e:
            print(f"Error checking OpenAlex: {e}")
        return results
