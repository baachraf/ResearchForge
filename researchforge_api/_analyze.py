"""Analyze + synthesize layer — uses ``gui.paths`` for the canonical layout.

Every function that writes to disk accepts an optional ``session_id``. When
given, outputs land in the GUI's exact layout (``<summary>/<session>/<model>/``
with ``detailed_topic_reviews/<topic>/`` subfolders) so the Check Summaries
tab, the per-paper cache, and global/related-work/introduction generation all
interoperate whether the work was done from the GUI or the headless API.

When ``output_dir`` is passed explicitly it is treated as the model root
directly (raw/ad-hoc mode for callers that know what they want). When neither
``session_id`` nor ``output_dir`` is given the function returns an error
rather than silently writing to a default location.
"""
import os
import re
import json
from researchforge_api import _config, _llm, _prompts
from gui import paths

from llm_pdf_engine import (
    TOPIC_SYNTHESIS_PROMPT, GLOBAL_SYNTHESIS_PROMPT, PAPER_SEPARATOR,
)


# ─── path resolution helpers ────────────────────────────────────────────────

def _session_name(session_id: str) -> str:
    """Resolve a session_id to its canonical ``name`` field. Returns '' if
    the session can't be loaded."""
    if not session_id:
        return ""
    from researchforge_api import _sessions
    s = _sessions.load_session(session_id)
    return (s.get("name") if s else "") or ""


def _resolve_model_root(session_id: str, output_dir: str) -> str:
    """Decide the model_output_root directory for synthesis writes.

    Priority: explicit ``output_dir`` (raw override) > session-derived layout
    (``paths.model_output_root``). Raises ``ValueError`` if neither is usable
    so callers can surface a clear error rather than write to a wrong place.
    """
    if output_dir:
        return output_dir
    sn = _session_name(session_id)
    if not sn:
        raise ValueError(
            "synthesize_* needs either an explicit output_dir or a valid "
            "session_id, so the output can land in the GUI's "
            "summary/<session>/<model>/ layout."
        )
    return paths.model_output_root(_config, sn)


def _resolve_topic_input(session_id: str, input_dir: str, topic_name: str) -> tuple[str, str]:
    """Resolve (input_dir, topic_name) for synthesize_topic.

    If ``input_dir`` is given, use it and derive topic_name from its basename.
    Else if ``session_id`` is given, derive input_dir from the session's
    downloads (per-topic if topic_name is set, else the session root).
    Returns ("", "")-ish error sentinels via the tuple; caller checks input_dir.
    """
    if input_dir:
        tn = topic_name or os.path.basename(os.path.normpath(input_dir))
        return input_dir, tn
    if not session_id:
        return "", topic_name
    sn = _session_name(session_id)
    if not sn:
        return "", topic_name
    if topic_name:
        return paths.topic_downloads_dir(_config, sn, topic_name), topic_name
    # No topic_name: caller must pass input_dir or topic_name with session_id.
    return "", topic_name


# ─── PDF text extraction ────────────────────────────────────────────────────

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
    try:
        text = ""
        with open(pdf_path, "rb") as fh:
            reader = pypdf.PdfReader(fh)
            for i in range(min(len(reader.pages), max_pages)):
                pt = reader.pages[i].extract_text()
                if pt:
                    text += pt
    except Exception:
        # Corrupt/truncated PDF → no extractable text. Caller treats "" as
        # unprocessable and writes a .skipped marker (GUI parity).
        return ""
    if len(text) <= max_chars:
        return text
    front = int(max_chars * 0.75)
    back = max_chars - front
    return text[:front] + "\n\n[...]\n\n" + text[-back:]


# ─── per-paper analysis (no layout writes — returns dict) ───────────────────

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


# ─── topic / global / section synthesis (session-aware, GUI layout) ─────────

def _write_skipped(cache_dir: str, pdf_file: str, reason: str) -> None:
    """Mirror the GUI's .skipped marker so a resume sees the paper as
    permanently unprocessable (HTML, no text, etc.)."""
    try:
        with open(os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".skipped"), "w") as sf:
            sf.write(reason)
    except OSError:
        pass


