import urllib.parse
import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource


class DoajSource(DocumentSource):
    """DOAJ — Directory of Open Access Journals. Free, no key. High PDF hit rate."""

    BASE_URL = "https://doaj.org/api/search/articles/"

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        results = []
        q = urllib.parse.quote(query.replace("+", " ").strip(), safe="")
        url = self.BASE_URL + q
        params = {"pageSize": min(max_results, 100)}
        min_year = None
        if after_date:
            try:
                min_year = int(after_date[:4])
            except ValueError:
                min_year = None

        headers = {"User-Agent": "ResearchForge/0.1 (https://github.com/baachraf/ResearchForge)",
                   "Accept": "application/json"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            for it in resp.json().get("results", []):
                bj = it.get("bibjson", {})
                title = bj.get("title")
                if not title:
                    continue
                year = bj.get("year")
                if min_year and year:
                    try:
                        if int(year) < min_year:
                            continue
                    except ValueError:
                        pass
                authors = [a.get("name", "") for a in bj.get("author", [])]
                doi = ""
                for ident in bj.get("identifier", []):
                    if ident.get("type") == "doi":
                        doi = ident.get("id", "")
                        break

                pdf_url = ""
                needs_resolve = True
                for link in bj.get("link", []):
                    if link.get("type") == "fulltext" and link.get("url"):
                        pdf_url = link["url"]
                        ct = (link.get("content_type") or "").lower()
                        needs_resolve = "pdf" not in ct
                        break
                if not pdf_url and doi:
                    pdf_url = f"https://doi.org/{doi}"
                    needs_resolve = True
                if not pdf_url:
                    continue

                results.append({
                    "id": it.get("id", "") or doi,
                    "title": title,
                    "url": pdf_url,
                    "source": "DOAJ",
                    "year": year,
                    "abstract": bj.get("abstract", "") or "",
                    "authors": [a for a in authors if a],
                    "doi": doi,
                    "needs_pdf_resolve": needs_resolve,
                })
        except Exception as e:
            print(f"Error checking DOAJ: {e}")
        return results
