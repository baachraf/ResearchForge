import os
import re
import json
from researchforge_api import _config, _llm, _prompts

from llm_pdf_engine import (
    TOPIC_SYNTHESIS_PROMPT, GLOBAL_SYNTHESIS_PROMPT, PAPER_SEPARATOR,
)


def _extract_paper_text(pdf_path: str, max_pages: int = 30, max_chars: int = 60000) -> str:
    try:
        from gui.section_detector import detect_sections
        sections, _ = detect_sections(pdf_path)
        if len(sections) >= 3:
            ref_text = sections.get("References", "")
            body_text = "\n\n".join(f"=== {k} ===\n{v}" for k, v in sections.items() if k != "References")
            ref_budget = min(len(ref_text), max_chars // 4)
            body_budget = max_chars - ref_budget
            content = body_text[:body_budget]
            if ref_text:
                content += f"\n\n=== References ===\n{ref_text[:ref_budget]}"
            return content
    except Exception:
        pass

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
    back = max_chars - front
    return text[:front] + "\n\n[...]\n\n" + text[-back:]


def analyze_paper(
    pdf_path: str,
    prompt_key: str = "per_paper_prompt",
    max_pages: int = 30,
    max_chars: int = 60000,
) -> dict:
    """Analyze a single PDF. Returns dict with 'title', 'analysis', 'error'."""
    if not os.path.isfile(pdf_path):
        return {"error": f"File not found: {pdf_path}", "title": os.path.basename(pdf_path)}

    title = os.path.basename(pdf_path)
    prompt_text = _prompts.get_prompt(prompt_key)
    if not prompt_text:
        return {"error": f"Prompt key '{prompt_key}' not found", "title": title}

    content = _extract_paper_text(pdf_path, max_pages, max_chars)
    if not content.strip():
        return {"error": "No extractable text", "title": title}

    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")

    try:
        res = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt_text.replace("{PDF_FILENAME}", title)},
                {"role": "user", "content": f"PROCESS TEXT:\n\n{content[:50000]}"},
            ],
            temperature=0.0, timeout=180.0,
        )
        analysis = res.choices[0].message.content
    except Exception as e:
        return {"error": str(e), "title": title}
    finally:
        try:
            client._client.close()
        except Exception:
            pass

    score = None
    m = re.search(r'\*\*Relevance Score:\s*(\d{1,3})/100\*\*', analysis or "")
    if m:
        score = int(m.group(1))

    return {
        "title": title,
        "analysis": analysis.strip() if analysis else "",
        "score": score,
        "char_count": len(content),
    }


def synthesize_topic(
    input_dir: str,
    output_dir: str = "",
    prompt_key: str = "topic_synthesis_prompt",
    progress_callback=None,
) -> dict:
    """Synthesize all PDFs in a folder into a topic summary. Returns dict with output paths."""
    if not os.path.isdir(input_dir):
        return {"error": f"Not a directory: {input_dir}"}

    folder_name = os.path.basename(input_dir)
    if not output_dir:
        base = _config.get("summary_output_dir", os.path.join(_config.get_app_data_dir(), "summaries"))
        output_dir = os.path.join(base, "synthesis")

    os.makedirs(output_dir, exist_ok=True)
    cache_dir = os.path.join(output_dir, "_cache")
    os.makedirs(cache_dir, exist_ok=True)

    pdf_files = [f for f in os.listdir(input_dir) if f.lower().endswith('.pdf')]
    if not pdf_files:
        return {"error": "No PDFs found", "topic": folder_name}

    prompt_template = _prompts.get_prompt("per_paper_prompt")
    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")

    cached = 0
    processed = 0
    errors = 0

    for pdf_file in pdf_files:
        cache_file = os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".md")
        if os.path.exists(cache_file):
            cached += 1
            continue

        pdf_path = os.path.join(input_dir, pdf_file)
        content = _extract_paper_text(pdf_path)
        if not content.strip():
            errors += 1
            continue

        if progress_callback:
            progress_callback(f"Analyzing: {pdf_file}")
        try:
            res = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt_template.replace("{PDF_FILENAME}", pdf_file)},
                    {"role": "user", "content": f"PROCESS TEXT:\n\n{content[:50000]}"},
                ],
                temperature=0.0, timeout=180.0,
            )
            analysis = res.choices[0].message.content
            if analysis and analysis.strip():
                with open(cache_file, "w", encoding="utf-8") as f:
                    f.write(analysis)
                processed += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    try:
        client._client.close()
    except Exception:
        pass

    master_content = ""
    for pdf in sorted(pdf_files):
        cf = os.path.join(cache_dir, os.path.splitext(pdf)[0] + ".md")
        if os.path.exists(cf):
            with open(cf, "r", encoding="utf-8") as f:
                master_content += f"\n### PAPER: {pdf}\n\n{f.read().strip()}\n\n{'-'*60}\n"

    master_path = os.path.join(output_dir, "MASTER_REPORT.md")
    with open(master_path, "w", encoding="utf-8") as f:
        f.write(f"# MASTER REPORT: {folder_name}\nModel: {model}\nPapers: {len(pdf_files)}\n\n{master_content}")

    summary_path = None
    if master_content.strip():
        topic_prompt = _prompts.get_prompt(prompt_key) or TOPIC_SYNTHESIS_PROMPT
        if progress_callback:
            progress_callback(f"Synthesizing topic: {folder_name}")

        client2 = _llm.create_client_from_config(timeout=180.0)
        try:
            res = client2.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": topic_prompt},
                    {"role": "user", "content": f"MASTER REPORT:\n\n{master_content[:100000]}"},
                ],
                temperature=0.0, timeout=180.0,
            )
            summary = res.choices[0].message.content
            summary_path = os.path.join(output_dir, f"{folder_name}_SUMMARY.md")
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(f"# TOPIC SUMMARY: {folder_name}\nModel: {model}\nPapers: {len(pdf_files)}\n\n{summary or ''}")
        except Exception as e:
            return {"error": f"Topic synthesis failed: {e}", "topic": folder_name}
        finally:
            try:
                client2._client.close()
            except Exception:
                pass

    return {
        "topic": folder_name,
        "total": len(pdf_files),
        "cached": cached,
        "processed": processed,
        "errors": errors,
        "master_report": master_path,
        "summary": summary_path,
    }