def synthesize_topic(
    input_dir: str = "",
    output_dir: str = "",
    *,
    session_id: str = "",
    topic_name: str = "",
    prompt_key: str = "topic_synthesis_prompt",
    progress_callback=None,
) -> dict:
    """Synthesize all PDFs in one topic folder into a topic-level summary.

    Output lands in the GUI's canonical layout so the Check Summaries tab and
    the per-paper cache interoperate:

        <summary>/<session>/<model>/
        ├── <topic>_SUMMARY.md
        └── detailed_topic_reviews/<topic>/
            ├── MASTER_REPORT.md
            └── _cache/<pdf>.md   (+ <pdf>.skipped markers)

    Path resolution:
      * Pass ``session_id`` (recommended) — input defaults to that session's
        downloads, output to its ``<summary>/<session>/<model>/`` folder.
      * Pass ``input_dir`` + ``output_dir`` explicitly for raw/ad-hoc use;
        ``output_dir`` is then the model root directly.
      * ``topic_name`` defaults to ``basename(input_dir)``.
    """
    input_dir, topic_name = _resolve_topic_input(session_id, input_dir, topic_name)
    if not input_dir:
        return {"error": "Provide input_dir, or session_id (+ optional topic_name)."}
    if not os.path.isdir(input_dir):
        return {"error": f"Not a directory: {input_dir}"}
    if not topic_name:
        topic_name = os.path.basename(os.path.normpath(input_dir))

    try:
        model_root = _resolve_model_root(session_id, output_dir)
    except ValueError as e:
        return {"error": str(e)}

    cache_dir = paths.topic_cache_dir(model_root, topic_name)
    os.makedirs(cache_dir, exist_ok=True)

    pdf_files = [f for f in os.listdir(input_dir) if f.lower().endswith('.pdf')]
    if not pdf_files:
        return {"error": "No PDFs found", "topic": topic_name}

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
        # Also count existing .skipped markers as "done" (GUI parity).
        if os.path.exists(os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".skipped")):
            cached += 1
            continue

        pdf_path = os.path.join(input_dir, pdf_file)
        with open(pdf_path, 'rb') as fh:
            header = fh.read(512)
        if header.lstrip().startswith(b'<') or not header.lstrip().startswith(b'%PDF'):
            errors += 1
            _write_skipped(cache_dir, pdf_file, "HTML or invalid PDF header")
            continue

        content = _extract_paper_text(pdf_path)
        if not content.strip():
            errors += 1
            _write_skipped(cache_dir, pdf_file, "No extractable text")
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
                _write_skipped(cache_dir, pdf_file, "LLM returned empty response")
        except Exception:
            errors += 1
            _write_skipped(cache_dir, pdf_file, "LLM error")

    try:
        client._client.close()
    except Exception:
        pass

    # Build per-topic master report from cache (GUI parity).
    master_content = ""
    for pdf in sorted(pdf_files):
        cf = os.path.join(cache_dir, os.path.splitext(pdf)[0] + ".md")
        if os.path.exists(cf):
            with open(cf, "r", encoding="utf-8") as f:
                master_content += f"\n### PAPER: {pdf}\n\n{f.read().strip()}\n\n{'-'*60}\n"

    master_path = paths.topic_master_report(model_root, topic_name)
    with open(master_path, "w", encoding="utf-8") as f:
        f.write(f"# MASTER REPORT: {topic_name}\nModel: {model}\nPapers: {len(pdf_files)}\n\n{master_content}")

    summary_path = None
    if master_content.strip():
        topic_prompt = _prompts.get_prompt(prompt_key) or TOPIC_SYNTHESIS_PROMPT
        if progress_callback:
            progress_callback(f"Synthesizing topic: {topic_name}")

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
            summary_path = paths.topic_summary_file(model_root, topic_name)
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(f"# TOPIC SUMMARY: {topic_name}\nModel: {model}\nPapers: {len(pdf_files)}\n\n{summary or ''}")
        except Exception as e:
            return {"error": f"Topic synthesis failed: {e}", "topic": topic_name}
        finally:
            try:
                client2._client.close()
            except Exception:
                pass

    return {
        "topic": topic_name,
        "total": len(pdf_files),
        "cached": cached,
        "processed": processed,
        "errors": errors,
        "master_report": master_path,
        "summary": summary_path,
        "model_root": model_root,
    }


