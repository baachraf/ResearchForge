import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource


class PqaiSource(DocumentSource):
    """PQAI — semantic prior-art search over patents and technical literature.

    Takes a plain-language invention description rather than boolean field queries,
    which lines up with how a session's research context is already phrased. Token
    is free for academic/non-commercial use on request.
    """

    SEARCH_URL = "https://api.projectpq.ai/search/102"
    PATENT_URL = "https://api.projectpq.ai/patents/{pn}"

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        token = self.credentials.get("api_key")
        if not token:
            return []

        params = {
            "q": query.replace("+", " ").strip(),
            "n": min(max_results, 100),
            "token": token,
        }
        if after_date:
            params["after"] = after_date

        results: List[Dict[str, Any]] = []
        try:
            resp = requests.get(self.SEARCH_URL, params=params, timeout=30)
            resp.raise_for_status()
            for it in resp.json().get("results", []) or []:
                pn = it.get("id") or it.get("publication_number") or ""
                title = it.get("title", "") or ""
                if not pn or not title:
                    continue
                date = it.get("publication_date", "") or ""
                owner = it.get("owner", "") or it.get("assignee", "") or ""

                results.append({
                    "id": pn,
                    "title": title,
                    "url": it.get("www_link") or f"https://patents.google.com/patent/{pn}",
                    "source": "PQAI",
                    "year": int(date[:4]) if date[:4].isdigit() else None,
                    "abstract": it.get("abstract", "") or "",
                    "authors": it.get("inventors", []) or [],
                    "doi": "",
                    "needs_pdf_resolve": False,
                    "doc_type": "patent",
                    "patent_meta": {
                        "publication_number": pn,
                        "kind_code": "",
                        "assignee": owner,
                        "assignees": [owner] if owner else [],
                        "inventors": it.get("inventors", []) or [],
                        "cpc": it.get("cpcs", []) or [],
                        "priority_date": date,
                        "claims_text": self._fetch_claims(pn, token),
                        "independent_claims": "",
                        "similarity_score": it.get("score"),
                    },
                })
        except Exception as e:
            print(f"Error checking PQAI: {e}")
        return results

    def _fetch_claims(self, pub_number: str, token: str) -> str:
        try:
            resp = requests.get(
                self.PATENT_URL.format(pn=pub_number),
                params={"token": token},
                timeout=25,
            )
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            claims = resp.json().get("claims", [])
            if isinstance(claims, list):
                return "\n\n".join(str(c) for c in claims if c)
            return str(claims or "")
        except Exception as e:
            print(f"PQAI claims unavailable for {pub_number}: {e}")
            return ""