def synthesize_global(
    output_dir: str = "",
    prompt_key: str = "global_synthesis_prompt",
    progress_callback=None,
) -> dict:
    """Global cross-topic synthesis across all topic summaries in output_dir."""
    if not output_dir:
        base = _config.get("summary_output_dir", os.path.join(_config.get_app_data_dir(), "summaries"))
        output_dir = os.path.join(base, "synthesis")

    if not os.path.isdir(output_dir):
        return {"error": f"Directory not found: {output_dir}"}

    topic_summaries = []
    for fn in os.listdir(output_dir):
        if fn.endswith("_SUMMARY.md"):
            sp = os.path.join(output_dir, fn)
            tn = fn.replace("_SUMMARY.md", "")
            topic_summaries.append((tn, sp))

    if not topic_summaries:
        return {"error": "No topic summaries found"}

    combined = ""
    for tn, sp in topic_summaries:
        if os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as f:
                combined += f"\n\n{'='*60}\n## TOPIC: {tn}\n{'='*60}\n\n{f.read().strip()}"

    if not combined.strip():
        return {"error": "No summary content"}

    global_prompt = _prompts.get_prompt(prompt_key) or GLOBAL_SYNTHESIS_PROMPT
    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")

    try:
        if progress_callback:
            progress_callback("Running global synthesis...")
        res = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": global_prompt},
                {"role": "user", "content": f"TOPIC SUMMARIES:\n\n{combined[:150000]}"},
            ],
            temperature=0.0, timeout=180.0,
        )
        global_summary = res.choices[0].message.content
        global_path = os.path.join(output_dir, "GLOBAL_SUMMARY.md")
        with open(global_path, "w", encoding="utf-8") as f:
            f.write(f"# GLOBAL SUMMARY\nModel: {model}\nTopics: {len(topic_summaries)}\n\n{global_summary or ''}")
        return {"global_summary": global_path, "topics": len(topic_summaries)}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass


def generate_queries(
    research_description: str,
    prompt_key: str = "query_generation_prompt",
) -> dict:
    """Generate search queries from a natural-language research description."""
    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    client = _llm.create_client_from_config(timeout=120.0)
    model = _config.get("llm_model", "")
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt + "\n\n" + research_description},
            ],
            temperature=0.3, timeout=120.0,
        )
        text = res.choices[0].message.content or ""
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass

    queries = []
    try:
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "query" in item:
                        queries.append({
                            "query": item["query"],
                            "class": item.get("class", ""),
                            "rationale": item.get("rationale", ""),
                        })
    except Exception:
        pass

    return {"raw": text, "queries": queries, "count": len(queries)}


def generate_related_work(
    output_dir: str = "",
    prompt_key: str = "related_work_prompt",
) -> dict:
    """Generate a Related Work section from existing summaries."""
    if not output_dir:
        base = _config.get("summary_output_dir", os.path.join(_config.get_app_data_dir(), "summaries"))
        output_dir = os.path.join(base, "synthesis")
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "RELATED_WORK.md")

    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    context = _config.get("our_work_context", "")
    if context:
        prompt = prompt + "\n\n" + context

    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=60000, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# RELATED WORK\n\n{text}")
        return {"path": out_path, "chars": len(text)}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass


def generate_introduction(
    output_dir: str = "",
    prompt_key: str = "introduction_prompt",
) -> dict:
    """Generate an Introduction section from existing summaries."""
    if not output_dir:
        base = _config.get("summary_output_dir", os.path.join(_config.get_app_data_dir(), "summaries"))
        output_dir = os.path.join(base, "synthesis")
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "INTRODUCTION.md")

    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    context = _config.get("our_work_context", "")
    if context:
        prompt = prompt + "\n\n" + context

    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=60000, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# INTRODUCTION\n\n{text}")
        return {"path": out_path, "chars": len(text)}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass


