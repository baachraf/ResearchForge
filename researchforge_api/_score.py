import sys
import json
import os
import re
import tempfile
import requests
import urllib3
from typing import Optional
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from researchforge_api import _config, _llm, _prompts
from research_downloader.pdf_resolver import resolve_pdf_url, is_direct_pdf


def _log(msg: str):
    sys.stderr.write(f"[ResearchForge] {msg}\n")
    sys.stderr.flush()


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


def _extract_abstract_and_intro(pdf_path: str) -> tuple[str, str]:
    try:
        from gui.section_detector import detect_sections
        sections, _ = detect_sections(pdf_path)
        text = ""
        for name, body in sections.items():
            if any(t in name.lower() for t in ("abstract", "introduction")):
                text += body + "\n\n"
        if len(text.strip()) >= 200:
            return text.strip().lower(), "sections"
    except Exception:
        pass

    try:
        import fitz
        doc = fitz.open(pdf_path)
        try:
            text = ""
            for i in range(min(2, len(doc))):
                text += doc[i].get_text()
            return text.lower() if text else "", "pages"
        finally:
            doc.close()
    except Exception:
        pass
    return "", "none"


# The relevance prompt ends with "Return ONLY an integer", so a plain call yields
# no reasoning and the GUI's Match tooltip stays blank. The GUI's own worker
# (gui/workers.py) sidesteps this by appending a reason request and parsing a
# dash-separated reason. We mirror that here so MCP-driven scoring populates
# score_reason too (open-issue #5).
_REASON_INSTRUCTION = (
    "\n\nOutput: <score 0-100> then a dash then a one-sentence reason for the "
    "score. Example: '75 - same compression-artifact domain but targets images "
    "not biosignals'"
)


def _parse_score_reason(text: str) -> tuple[int, str]:
    """Parse '<score> - <reason>' from an LLM reply. Mirrors the dash-parse in
    gui/workers.py so API and GUI extract the same score/reason."""
    text = (text or "").strip()
    nums = re.findall(r'\b(\d{1,3})\b', text)
    score = int(nums[-1]) if nums else -1
    if score > 100:
        score = -1
    reason = ""
    for dash in ("—", "--", "- "):
        if dash in text:
            after = text.split(dash, 1)[1].strip()
            if after:
                reason = after[:120]
            break
    if not reason and nums:
        remainder = text[text.rfind(str(nums[-1])) + len(str(nums[-1])):].strip().lstrip(".-—: ")[:120]
        if remainder:
            reason = remainder
    return score, reason


def _band_reason(score: int) -> str:
    """Fallback reason describing the relevance band (matches the prompt's
    0-100 bands), used when the model returned a bare integer with no prose."""
    if score >= 90:
        return "same problem, same domain, similar methods"
    if score >= 70:
        return "same problem, same domain, different approach"
    if score >= 50:
        return "related sub-problem in same domain"
    if score >= 25:
        return "same broad domain, different specific problem"
    return "different problem"


def _keyword_score(research_context: str, focus_keywords: str, avoid_topics: str,
                   paper_text: str, title: str) -> int:
    def _terms(t):
        return set(re.findall(r'[a-z0-9]{3,}', t.lower()))
    ctx_terms = _terms(research_context)
    focus_terms = _terms(focus_keywords)
    paper_terms = _terms(title + " " + paper_text)
    if not ctx_terms and not focus_terms:
        return 25
    ctx_overlap = len(ctx_terms & paper_terms) / max(len(ctx_terms), 1) if ctx_terms else 0
    focus_overlap = len(focus_terms & paper_terms) / max(len(focus_terms), 1) if focus_terms else 0
    avoid_terms = _terms(avoid_topics)
    if avoid_terms:
        avoid_hits = len(avoid_terms & paper_terms) / max(len(avoid_terms), 1)
        ctx_overlap -= avoid_hits * 0.5
    raw = int(ctx_overlap * 60 + focus_overlap * 40)
    return max(0, min(100, raw))


def _score_batch_llm(client, model: str, batch: list[dict], research_context: str, intent: str, focus_keywords: str, avoid_topics: str) -> dict[int, tuple[int, str]]:
    """Score a batch of up to 15 papers in ONE LLM call. Returns dict mapping paper index in batch -> (score, reason)."""
    papers_text = []
    for idx, p in enumerate(batch):
        t = p.get("title", "Untitled")
        c = (p.get("abstract") or p.get("content") or "")[:500]
        papers_text.append(f"[{idx+1}] Title: {t}\nContent: {c}")

    prompt = (
        "You are an expert research relevance evaluator. Rate each paper's relevance (0-100) to the research context below.\n\n"
        f"RESEARCH CONTEXT:\nContext: {research_context}\nIntent: {intent}\nKeywords: {focus_keywords}\nAvoid: {avoid_topics}\n\n"
        "SCORING BANDS (0-100):\n"
        "- 90-100: Same core problem, same domain, similar methods\n"
        "- 70-89: Same core problem, same domain, different approach\n"
        "- 50-69: Related sub-problem in same domain\n"
        "- 25-49: Same broad domain, different specific problem\n"
        "- 0-24: Different problem entirely (<=15 if covers avoid topics)\n"
        "PROBLEM MATCH IS THE GATE. If the problem is different, score <=10 regardless of shared techniques.\n\n"
        "PAPERS TO SCORE:\n" + "\n\n".join(papers_text) + "\n\n"
        "Respond ONLY with a valid JSON array containing an object for each paper:\n"
        '[{"id": 1, "score": 85, "reason": "1-sentence reason"}, {"id": 2, "score": 30, "reason": "1-sentence reason"}]'
    )

    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=2048, timeout=40.0,
        )
        text = res.choices[0].message.content or ""
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            out = {}
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "id" in item:
                        try:
                            pid = int(item["id"]) - 1
                            sc = int(item.get("score", -1))
                            rs = str(item.get("reason", "")).strip()
                            if 0 <= sc <= 100 and 0 <= pid < len(batch):
                                out[pid] = (sc, rs or _band_reason(sc))
                        except Exception:
                            pass
            return out
    except Exception as e:
        _log(f"Batch LLM scoring warning: {e}")
    return {}