def synthesize_global(
    output_dir: str = "",
    *,
    session_id: str = "",
    prompt_key: str = "global_synthesis_prompt",
    progress_callback=None,
) -> dict:
    """Global cross-topic synthesis across all topic summaries in the model root.

    Reads every ``<topic>_SUMMARY.md`` from the model root and writes
    ``GLOBAL_SUMMARY.md`` there. Path resolution mirrors ``synthesize_topic``.
    """
    try:
        model_root = _resolve_model_root(session_id, output_dir)
    except ValueError as e:
        return {"error": str(e)}
    if not os.path.isdir(model_root):
        return {"error": f"Directory not found: {model_root}"}

    topic_summaries = []
    for fn in os.listdir(model_root):
        if fn.endswith("_SUMMARY.md") and fn != "GLOBAL_SUMMARY.md":
            topic_summaries.append((fn.replace("_SUMMARY.md", ""), os.path.join(model_root, fn)))

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
        global_path = paths.global_summary_file(model_root)
        with open(global_path, "w", encoding="utf-8") as f:
            f.write(f"# GLOBAL SUMMARY\nModel: {model}\nTopics: {len(topic_summaries)}\n\n{global_summary or ''}")
        return {"global_summary": global_path, "topics": len(topic_summaries), "model_root": model_root}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass


# ─── query / enhance / analyze-own-paper (no layout writes) ─────────────────

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


def _session_context_intent(session_id: str) -> tuple[str, str]:
    """Pull (context, intent) for prompt-placeholder filling.

    Prefers the session's own ``context``/``intent`` fields; falls back to the
    global ``our_work_context`` setting when no session is given or the session
    has no context. Mirrors the GUI's ``_get_research_context`` resolution.
    """
    if not session_id:
        return (_config.get("our_work_context", "") or ""), ""
    from researchforge_api import _sessions
    s = _sessions.load_session(session_id) or {}
    context = (s.get("context") or "").strip() or (_config.get("our_work_context", "") or "")
    intent = (s.get("intent") or "").strip()
    return context, intent


def _gather_per_paper_analyses(model_root: str) -> str:
    """Concatenate every per-paper analysis (.md) from
    ``<model_root>/detailed_topic_reviews/<topic>/_cache/``.

    Mirrors the GUI's ``_on_introduction`` gathering: each topic's cache is
    read in sorted order, joined with a separator. Returns "" if none found.
    """
    parts: list[str] = []
    parent = paths.topic_reviews_parent(model_root)
    if os.path.isdir(parent):
        for folder in sorted(os.listdir(parent)):
            cache = paths.topic_cache_dir(model_root, folder)
            if not os.path.isdir(cache):
                continue
            for mf in sorted(os.listdir(cache)):
                if mf.endswith(".md"):
                    try:
                        with open(os.path.join(cache, mf), "r", encoding="utf-8") as f:
                            parts.append(f.read().strip())
                    except OSError:
                        pass
    return "\n\n---\n\n".join(parts)


