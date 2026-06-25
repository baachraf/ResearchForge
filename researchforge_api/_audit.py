import os
import re
import json
from datetime import datetime
from researchforge_api import _config, _llm, _prompts
from gui import paths
from gui.app_info import APP_VERSION


def detect_sections(pdf_path: str) -> dict:
    """Detect paper sections from PDF. Returns {section_name: section_text}."""
    if not os.path.isfile(pdf_path):
        return {"error": f"File not found: {pdf_path}"}
    try:
        from gui.section_detector import detect_sections as _ds
        sections, method = _ds(pdf_path)
        result = dict(sections)
        result["_method"] = method
        result["_count"] = len(sections)
        return result
    except Exception as e:
        return {"error": str(e)}


def get_section_text(pdf_path: str, section_name: str) -> str:
    """Get the text of a specific section by name (e.g. 'Introduction', 'Methods')."""
    sections = detect_sections(pdf_path)
    if "error" in sections:
        return ""
    for name, text in sections.items():
        if name == "_method" or name == "_count":
            continue
        if section_name.lower() in name.lower():
            return text
    return ""


def _audit_cache_path(pdf_path: str) -> str:
    """Deterministic path where audit_paper persists its full result.

    The audit is a long-running op (N LLM calls); the MCP client often times
    out before the result returns. Writing here (keyed by the source PDF name)
    means the result survives the timeout and can be read back via
    ``get_audit_result`` — mirroring how synthesize_* persist to disk.
    """
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    slug = paths.title_to_slug(base) or "paper"
    slug = re.sub(r'[\\/*?:"<>|]', '_', slug) or "paper"
    return os.path.join(paths.audit_root(_config), f"{slug}_audit.json")


def audit_paper(
    pdf_path: str,
    mode: str = "section",
    progress_callback=None,
) -> dict:
    """Run a 10-dimension IEEE pre-submission audit on a paper.

    The result is persisted to ``<audit_dir>/<slug>_audit.json`` so it survives
    an MCP client timeout; retrieve it afterwards with ``get_audit_result``.
    """
    if not os.path.isfile(pdf_path):
        return {"error": f"File not found: {pdf_path}"}

    result = (_audit_section_by_section(pdf_path, progress_callback)
              if mode == "section"
              else _audit_single_call(pdf_path, progress_callback))

    # Persist the completed audit so the result survives an MCP client timeout.
    # Written in the GUI's researchforge.audit/1 shape (nested paper/audit) so
    # the desktop app's "Load Audit" dialog accepts it directly.
    if isinstance(result, dict) and "error" not in result:
        try:
            cache_path = _audit_cache_path(pdf_path)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            q_list = result.get("questions") or []
            if isinstance(q_list, list):
                q_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(q_list))
            else:
                q_text = str(q_list)
            bundle = {
                "schema": "researchforge.audit/1",
                "app_version": APP_VERSION,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "paper": {
                    "name": os.path.basename(pdf_path),
                    "path": pdf_path,
                    "source_type": "pdf",
                    "title": os.path.splitext(os.path.basename(pdf_path))[0],
                },
                "audit": {
                    "context_index": 1 if mode == "full" else 0,
                    "model": _config.get("llm_model", ""),
                    "endpoint": _config.get("llm_endpoint", ""),
                    "results": result.get("report", ""),
                    "scores": result.get("scores", {}),
                    "questions": q_text,
                },
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(bundle, f, ensure_ascii=False, indent=2)
            result["cache_path"] = cache_path
        except Exception:
            pass
    return result


def get_audit_result(pdf_path: str) -> dict:
    """Read the persisted audit result for a paper.

    Use after ``rf_audit_paper`` times out at the MCP client: the audit still
    completes server-side and writes its result to the deterministic cache path
    (``<audit_dir>/<slug>_audit.json``), so this call returns it without
    re-running any LLM call. Returns an ``error`` dict if no cached audit exists.
    """
    cache_path = _audit_cache_path(pdf_path)
    if not os.path.isfile(cache_path):
        return {"error": f"No cached audit for {pdf_path}. Run rf_audit_paper first."}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}


def _audit_single_call(pdf_path: str, progress_callback=None) -> dict:
    from gui.section_detector import detect_sections as _ds
    sections, _ = _ds(pdf_path)
    pdf_text = "\n\n".join(f"=== {k} ===\n{v}" for k, v in sections.items())

    prompt = _prompts.get_prompt("paper_review_prompt")
    if not prompt:
        return {"error": "paper_review_prompt not found"}

    if progress_callback:
        progress_callback("Running single-call audit...")

    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"PAPER TEXT:\n\n{pdf_text[:60000]}"},
            ],
            temperature=0.0, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass

    scores = _parse_scores(text)
    questions = _parse_questions(text)
    return {
        "report": text.strip(),
        "scores": scores,
        "questions": questions,
        "mode": "single_call",
        "section_count": len(sections),
    }