def score_papers(
    papers: list[dict],
    research_context: str = "",
    intent: str = "",
    focus_keywords: str = "",
    avoid_topics: str = "",
    scoring_depth: int = 1,
    progress_callback=None,
) -> list[dict]:
    """Score each paper's relevance to the research context. Returns list of (paper, score, reason)."""
    client = _llm.create_client_from_config(timeout=120.0)
    model = _config.get("llm_model", "")

    _log(f"Scoring {len(papers)} papers (depth={scoring_depth}, model={model or 'default'})...")

    # Extract/prepare missing local content
    for paper in papers:
        if not paper.get("abstract") and not paper.get("content"):
            url = paper.get("url", "")
            if paper.get("source") == "Local" and os.path.exists(url):
                c, _ = _extract_abstract_and_intro(url)
                if c:
                    paper["content"] = c

    batch_size = 15
    results = []
    total_batches = (len(papers) + batch_size - 1) // max(batch_size, 1)

    for b_idx, b_start in enumerate(range(0, len(papers), batch_size)):
        b_end = min(b_start + batch_size, len(papers))
        chunk = papers[b_start:b_end]

        _log(f"Scoring batch {b_idx + 1}/{total_batches} (papers {b_start + 1}-{b_end} of {len(papers)})...")
        if progress_callback:
            progress_callback(f"Scoring papers {b_start + 1}-{b_end} of {len(papers)}...")

        scores_dict = _score_batch_llm(client, model, chunk, research_context, intent, focus_keywords, avoid_topics)

        for idx, paper in enumerate(chunk):
            title = paper.get("title", "Untitled")
            content = paper.get("abstract") or paper.get("content") or ""

            if idx in scores_dict:
                score, reason = scores_dict[idx]
            elif not content:
                score, reason = -1, "no_content"
            else:
                score = _keyword_score(research_context, focus_keywords, avoid_topics, content, title)
                reason = _band_reason(score) + " (keyword)"

            paper["relevance_score"] = score
            paper["score_reason"] = reason
            results.append(dict(paper=paper, score=score, reason=reason))

    try:
        client._client.close()
    except Exception:
        pass

    _log(f"Scoring complete: {len(results)} papers scored.")
    return results


def score_session(session_id: str,
                  paper_ids: Optional[list] = None,
                  scoring_depth: int = 1,
                  progress_callback=None) -> dict:
    """Score a session's results against its own research context and persist the
    scores onto the session (so a later reload keeps the ranking the GUI would).

    Scores the session's result dicts in place using context/intent/keywords from
    the session itself, then saves. ``paper_ids=None`` scores all results.
    """
    from researchforge_api import _sessions
    session = _sessions.load_session(session_id)
    if not session:
        return {"error": f"Session '{session_id}' not found"}
    session = _sessions.ensure_full_schema(session)

    results = session.get("results", [])
    targets = [r for r in results
               if paper_ids is None or r.get("id") in paper_ids]
    if not targets:
        # Nothing matched. Do NOT save — saving the loaded copy here is how a
        # stale/empty session used to clobber results added by an interleaved
        # search. Return a diagnostic instead.
        return {"session_id": session_id, "scored": 0, "total": len(results),
                "saved": False,
                "note": ("no results matched paper_ids; session left unmodified"
                         if paper_ids is not None else "session has no results")}

    # score_papers mutates each dict in place; targets are references into
    # session["results"].
    score_papers(
        targets,
        research_context=session.get("context", ""),
        intent=session.get("intent", ""),
        focus_keywords=session.get("focus_keywords", ""),
        avoid_topics=session.get("avoid_topics", ""),
        scoring_depth=scoring_depth,
        progress_callback=progress_callback,
    )

    # Persist by id onto a FRESH reload, so any results added between our load
    # and now (e.g. by a concurrent search) are preserved rather than clobbered.
    score_map = {r.get("id"): (r.get("relevance_score"), r.get("score_reason", ""))
                 for r in targets
                 if r.get("id") is not None and r.get("relevance_score", -1) >= 0}
    fresh = _sessions.ensure_full_schema(_sessions.load_session(session_id) or session)
    applied = 0
    for r in fresh.get("results", []):
        sc = score_map.get(r.get("id"))
        if sc is not None:
            r["relevance_score"], r["score_reason"] = sc[0], sc[1]
            applied += 1
    _sessions.save_session(session_id, fresh)

    scored = [{"id": r.get("id"), "title": r.get("title", ""),
               "relevance_score": r.get("relevance_score")} for r in targets]
    return {"session_id": session_id, "scored": applied,
            "matched": len(targets), "total": len(fresh.get("results", [])),
            "saved": True, "results": scored}
