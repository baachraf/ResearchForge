"""
Background workers (QThread) for search, download, LLM processing, and discovery.

Classes:
  DiscoveryWorker       — Scans localhost for LM Studio / Ollama endpoints
  SearchWorker          — Searches a single query across 5 academic sources
  RelevanceScoringWorker — Scores checked papers against research context via LLM
  DownloadWorker        — Downloads paper PDFs sequentially with validation
  LLMProcessWorker      — Runs the full 3-pass LLM summarization pipeline
"""
import os
import re
import json
import time
import traceback
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

logging.getLogger("pypdf").setLevel(logging.ERROR)

from PySide6.QtCore import QThread, Signal
from openai import OpenAI

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from research_downloader.registry import Registry
from research_downloader.downloader import Downloader
from research_downloader.sources.arxiv_source import ArxivSource
from research_downloader.sources.semantic_scholar_source import SemanticScholarSource
from research_downloader.sources.web_source import WebSource
from research_downloader.sources.brave_source import BraveSource
from research_downloader.sources.pubmed_source import PubMedSource
from research_downloader.relevance_filter import (
    passes_title_filter,
    passes_content_filter,
    get_pdf_text_first_pages,
)
from research_downloader.pdf_resolver import resolve_pdf_url, is_direct_pdf
from gui.discovery import discover as discover_endpoints
from gui.llm_provider import create_llm_client, check_provider_connection
from llm_pdf_engine import LLMPdfEngine, TOPIC_SYNTHESIS_PROMPT, GLOBAL_SYNTHESIS_PROMPT


def _extract_paper_for_analysis(pdf_path: str, max_pages: int, max_chars: int) -> str:
    """
    Extract paper text for per-paper LLM analysis, guaranteeing the reference
    list is included regardless of paper length.

    Primary path: section detection via section_detector — assembles body sections
    up to 75% of the char budget then appends the References section in the
    remaining 25%, so §9/§10/§13 always receive the bibliography.

    Fallback: pypdf front+back split — takes the first 75% and last 25% of the
    raw page text so the reference list at the end of the paper is preserved.

    Raises on file read / parse errors so the caller can log and skip the file.
    Returns an empty string when the PDF contains no extractable text.
    """
    # Primary: section-aware extraction
    try:
        from gui.section_detector import detect_sections
        sections, _ = detect_sections(pdf_path)
        if len(sections) >= 3:
            ref_text  = sections.get("References", "")
            body_text = "\n\n".join(
                f"=== {k} ===\n{v}" for k, v in sections.items() if k != "References"
            )
            ref_budget  = min(len(ref_text), max_chars // 4)
            body_budget = max_chars - ref_budget
            content = body_text[:body_budget]
            if ref_text:
                content += f"\n\n=== References ===\n{ref_text[:ref_budget]}"
            return content
    except Exception:
        pass  # fall through to pypdf path

    # Fallback: flat pypdf extraction with front+back split
    import pypdf
    text = ""
    with open(pdf_path, "rb") as fh:
        reader = pypdf.PdfReader(fh)
        for i in range(min(len(reader.pages), max_pages)):
            pt = reader.pages[i].extract_text()
            if pt:
                text += pt

    if len(text) <= max_chars:
        return text

    front = int(max_chars * 0.75)
    back  = max_chars - front
    return text[:front] + "\n\n[...]\n\n" + text[-back:]


def _clean_query_for_apis(query: str) -> str:
    """Remove boolean operators and special chars that confuse academic APIs."""
    cleaned = re.sub(r'[()]', ' ', query)
    cleaned = re.sub(r'\b(AND|OR|NOT)\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _sanitize_date(date_str: str) -> Optional[str]:
    """Validate and normalize a date string to YYYY-MM-DD, or return None."""
    if not date_str or not date_str.strip():
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if fmt == "%Y-%m-%d":
                return dt.strftime("%Y-%m-%d")
            elif fmt == "%Y-%m":
                return dt.strftime("%Y-%m-01")
            else:
                return dt.strftime("%Y-01-01")
        except ValueError:
            continue
    return None


class DiscoveryWorker(QThread):
    """Scans localhost for running LM Studio and Ollama endpoints.

    Signals:
      finished(list[dict]) — List of discovered endpoint configs
      error(str)           — Error message on failure
    """
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, timeout: float = 3.0):
        super().__init__()
        self.timeout = timeout

    def run(self):
        try:
            results = discover_endpoints(timeout=self.timeout)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))
            traceback.print_exc()


