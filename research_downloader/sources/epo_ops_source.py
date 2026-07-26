import time
import requests
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource


class EpoOpsSource(DocumentSource):
    """EPO Open Patent Services — worldwide patent bibliographic data.

    OAuth2 client-credentials: a consumer key + secret are exchanged for a
    short-lived bearer token, cached on the instance until expiry.

    Coverage asymmetry to be aware of: bibliographic data is worldwide, but OPS
    full text (and therefore claims) is mainly EP and WO documents. Non-EP/WO hits
    come back as metadata only, which is expected, not an error.
    """

    AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
    SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"
    CLAIMS_URL = "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/{num}/claims"

    def __init__(self, credentials: Dict[str, Any] = None):
        super().__init__(credentials)
        self._token = ""
        self._token_expiry = 0.0

    def _get_token(self) -> str:
        key = self.credentials.get("consumer_key", "")
        secret = self.credentials.get("consumer_secret", "")
        if not key or not secret:
            return ""
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        try:
            resp = requests.post(
                self.AUTH_URL,
                data={"grant_type": "client_credentials"},
                auth=(key, secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            self._token = payload.get("access_token", "")
            self._token_expiry = time.time() + float(payload.get("expires_in", 1200) or 1200)
            return self._token
        except Exception as e:
            print(f"EPO OPS auth failed: {e}")
            return ""

    def search(self, query: str, max_results: int = 10, language: Optional[str] = None,
               after_date: Optional[str] = None, force_plus: bool = False) -> List[Dict[str, Any]]:
        token = self._get_token()
        if not token:
            return []

        terms = query.replace("+", " ").strip()
        cql = f'ti="{terms}" or ab="{terms}"'
        if after_date and after_date[:4].isdigit():
            cql = f'({cql}) and pd within "{after_date[:4]}-{time.strftime("%Y")}"'

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        params = {"q": cql, "Range": f"1-{min(max_results, 100)}"}

        results: List[Dict[str, Any]] = []
        try:
            resp = requests.get(self.SEARCH_URL, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            docs = self._extract_documents(resp.json())
            for doc in docs[:max_results]:
                parsed = self._parse_document(doc)
                if not parsed:
                    continue
                num = parsed["patent_meta"]["publication_number"]
                claims = self._fetch_claims(num, headers)
                parsed["patent_meta"]["claims_text"] = claims
                parsed["patent_meta"]["independent_claims"] = ""
                results.append(parsed)
        except Exception as e:
            print(f"Error checking EPO OPS: {e}")
        return results

    # -- OPS JSON is deeply nested and inconsistently list-vs-dict; normalise defensively --

    @staticmethod
    def _as_list(node) -> list:
        if node is None:
            return []
        return node if isinstance(node, list) else [node]

    def _extract_documents(self, payload: dict) -> list:
        try:
            world = payload.get("ops:world-patent-data", {})
            biblio = world.get("ops:biblio-search", {})
            refs = biblio.get("ops:search-result", {})
            return self._as_list(refs.get("exchange-documents"))
        except Exception:
            return []

    def _parse_document(self, node: dict) -> Optional[Dict[str, Any]]:
        try:
            doc = self._as_list(node.get("exchange-document"))
            if not doc:
                return None
            doc = doc[0]
            country = doc.get("@country", "")
            docnum = doc.get("@doc-number", "")
            kind = doc.get("@kind", "")
            if not docnum:
                return None
            pub = f"{country}{docnum}{kind}"

            biblio = doc.get("bibliographic-data", {}) or {}

            title = ""
            for t in self._as_list(biblio.get("invention-title")):
                if isinstance(t, dict) and t.get("$"):
                    title = t["$"]
                    if t.get("@lang") == "en":
                        break
            if not title:
                return None

            assignees = []
            parties = biblio.get("parties", {}) or {}
            for a in self._as_list(parties.get("applicants", {}).get("applicant")):
                name = (a.get("applicant-name", {}) or {}).get("name", {})
                if isinstance(name, dict) and name.get("$"):
                    assignees.append(name["$"])
            assignees = list(dict.fromkeys(assignees))

            inventors = []
            for i in self._as_list(parties.get("inventors", {}).get("inventor")):
                name = (i.get("inventor-name", {}) or {}).get("name", {})
                if isinstance(name, dict) and name.get("$"):
                    inventors.append(name["$"])
            inventors = list(dict.fromkeys(inventors))

            date = ""
            for d in self._as_list(biblio.get("publication-reference", {}).get("document-id")):
                if isinstance(d, dict) and isinstance(d.get("date"), dict):
                    date = d["date"].get("$", "")
                    if date:
                        break

            abstract = ""
            for ab in self._as_list(doc.get("abstract")):
                for p in self._as_list((ab or {}).get("p")):
                    if isinstance(p, dict) and p.get("$"):
                        abstract += p["$"] + " "
            abstract = abstract.strip()

            cpc = []
            for c in self._as_list(biblio.get("classifications-cpc", {}).get("classification-cpc")):
                if isinstance(c, dict) and c.get("text", {}).get("$"):
                    cpc.append(c["text"]["$"])

            return {
                "id": pub,
                "title": title,
                "url": f"https://worldwide.espacenet.com/patent/search?q={pub}",
                "source": "EPO OPS",
                "year": int(date[:4]) if date[:4].isdigit() else None,
                "abstract": abstract,
                "authors": inventors,
                "doi": "",
                "needs_pdf_resolve": False,
                "doc_type": "patent",
                "patent_meta": {
                    "publication_number": pub,
                    "kind_code": kind,
                    "assignee": assignees[0] if assignees else "",
                    "assignees": assignees,
                    "inventors": inventors,
                    "cpc": cpc,
                    "priority_date": date,
                },
            }
        except Exception as e:
            print(f"EPO OPS parse error: {e}")
            return None

    def _fetch_claims(self, pub_number: str, headers: Dict[str, str]) -> str:
        """Full text is EP/WO-mostly. A miss is normal coverage, not a failure."""
        try:
            resp = requests.get(self.CLAIMS_URL.format(num=pub_number), headers=headers, timeout=25)
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            world = resp.json().get("ops:world-patent-data", {})
            fulltext = world.get("ftxt:fulltext-documents", {})
            docs = self._as_list(fulltext.get("ftxt:fulltext-document"))
            out = []
            for d in docs:
                for c in self._as_list((d or {}).get("claims")):
                    for p in self._as_list((c or {}).get("claim")):
                        for t in self._as_list((p or {}).get("claim-text")):
                            if isinstance(t, dict) and t.get("$"):
                                out.append(t["$"])
                            elif isinstance(t, str):
                                out.append(t)
            return "\n\n".join(out)
        except Exception as e:
            print(f"EPO OPS claims unavailable for {pub_number}: {e}")
            return ""
