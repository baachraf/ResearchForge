import os
import re
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
    come back as metadata only, which is expected, not an error. Measured live
    2026-07-27 over 20 mixed hits: claims returned for WO/EP/GB (10/20), 404 for
    US/CN/KR/MA (10/20).
    """

    AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
    SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"
    CLAIMS_URL = "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/{num}/claims"
    IMAGES_URL = "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/{num}/images"
    IMAGE_RETRIEVE_URL = "https://ops.epo.org/3.2/rest-services/{link}.pdf"

    # A patent's original document can run to hundreds of image pages (a WO with
    # its search report hit 201). OPS serves one page per retrieval call, so the
    # fetch is one paced call per page. The cap only guards against a pathological
    # giant; it must be high enough that a normal patent is never truncated — at
    # 60 real documents were being cut off mid-description.
    MAX_PDF_PAGES = 300

    # search() spends one claims call per hit, so a 20-hit search is 21 calls.
    # This paces the *retrieval* bucket (50/min observed), which is what the
    # claims loop consumes. It does NOT cover the search bucket: OPS advertises
    # its remaining budget in X-Throttling-Control and dropped that one to 5/min
    # — 12s apart — while reporting itself overloaded on 2026-07-27. Many queries
    # in one run rely on the 403 backoff below, not on this interval.
    _MIN_INTERVAL = 0.4

    PAGE_SIZE = 100    # OPS Range maximum per call
    MAX_RANGE = 2000   # OPS refuses ranges beyond this

    def __init__(self, credentials: Dict[str, Any] = None):
        super().__init__(credentials)
        self._token = ""
        self._token_expiry = 0.0
        self._last_call = 0.0

    def _request(self, url: str, headers: Dict[str, str], params: dict = None,
                 retries: int = 2):
        """GET with pacing and one backoff on a throttle rejection.

        OPS signals throttling with 403 + X-Rejection-Reason, not only 429. Both
        are retried; every other status (including 404, which is ordinary missing
        full text) is handed straight back to the caller.
        """
        for attempt in range(retries):
            gap = time.time() - self._last_call
            if gap < self._MIN_INTERVAL:
                time.sleep(self._MIN_INTERVAL - gap)
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            self._last_call = time.time()

            if resp.status_code not in (403, 429):
                return resp
            reason = resp.headers.get("X-Rejection-Reason", "")
            if resp.status_code == 403 and not reason:
                return resp  # a real authorisation failure, not throttling
            if attempt == retries - 1:
                print(f"EPO OPS throttled ({resp.status_code} {reason}) — giving up on {url}")
                return resp
            wait = float(resp.headers.get("Retry-After", 0) or 0) or 2.0 * (attempt + 1)
            print(f"EPO OPS throttled ({reason or resp.status_code}); retrying in {wait:.0f}s")
            time.sleep(wait)
        return resp

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

        cql = self._build_cql(query)
        if not cql:
            return []
        if after_date and after_date[:4].isdigit():
            cql = f'({cql}) and pd within "{after_date[:4]}-{time.strftime("%Y")}"'

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        results: List[Dict[str, Any]] = []
        try:
            docs = []
            # OPS caps Range at 100 per call and refuses beyond MAX_RANGE, so a
            # larger max_results has to be paged rather than silently truncated.
            wanted = max(1, min(max_results, self.MAX_RANGE))
            for start in range(1, wanted + 1, self.PAGE_SIZE):
                end = min(start + self.PAGE_SIZE - 1, wanted)
                resp = self._request(self.SEARCH_URL, headers,
                                     params={"q": cql, "Range": f"{start}-{end}"})
                resp.raise_for_status()
                payload = resp.json()
                self._raise_on_error_body(payload)
                page = self._extract_documents(payload)
                docs.extend(page)
                if len(page) < (end - start + 1):
                    break   # last page

            for doc in docs[:max_results]:
                parsed = self._parse_document(doc)
                if not parsed:
                    continue
                num = parsed["patent_meta"]["publication_number"]
                claims = self._fetch_claims(num, headers)
                parsed["patent_meta"]["claims_text"] = claims
                parsed["patent_meta"]["independent_claims"] = self._independent_claims(claims)
                results.append(parsed)
        except Exception as e:
            print(f"Error checking EPO OPS: {e}")
        return results

    @staticmethod
    def _raise_on_error_body(payload) -> None:
        """OPS can answer HTTP 200 with an error document.

        Confirmed live 2026-07-27 on /classification/cpc: status 200, body
        `{"error": {...}}` reporting an upstream 400. `raise_for_status()` sees
        nothing wrong and the parser then finds no documents, so the failure is
        indistinguishable from "your query matched nothing". Surface it instead.
        """
        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"]
            msg = err.get("message") if isinstance(err, dict) else err
            raise RuntimeError(f"EPO OPS returned an error body with HTTP 200: {msg}")

    @staticmethod
    def _build_cql(query: str) -> str:
        """Build a CQL query against the `ta` (title-or-abstract) index.

        Quoting matters enormously here. `ta="a b c"` is an exact-PHRASE search:
        verified live 2026-07-27, `ti="photoplethysmography blood pressure" or
        ab="..."` returned **1** hit where the unquoted form returned **925**.
        Multi-word queries are therefore passed unquoted so OPS ANDs the terms,
        unless the caller deliberately wrapped the whole query in double quotes,
        which is honoured as an explicit phrase search.

        `ta=` is exactly the `ti= or ab=` union (both forms returned 925) at half
        the query length.
        """
        terms = query.replace("+", " ").strip()
        explicit_phrase = len(terms) > 1 and terms[0] == '"' and terms[-1] == '"'
        # Strip quotes either way — an unbalanced one is a CQL syntax error.
        terms = terms.replace('"', " ").strip()
        terms = " ".join(terms.split())
        if not terms:
            return ""
        return f'ta="{terms}"' if explicit_phrase else f"ta={terms}"

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

    @classmethod
    def _pick_lang(cls, nodes: list, body_key: str = None, prefer: str = "en") -> str:
        """Pick the preferred-language variant out of a repeated OPS field.

        OPS repeats `invention-title` and `abstract` once per language. Joining
        them yields an English paragraph followed by its Chinese/German original,
        which then goes to the LLM as one blob. Take one language instead.
        """
        best, first = "", ""
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if body_key:
                text = " ".join(
                    p["$"] for p in cls._as_list(n.get(body_key))
                    if isinstance(p, dict) and p.get("$")
                ).strip()
            else:
                text = (n.get("$") or "").strip()
            if not text:
                continue
            if not first:
                first = text
            if (n.get("@lang") or "").lower() == prefer:
                best = text
                break
        return best or first

    @classmethod
    def _extract_cpc(cls, biblio: dict) -> list:
        """Assemble CPC symbols from `patent-classifications`.

        There is no `classifications-cpc` node in the live biblio payload
        (verified 2026-07-27 — 0 of 10 documents had one), so the previous
        lookup silently produced an empty list on every hit. CPC arrives split
        into section/class/subclass/main-group/subgroup components under
        `patent-classifications.patent-classification[]`, alongside IPC entries
        that are excluded here by scheme. `A` + `61` + `B` + `5` + `1102`
        reassembles as `A61B5/1102`.
        """
        out = []
        for c in cls._as_list((biblio.get("patent-classifications") or {}).get("patent-classification")):
            if not isinstance(c, dict):
                continue
            scheme = ((c.get("classification-scheme") or {}).get("@scheme") or "")
            if not scheme.upper().startswith("CPC"):
                continue

            def part(key):
                v = c.get(key)
                return (v.get("$") or "").strip() if isinstance(v, dict) else ""

            sec, cls_, sub = part("section"), part("class"), part("subclass")
            main, subg = part("main-group"), part("subgroup")
            if not (sec and cls_ and sub and main):
                continue
            sym = f"{sec}{cls_}{sub}{main}"
            if subg:
                sym += f"/{subg}"
            if sym not in out:
                out.append(sym)
        return out

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

            # Titles and abstracts are repeated per language ("en" plus the
            # original, e.g. "ol" for a CN filing). Prefer English; fall back to
            # the first available rather than concatenating languages together.
            title = self._pick_lang(self._as_list(biblio.get("invention-title")))
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

            pub_date = ""
            for d in self._as_list(biblio.get("publication-reference", {}).get("document-id")):
                if isinstance(d, dict) and isinstance(d.get("date"), dict):
                    pub_date = d["date"].get("$", "")
                    if pub_date:
                        break

            # The earliest priority claim — the date that actually matters when
            # dating prior art. Falls back to the publication date when the
            # document declares no priority claim.
            prio_dates = []
            for pc in self._as_list(biblio.get("priority-claims", {}).get("priority-claim")):
                for d in self._as_list((pc or {}).get("document-id")):
                    if isinstance(d, dict) and isinstance(d.get("date"), dict):
                        v = d["date"].get("$", "")
                        if v:
                            prio_dates.append(v)
            priority_date = min(prio_dates) if prio_dates else pub_date

            abstract = self._pick_lang(self._as_list(doc.get("abstract")), body_key="p")

            cpc = self._extract_cpc(biblio)

            return {
                "id": pub,
                "title": title,
                "url": f"https://worldwide.espacenet.com/patent/search?q={pub}",
                "source": "EPO OPS",
                "year": int(pub_date[:4]) if pub_date[:4].isdigit() else None,
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
                    "priority_date": priority_date,
                    "publication_date": pub_date,
                },
            }
        except Exception as e:
            print(f"EPO OPS parse error: {e}")
            return None

    # A dependent claim back-references another claim; an independent one does not.
    _DEPENDENT_RE = re.compile(
        r"\b(?:as\s+claimed\s+in|according\s+to|as\s+defined\s+in|as\s+set\s+forth\s+in|of|in)\s+"
        r"(?:any\s+(?:one\s+)?of\s+)?(?:the\s+)?(?:preceding|foregoing|previous)?\s*claims?\b",
        re.IGNORECASE,
    )

    # Claims are numbered "1. ", "15. " at the start of a line.
    _CLAIM_START_RE = re.compile(r"(?m)^[ \t]*(\d{1,3})[ \t]*\.[ \t]+")

    @staticmethod
    def _strip_running_headers(chunks: list) -> list:
        """Drop the page running-head from chunked full text.

        US/WO claims arrive as page-sized chunks that each open with the page
        header (e.g. an attorney docket line). It is spliced into running text
        rather than isolated on its own line — chunk 3 of WO2026136374A1 reads
        "Atty. Dkt No. 10085-01-0191-PCT of: eumelanin, ..." — so a
        repeated-whole-line filter does not see it. It is reliably a common
        *prefix* of the chunks, which is what this detects, then removes
        everywhere (the first chunk carries it mid-line too).

        Bounded on both sides: too short and it would be ordinary shared claim
        wording, too long and it is genuine text.
        """
        if len(chunks) < 3:
            return chunks
        tail = [c for c in chunks[1:] if c]
        if len(tail) < 2:
            return chunks
        prefix = tail[0]
        for c in tail[1:]:
            i = 0
            while i < len(prefix) and i < len(c) and prefix[i] == c[i]:
                i += 1
            prefix = prefix[:i]
            if not prefix:
                return chunks
        prefix = prefix.strip()
        if not (8 <= len(prefix) <= 120):
            return chunks
        return [c.replace(prefix, " ") for c in chunks]

    @classmethod
    def _split_claims(cls, claims_text: str) -> list:
        """Split a claims blob into (number, text) pairs.

        Two shapes occur live (verified 2026-07-27). EP publications return one
        `claim-text` entry per claim, already clean. US/WO publications return a
        handful of arbitrary page-sized chunks with a running attorney-docket
        header, claims running across chunk boundaries — so the entry structure
        carries no claim boundaries at all and only the numbering does.

        A chunk can open with a stray page number that collides with a real claim
        number, so a repeated number keeps its longest text.
        """
        marks = list(cls._CLAIM_START_RE.finditer(claims_text))
        if not marks:
            return []
        best = {}
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(claims_text)
            num = int(m.group(1))
            body = claims_text[m.end():end].strip()
            if len(body) > len(best.get(num, "")):
                best[num] = body
        return [(n, best[n]) for n in sorted(best)]

    @classmethod
    def _independent_claims(cls, claims_text: str) -> str:
        """The independent claims define the actual scope; the prompt asks for them.

        Heuristic and English-only — a claim that back-references another claim is
        dependent. If nothing survives (non-English text, unusual phrasing), return
        empty rather than guessing, so the prompt's CLAIMS NOT AVAILABLE path holds.
        """
        if not claims_text:
            return ""
        claims = cls._split_claims(claims_text)
        if not claims:
            return ""
        keep = [f"{n}. {body}" for n, body in claims if not cls._DEPENDENT_RE.search(body)]
        return "\n\n".join(keep)

    def _fetch_claims(self, pub_number: str, headers: Dict[str, str]) -> str:
        """Full text is EP/WO-mostly. A miss is normal coverage, not a failure."""
        try:
            resp = self._request(self.CLAIMS_URL.format(num=pub_number), headers)
            if resp.status_code == 404:
                return ""  # ordinary missing full text (US/CN/KR/…), not a failure
            resp.raise_for_status()
            world = resp.json().get("ops:world-patent-data", {})
            fulltext = world.get("ftxt:fulltext-documents", {})
            docs = self._as_list(fulltext.get("ftxt:fulltext-document"))

            # An EP-B1 publishes its claims in EN, DE and FR. Concatenating every
            # block would triple the claim set in three languages; keep one.
            blocks = []
            for d in docs:
                for c in self._as_list((d or {}).get("claims")):
                    if not isinstance(c, dict):
                        continue
                    texts = []
                    for p in self._as_list(c.get("claim")):
                        for t in self._as_list((p or {}).get("claim-text")):
                            if isinstance(t, dict) and t.get("$"):
                                texts.append(t["$"])
                            elif isinstance(t, str):
                                texts.append(t)
                    if texts:
                        blocks.append(((c.get("@lang") or "").upper(), texts))
            if not blocks:
                return ""
            chosen = next((t for lang, t in blocks if lang == "EN"), blocks[0][1])
            return "\n\n".join(self._strip_running_headers(chosen))
        except Exception as e:
            print(f"EPO OPS claims unavailable for {pub_number}: {e}")
            return ""

    def _full_document_ref(self, pub_number: str, headers: Dict[str, str]):
        """Return (retrieval_link, page_count) for the original FullDocument, or
        (None, 0) when OPS has no document image for this publication."""
        resp = self._request(self.IMAGES_URL.format(num=pub_number), headers)
        if resp.status_code != 200:
            return None, 0
        try:
            inq = (resp.json().get("ops:world-patent-data", {})
                   .get("ops:document-inquiry", {})
                   .get("ops:inquiry-result", {}))
        except ValueError:
            return None, 0
        for inst in self._as_list(inq.get("ops:document-instance")):
            if (inst or {}).get("@desc") == "FullDocument":
                link = inst.get("@link")
                try:
                    pages = int(inst.get("@number-of-pages", 0))
                except (TypeError, ValueError):
                    pages = 0
                return link, pages
        return None, 0

    def download_original_pdf(self, pub_number: str, dest_path: str,
                              on_page=None) -> bool:
        """Download the original published document to ``dest_path`` as a PDF.

        OPS serves the document as page images, one page per retrieval call, so
        this fetches each page and merges them. Coverage is broad (incl.
        US/CN/KR/JP) — this is EPO's "Original document", not the parsed full
        text. Returns True on success. A miss (no document image, auth failure,
        or no page fetched) returns False without raising.
        """
        token = self._get_token()
        if not token:
            return False
        json_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        link, pages = self._full_document_ref(pub_number, json_headers)
        if not link or pages <= 0:
            return False

        try:
            import fitz  # PyMuPDF — already a dependency (used for PDF text)
        except ImportError:
            return False

        pdf_headers = {"Authorization": f"Bearer {token}", "Accept": "application/pdf"}
        url = self.IMAGE_RETRIEVE_URL.format(link=link)
        merged = fitz.open()
        got = 0
        try:
            for page in range(1, min(pages, self.MAX_PDF_PAGES) + 1):
                resp = self._request(url, pdf_headers, params={"Range": str(page)})
                if resp.status_code != 200 or resp.content[:4] != b"%PDF":
                    continue
                try:
                    with fitz.open(stream=resp.content, filetype="pdf") as one:
                        merged.insert_pdf(one)
                    got += 1
                    if on_page:
                        try:
                            on_page(got, min(pages, self.MAX_PDF_PAGES))
                        except Exception:
                            pass
                except Exception:
                    continue
            if got == 0:
                merged.close()
                return False
            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            merged.save(dest_path)
            return True
        finally:
            merged.close()