class SearchWorker(QThread):
    """Searches one query across configured academic sources (arXiv, S2, DDG, Brave, PubMed).

    Signals:
      progress(str)           — Status messages
      results_ready(dict, list) — (query_cfg, list[paper_dict])
      finished()              — Search complete (all sources exhausted)
      error(str)              — Fatal error
    """
    progress = Signal(str)
    results_ready = Signal(object, object)
    finished = Signal()
    error = Signal(str)

    def __init__(self, query_cfg: dict, credentials: dict, parent=None):
        """
        :param query_cfg: Query dict with keys: query, sources, must_contain, must_not,
                          after_date, max_results, language, force_plus, search_mode
        :param credentials: API credentials dict (brave_search, arxiv, semantic_scholar, pubmed)
        """
        super().__init__(parent)
        self.query_cfg = query_cfg
        self.credentials = credentials
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            all_results = []
            raw_query = self.query_cfg.get("query", "")
            search_term = _clean_query_for_apis(raw_query)
            must_contain = self.query_cfg.get("must_contain", [])
            if must_contain:
                missing = [kw for kw in must_contain if kw.lower() not in search_term.lower()]
                if missing:
                    search_term = search_term + " " + " ".join(missing)
            language = self.query_cfg.get("language")
            after_date = self.query_cfg.get("after_date", "").strip()
            after_date = _sanitize_date(after_date)
            max_results = self.query_cfg.get("max_results", 20)
            sources_to_use = self.query_cfg.get("sources", ["arxiv", "semantic_scholar"])
            force_plus = self.query_cfg.get("force_plus", False)
            search_mode = self.query_cfg.get("search_mode", "academic")

            print(f"[DEBUG] SEARCH: query={raw_query!r} must_contain={must_contain} sources={sources_to_use}", flush=True)

            available_sources = {"web": WebSource()}

            brave_creds = self.credentials.get("brave_search", {})
            has_brave = bool(brave_creds.get("api_key", ""))
            print(f"[DEBUG] Brave: enabled={brave_creds.get('enabled')} key_len={len(brave_creds.get('api_key', ''))} has_key={has_brave}", flush=True)
            if brave_creds.get("enabled", True) and brave_creds.get("api_key"):
                available_sources["brave"] = BraveSource(credentials=brave_creds)

            arxiv_creds = self.credentials.get("arxiv", {})
            if arxiv_creds.get("enabled", True):
                available_sources["arxiv"] = ArxivSource(credentials=arxiv_creds)

            s2_creds = self.credentials.get("semantic_scholar", {})
            if s2_creds.get("enabled", True):
                available_sources["semantic_scholar"] = SemanticScholarSource(credentials=s2_creds)

            pubmed_creds = self.credentials.get("pubmed", {})
            if pubmed_creds.get("enabled", True):
                available_sources["pubmed"] = PubMedSource(credentials=pubmed_creds)

            print(f"[DEBUG] avail={list(available_sources.keys())}", flush=True)

            for source_name, source_obj in available_sources.items():
                if self._stop:
                    break
                in_use = source_name in sources_to_use
                print(f"[DEBUG] src={source_name} in_sources={in_use}", flush=True)
                if not in_use:
                    print(f"[DEBUG] SKIP {source_name}", flush=True)
                    continue

                self.progress.emit(f"Searching {source_name}: '{search_term}' ...")
                print(f"[DEBUG] Searching {source_name} with term={search_term!r}...", flush=True)
                try:
                    kwargs = dict(
                        query=search_term,
                        max_results=max_results,
                        language=language,
                        after_date=after_date,
                        force_plus=force_plus,
                    )
                    if source_name == "web":
                        kwargs["mode"] = search_mode
                    results = source_obj.search(**kwargs)
                    print(f"[DEBUG] {source_name}: {len(results)} results", flush=True)
                    for r in results:
                        r["query_source"] = source_name
                    all_results.extend(results)
                    self.progress.emit(f"\U0001f50d {source_name} ({len(results)} found)")
                    self.results_ready.emit(self.query_cfg, results)
                except Exception as e:
                    print(f"[DEBUG] {source_name} error: {e}", flush=True)
                    self.progress.emit(f"  {source_name} error: {e}")

                if source_name == "arxiv":
                    time.sleep(3.0)
                elif source_name == "semantic_scholar":
                    time.sleep(1.5)

            print(f"[DEBUG] DONE: total={len(all_results)}", flush=True)
            self.finished.emit()
        except Exception as e:
            print(f"[DEBUG] FATAL: {e}", flush=True)
            traceback.print_exc()
            self.error.emit(str(e))


