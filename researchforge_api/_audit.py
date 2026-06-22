import os
import re
from researchforge_api import _config, _llm, _prompts


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


def audit_paper(
    pdf_path: str,
    mode: str = "section",
    progress_callback=None,
) -> dict:
    """Run a 10-dimension IEEE pre-submission audit on a paper."""
    if not os.path.isfile(pdf_path):
        return {"error": f"File not found: {pdf_path}"}

    if mode == "section":
        return _audit_section_by_section(pdf_path, progress_callback)
    else:
        return _audit_single_call(pdf_path, progress_callback)


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
) -> str:
    """Save audit results to a Markdown file. Returns the filepath."""
    if not output_dir:
        output_dir = os.path.join(_config.get_app_data_dir(), "audit_results")
    os.makedirs(output_dir, exist_ok=True)

    from datetime import datetime
    now = datetime.now()
    day_name = now.strftime("%A")
    date_part = now.strftime("%d%b%Y_%H%M%S")

    title = ""
    if pdf_path:
        title = os.path.splitext(os.path.basename(pdf_path))[0]
        title = "".join(c if c.isalnum() else "_" for c in title)[:60]
        title = title.strip("_")

    if title:
        filename = f"{title}_{day_name}_{date_part}.md"
    else:
        filename = f"Audit_{day_name}_{date_part}.md"

    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Paper Audit\nDate: {now.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(report)
    return filepath


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