def _gather_related_work_source(model_root: str) -> str:
    """Source text for the related-work prompt.

    Prefers the GLOBAL_SUMMARY.md (the GUI's first choice), falling back to the
    concatenated per-paper analyses when no global synthesis exists yet.
    Returns "" if neither is available.
    """
    global_path = paths.global_summary_file(model_root)
    if os.path.isfile(global_path):
        try:
            with open(global_path, "r", encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                return txt
        except OSError:
            pass
    return _gather_per_paper_analyses(model_root)


def generate_related_work(
    output_dir: str = "",
    *,
    session_id: str = "",
    prompt_key: str = "related_work_prompt",
) -> dict:
    """Generate a Related Work section from existing summaries.

    Gathers the global synthesis (or, failing that, the per-paper analyses)
    plus the session's context/intent, fills the ``{context}``/``{intent}``/
    ``{topic_summaries}`` placeholders, then runs the LLM. Mirrors the GUI's
    ``_on_related_work``. Writes ``RELATED_WORK.md`` to the model root.
    """
    try:
        model_root = _resolve_model_root(session_id, output_dir)
    except ValueError as e:
        return {"error": str(e)}
    os.makedirs(model_root, exist_ok=True)
    out_path = paths.related_work_file(model_root)

    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    source_text = _gather_related_work_source(model_root)
    if not source_text.strip():
        return {"error": "No summaries found. Run synthesize_topic or synthesize_global first."}

    context, intent = _session_context_intent(session_id)
    full_prompt = prompt.format(
        context=context,
        intent=intent or "Not specified",
        topic_summaries=source_text,
    )

    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3, max_tokens=60000, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# RELATED WORK\n\n{text}")
        return {"path": out_path, "chars": len(text), "model_root": model_root}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass


def generate_introduction(
    output_dir: str = "",
    *,
    session_id: str = "",
    prompt_key: str = "introduction_prompt",
) -> dict:
    """Generate an Introduction section from existing per-paper analyses.

    Gathers every per-paper analysis from ``detailed_topic_reviews/<topic>/_cache/``
    plus the session's context/intent, fills the ``{context}``/``{intent}``/
    ``{paper_analyses}`` placeholders, then runs the LLM. Mirrors the GUI's
    ``_on_introduction``. Writes ``INTRODUCTION.md`` to the model root.
    """
    try:
        model_root = _resolve_model_root(session_id, output_dir)
    except ValueError as e:
        return {"error": str(e)}
    os.makedirs(model_root, exist_ok=True)
    out_path = paths.introduction_file(model_root)

    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    paper_analyses = _gather_per_paper_analyses(model_root)
    if not paper_analyses.strip():
        return {"error": "No per-paper analyses found. Run synthesize_topic first."}

    context, intent = _session_context_intent(session_id)
    full_prompt = prompt.format(
        context=context,
        intent=intent or "Not specified",
        paper_analyses=paper_analyses,
    )

    client = _llm.create_client_from_config(timeout=180.0)
    model = _config.get("llm_model", "")
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3, max_tokens=60000, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# INTRODUCTION\n\n{text}")
        return {"path": out_path, "chars": len(text), "model_root": model_root}
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
    *,
    session_id: str = "",
) -> dict:
    """Analyze user's own paper to extract claims, results, comparisons.

    When ``session_id`` is given, the analysis is persisted into that session
    (``paper_data``, ``paper_path``, ``paper_titles``, ``paper_topic_name``,
    ``paper_size_mb``) — mirroring the GUI's "Analyze My Paper" tab, so the
    session opened in the desktop app shows the analyzed own-paper. Without
    ``session_id`` the analysis is returned only (legacy behaviour).
    """
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
    title = os.path.basename(pdf_path)
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"PAPER TEXT:\n\n{content[:50000]}"},
            ],
            temperature=0.0, timeout=180.0,
        )
        text = (res.choices[0].message.content or "").strip()
        result = {"analysis": text, "title": title}

        # Persist into the session so the GUI's "Analyze My Paper" view of this
        # session shows the analyzed own-paper (GUI parity).
        if session_id and text:
            try:
                from researchforge_api import _sessions
                size_mb = 0.0
                try:
                    size_mb = os.path.getsize(pdf_path) / (1024 ** 2)
                except OSError:
                    pass
                _sessions.update_session(
                    session_id,
                    fields_json=json.dumps({
                        "paper_data": {title: text},
                        "paper_path": pdf_path,
                        "paper_titles": [title],
                        "paper_topic_name": "MyPaper",
                        "paper_size_mb": size_mb,
                    }),
                    append_log=f"Analyzed own paper: {title}",
                )
                result["persisted_to_session"] = session_id
            except Exception as e:
                result["persist_error"] = str(e)
        return result
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass


# ─── summary listing + cache read-back ──────────────────────────────────────

_SUMMARY_TYPES = (
    ("GLOBAL_SUMMARY.md", "global"),
    ("RELATED_WORK.md", "related_work"),
    ("INTRODUCTION.md", "introduction"),
)


def _classify_summary_file(fname: str) -> str:
    if fname == "GLOBAL_SUMMARY.md":
        return "global"
    if fname == "RELATED_WORK.md":
        return "related_work"
    if fname == "INTRODUCTION.md":
        return "introduction"
    if fname == "MASTER_REPORT.md":
        return "master"
    if fname.endswith("_SUMMARY.md"):
        return "topic"
    if fname.endswith(".md"):
        return "per_paper"
    return ""