def _audit_section_by_section(pdf_path: str, progress_callback=None) -> dict:
    from gui.section_detector import detect_sections as _ds
    sections, _ = _ds(pdf_path)

    preaudit_prompt = _prompts.get_prompt("section_preaudit_prompt")
    synthesis_prompt = _prompts.get_prompt("paper_review_synthesis_prompt")
    if not preaudit_prompt or not synthesis_prompt:
        return {"error": "Audit prompts not found"}

    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")
    section_audits = []
    section_results = []

    try:
        for i, (name, body) in enumerate(sections.items()):
            if name == "References":
                continue
            if progress_callback:
                progress_callback(f"Auditing section {i+1}: {name}")

            capped = body[:12000]
            try:
                res = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": preaudit_prompt.replace("{section_name}", name)},
                        {"role": "user", "content": f"SECTION TEXT:\n\n{capped}"},
                    ],
                    temperature=0.0, timeout=120.0,
                )
                audit_text = res.choices[0].message.content or ""
            except Exception as e:
                section_results.append({"section": name, "error": str(e)})
                continue

            section_audits.append(f"=== {name} ===\n{audit_text}")
            section_results.append({"section": name, "chars": len(body), "status": "done"})

        if progress_callback:
            progress_callback("Synthesizing audit report...")

        combined = "\n\n".join(section_audits)
        res = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": synthesis_prompt},
                {"role": "user", "content": f"SECTION AUDITS:\n\n{combined[:60000]}"},
            ],
            temperature=0.0, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
    except Exception as e:
        return {"error": str(e), "sections_done": section_results}
    finally:
        try:
            client._client.close()
        except Exception:
            pass

    scores = _parse_scores(text)
    questions = _parse_questions(text)
    return {
        "report": text.strip(),
        "scores": scores,
        "questions": questions,
        "mode": "section_by_section",
        "sections_audited": len(section_results),
        "section_details": section_results,
    }


def _parse_scores(text: str) -> dict:
    m = re.search(r'\bSCORES\b(.*?)\bEND_SCORES\b', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return {}
    block = m.group(1).strip()
    scores = {}
    for line in block.splitlines():
        parts = line.strip().split(":", 1)
        if len(parts) == 2:
            key = parts[0].strip().title()
            val = parts[1].strip()
            try:
                scores[key] = int(re.search(r'\d+', val).group(0))
            except Exception:
                scores[key] = val
    return scores


def save_audit_results(
    report: str,
    pdf_path: str = "",
    output_dir: str = "",
    *,
    scores: dict | None = None,
    questions: list | None = None,
    questions_text: str = "",
    model: str = "",
    endpoint: str = "",
    paper_title: str = "",
    source_type: str = "pdf",
    context_index: int = 0,
    save_name: str = "",
) -> dict:
    """Save an audit report as a GUI-loadable bundle.

    Writes two files to ``<audit_output_dir>/``:
      * ``<save_name>.json`` — a ``researchforge.audit/1`` bundle identical in
        schema to what the GUI's "Save Results" writes, so it reappears in the
        GUI's "Load Audit" dialog.
      * ``<save_name>.md`` — a human-readable copy.

    ``save_name`` defaults to ``paths.audit_filename(paper_title, source_type)``
    so API-saved audits are named identically to GUI-saved ones.

    Returns ``{"json_path": ..., "md_path": ...}``.
    """
    out_root = output_dir if output_dir else paths.audit_root(_config)
    os.makedirs(out_root, exist_ok=True)

    now = datetime.now()
    base = save_name.strip() or paths.audit_filename(paper_title, source_type=source_type, now=now)
    # Strip any caller-supplied extension so we control both .json and .md.
    for ext in (".json", ".md"):
        if base.lower().endswith(ext):
            base = base[:-len(ext)]

    paper_name = os.path.basename(pdf_path) if pdf_path else ""
    # questions_text takes precedence (raw block); fall back to joining the list.
    qtext = questions_text or ""
    if not qtext and questions:
        qtext = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))

    bundle = {
        "schema": "researchforge.audit/1",
        "app_version": APP_VERSION,
        "saved_at": now.isoformat(timespec="seconds"),
        "paper": {
            "name": paper_name,
            "path": pdf_path or "",
            "source_type": source_type,
            "title": paper_title or paper_name,
        },
        "audit": {
            "context_index": context_index,
            "model": model or _config.get("llm_model", ""),
            "endpoint": endpoint or _config.get("llm_endpoint", ""),
            "results": report,
            "scores": scores or {},
            "questions": qtext,
        },
    }

    json_path = os.path.join(out_root, base + ".json")
    md_path = os.path.join(out_root, base + ".md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_render_audit_markdown(report, paper_name, source_type, qtext, now))
    return {"json_path": json_path, "md_path": md_path}


def _render_audit_markdown(report: str, paper_name: str, source_type: str,
                           questions_text: str, now: datetime) -> str:
    """Human-readable markdown copy mirroring the GUI's ``_render_markdown``."""
    src_label = "LaTeX source" if source_type == "latex" else "PDF"
    header = (
        f"# Audit Report\n"
        f"**File:** {paper_name or 'unknown'}  ({src_label})\n"
        f"**Date:** {now.strftime('%A, %d %B %Y  %H:%M')}\n\n"
        + "━" * 60 + "\n\n"
    )
    text = header + (report or "").strip()
    if questions_text:
        text = (text + "\n\n" + "━" * 60
                + "\nQUESTIONS FOR THE AUTHOR\n" + "━" * 60 + "\n"
                + questions_text.strip())
    return text


def _parse_questions(text: str) -> list[str]:
    m = re.search(r'QUESTIONS FOR THE AUTHOR\b(.*?)(?:SCORES|\Z)', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    block = m.group(1).strip()
    questions = []
    for line in block.splitlines():
        line = line.strip()
        if line and (line[0].isdigit() or line.startswith("- ")):
            questions.append(re.sub(r'^\d+\.\s*', '', line).strip().lstrip("- "))
    return [q for q in questions if len(q) > 10]