class RelevanceScoringWorker(QThread):
    """Scores selected (checked) papers against research context via LLM.

    Used by the "Check Relevance" button in the Search & Download tab.

    Per-paper flow:
      1. Pre-resolve PDF URLs in parallel (ThreadPoolExecutor, max 5 workers)
      2. Check duplicate by paper ID → reuse existing score
      3. Acquire content: abstract > local PDF > remote PDF download
      4. Summarize long content (>600 chars) to 2-3 sentences via LLM
      5. Build scoring prompt with .replace() on PAPER/RESEARCH placeholders
      6. Call LLM (single or multi-turn depending on scoring_depth)
      7. If LLM returns -1: keyword overlap fallback (_keyword_score)

    Signals:
      progress(str)            — Status messages
      paper_scored(int,int,str) — (index, score, reason) — MUST be 3 args
      finished()               — All papers processed
      error(str)               — Fatal error
    """
    progress = Signal(str)
    paper_scored = Signal(int, int, str)  # index, score, reason
    finished = Signal()
    error = Signal(str)

    FALLBACK_SCORING = """\
Rate how relevant this paper is to the research described below.

PAPER:
Title: {title}
Content: {content}

RESEARCH:
Context: {context}
Intent: {intent}
Keywords: {focus_keywords}
Avoid: {avoid_topics}

Score 0-100:
- 90-100: Same core problem, same domain, similar methods
- 70-89: Same core problem, same domain, different approach
- 50-69: Related sub-problem in same domain
- 25-49: Same broad domain, different specific problem
- 0-24: Different problem entirely
- <=15 if covers avoid-topics

PROBLEM MATCH IS THE GATE. If the problem is different, score <=10 regardless of shared techniques.

Return ONLY an integer 0-100."""

    def __init__(self, endpoint: str, model_id: str, papers: list[dict],
                 research_context: str, scoring_prompt: str = "",
                 intent: str = "", focus_keywords: str = "", avoid_topics: str = "",
                 provider_name: str = "", api_key: str = "not-needed",
                 scoring_depth: int = 1):
        """
        :param endpoint: OpenAI-compatible API base URL
        :param model_id: Model name for scoring calls
        :param papers: List of paper dicts (checked rows from results table)
        :param research_context: User's research description (scoring context)
        :param scoring_prompt: Custom prompt template (falls back to FALLBACK_SCORING)
        :param intent: Structured research intent text
        :param focus_keywords: Comma-separated keywords to prioritize
        :param avoid_topics: Comma-separated topics to penalize
        :param provider_name: Provider identifier (for create_llm_client)
        :param api_key: API key ("not-needed" for local)
        :param scoring_depth: 1=single call, 2=compare+score, 3=analyze+compare+score
        """
        super().__init__()
        self.endpoint = endpoint
        self.model_id = model_id
        self.papers = papers
        self.research_context = research_context
        self.intent = intent
        self.focus_keywords = focus_keywords
        self.avoid_topics = avoid_topics
        self.scoring_prompt = scoring_prompt.strip() or self.FALLBACK_SCORING
        self.provider_name = provider_name
        self.api_key = api_key
        self.scoring_depth = scoring_depth
        self._stop = False

    def stop(self):
        self._stop = True

    def _extract_abstract_and_intro(self, filepath):
        """Extract Abstract + Introduction via section detection; fall back to first 2 pages.

        Returns (text, source) where source is 'sections' or 'pages'.
        """
        try:
            from .section_detector import detect_sections
            sections, _ = detect_sections(filepath)
            text = ""
            for name, body in sections.items():
                if any(t in name.lower() for t in ("abstract", "introduction")):
                    text += body + "\n\n"
            if len(text.strip()) >= 200:
                print(f"[SCORE] Section extraction: {len(text)} chars (abstract+intro)", flush=True)
                return text.strip().lower(), "sections"
            print(f"[SCORE] Section extraction too short ({len(text.strip())} chars) — falling back", flush=True)
        except Exception as e:
            print(f"[SCORE] Section detection failed: {e} — falling back to page extract", flush=True)
        return self._extract_scoring_content_pages(filepath), "pages"

    def _extract_scoring_content_pages(self, filepath, n_pages=2):
        """Fallback: extract first n_pages pages from a PDF."""
        try:
            import fitz
            import contextlib
            with open(os.devnull, "w") as devnull:
                with contextlib.redirect_stderr(devnull):
                    doc = fitz.open(filepath)
            try:
                num_pages = min(n_pages, len(doc))
                text = ""
                for i in range(num_pages):
                    text += doc[i].get_text()
                return text.lower() if text else None
            finally:
                doc.close()
        except Exception as e:
            print(f"[SCORE] Page extraction error: {e}", flush=True)
            return None

    def _keyword_score(self, paper_text: str, title: str) -> int:
        """Compute relevance score from keyword overlap between research context and paper text."""
        import re as _re
        def _terms(text):
            return set(_re.findall(r'[a-z0-9]{3,}', text.lower()))
        ctx_terms = _terms(self.research_context)
        focus_terms = _terms(self.focus_keywords)
        paper_terms = _terms(title + " " + paper_text)
        if not ctx_terms and not focus_terms:
            return 25
        ctx_overlap = len(ctx_terms & paper_terms) / max(len(ctx_terms), 1) if ctx_terms else 0
        focus_overlap = len(focus_terms & paper_terms) / max(len(focus_terms), 1) if focus_terms else 0
        avoid_terms = _terms(self.avoid_topics)
        if avoid_terms:
            avoid_hits = len(avoid_terms & paper_terms) / max(len(avoid_terms), 1)
            ctx_overlap -= avoid_hits * 0.5
        raw = int(ctx_overlap * 60 + focus_overlap * 40)
        return max(0, min(100, raw))

    def _summarize_for_scoring(self, client, content: str, title: str) -> str:
        """Summarize content to key points for efficient scoring. Returns empty string on failure."""
        if len(content) < 600:
            return ""  # already short enough, no need to summarize
        prompt = (
            f"Summarize this paper's core elements in 2-3 sentences:\n"
            f"1) Problem it solves\n2) Method used\n3) Domain/field\n\n"
            f"Title: {title}\nContent: {content[:2000]}\n\n"
            f"Concise summary:"
        )
        try:
            res = client.chat.completions.create(
                model=self.model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1024,
                timeout=20.0,
            )
            text = res.choices[0].message.content
            if text and text.strip():
                summary = text.strip()[:500]
                print(f"[SCORE] summary: {summary[:80]!r}", flush=True)
                return summary
        except Exception as e:
            print(f"[SCORE] summary error: {e}", flush=True)
        return ""

    def _score_with_depth(self, client, prompt_base: str, title: str):
        """Score a paper with optional reasoning. Returns (score, reason_str)."""
        depth = self.scoring_depth
        if depth <= 1:
            return self._single_call_score(client, prompt_base)

        # Truncate aggressively for depth>1 to stay within context window
        import re as _re
        short_prompt = re.sub(r'\n{3,}', '\n\n', prompt_base)
        short_prompt = short_prompt[:3500]

        instruction = "\n\nOutput: <score 0-100> then a dash then a one-sentence reason why this score. Example: '75 — same compression artifact domain but targets images not biosignals'"
        if depth == 3:
            instruction = "\n\nAnalyze problem/method/domain, compare against researcher's work. Output: <score 0-100> then a dash then a one-sentence reason."

        prompt = short_prompt + instruction
        print(f"[SCORE-D{depth}] prompt_len={len(prompt)} model={self.model_id}", flush=True)
        try:
            res = client.chat.completions.create(
                model=self.model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1024,
            )
            text = res.choices[0].message.content
            if not text or not text.strip():
                print(f"[SCORE-D{depth}] empty response — finish_reason={getattr(res.choices[0], 'finish_reason', '?')}", flush=True)
                return -1, ""
            text = text.strip()
            print(f"[SCORE-D{depth}] raw={text[:200]!r}", flush=True)
            import re as _re
            nums = _re.findall(r'(?<![\d.])\b(\d{1,3})\b(?![\d.])', text)
            if not nums:
                nums = _re.findall(r'\b(\d{1,3})\b', text)
                nums = [n for n in nums if f".{n}" not in text and f"{n}." not in text]
            score = int(nums[-1]) if nums else -1
            reason = ""
            for dash in ("—", "--", "- "):
                if dash in text:
                    after = text.split(dash, 1)[1].strip()
                    if after:
                        reason = after[:120]
                    break
            if not reason and nums:
                remainder = text[text.rfind(str(nums[-1])) + len(str(nums[-1])):].strip().lstrip(".-— ")[:120]
                if remainder:
                    reason = remainder
            print(f"[SCORE-D{depth}] nums={nums} score={score} reason={reason[:40]!r}", flush=True)
            return score if 0 <= score <= 100 else -1, reason
        except Exception as e:
            print(f"[SCORE-D{depth}] error: {e}", flush=True)
            return -1, ""

    def _single_call_score(self, client, prompt: str):
        """Single LLM call for scoring. Returns (score, reason_str)."""
        try:
            res = client.chat.completions.create(
                model=self.model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1024,
            )
            text = res.choices[0].message.content
            print(f"[SCORE-D1] raw={text!r}", flush=True)
            if not text:
                reasoning = getattr(res.choices[0].message, 'reasoning_content', None) or ""
                if reasoning:
                    text = reasoning
                else:
                    print(f"[SCORE-D1] empty response, no reasoning", flush=True)
                    return -1, ""
            text = text.strip()
            nums = re.findall(r'\b(\d{1,3})\b', text)
            score = int(nums[-1]) if nums else -1
            if score > 100:
                score = -1
            reason = ""
            match = re.search(r'\b(\d{1,3})\b(.*)', text)
            if match:
                remainder = match.group(2).strip()
                if remainder and len(remainder) > 2:
                    reason = remainder[:120]
            print(f"[SCORE-D1] nums={nums} score={score} reason={reason[:40]!r}", flush=True)
            return score if 0 <= score <= 100 else -1, reason
        except Exception as e:
            print(f"[SCORE-D1] error: {e}", flush=True)
            return -1, f"LLM error: {str(e)[:80]}"

    def run(self):
        client = None
        try:
            import time as _time
            import tempfile
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            client = create_llm_client(self.endpoint, self.api_key, self.provider_name, timeout=120.0)

            # Pre-resolve PDF URLs in parallel before the main scoring loop
            resolve_indices = []
            for i, paper in enumerate(self.papers):
                url = paper.get("url", "")
                if url and not paper.get("abstract") and paper.get("source") != "Local" and not is_direct_pdf(url):
                    resolve_indices.append(i)
            if resolve_indices:
                self.progress.emit(f"Resolving {len(resolve_indices)} PDF URLs in parallel...")
                from concurrent.futures import ThreadPoolExecutor, as_completed
                futures = {}
                executor = ThreadPoolExecutor(max_workers=5)
                try:
                    for i in resolve_indices:
                        futures[executor.submit(resolve_pdf_url, self.papers[i]["url"], 12)] = i
                    for future in as_completed(futures):
                        i = futures[future]
                        if self._stop:
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
                        try:
                            resolved = future.result()
                            if resolved:
                                self.papers[i]["_resolved_pdf_url"] = resolved
                            else:
                                self.papers[i]["_resolved_pdf_url"] = None
                        except Exception as e:
                            self.papers[i]["_resolved_pdf_url"] = None
                            print(f"[SCORE] Pre-resolve error #{i}: {e}", flush=True)
                finally:
                    executor.shutdown(wait=False)

            seen_ids = {}  # pid → paper dict of first occurrence (to copy score/reason to duplicates)
            for i, paper in enumerate(self.papers):
                if self._stop:
                    break
                pid = paper.get("id", paper.get("url", ""))
                if pid in seen_ids:
                    first = seen_ids[pid]
                    score = first.get("relevance_score", -1)
                    paper["relevance_score"] = score
                    paper["score_reason"] = first.get("score_reason", "duplicate")
                    self.paper_scored.emit(i, score, first.get("score_reason", ""))
                    continue
                seen_ids[pid] = paper
                title = paper.get("title", "Untitled")
                content = paper.get("abstract", "")
                has_abstract = bool(content)
                temp_file = None
                print(f"[SCORE] {i+1}/{len(self.papers)}: has_abstract={has_abstract} title={title[:60]}", flush=True)

                score_fail_reason = ""  # populated on failure before LLM call
                extraction_source = "abstract"  # 'abstract', 'sections', or 'pages'

                if not content:
                    url = paper.get("url", "")
                    if not url:
                        score_fail_reason = "no_url"
                        print(f"[SCORE] No URL for paper", flush=True)
                    elif paper.get("source") == "Local" and os.path.exists(url):
                        # Local PDF — read directly from disk
                        self.progress.emit(f"Reading local: {title[:50]}...")
                        print(f"[SCORE] Reading local file: {url}", flush=True)
                        content, extraction_source = self._extract_abstract_and_intro(url)
                        content = content or ""
                        print(f"[SCORE] Extracted {len(content)} chars via {extraction_source}", flush=True)
                        if not content:
                            score_fail_reason = "no_text"
                    else:
                        # Use pre-resolved PDF URL (resolved in parallel above)
                        resolved = paper.get("_resolved_pdf_url")
                        if resolved:
                            pdf_url = resolved
                        elif is_direct_pdf(url):
                            pdf_url = url
                        else:
                            score_fail_reason = "no_pdf_found"
                            print(f"[SCORE] PDF resolver found nothing (no pre-resolved URL)", flush=True)

                        if not score_fail_reason:
                            self.progress.emit(f"Fetching: {title[:50]}...")
                            print(f"[SCORE] Fetching for scoring: {pdf_url[:80]}", flush=True)
                            try:
                                import requests
                                r = requests.get(pdf_url, timeout=30, stream=True, verify=False,
                                                 allow_redirects=True)
                                ct = r.headers.get("Content-Type", "").lower()
                                print(f"[SCORE] status={r.status_code} ct={ct[:40]}", flush=True)
                                if r.status_code == 200 and "pdf" in ct:
                                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                                        for chunk in r.iter_content(8192):
                                            tf.write(chunk)
                                        temp_file = tf.name
                                    content, extraction_source = self._extract_abstract_and_intro(temp_file)
                                    content = content or ""
                                    print(f"[SCORE] Extracted {len(content)} chars via {extraction_source}", flush=True)
                                    if not content:
                                        score_fail_reason = "no_text"
                                elif r.status_code != 200:
                                    score_fail_reason = f"HTTP {r.status_code}"
                                    print(f"[SCORE] Bad status: {r.status_code}", flush=True)
                                else:
                                    # Got 200 but not a PDF — maybe HTML redirect or paywall
                                    score_fail_reason = f"not_pdf ({ct[:20].strip()})"
                                    print(f"[SCORE] Not a PDF, ct={ct[:40]}", flush=True)
                            except requests.exceptions.ConnectionError as e:
                                score_fail_reason = "conn_failed"
                                print(f"[SCORE] Connection error: {e}", flush=True)
                            except requests.exceptions.Timeout:
                                score_fail_reason = "timeout"
                                print(f"[SCORE] Timeout", flush=True)
                            except Exception as dl_err:
                                score_fail_reason = f"err: {str(dl_err)[:40]}"
                                print(f"[SCORE] Download error: {dl_err}", flush=True)

                if not content:
                    score = -1
                    reason = score_fail_reason or "no_content"
                    print(f"[SCORE] NO CONTENT -> score=-1 reason={reason}", flush=True)
                else:
                    summary = self._summarize_for_scoring(client, content, title)
                    if summary:
                        content = summary

                    prompt_base = self.scoring_prompt
                    for ph, val in [("{context}", self.research_context), ("{intent}", self.intent),
                                     ("{focus_keywords}", self.focus_keywords), ("{avoid_topics}", self.avoid_topics),
                                     ("{title}", title), ("{content}", content[:800]), ("{abstract}", content[:800])]:
                        prompt_base = prompt_base.replace(ph, val or "")
                    score, reason = self._score_with_depth(client, prompt_base, title)
                    if score == -1 and self.scoring_depth > 1:
                        print(f"[SCORE] depth={self.scoring_depth} failed — falling back to single-call", flush=True)
                        score, reason = self._single_call_score(client, prompt_base)
                    if score == -1 and not reason:
                        reason = "LLM did not return a valid score number"
                    if score == -1:
                        kw_score = self._keyword_score(content, title)
                        score = kw_score
                        reason = "keyword fallback — LLM returned no valid score"
                        print(f"[SCORE] Keyword fallback: {kw_score}% for {title[:60]}", flush=True)

                if extraction_source == "pages":
                    reason = f"[2-page extract] {reason}" if reason else "[2-page extract]"
                print(f"[SCORE] FINAL score={score} reason={reason[:60] if reason else ''}", flush=True)
                paper["relevance_score"] = score
                paper["score_reason"] = reason
                self.paper_scored.emit(i, score, reason or "")

                if temp_file:
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass

                _time.sleep(0.5)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit()
        finally:
            if client:
                try:
                    if hasattr(client, '_client') and hasattr(client._client, 'close'):
                        client._client.close()
                except Exception:
                    pass
                import time
                time.sleep(0.5)