def list_summaries(summary_dir: str = "", *, session_id: str = "") -> list[dict]:
    """List generated summaries with their type, topic, and model.

    Session-aware (recommended): pass ``session_id`` to scope the listing to
    that session's ``<summary>/<session>/`` folder — exactly what the Check
    Summaries tab shows. Without ``session_id`` the listing walks the whole
    ``summary_output_dir`` (legacy/flat behaviour, useful for global audits).
    """
    if session_id:
        sn = _session_name(session_id)
        if not sn:
            return []
        root = paths.session_summary_root(_config, sn)
    else:
        root = summary_dir or paths.summary_root(_config)
    if not os.path.isdir(root):
        return []

    results: list[dict] = []
    for dirpath, dirs, files in os.walk(root):
        for fname in files:
            ftype = _classify_summary_file(fname)
            if not ftype:
                continue
            path = os.path.join(dirpath, fname)
            rel = os.path.relpath(dirpath, root)
            # rel is either ".", "<model>", or "<model>/detailed_topic_reviews/<topic>/_cache"
            parts = [p for p in rel.split(os.sep) if p and p != "."]
            model = parts[0] if parts else ""
            topic = ""
            if "detailed_topic_reviews" in parts:
                try:
                    topic = parts[parts.index("detailed_topic_reviews") + 1]
                except IndexError:
                    topic = ""
            elif ftype == "topic":
                topic = fname.replace("_SUMMARY.md", "")
            elif ftype == "master":
                topic = parts[-1] if parts else ""
            results.append({
                "path": path, "name": fname, "type": ftype,
                "topic": topic, "model": model,
                "size": os.path.getsize(path),
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
    input_dir: str = "",
    output_dir: str = "",
    *,
    session_id: str = "",
    progress_callback=None,
) -> dict:
    """Run the complete 3-pass pipeline: per-paper → topic synthesis → global.

    With ``session_id`` the input defaults to the session's downloads and the
    output lands in ``<summary>/<session>/<model>/``. Each topic subfolder of
    the input is synthesized in turn (per-paper analysis → topic synthesis),
    then global synthesis runs across all topic summaries.
    """
    # Resolve input.
    if input_dir:
        in_root = input_dir
    elif session_id:
        sn = _session_name(session_id)
        if not sn:
            return {"error": f"Session '{session_id}' not found."}
        in_root = paths.session_downloads_root(_config, sn)
    else:
        return {"error": "Provide input_dir or session_id."}

    if not os.path.isdir(in_root):
        return {"error": f"Input directory not found: {in_root}"}

    # Resolve model root.
    try:
        model_root = _resolve_model_root(session_id, output_dir)
    except ValueError as e:
        return {"error": str(e)}

    results = {"per_paper": {}, "topics": [], "global": None, "model_root": model_root}

    if progress_callback:
        progress_callback("Step 1: Per-paper analysis + topic synthesis...")

    subfolders = [f.path for f in os.scandir(in_root) if f.is_dir()]
    for folder_path in subfolders:
        folder_name = os.path.basename(folder_path)
        if progress_callback:
            progress_callback(f"  Processing topic: {folder_name}")
        topic_result = synthesize_topic(
            input_dir=folder_path,
            output_dir=model_root,            # keep all topics under one model root
            topic_name=folder_name,
            session_id="",                    # explicit output_dir already resolves it
            progress_callback=progress_callback,
        )
        results["topics"].append({"name": folder_name, "result": topic_result})

    if results["topics"] and not any(t["result"].get("error") for t in results["topics"]):
        if progress_callback:
            progress_callback("Step 2: Global synthesis...")
        results["global"] = synthesize_global(output_dir=model_root, progress_callback=progress_callback)

    return results


def create_session_full(
    name: str,
    research_description: str,
    focus_keywords: str = "",
    topic: str = "General",
) -> dict:
    """Full session creation flow: enhance → generate queries → save session.

    Builds the canonical GUI schema (via _sessions.blank_session) so the session
    loads identically in the desktop app. LLM steps (enhance/generate) are
    best-effort: if the LLM is unavailable the session is still created with the
    correct schema and an empty query list.
    Returns the created session dict.
    """
    from researchforge_api import _sessions

    context = research_description
    intent = ""
    try:
        enhanced = enhance_research(research_description)
        if isinstance(enhanced, dict) and "error" not in enhanced:
            context = enhanced.get("context") or research_description
            intent = enhanced.get("intent", "") or ""
    except Exception:
        pass

    queries = []
    try:
        gen = generate_queries(research_description)
        if isinstance(gen, dict):
            queries = gen.get("queries", []) or []
    except Exception:
        pass

    session_data = _sessions.blank_session(
        name=name,
        context=context,
        intent=intent,
        focus_keywords=focus_keywords,
    )
    session_data["queries"] = [_sessions.normalize_query(q) for q in queries]

    path = _sessions.save_session(name, session_data)
    session_data["_path"] = path
    return session_data


# ─── patents ─────────────────────────────────────────────────────────────────
#
# Patents never travel through the PDF download/extract path: their text arrives
# with the search hit and lives in ``result["patent_meta"]``. These functions read
# the session records directly, so a patent with no PDF on disk still analyses.
#
# Prompts are filled with ``.replace()`` rather than ``.format()`` — the patent
# templates contain literal ``{...}`` citation examples that ``str.format`` would
# raise KeyError on. Same reasoning as ``RelevanceScoringWorker``.

def _fill(template: str, values: dict) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v if v not in (None, "") else "Not available"))
    return out


