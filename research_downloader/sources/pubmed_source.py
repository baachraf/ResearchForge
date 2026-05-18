import time
import hashlib
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from .base_source import DocumentSource

ES_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EF_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class PubMedSource(DocumentSource):
    def __init__(self, credentials: Dict[str, Any] = None):
        super().__init__(credentials)
        self.api_key = self.credentials.get("api_key", "")

    def search(self, query: str, max_results: int = 10,
               language: Optional[str] = None, after_date: Optional[str] = None,
               force_plus: bool = False) -> List[Dict[str, Any]]:
        results = []
        print(f"[PUBMED] search called: query={query!r} max={max_results}", flush=True)

        import requests
        try:
            # --- Step 1: search for PMIDs ---
            es_params = {
                "db": "pubmed",
                "term": query,
                "retmax": min(max_results * 2, 100),
                "sort": "relevance",
                "retmode": "xml",
            }
            if self.api_key:
                es_params["api_key"] = self.api_key
            if after_date:
                try:
                    year = after_date.split("-")[0]
                    es_params["mindate"] = f"{year}/01/01"
                    es_params["maxdate"] = "2026/12/31"
                    es_params["datetype"] = "pdat"
                except Exception:
                    pass

            resp = requests.get(ES_URL, params=es_params, timeout=15)
            resp.raise_for_status()
            print(f"[PUBMED] esearch URL: {resp.url[:120]}", flush=True)
            root = ET.fromstring(resp.content)
            pmids = [e.text for e in root.findall(".//Id") if e.text]
            print(f"[PUBMED] Found {len(pmids)} PMIDs: {pmids[:5]}", flush=True)
            if not pmids:
                return results

            pmids = pmids[:max_results]
            time.sleep(0.35)

            # --- Step 2: fetch details ---
            ef_params = {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
                "rettype": "abstract",
            }
            if self.api_key:
                ef_params["api_key"] = self.api_key

            resp = requests.get(EF_URL, params=ef_params, timeout=20)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            for art in root.findall(".//PubmedArticle"):
                try:
                    med = art.find(".//MedlineCitation")
                    if med is None:
                        continue

                    title_el = med.find(".//ArticleTitle")
                    title = title_el.text if title_el is not None else "Untitled"
                    if not title:
                        title = "Untitled"

                    abs_el = med.find(".//AbstractText")
                    abstract = ""
                    if abs_el is not None and abs_el.text:
                        abstract = abs_el.text
                    else:
                        abs_parts = med.findall(".//AbstractText")
                        if abs_parts:
                            abstract = " ".join(p.text for p in abs_parts if p.text)

                    authors = []
                    for auth_el in med.findall(".//Author"):
                        last = auth_el.findtext("LastName", "")
                        fore = auth_el.findtext("ForeName", "")
                        name = f"{fore} {last}".strip()
                        if name:
                            authors.append(name)

                    pmid_el = med.findtext("PMID", "")
                    pmid = str(pmid_el) if pmid_el else ""

                    year = None
                    date_el = med.find(".//DateCompleted/Year")
                    if date_el is not None and date_el.text:
                        try:
                            year = int(date_el.text)
                        except ValueError:
                            pass
                    if year is None:
                        pub_date = med.find(".//PubDate/Year")
                        if pub_date is not None and pub_date.text:
                            try:
                                year = int(pub_date.text)
                            except ValueError:
                                pass

                    # Try to get a PDF link from PMC
                    pdf_url = ""
                    has_open_access = False
                    for aid in art.findall(".//ArticleIdList/ArticleId"):
                        if aid.get("IdType") == "pmc":
                            pmc_id = aid.text
                            if pmc_id:
                                pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/pdf/"
                                has_open_access = True
                                break
                    if not has_open_access:
                        for aid in art.findall(".//ArticleIdList/ArticleId"):
                            if aid.get("IdType") == "doi":
                                doi = aid.text
                                if doi:
                                    pdf_url = f"https://doi.org/{doi}"
                                    break

                    url_hash = hashlib.md5(pmid.encode()).hexdigest()[:12]
                    results.append({
                        "id": f"pmid_{url_hash}",
                        "title": title,
                        "url": pdf_url,
                        "source": "PubMed",
                        "year": year,
                        "abstract": abstract,
                        "authors": authors,
                        "pmid": pmid,
                    })

                    if len(results) >= max_results:
                        break
                except Exception:
                    continue

        except Exception as e:
            print(f"Error checking PubMed: {e}")

        return results
