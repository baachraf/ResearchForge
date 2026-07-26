import json
import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource


class PatentsViewSource(DocumentSource):
    """PatentsView PatentSearch API — US granted patents with structured metadata.

    Claims live on a separate endpoint from the search result, so a full hit costs
    two calls. The claim endpoints are still beta upstream, so a claims failure
    degrades the hit to abstract-only rather than dropping it.
    """

    SEARCH_URL = "https://search.patentsview.org/api/v1/patent/"
    CLAIM_URL = "https://search.patentsview.org/api/v1/g_claim/"

    _FIELDS = [
        "patent_id", "patent_title", "patent_date", "patent_abstract",
        "assignees.assignee_organization",
        "inventors.inventor_name_first", "inventors.inventor_name_last",
        "cpc_current.cpc_group_id",
    ]

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        api_key = self.credentials.get("api_key")
        if not api_key:
            return []

        terms = query.replace("+", " ").strip()
        clauses: List[Dict[str, Any]] = [{"_text_any": {"patent_title": terms}}]
        if after_date:
            clauses.append({"_gte": {"patent_date": after_date}})
        q = clauses[0] if len(clauses) == 1 else {"_and": clauses}

        params = {
            "q": json.dumps(q),
            "f": json.dumps(self._FIELDS),
            "o": json.dumps({"size": min(max_results, 100)}),
        }
        headers = {"X-Api-Key": api_key}

        results: List[Dict[str, Any]] = []
        try:
            resp = requests.get(self.SEARCH_URL, params=params, headers=headers, timeout=25)
            resp.raise_for_status()
            for it in resp.json().get("patents", []) or []:
                title = it.get("patent_title")
                pid = str(it.get("patent_id", "") or "")
                if not title or not pid:
                    continue

                assignees = [
                    a.get("assignee_organization", "")
                    for a in (it.get("assignees") or []) if a.get("assignee_organization")
                ]
                inventors = [
                    f"{i.get('inventor_name_first','')} {i.get('inventor_name_last','')}".strip()
                    for i in (it.get("inventors") or [])
                ]
                cpc = [
                    c.get("cpc_group_id", "")
                    for c in (it.get("cpc_current") or []) if c.get("cpc_group_id")
                ]
                date = it.get("patent_date", "") or ""

                claims_text, independent = self._fetch_claims(pid, headers)

                results.append({
                    "id": f"US{pid}",
                    "title": title,
                    "url": f"https://patents.google.com/patent/US{pid}",
                    "source": "PatentsView",
                    "year": int(date[:4]) if date[:4].isdigit() else None,
                    "abstract": it.get("patent_abstract", "") or "",
                    "authors": [i for i in inventors if i],
                    "doi": "",
                    "needs_pdf_resolve": False,
                    "doc_type": "patent",
                    "patent_meta": {
                        "publication_number": f"US{pid}",
                        "kind_code": "",
                        "assignee": assignees[0] if assignees else "",
                        "assignees": assignees,
                        "inventors": [i for i in inventors if i],
                        "cpc": cpc,
                        "priority_date": date,
                        "claims_text": claims_text,
                        "independent_claims": independent,
                    },
                })
        except Exception as e:
            print(f"Error checking PatentsView: {e}")
        return results

    def _fetch_claims(self, patent_id: str, headers: Dict[str, str]) -> tuple:
        """Return (full claims text, independent claims). Beta endpoint — degrade quietly."""
        params = {
            "q": json.dumps({"_eq": {"patent_id": patent_id}}),
            "f": json.dumps(["claim_sequence", "claim_text", "claim_dependent"]),
            "o": json.dumps({"size": 100}),
        }
        try:
            resp = requests.get(self.CLAIM_URL, params=params, headers=headers, timeout=25)
            resp.raise_for_status()
            rows = resp.json().get("g_claims", []) or []
            rows.sort(key=lambda r: r.get("claim_sequence") or 0)
            all_text, independent = [], []
            for r in rows:
                text = (r.get("claim_text") or "").strip()
                if not text:
                    continue
                all_text.append(text)
                if not r.get("claim_dependent"):
                    independent.append(text)
            return "\n\n".join(all_text), "\n\n".join(independent)
        except Exception as e:
            print(f"PatentsView claims unavailable for {patent_id}: {e}")
            return "", ""