class DownloadWorker(QThread):
    """Download paper PDFs sequentially with content-type validation.

    Skips HTML/invalid files, enforces size limits, skips junk content via
    passes_content_filter (unless skip_content_filter is True).

    Signals:
      progress(str)            — Status messages
      paper_done(str, bool, float) — (title, success, size_mb)
      finished()               — All downloads complete
      error(str)               — Fatal error
    """
    progress = Signal(str)
    paper_done = Signal(str, bool, float)
    finished = Signal()
    error = Signal(str)

    def __init__(self, papers: List[dict], target_dir: str, max_size_mb: float = 50.0,
                 skip_content_filter: bool = False):
        """
        :param papers: List of paper dicts with 'url', 'title', 'source' keys
        :param target_dir: Directory to save downloaded PDFs
        :param max_size_mb: Max PDF file size (rejects larger files)
        :param skip_content_filter: If True, skip junk-content validation
        """
        super().__init__()
        self.papers = papers
        self.target_dir = target_dir
        self.max_size_mb = max_size_mb
        self.skip_content_filter = skip_content_filter
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            ok = 0
            fail = 0
            skip = 0
            for i, paper in enumerate(self.papers):
                if self._stop:
                    self.progress.emit("Download cancelled.")
                    break

                title = paper.get("title", "Untitled")
                url = paper.get("url", "")
                paper_id = paper.get("id", "")

                if not url:
                    self.progress.emit(f"  No URL: {title[:70]}")
                    self.paper_done.emit(title, False, 0.0)
                    fail += 1
                    print(f"[DL] {i+1}/{len(self.papers)} NO_URL | {title[:60]}", flush=True)
                    continue

                subfolder = paper.get("output_folder", "default")
                paper_target_dir = os.path.join(self.target_dir, subfolder)
                os.makedirs(paper_target_dir, exist_ok=True)
                max_size = paper.get("max_size_mb", self.max_size_mb)
                force = paper.get("force_download", False)
                must_contain = paper.get("must_contain", [])
                relevance_threshold = paper.get("relevance_threshold", 2)

                registry = Registry(os.path.join(paper_target_dir, "downloads_registry.db"))
                if registry.is_downloaded(paper_id):
                    size_mb = 0.0
                    fpath = registry.get_filepath(paper_id)
                    if fpath and os.path.exists(fpath):
                        size_mb = os.path.getsize(fpath) / (1024 * 1024)
                    self.paper_done.emit(title, True, size_mb)
                    ok += 1
                    print(f"[DL] {i+1}/{len(self.papers)} CACHED | {title[:60]}", flush=True)
                    continue

                downloader = Downloader(registry=registry, max_size_mb=max_size)
                self.progress.emit(f"Downloading: {title[:80]}...")

                filepath = downloader.download(
                    paper_id=paper_id,
                    title=title,
                    url=url,
                    source=paper.get("source", "unknown"),
                    target_dir=paper_target_dir,
                    year=paper.get("year"),
                )

                if filepath and isinstance(filepath, str):
                    if (not force and not self.skip_content_filter and must_contain
                            and not passes_content_filter(filepath, must_contain, relevance_threshold)):
                        self.progress.emit(f"  Content rejected: {title[:70]}")
                        os.remove(filepath)
                        registry.remove_download(paper_id)
                        self.paper_done.emit(title, False, 0.0)
                        fail += 1
                        print(f"[DL] {i+1}/{len(self.papers)} REJECTED | {title[:60]}", flush=True)
                    else:
                        size_mb = os.path.getsize(filepath) / (1024 * 1024)
                        self.progress.emit(f"\u2b07  Downloaded: {os.path.basename(filepath)}  ({size_mb:.1f}MB)")
                        self.paper_done.emit(title, True, size_mb)
                        ok += 1
                        print(f"[DL] {i+1}/{len(self.papers)} OK {size_mb:.1f}MB | {title[:60]}", flush=True)
                elif filepath is True:
                    self.paper_done.emit(title, True, 0.0)
                    ok += 1
                    print(f"[DL] {i+1}/{len(self.papers)} OK (cached) | {title[:60]}", flush=True)
                else:
                    self.progress.emit(f"  Failed: {title[:70]}")
                    self.paper_done.emit(title, False, 0.0)
                    fail += 1
                    print(f"[DL] {i+1}/{len(self.papers)} FAILED | {title[:60]}", flush=True)

            print(f"[DL] DONE: {ok} ok | {fail} failed | {skip} skipped | total={len(self.papers)}", flush=True)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
            traceback.print_exc()