def _patent_results(session_id: str) -> list:
    """Every ``doc_type == 'patent'`` result in a session.

    ``session["results"]`` is a flat list (see ``_sessions.ensure_full_schema``),
    not a per-query mapping.
    """
    from researchforge_api import _sessions
    sess = _sessions.load_session(session_id)
    if not sess or "error" in sess:
        return []
    return [
        r for r in (sess.get("results") or [])
        if isinstance(r, dict) and r.get("doc_type") == "patent"
    ]


def analyze_patent(patent: dict, *, prompt_key: str = "per_patent_prompt",
                   context: str = "", intent: str = "") -> dict:
    """Analyse one patent record. Returns ``{"text": ...}`` or ``{"error": ...}``."""
    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    meta = patent.get("patent_meta", {}) or {}
    claims = meta.get("claims_text", "") or ""
    full_prompt = _fill(prompt, {
        "publication_number": meta.get("publication_number", patent.get("id", "")),
        "title": patent.get("title", ""),
        "assignee": meta.get("assignee", ""),
        "priority_date": meta.get("priority_date", ""),
        "abstract": patent.get("abstract", ""),
        "claims_text": claims,
        "context": context,
        "intent": intent,
    })

    client = _llm.create_client_from_config(timeout=180.0)
    try:
        res = client.chat.completions.create(
            model=_config.get("llm_model", ""),
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.2, max_tokens=8000, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
        if not text.strip():
            return {"error": "LLM returned an empty analysis"}
        return {"text": text, "claims_available": bool(claims.strip())}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass


def generate_patent_landscape(
    output_dir: str = "",
    *,
    session_id: str = "",
    prompt_key: str = "patent_landscape_prompt",
    per_patent_prompt_key: str = "per_patent_prompt",
) -> dict:
    """Analyse every patent in a session, then synthesise ``PATENT_LANDSCAPE.md``.

    Per-patent analyses are cached under the model root so re-running only pays
    for patents that have not been analysed yet.
    """
    try:
        model_root = _resolve_model_root(session_id, output_dir)
    except ValueError as e:
        return {"error": str(e)}
    os.makedirs(model_root, exist_ok=True)

    patents = _patent_results(session_id)
    if not patents:
        return {"error": "No patents in this session. Search a patent source first."}

    prompt = _prompts.get_prompt(prompt_key)
    if not prompt:
        return {"error": f"Prompt '{prompt_key}' not found"}

    context, intent = _session_context_intent(session_id)
    cache_dir = os.path.join(model_root, "_patent_cache")
    os.makedirs(cache_dir, exist_ok=True)

    analyses, failed, no_claims = [], [], 0
    for p in patents:
        meta = p.get("patent_meta", {}) or {}
        pub = meta.get("publication_number") or p.get("id") or ""
        safe = re.sub(r'[\/*?:"<>|]', "_", str(pub)) or "unknown"
        cache_path = os.path.join(cache_dir, f"{safe}.md")

        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as fh:
                analyses.append(fh.read())
            continue

        res = analyze_patent(p, prompt_key=per_patent_prompt_key,
                             context=context, intent=intent)
        if "error" in res:
            failed.append(f"{pub}: {res['error']}")
            continue
        if not res.get("claims_available"):
            no_claims += 1
        block = f"### {pub} — {p.get('title','')}\n\n{res['text']}"
        with open(cache_path, "w", encoding="utf-8") as fh:
            fh.write(block)
        analyses.append(block)

    if not analyses:
        return {"error": "Every patent analysis failed: " + "; ".join(failed[:3])}

    full_prompt = _fill(prompt, {
        "patent_analyses": "\n\n---\n\n".join(analyses),
        "context": context,
        "intent": intent,
    })

    out_path = paths.patent_landscape_file(model_root)
    client = _llm.create_client_from_config(timeout=180.0)
    try:
        res = client.chat.completions.create(
            model=_config.get("llm_model", ""),
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3, max_tokens=60000, timeout=180.0,
        )
        text = res.choices[0].message.content or ""
        if not text.strip():
            return {"error": "LLM returned an empty landscape report"}
        header = f"# PATENT LANDSCAPE\n\n_{len(analyses)} patents analysed"
        if no_claims:
            header += f"; {no_claims} without claims text (metadata only)"
        header += "._\n\n"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(header + text)
        return {
            "path": out_path,
            "chars": len(text),
            "patents": len(analyses),
            "without_claims": no_claims,
            "failed": failed,
            "model_root": model_root,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            client._client.close()
        except Exception:
            pass
