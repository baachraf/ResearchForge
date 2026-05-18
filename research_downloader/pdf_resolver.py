"""
PDF URL resolver: given a web page URL, find the direct PDF download URL.

Strategy chain:
  1. citation_pdf_url meta tag   — used by Google Scholar, Springer, Nature, IEEE, MDPI, ...
  2. arXiv abs → pdf             — pattern rewrite
  3. Semantic Scholar API        — when an S2 paper page is provided
  4. Direct .pdf hrefs in HTML   — same-domain preferred
  5. Download-button anchors     — links labelled "PDF", "Full text", "Download"
  6. Site-specific patterns      — OpenReview, ACL Anthology, PMLR, ...
  7. Unpaywall API               — open-access record for any DOI
"""
import re
import requests
from urllib.parse import urljoin, urlparse
from typing import Optional

ACADEMIC_DOMAINS = frozenset({
    "arxiv.org",
    "semanticscholar.org",
    "researchgate.net",
    "academia.edu",
    "ieeexplore.ieee.org", "ieee.org",
    "link.springer.com", "springer.com",
    "nature.com",
    "dl.acm.org", "acm.org",
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov",
    "sciencedirect.com", "elsevier.com",
    "onlinelibrary.wiley.com", "wiley.com",
    "tandfonline.com",
    "mdpi.com",
    "plos.org", "plosone.org",
    "biorxiv.org", "medrxiv.org",
    "ssrn.com",
    "openreview.net",
    "papers.nips.cc", "proceedings.neurips.cc",
    "proceedings.mlr.press",
    "aclanthology.org",
    "hal.science", "hal.archives-ouvertes.fr",
    "jstor.org",
    "pnas.org",
    "science.org",
    "cell.com",
    "worldscientific.com",
    "hindawi.com",
    "frontiersin.org",
    "jmlr.org",
    "aaai.org",
    "ijcai.org",
    "cvf.com", "thecvf.com",
    "ecva.net",
    "nips.cc", "icml.cc",
    "aps.org",
    "iopscience.iop.org",
    "rsc.org",
    "bmj.com",
    "jamanetwork.com",
    "nejm.org",
    "acpjournals.org",
})

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_academic_domain(url: str) -> bool:
    """Return True if url belongs to a known academic publisher / repository."""
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return any(domain == d or domain.endswith("." + d) for d in ACADEMIC_DOMAINS)
    except Exception:
        return False


def is_direct_pdf(url: str) -> bool:
    """Return True if the URL very likely points directly to a PDF file (no crawling needed)."""
    try:
        clean = url.split("?")[0].split("#")[0].lower()
        if clean.endswith(".pdf"):
            return True
        if re.search(r'arxiv\.org/pdf/', url, re.IGNORECASE):
            return True
        if re.search(r'ncbi\.nlm\.nih\.gov/pmc/articles/PMC\d+/pdf', url, re.IGNORECASE):
            return True
        if re.search(r'openreview\.net/pdf\?', url, re.IGNORECASE):
            return True
        if re.search(r'aclanthology\.org/\S+\.pdf', url, re.IGNORECASE):
            return True
    except Exception:
        pass
    return False