class LLMProcessWorker(QThread):
    """Runs the full 3-pass LLM summarization pipeline for the Generate Reports tab.

    Uses LLMPdfEngine for per-paper analysis and hardcoded prompts for topic/global
    synthesis (overridable via topic_synthesis_prompt / global_synthesis_prompt).

    Pass: 1. Per-paper analysis → 2. Per-topic synthesis → 3. Global cross-topic synthesis

    Signals:
      progress(str)            — Status messages
      paper_processed(str, str) — (title, summary_text)
      topic_summary_done(str, str) — (topic_name, summary_path)
      global_summary_done(str) — Path to GLOBAL_SUMMARY.md
      error(str)               — Fatal error
    """
    progress = Signal(str)
    paper_processed = Signal(str, str)
    topic_summary_done = Signal(str, str)
    global_summary_done = Signal(str)
    error = Signal(str)

    def __init__(self, endpoint: str, model_id: str, prompt_path: str,
                 input_path: str, output_path: str,
                 topic_synthesis_prompt: str = "",
                 global_synthesis_prompt: str = "",
                 max_pages: int = 30, max_chars: int = 60000,
                 research_context: str = "", run_mode: str = "full",
                 force_rerun: bool = False, provider_name: str = "",
                 api_key: str = "not-needed"):
        """
        :param endpoint: OpenAI-compatible API base URL
        :param model_id: Model name for LLM calls
        :param prompt_path: Path to the per-paper analysis prompt template
        :param input_path: Directory with topic subfolders containing PDFs
        :param output_path: Directory for generated summaries
        :param topic_synthesis_prompt: Custom topic synthesis prompt (empty = use hardcoded)
        :param global_synthesis_prompt: Custom global synthesis prompt (empty = use hardcoded)
        :param max_pages: Max PDF pages to extract per paper
        :param max_chars: Max chars to send to LLM per paper
        :param research_context: Additional context appended to prompts
        :param run_mode: "full" = all 3 passes, "per_paper" = only pass 1
        :param force_rerun: If True, re-process even if cached outputs exist
        :param provider_name: Provider identifier
        :param api_key: API key ("not-needed" for local)
        """
        super().__init__()
        self.endpoint = endpoint
        self.model_id = model_id
        self.prompt_path = prompt_path
        self.input_path = input_path
        self.output_path = output_path
        self.max_pages = max_pages
        self.max_chars = max_chars
        self._stop = False
        self._custom_topic_prompt = topic_synthesis_prompt
        self._custom_global_prompt = global_synthesis_prompt
        self._research_context = research_context
        self._run_mode = run_mode
        self._force_rerun = force_rerun
        self.provider_name = provider_name
        self.api_key = api_key

    def stop(self):
        self._stop = True

    def _do_global_synthesis(self, client, topic_summaries):
        global_path = os.path.join(self.output_path, "GLOBAL_SUMMARY.md")
        if not self._force_rerun and os.path.exists(global_path):
            self.progress.emit(f"Global synthesis cached: {global_path}")
            self.global_summary_done.emit(global_path)
            return

        self.progress.emit(f"Global synthesis across {len(topic_summaries)} topics ...")
        print(f"[LLM] Global synthesis across {len(topic_summaries)} topics...", flush=True)
        combined = ""
        for tn, sp in topic_summaries:
            if os.path.exists(sp):
                with open(sp, 'r', encoding='utf-8') as fh:
                    combined += f"\n\n{'='*60}\n## TOPIC: {tn}\n{'='*60}\n\n{fh.read().strip()}"

        global_prompt = self._custom_global_prompt or GLOBAL_SYNTHESIS_PROMPT
        if self._research_context:
            global_prompt = global_prompt + "\n\n" + self._research_context
        truncated_combined = combined[:150000]
        try:
            res = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": global_prompt},
                    {"role": "user", "content": f"TOPIC SUMMARIES:\n\n{truncated_combined}"},
                ],
                temperature=0.0,
                timeout=180.0,
            )
            global_summary = res.choices[0].message.content
            with open(global_path, 'w', encoding='utf-8') as fh:
                fh.write(f"# GLOBAL SUMMARY\nModel: {self.model_id}\nTopics: {len(topic_summaries)}\n\n{global_summary if global_summary else ''}")
            self.global_summary_done.emit(global_path)
            self.progress.emit(f"  Saved global summary: {global_path}")
        except Exception as e:
            self.progress.emit(f"  Global synthesis error: {e}")

    def run(self):
        client = None
        try:
            import pypdf

            client = create_llm_client(self.endpoint, self.api_key, self.provider_name, timeout=120.0)

            self.progress.emit(f"Connecting to {self.endpoint} ...")
            try:
                ok, msg = check_provider_connection(
                    self.provider_name, self.endpoint, self.api_key, self.model_id)
                if ok:
                    self.progress.emit(msg)
                else:
                    self.progress.emit(f"Warning: {msg}")
            except Exception:
                self.progress.emit("Warning: Could not verify connection, proceeding anyway.")

            if not os.path.exists(self.input_path):
                self.error.emit(f"Input directory not found: {self.input_path}")
                return

            if self._run_mode == "related_work_custom":
                path = os.path.join(self.output_path, "RELATED_WORK.md")
                if not self._force_rerun and os.path.isfile(path):
                    self.progress.emit("Related Work section already exists — skipping.")
                    self.global_summary_done.emit(path)
                    return
                self.progress.emit("Generating Related Work section...")
                try:
                    res = client.chat.completions.create(
                        model=self.model_id,
                        messages=[{"role": "user", "content": self._custom_global_prompt}],
                        temperature=0.3,
                        max_tokens=60000,
                        timeout=180.0,
                    )
                    rw_text = res.choices[0].message.content
                    os.makedirs(self.output_path, exist_ok=True)
                    with open(path, 'w', encoding='utf-8') as fh:
                        fh.write(f"# RELATED WORK\n\n{rw_text}")
                    self.global_summary_done.emit(path)
                    self.progress.emit("Related Work section generated.")
                except Exception as e:
                    self.error.emit(f"Related Work error: {e}")
                return

            if self._run_mode == "introduction_custom":
                path = os.path.join(self.output_path, "INTRODUCTION.md")
                if not self._force_rerun and os.path.isfile(path):
                    self.progress.emit("Introduction section already exists — skipping.")
                    self.global_summary_done.emit(path)
                    return
                self.progress.emit("Generating Introduction section...")
                try:
                    res = client.chat.completions.create(
                        model=self.model_id,
                        messages=[{"role": "user", "content": self._custom_global_prompt}],
                        temperature=0.3,
                        max_tokens=60000,
                        timeout=180.0,
                    )
                    intro_text = res.choices[0].message.content
                    os.makedirs(self.output_path, exist_ok=True)
                    with open(path, 'w', encoding='utf-8') as fh:
                        fh.write(f"# INTRODUCTION\n\n{intro_text}")
                    self.global_summary_done.emit(path)
                    self.progress.emit("Introduction section generated.")
                except Exception as e:
                    self.error.emit(f"Introduction error: {e}")
                return

            if self._run_mode == "global_only":
                topic_summaries = []
                for fn in os.listdir(self.output_path):
                    if fn.endswith("_SUMMARY.md"):
                        sp = os.path.join(self.output_path, fn)
                        tn = fn.replace("_SUMMARY.md", "")
                        topic_summaries.append((tn, sp))
                if not topic_summaries:
                    self.error.emit("No topic summaries found for global synthesis.")
                    return
                self._do_global_synthesis(client, topic_summaries)
                return

            model_output_dir = self.output_path
            detailed_dir = os.path.join(model_output_dir, "detailed_topic_reviews")
            os.makedirs(detailed_dir, exist_ok=True)
            self.progress.emit(f"Input: {self.input_path}")
            self.progress.emit(f"Cache dir: {detailed_dir}")
            print(f"[LLMWorker] Input: {self.input_path}", flush=True)
            print(f"[LLMWorker] Cache: {detailed_dir}", flush=True)

            with open(self.prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()

            if self._research_context:
                prompt_template = prompt_template + "\n\n" + self._research_context

            topic_summaries = []
            subfolders = [f.path for f in os.scandir(self.input_path) if f.is_dir()]
            stats = dict(total=0, cached=0, html=0, invalid=0, notext=0, readerr=0,
                         saved=0, empty=0, llerr=0, topics_syn=0)

            for folder_path in subfolders:
                if self._stop:
                    break
                folder_name = os.path.basename(folder_path)
                pdf_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.pdf')]
                stats['total'] += len(pdf_files)
                if not pdf_files:
                    continue

                topic_dir = os.path.join(detailed_dir, folder_name)
                cache_dir = os.path.join(topic_dir, "_cache")
                os.makedirs(cache_dir, exist_ok=True)
                if self._force_rerun:
                    for f in os.listdir(cache_dir):
                        if f.endswith(".skipped"):
                            os.remove(os.path.join(cache_dir, f))

                done = [] if self._force_rerun else [f for f in pdf_files if os.path.exists(
                    os.path.join(cache_dir, os.path.splitext(f)[0] + ".md")) or os.path.exists(
                    os.path.join(cache_dir, os.path.splitext(f)[0] + ".skipped"))]
                todo = [f for f in pdf_files if f not in done]
                stats['cached'] += len(done)

                self.progress.emit(f"Topic '{folder_name}': {len(pdf_files)} PDFs, {len(todo)} to process")

                for pdf_file in todo:
                    if self._stop:
                        break
                    cache_file = os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".md")
                    pdf_path = os.path.join(folder_path, pdf_file)

                    with open(pdf_path, 'rb') as fh:
                        header = fh.read(512)
                    if header.lstrip().startswith(b'<'):
                        self.progress.emit(f"  Skipping HTML: {pdf_file}")
                        stats['html'] += 1
                        with open(os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".skipped"), 'w') as sf:
                            sf.write("HTML file, not a PDF")
                        continue
                    if not header.lstrip().startswith(b'%PDF'):
                        self.progress.emit(f"  Not a valid PDF: {pdf_file}")
                        stats['invalid'] += 1
                        with open(os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".skipped"), 'w') as sf:
                            sf.write("Invalid PDF header")
                        continue

                    try:
                        content = _extract_paper_for_analysis(pdf_path, self.max_pages, self.max_chars)
                    except Exception as e:
                        self.progress.emit(f"  Read error: {pdf_file} - {e}")
                        stats['readerr'] += 1
                        with open(os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".skipped"), 'w') as sf:
                            sf.write(f"Read error: {e}")
                        continue

                    if not content.strip():
                        self.progress.emit(f"  No text: {pdf_file}")
                        stats['notext'] += 1
                        with open(os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".skipped"), 'w') as sf:
                            sf.write("No extractable text")
                        continue

                    self.progress.emit(f"  LLM processing: {pdf_file} ({len(content)} chars)")
                    print(f"[LLM] Calling {self.model_id} for {pdf_file} ({len(content)} chars)...", flush=True)

                    try:
                        res = client.chat.completions.create(
                            model=self.model_id,
                            messages=[
                                {"role": "system", "content": prompt_template.replace("{PDF_FILENAME}", pdf_file)},
                                {"role": "user", "content": f"PROCESS TEXT:\n\n{content}"},
                            ],
                            temperature=0.0,
                            timeout=180.0,
                        )
                        analysis = res.choices[0].message.content
                        if analysis and analysis.strip():
                            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                            with open(cache_file, 'w', encoding='utf-8') as fh:
                                fh.write(analysis)

                            score_match = re.search(r'\*\*Relevance Score:\s*(\d{1,3})/100\*\*', analysis)
                            if score_match:
                                score_val = int(score_match.group(1))
                                scores_path = os.path.join(model_output_dir, "_relevance_scores.json")
                                scores = {}
                                if os.path.isfile(scores_path):
                                    try:
                                        with open(scores_path, 'r', encoding='utf-8') as sf:
                                            scores = json.load(sf)
                                    except Exception:
                                        pass
                                scores[pdf_file] = score_val
                                with open(scores_path, 'w', encoding='utf-8') as sf:
                                    json.dump(scores, sf, indent=2)

                            if os.path.exists(cache_file):
                                sz = os.path.getsize(cache_file)
                                self.paper_processed.emit(pdf_file, folder_name)
                                msg = f"  Saved: {cache_file} ({sz}B)"
                                self.progress.emit(msg)
                                print(msg, flush=True)
                            else:
                                msg = f"  FAILED write: {cache_file}"
                                self.progress.emit(msg)
                                print(msg, flush=True)
                        else:
                            msg = f"  Empty response: {pdf_file}"
                            self.progress.emit(msg)
                            print(msg, flush=True)
                            stats['empty'] += 1
                            with open(os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".skipped"), 'w') as sf:
                                sf.write("LLM returned empty response")
                    except Exception as e:
                        self.progress.emit(f"  LLM error: {pdf_file} - {e}")
                        stats['llerr'] += 1
                        with open(os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".skipped"), 'w') as sf:
                            sf.write(f"LLM error: {e}")

                master_content = ""
                for pdf in sorted(pdf_files):
                    cf = os.path.join(cache_dir, os.path.splitext(pdf)[0] + ".md")
                    if os.path.exists(cf):
                        with open(cf, 'r', encoding='utf-8') as fh:
                            body = fh.read().strip()
                        master_content += f"\n### PAPER: {pdf}\n\n{body}\n\n{'-'*60}\n"

                master_path = os.path.join(topic_dir, "MASTER_REPORT.md")
                with open(master_path, 'w', encoding='utf-8') as fh:
                    fh.write(f"# MASTER REPORT: {folder_name}\nModel: {self.model_id}\nPapers: {len(pdf_files)}\n\n{master_content}")
                self.progress.emit(f"  MASTER_REPORT saved: {master_path}")

                summary_path = os.path.join(model_output_dir, f"{folder_name}_SUMMARY.md")
                if master_content.strip() and not self._stop and self._run_mode in ("topic", "global", "all"):
                    cached_paper_count = -1
                    if not self._force_rerun and os.path.exists(summary_path):
                        try:
                            with open(summary_path, 'r', encoding='utf-8') as sf:
                                for line in sf:
                                    if line.startswith("Papers:"):
                                        cached_paper_count = int(line.split(":")[1].strip())
                                        break
                        except Exception:
                            pass
                        if cached_paper_count == -1:
                            cached_paper_count = len(pdf_files)
                    if cached_paper_count == len(pdf_files):
                        self.progress.emit(f"  Topic synthesis cached: {folder_name}")
                        topic_summaries.append((folder_name, summary_path))
                        self.topic_summary_done.emit(folder_name, summary_path)
                        stats['topics_syn'] += 1
                        continue
                    topic_prompt = self._custom_topic_prompt or TOPIC_SYNTHESIS_PROMPT
                    if self._research_context:
                        topic_prompt = topic_prompt + "\n\n" + self._research_context
                    truncated_master = master_content[:100000]
                    self.progress.emit(f"  Synthesizing topic: {folder_name} ...")
                    print(f"[LLM] Synthesizing topic: {folder_name}...", flush=True)
                    try:
                        res = client.chat.completions.create(
                            model=self.model_id,
                            messages=[
                                {"role": "system", "content": topic_prompt},
                                {"role": "user", "content": f"MASTER REPORT:\n\n{truncated_master}"},
                            ],
                            temperature=0.0,
                            timeout=180.0,
                        )
                        summary = res.choices[0].message.content
                        summary_path = os.path.join(model_output_dir, f"{folder_name}_SUMMARY.md")
                        with open(summary_path, 'w', encoding='utf-8') as fh:
                            fh.write(f"# TOPIC SUMMARY: {folder_name}\nModel: {self.model_id}\nPapers: {len(pdf_files)}\n\n{summary if summary else ''}")
                        topic_summaries.append((folder_name, summary_path))
                        self.topic_summary_done.emit(folder_name, summary_path)
                        self.progress.emit(f"  Saved topic summary: {summary_path}")
                        stats['topics_syn'] += 1
                    except Exception as e:
                        self.progress.emit(f"  Topic synthesis error: {e}")

            if topic_summaries and not self._stop and self._run_mode in ("global", "all"):
                self._do_global_synthesis(client, topic_summaries)

            print(f"[LLMWorker] DONE. Stats: {stats}", flush=True)
            self.progress.emit(f"Stats: {stats}")
        except Exception as e:
            self.error.emit(str(e))
            traceback.print_exc()
        finally:
            if client:
                try:
                    client._client.close()
                except Exception:
                    pass
            import time, gc
            time.sleep(1.0)
            gc.collect()
            print("[LLMWorker] EXIT", flush=True)