def analyze_own_paper(
    pdf_path: str,
    prompt_key: str = "analyze_own_paper_prompt",
) -> dict:
    """Analyze user's own paper to extract claims, results, comparisons."""
    if not os.path.isfile(pdf_path):
        return {"error": f"File not found: {pdf_path}"}

    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    content = _extract_paper_text(pdf_path)
    if not content.strip():
        return {"error": "No extractable text"}

    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"PAPER TEXT:\n\n{content[:50000]}"},
            ],
            temperature=0.0, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
        return {"analysis": text.strip(), "title": os.path.basename(pdf_path)}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass


def list_summaries(summary_dir: str = "") -> list[dict]:
    """List generated summaries. Returns dicts with path, type, topic, size."""
    if not summary_dir:
        summary_dir = _config.get("summary_output_dir", os.path.join(_config.get_app_data_dir(), "summaries"))
    if not os.path.isdir(summary_dir):
        return []
    results = []
    for root, dirs, files in os.walk(summary_dir):
        for fname in files:
            path = os.path.join(root, fname)
            ftype = ""
            if fname == "GLOBAL_SUMMARY.md":
                ftype = "global"
            elif fname.endswith("_SUMMARY.md"):
                ftype = "topic"
            elif fname == "MASTER_REPORT.md":
                ftype = "master"
            elif fname == "RELATED_WORK.md":
                ftype = "related_work"
            elif fname == "INTRODUCTION.md":
                ftype = "introduction"
            elif fname.endswith(".md"):
                ftype = "per_paper"
            else:
                continue
            rel = os.path.relpath(root, summary_dir)
            topic = rel.split(os.sep)[-1] if rel != "." else ""
            results.append({
                "path": path, "name": fname, "type": ftype,
                "topic": topic, "size": os.path.getsize(path),
            })
    return results


def get_cached_analysis(cache_path: str) -> str:
    """Read a cached analysis file and return its content."""
    if not os.path.isfile(cache_path):
        return ""
    with open(cache_path, "r", encoding="utf-8") as f:
        return f.read()


def enhance_research(
    research_description: str,
    prompt_key: str = "enhance_research_prompt",
) -> dict:
    """Enhance research description — split into CONTRIBUTION and PROBLEM SPACE."""
    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    client = _llm.create_client_from_config(timeout=120.0)
    model = _config.get("llm_model", "")
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt + "\n\n" + research_description}],
            temperature=0.3, timeout=120.0,
        )
        text = res.choices[0].message.content or ""
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass

    contribution = ""
    problem_space = ""
    if "CONTRIBUTION:" in text and "PROBLEM SPACE:" in text:
        parts = text.split("PROBLEM SPACE:", 1)
        contribution = parts[0].replace("CONTRIBUTION:", "").strip()
        problem_space = parts[1].strip()
    else:
        contribution = text

    return {"contribution": contribution, "problem_space": problem_space, "raw": text}


def run_full_pipeline(
    input_dir: str,
    output_dir: str = "",
    progress_callback=None,
) -> dict:
    """Run the complete 3-pass pipeline: per-paper → topic synthesis → global synthesis."""
    if not output_dir:
        base = _config.get("summary_output_dir", os.path.join(_config.get_app_data_dir(), "summaries"))
        output_dir = os.path.join(base, "synthesis")

    results = {"per_paper": {}, "topics": [], "global": None}

    if progress_callback:
        progress_callback("Step 1: Per-paper analysis + topic synthesis...")

    if os.path.isdir(input_dir):
        subfolders = [f.path for f in os.scandir(input_dir) if f.is_dir()]
        for folder_path in subfolders:
            folder_name = os.path.basename(folder_path)
            if progress_callback:
                progress_callback(f"  Processing topic: {folder_name}")
            topic_result = synthesize_topic(folder_path, output_dir, progress_callback=progress_callback)
            results["topics"].append({"name": folder_name, "result": topic_result})
    else:
        return {"error": f"Input directory not found: {input_dir}"}

    if results["topics"] and not any(t["result"].get("error") for t in results["topics"]):
        if progress_callback:
            progress_callback("Step 2: Global synthesis...")
        global_result = synthesize_global(output_dir, progress_callback=progress_callback)
        results["global"] = global_result

    return results


def create_session_full(
    name: str,
    research_description: str,
    focus_keywords: str = "",
    topic: str = "General",
) -> dict:
    """Full session creation flow: enhance → generate queries → save session.
    Returns the created session dict."""
    from researchforge_api import _sessions

    enhanced = {}
    queries = {}
    try:
        enhanced = enhance_research(research_description)
    except Exception as e:
        enhanced = {"error": str(e)}

    try:
        queries = generate_queries(research_description)
    except Exception as e:
        queries = {"error": str(e)}

    session_data = {
        "name": name,
        "topic": topic,
        "description": research_description,
        "focus_keywords": focus_keywords,
        "enhanced": enhanced,
        "queries": queries.get("queries", []),
        "results": [],
    }

    path = _sessions.save_session(name, session_data)
    session_data["_path"] = path
    return session_data
