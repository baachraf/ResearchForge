import json
import time
import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource


class PatentsViewSource(DocumentSource):
    """PatentsView PatentSearch API — US granted patents with structured metadata.

    Call budget matters here: PatentsView allows ~45 calls/minute per key. Claims
    and citations therefore are fetched **in batches across all hits**, not once
    per patent, so a search costs a small constant number of calls (typically 3)
    rather than 1 + 2N.

    Everything after the search itself degrades quietly: the long-text and
    citation endpoints are upstream beta, and losing them must cost detail, never
    the patent.
    """

    SEARCH_URL = "https://search.patentsview.org/api/v1/patent/"
    CLAIM_URL = "https://search.patentsview.org/api/v1/g_claim/"
    PAT_CITATION_URL = "https://search.patentsview.org/api/v1/g_us_patent_citation/"
    OTHER_REF_URL = "https://search.patentsview.org/api/v1/g_other_reference/"

    # Conservative against the documented 45/min: pace calls and back off on 429.
    _MIN_INTERVAL = 1.4
    _MAX_RETRIES = 3
    _BATCH = 25          # patent_ids per batched lookup
    _PAGE = 1000         # rows per batched response

    _FIELDS = [
        "patent_id", "patent_title", "patent_date", "patent_abstract",
        "assignees.assignee_organization",
        "inventors.inventor_name_first", "inventors.inventor_name_last",
        "cpc_current.cpc_group_id",
    ]

    def __init__(self, credentials: Dict[str, Any] = None):
        super().__init__(credentials)
        self._last_call = 0.0

    # ── transport ────────────────────────────────────────────────────────────

    def _get(self, url: str, params: dict, headers: dict):
        """Paced GET with 429 backoff. Returns the response or None."""
        for attempt in range(self._MAX_RETRIES):
            gap = time.time() - self._last_call
            if gap < self._MIN_INTERVAL:
                time.sleep(self._MIN_INTERVAL - gap)
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=30)
                self._last_call = time.time()
            except Exception as e:
                print(f"PatentsView request failed: {e}")
                return None
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"PatentsView rate-limited (429) — attempt {attempt+1}/"
                      f"{self._MAX_RETRIES}, waiting {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                print(f"PatentsView HTTP {resp.status_code} for {url}")
                return None
            return resp
        print("PatentsView rate limit not cleared after retries")
        return None

    # ── search ───────────────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        api_key = self.credentials.get("api_key")
        if not api_key:
            return []
        headers = {"X-Api-Key": api_key}

        terms = query.replace("+", " ").strip()
        clauses: List[Dict[str, Any]] = [{"_text_any": {"patent_title": terms}}]
        if after_date:
            clauses.append({"_gte": {"patent_date": after_date}})
        q = clauses[0] if len(clauses) == 1 else {"_and": clauses}

        resp = self._get(self.SEARCH_URL, {
            "q": json.dumps(q),
            "f": json.dumps(self._FIELDS),
            "o": json.dumps({"size": min(max_results, 100)}),
        }, headers)
        if resp is None:
            return []

        try:
            rows = resp.json().get("patents", []) or []
        except Exception as e:
            print(f"PatentsView: unreadable search payload: {e}")
            return []

        results: List[Dict[str, Any]] = []
        for it in rows:
            parsed = self._parse_patent(it)
            if parsed:
                results.append(parsed)
        if not results:
            return []

        ids = [r["patent_meta"]["_patent_id"] for r in results]
        claims = self._fetch_claims_batched(ids, headers)
        cited = self._fetch_citations_batched(ids, headers)

        for r in results:
            pid = r["patent_meta"].pop("_patent_id")
            full, indep = claims.get(pid, ("", ""))
            r["patent_meta"]["claims_text"] = full
            r["patent_meta"]["independent_claims"] = indep
            r["patent_meta"]["cited_patents"] = cited.get(pid, {}).get("patents", [])
            r["patent_meta"]["cited_literature"] = cited.get(pid, {}).get("literature", [])
        return results

    def _parse_patent(self, it: dict) -> Optional[Dict[str, Any]]:
        title = it.get("patent_title")
        pid = str(it.get("patent_id", "") or "")
        if not title or not pid:
            return None
        assignees = [
            a.get("assignee_organization", "")
            for a in (it.get("assignees") or []) if a.get("assignee_organization")
        ]
        inventors = [
            f"{i.get('inventor_name_first','')} {i.get('inventor_name_last','')}".strip()
            for i in (it.get("inventors") or [])
        ]
        inventors = [i for i in inventors if i]
        cpc = [
            c.get("cpc_group_id", "")
            for c in (it.get("cpc_current") or []) if c.get("cpc_group_id")
        ]
        date = it.get("patent_date", "") or ""
        return {
            "id": f"US{pid}",
            "title": title,
            "url": f"https://patents.google.com/patent/US{pid}",
            "source": "PatentsView",
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "abstract": it.get("patent_abstract", "") or "",
            "authors": inventors,
            "doi": "",
            "needs_pdf_resolve": False,
            "doc_type": "patent",
            "patent_meta": {
                "_patent_id": pid,          # internal, popped before returning
                "publication_number": f"US{pid}",
                "kind_code": "",
                "assignee": assignees[0] if assignees else "",
                "assignees": assignees,
                "inventors": inventors,
                "cpc": cpc,
                "priority_date": date,
            },
        }

    # ── batched lookups ──────────────────────────────────────────────────────

    @staticmethod
    def _chunks(seq, n):
        for i in range(0, len(seq), n):
            yield seq[i:i + n]

    def _fetch_claims_batched(self, patent_ids: List[str], headers: dict) -> Dict[str, tuple]:
        """{patent_id: (all claims, independent claims)} for every id, in batches."""
        out: Dict[str, tuple] = {}
        for chunk in self._chunks(patent_ids, self._BATCH):
            resp = self._get(self.CLAIM_URL, {
                "q": json.dumps({"_in": {"patent_id": chunk}}),
                "f": json.dumps(["patent_id", "claim_sequence", "claim_text", "claim_dependent"]),
                "o": json.dumps({"size": self._PAGE}),
                "s": json.dumps([{"patent_id": "asc"}, {"claim_sequence": "asc"}]),
            }, headers)
            if resp is None:
                continue
            try:
                rows = resp.json().get("g_claims", []) or []
            except Exception as e:
                print(f"PatentsView: unreadable claims payload: {e}")
                continue
            grouped: Dict[str, list] = {}
            for r in rows:
                grouped.setdefault(str(r.get("patent_id", "")), []).append(r)
            for pid, items in grouped.items():
                items.sort(key=lambda r: r.get("claim_sequence") or 0)
                allt, indep = [], []
                for r in items:
                    txt = (r.get("claim_text") or "").strip()
                    if not txt:
                        continue
                    allt.append(txt)
                    if not r.get("claim_dependent"):
                        indep.append(txt)
                out[pid] = ("\n\n".join(allt), "\n\n".join(indep))
        return out

    def _fetch_citations_batched(self, patent_ids: List[str], headers: dict) -> Dict[str, dict]:
        """Prior art each patent cites.

        Two classes, both useful for different reasons:
          * ``patents``    — earlier patents cited (who else is in this space)
          * ``literature`` — non-patent references, i.e. actual papers the
            applicant or examiner had to disclose. These are search leads.

        Both endpoints are best-effort: a failure returns no citations for that
        batch and never affects the patents themselves.
        """
        out: Dict[str, dict] = {}

        for chunk in self._chunks(patent_ids, self._BATCH):
            resp = self._get(self.PAT_CITATION_URL, {
                "q": json.dumps({"_in": {"patent_id": chunk}}),
                "f": json.dumps(["patent_id", "citation_patent_id", "citation_date"]),
                "o": json.dumps({"size": self._PAGE}),
            }, headers)
            if resp is None:
                continue
            try:
                rows = resp.json().get("g_us_patent_citations", []) or []
            except Exception as e:
                print(f"PatentsView: unreadable patent-citation payload: {e}")
                continue
            for r in rows:
                pid = str(r.get("patent_id", ""))
                cited = r.get("citation_patent_id")
                if pid and cited:
                    out.setdefault(pid, {}).setdefault("patents", []).append(f"US{cited}")

        for chunk in self._chunks(patent_ids, self._BATCH):
            resp = self._get(self.OTHER_REF_URL, {
                "q": json.dumps({"_in": {"patent_id": chunk}}),
                "f": json.dumps(["patent_id", "otherreference_text"]),
                "o": json.dumps({"size": self._PAGE}),
            }, headers)
            if resp is None:
                continue
            try:
                rows = resp.json().get("g_other_references", []) or []
            except Exception as e:
                print(f"PatentsView: unreadable other-reference payload: {e}")
                continue
            for r in rows:
                pid = str(r.get("patent_id", ""))
                txt = (r.get("otherreference_text") or "").strip()
                if pid and txt:
                    out.setdefault(pid, {}).setdefault("literature", []).append(txt)

        return out