def resolve_pdf_url(url: str, timeout: int = 14) -> Optional[str]:
    """
    Try to find the direct PDF download URL from a paper landing page.
    Returns a PDF URL string, or None if resolution failed.
    """
    if not url:
        return None
    if is_direct_pdf(url):
        return url

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return None

        # Server sent a PDF directly (redirect to PDF, e.g. doi.org → publisher PDF)
        ct = resp.headers.get("content-type", "").lower()
        if "application/pdf" in ct:
            return resp.url

        final_url = resp.url
        html = resp.text

        # ── 1. citation_pdf_url meta tag ────────────────────────────────────
        # The de-facto standard used by most academic publishers.
        for pat in (
            r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\'](.*?)["\']',
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']citation_pdf_url["\']',
        ):
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip()
                if candidate:
                    return urljoin(final_url, candidate)

        # ── 2. arXiv abs → pdf ──────────────────────────────────────────────
        m = re.search(r'arxiv\.org/abs/([\d.]+v?\d*)', final_url)
        if m:
            return f"https://arxiv.org/pdf/{m.group(1)}.pdf"

        # ── 3. Semantic Scholar paper page → API ────────────────────────────
        m = re.search(r'semanticscholar\.org/paper/[^/?#]+/([a-f0-9]{20,})', final_url)
        if m:
            try:
                api_url = (
                    f"https://api.semanticscholar.org/graph/v1/paper/{m.group(1)}"
                    "?fields=openAccessPdf"
                )
                r2 = requests.get(api_url, timeout=7)
                if r2.status_code == 200:
                    info = r2.json().get("openAccessPdf") or {}
                    if info.get("url"):
                        return info["url"]
            except Exception:
                pass

        # ── 4. Direct .pdf hrefs anywhere in the page ───────────────────────
        parsed_base = urlparse(final_url)
        base_domain = parsed_base.netloc

        abs_pdfs = re.findall(
            r'href=["\']((https?://)[^"\']*\.pdf(?:\?[^"\']*)?)["\']', html, re.IGNORECASE
        )
        abs_pdfs = [m[0] for m in abs_pdfs]

        rel_pdfs = re.findall(
            r'href=["\'](/[^"\']*\.pdf(?:\?[^"\']*)?)["\']', html, re.IGNORECASE
        )
        rel_pdfs = [urljoin(final_url, u) for u in rel_pdfs]

        all_pdfs = abs_pdfs + rel_pdfs
        if all_pdfs:
            same_domain = [u for u in all_pdfs if urlparse(u).netloc == base_domain]
            return (same_domain or all_pdfs)[0]

        # ── 5. Download / PDF button anchors ────────────────────────────────
        btn_pat = re.compile(
            r'<a[^>]+href=["\']((https?://)[^"\']{8,})["\'][^>]*>'
            r'(?:[^<]{0,120}?)(?:pdf|full.?text|full.?paper|download|get paper)[^<]{0,60}?'
            r'</a>',
            re.IGNORECASE | re.DOTALL,
        )
        for m in btn_pat.finditer(html):
            link = m.group(1)
            if not re.search(r'\.(html?|php|asp|jsp)(\?|$)', link, re.IGNORECASE):
                return link

        # Also check data-href and data-url attributes that may hold PDF links
        for attr_val in re.findall(r'data-(?:href|url)=["\'](https?://[^"\']+\.pdf[^"\']*)["\']', html, re.IGNORECASE):
            return attr_val

        # ── 6. Site-specific patterns ────────────────────────────────────────
        domain = base_domain.lower()

        if "openreview.net" in domain:
            m = re.search(r'[?&]id=([A-Za-z0-9_\-]+)', final_url)
            if m:
                return f"https://openreview.net/pdf?id={m.group(1)}"

        if "aclanthology.org" in domain:
            m = re.search(r'aclanthology\.org/([A-Z0-9.\-]+?)(?:\.html?|/)?$', final_url, re.IGNORECASE)
            if m:
                return f"https://aclanthology.org/{m.group(1)}.pdf"

        if "proceedings.mlr.press" in domain:
            m = re.search(r'/([a-z]\d+/[a-z0-9\-]+)(?:\.html?)?$', final_url, re.IGNORECASE)
            if m:
                slug = m.group(1)
                paper = slug.split("/")[-1]
                return f"https://proceedings.mlr.press/{slug}/{paper}.pdf"

        if "papers.nips.cc" in domain or "proceedings.neurips.cc" in domain:
            m = re.search(r'hash/([a-f0-9]+)', final_url)
            if m:
                return f"https://papers.nips.cc/paper_files/paper/{m.group(1)}-Paper.pdf"

        if "hal.science" in domain or "hal.archives-ouvertes.fr" in domain:
            m = re.search(r'/(hal-\d+)', final_url)
            if m:
                hal_id = m.group(1)
                return f"https://hal.science/{hal_id}/document"

        # ── 7. Unpaywall API for any DOI ────────────────────────────────────
        doi_m = re.search(r'doi\.org/(10\.\d{4,}/[^\s"\'<>#\]]+)', final_url)
        if not doi_m:
            doi_m = re.search(
                r'\b(?:doi|DOI)[=:/]\s*(10\.\d{4,}/[^\s"\'<>#\]]+)',
                html[:8000],
            )
        if doi_m:
            doi = doi_m.group(1).rstrip(".,;)")
            try:
                r3 = requests.get(
                    f"https://api.unpaywall.org/v2/{doi}?email=research@llm-tool.local",
                    timeout=9,
                )
                if r3.status_code == 200:
                    oa = r3.json().get("best_oa_location") or {}
                    pdf = oa.get("url_for_pdf") or oa.get("url")
                    if pdf and is_direct_pdf(pdf):
                        return pdf
            except Exception:
                pass

    except Exception:
        pass

    return None
