import requests
import time
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource

class SemanticScholarSource(DocumentSource):
    SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    
    def search(self, query: str, max_results: int = 10, language: Optional[str] = None, after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        results = []
        
        s2_query = query
        if force_plus:
            s2_query = query.replace(' ', '+')
        else:
            # S2 needs spaces; otherwise requests url-encodes '+' into literal '%2B' failing the search
            s2_query = query.replace('+', ' ').strip()
        
        params = {
            "query": s2_query,
            "limit": max_results,
            "fields": "paperId,title,openAccessPdf,publicationLanguage,year,abstract,authors,externalIds"
        }
        
        # S2 supports filtering by year using year=YYYY-
        if after_date:
            try:
                year = after_date.split("-")[0]
                params["year"] = f"{year}-"
            except Exception:
                pass

        headers = {}
        api_key = self.credentials.get("api_key")
        if api_key:
            headers["x-api-key"] = api_key
        
        try:
            for attempt in range(3):
                if attempt > 0:
                    wait = 2.0 * (2 ** (attempt - 1))
                    time.sleep(wait)
                if not api_key:
                    time.sleep(1.5)
                response = requests.get(self.SEARCH_URL, params=params, headers=headers, timeout=15)
                if response.status_code == 429:
                    print(f"Semantic Scholar rate-limited (429) — attempt {attempt+1}/3")
                    continue
                response.raise_for_status()
                data = response.json()
                break
            else:
                raise RuntimeError("429 Too Many Requests (3 retries exhausted)")

            for item in data.get('data', []):
                if not item.get("title"):
                    continue

                # If language filtering requested, try to use publicationLanguage
                if language:
                    pub_lang = item.get("publicationLanguage")
                    if pub_lang and pub_lang.lower() != language.lower():
                        continue

                open_pdf = item.get("openAccessPdf") or {}
                pdf_url = open_pdf.get("url", "")
                needs_resolve = False

                if not pdf_url:
                    # Fall back to DOI URL or S2 paper page — resolver will find PDF
                    ext_ids = item.get("externalIds") or {}
                    doi = ext_ids.get("DOI", "")
                    paper_id = item.get("paperId", "")
                    if doi:
                        pdf_url = f"https://doi.org/{doi}"
                        needs_resolve = True
                    elif paper_id:
                        pdf_url = f"https://www.semanticscholar.org/paper/{paper_id}"
                        needs_resolve = True
                    else:
                        continue  # no URL at all — truly skip

                authors = [a.get("name", "") for a in item.get("authors", [])]
                results.append({
                    "id": item["paperId"],
                    "title": item["title"],
                    "url": pdf_url,
                    "source": "SemanticScholar",
                    "year": item.get("year"),
                    "abstract": item.get("abstract", ""),
                    "authors": authors,
                    "doi": (item.get("externalIds") or {}).get("DOI", ""),
                    "needs_pdf_resolve": needs_resolve,
                })
                
        except Exception as e:
            print(f"Error checking Semantic Scholar: {e}")

        return results

    def lookup_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Find a single paper by exact title. Returns None if no exact match found."""
        params = {
            "query": title,
            "limit": 5,
            "fields": "paperId,title,openAccessPdf,publicationLanguage,year,abstract,authors,externalIds"
        }
        headers = {}
        api_key = self.credentials.get("api_key")
        if api_key:
            headers["x-api-key"] = api_key
        else:
            time.sleep(1.5)
        try:
            for attempt in range(3):
                if attempt > 0:
                    wait = 2.0 * (2 ** (attempt - 1))
                    time.sleep(wait)
                if not api_key:
                    time.sleep(1.5)
                response = requests.get(self.SEARCH_URL, params=params, headers=headers, timeout=15)
                if response.status_code == 429:
                    print(f"[LOOKUP] S2 rate-limited (429) — attempt {attempt+1}/3")
                    continue
                response.raise_for_status()
                data = response.json()
                break
            else:
                print(f"[LOOKUP] S2 429 Too Many Requests (3 retries exhausted)")
                return None

            title_lower = title.lower()
            for item in data.get("data", []):
                if not item.get("title"):
                    continue
                if item["title"].lower() != title_lower:
                    continue
                open_pdf = item.get("openAccessPdf") or {}
                pdf_url = open_pdf.get("url", "")
                needs_resolve = False
                if not pdf_url:
                    ext_ids = item.get("externalIds") or {}
                    doi = ext_ids.get("DOI", "")
                    paper_id = item.get("paperId", "")
                    if doi:
                        pdf_url = f"https://doi.org/{doi}"
                        needs_resolve = True
                    elif paper_id:
                        pdf_url = f"https://www.semanticscholar.org/paper/{paper_id}"
                        needs_resolve = True
                    else:
                        continue
                authors = [a.get("name", "") for a in item.get("authors", [])]
                return {
                    "id": item["paperId"],
                    "title": item["title"],
                    "url": pdf_url,
                    "source": "SemanticScholar",
                    "year": item.get("year"),
                    "abstract": item.get("abstract", ""),
                    "authors": authors,
                    "doi": (item.get("externalIds") or {}).get("DOI", ""),
                    "needs_pdf_resolve": needs_resolve,
                }
        except Exception as e:
            print(f"[LOOKUP] S2 title lookup error: {e}")
        return None
