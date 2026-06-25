import os
import re
import tempfile
import requests
import urllib3
from typing import Optional
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from researchforge_api import _config, _llm, _prompts
from research_downloader.pdf_resolver import resolve_pdf_url, is_direct_pdf


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

    prompt_template = _prompts.get_prompt("rate_relevance_prompt") or FALLBACK_SCORING
    if prompt_template.strip():
        pl = prompt_template.strip().lower()
        if "research:" not in pl or "paper:" not in pl or pl.find("research:") < pl.find("paper:"):
            prompt_template = FALLBACK_SCORING

    results = []

    for i, paper in enumerate(papers):
        if progress_callback:
            progress_callback(f"Scoring {i+1}/{len(papers)}: {paper.get('title', 'Untitled')[:60]}")

        title = paper.get("title", "Untitled")
        content = paper.get("abstract", "")
        reason = ""
        score = -1

        if not content:
            url = paper.get("url", "")
            if paper.get("source") == "Local" and os.path.exists(url):
                content, _ = _extract_abstract_and_intro(url)
            elif url:
                resolved = paper.get("_resolved_pdf_url") or (resolve_pdf_url(url, 12) if not is_direct_pdf(url) else None)
                pdf_url = resolved or (url if is_direct_pdf(url) else None)
                if pdf_url:
                    try:
                        r = requests.get(pdf_url, timeout=30, stream=True, verify=False, allow_redirects=True)
                        ct = r.headers.get("Content-Type", "").lower()
                        if r.status_code == 200 and "pdf" in ct:
                            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                                for chunk in r.iter_content(8192):
                                    tf.write(chunk)
                                temp_path = tf.name
                            content, _ = _extract_abstract_and_intro(temp_path)
                            try:
                                os.remove(temp_path)
                            except Exception:
                                pass
                    except Exception:
                        pass

        if not content:
            score = -1
            reason = "no_content"
        else:
            if len(content) > 600:
                try:
                    summary_prompt = (
                        f"Summarize this paper's core elements in 2-3 sentences:\n"
                        f"1) Problem it solves\n2) Method used\n3) Domain/field\n\n"
                        f"Title: {title}\nContent: {content[:2000]}\n\nConcise summary:"
                    )
                    res = client.chat.completions.create(
                        model=model, messages=[{"role": "user", "content": summary_prompt}],
                        temperature=0.1, max_tokens=1024, timeout=20.0,
                    )
                    s = res.choices[0].message.content
                    if s and s.strip():
                        content = s.strip()[:500]
                except Exception:
                    pass

            filled = prompt_template
            for ph, val in [("{context}", research_context), ("{intent}", intent),
                            ("{focus_keywords}", focus_keywords), ("{avoid_topics}", avoid_topics),
                            ("{title}", title), ("{content}", content[:800]), ("{abstract}", content[:800])]:
                filled = filled.replace(ph, val or "")

            try:
                res = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": filled}],
                    temperature=0.1, max_tokens=1024,
                )
                text = res.choices[0].message.content or ""
                text = text.strip()
                nums = re.findall(r'\b(\d{1,3})\b', text)
                score = int(nums[-1]) if nums else -1
                if score > 100:
                    score = -1
                match = re.search(r'\b(\d{1,3})\b(.*)', text)
                if match and len(match.group(2).strip()) > 2:
                    reason = match.group(2).strip()[:120]
            except Exception as e:
                score = -1
                reason = f"LLM error: {str(e)[:80]}"

            if score == -1:
                score = _keyword_score(research_context, focus_keywords, avoid_topics, content, title)
                reason = reason or "keyword fallback"

        paper["relevance_score"] = score
        paper["score_reason"] = reason
        results.append(dict(paper=paper, score=score, reason=reason))

    try:
        client._client.close()
    except Exception:
        pass
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
