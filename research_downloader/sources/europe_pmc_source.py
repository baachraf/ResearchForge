import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource


class EuropePmcSource(DocumentSource):
    """Europe PMC — free, no key. Biomedical + a lot of open full text."""

    SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        results = []
        params = {
            "query": query.replace("+", " ").strip(),
            "format": "json",
            "pageSize": min(max_results, 100),
            "resultType": "core",
        }
        min_year = None
        if after_date:
            try:
                min_year = int(after_date[:4])
            except ValueError:
                min_year = None

        headers = {"User-Agent": "ResearchForge/0.1 (https://github.com/baachraf/ResearchForge)",
                   "Accept": "application/json"}
        try:
            resp = requests.get(self.SEARCH_URL, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            items = (resp.json().get("resultList") or {}).get("result", [])
            for it in items:
                title = it.get("title")
                if not title:
                    continue
                year = it.get("pubYear")
                if min_year and year:
                    try:
                        if int(year) < min_year:
                            continue
                    except ValueError:
                        pass
                doi = it.get("doi", "")
                author_str = it.get("authorString", "") or ""
                authors = [a.strip() for a in author_str.rstrip(".").split(",") if a.strip()]

                pdf_url = ""
                needs_resolve = True
                ft = (it.get("fullTextUrlList") or {}).get("fullTextUrl", [])
                for f in ft:
                    if f.get("documentStyle") == "pdf" and f.get("url"):
                        pdf_url = f["url"]
                        needs_resolve = False
                        break
                if not pdf_url:
                    pmcid = it.get("pmcid")
                    if pmcid:
                        pdf_url = (f"https://www.ebi.ac.uk/europepmc/webservices/rest/"
                                   f"{pmcid}/fullTextPDF")
                        needs_resolve = False
                    elif doi:
                        pdf_url = f"https://doi.org/{doi}"
                        needs_resolve = True
                if not pdf_url:
                    continue

                results.append({
                    "id": it.get("id", "") or doi,
                    "title": title,
                    "url": pdf_url,
                    "source": "EuropePMC",
                    "year": year,
                    "abstract": it.get("abstractText", "") or "",
                    "authors": authors,
                    "doi": doi,
                    "needs_pdf_resolve": needs_resolve,
                })
        except Exception as e:
            print(f"Error checking Europe PMC: {e}")
        return results
