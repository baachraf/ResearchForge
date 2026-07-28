"""
ResearchForge MCP Server — exposes all 53 API functions as MCP tools.

Usage (stdio transport, for opencode/claude):
    python mcp_server_researchforge.py

Register in opencode.json:
    "mcp": {
        "researchforge": {
            "type": "local",
            "command": ["<venv>/Scripts/python.exe", "<project>/mcp_server_researchforge.py"],
            "enabled": true
        }
    }
"""
import os
import re
import sys
import json
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
import researchforge_api as rf

mcp = FastMCP("ResearchForge")


def _safe(result):
    """Ensure result is JSON-serializable for MCP transport."""
    if result is None:
        return None
    if isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, (list, dict)):
        return result
    return str(result)


# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_get_config(key: str, default: str = "") -> str:
    """Get a single config value from ResearchForge settings."""
    return _safe(rf.get(key, default))


@mcp.tool()
def rf_get_all_config() -> dict:
    """Get ALL ResearchForge settings as a dict."""
    return _safe(rf.get_all())


@mcp.tool()
def rf_set_config(key: str, value: str) -> str:
    """Set a single config value.

    Scalar values are stored as-is. For list/object settings (e.g.
    default_sources), pass a JSON array string like '["arxiv","pubmed"]' — it is
    parsed back into a real list before storing, so it is never mishandled as a
    character sequence."""
    rf.set_config(key, value)
    stored = rf.get(key)
    return f"Set {key} = {stored!r}"


@mcp.tool()
def rf_set_api_key(provider: str, api_key: str) -> str:
    """Set an API key for a provider. Valid providers: deepseek, gemini, brave, semantic_scholar, core, pubmed."""
    rf.set_api_key(provider, api_key)
    return f"API key set for {provider}"


@mcp.tool()
def rf_set_llm(provider: str = "", endpoint: str = "", model: str = "") -> str:
    """Configure the LLM provider, endpoint, and/or model. Pass empty string to skip a field."""
    rf.set_llm(provider=provider or None, endpoint=endpoint or None, model=model or None)
    return f"LLM configured: provider={rf.get('llm_provider')}, model={rf.get('llm_model')}"


@mcp.tool()
def rf_select_llm_mode(mode: str = "", provider: str = "", endpoint: str = "", model: str = "", api_key: str = "") -> str:
    """Select or query the execution mode for synthesis LLM operations.
    If `mode` is empty, returns current selection status and options.
    Valid modes:
      - 'configured': Use ResearchForge's registered cloud provider (DeepSeek, OpenAI, etc.)
      - 'local': Use a local model server (LM Studio, Ollama at http://localhost:11434/v1)
      - 'agent': Delegate LLM synthesis/completion to the calling AI agent in-context.
    Optionally pass provider, endpoint, model, or api_key to configure them in settings at the same time."""
    if not mode:
        current = rf.get("mcp_llm_mode", "NOT_SET")
        return (f"Current LLM Mode: {current!r}. Options: 'configured' (cloud), 'local' (Ollama/LM Studio), 'agent' (calling AI agent). "
                f"Configured Provider={rf.get('llm_provider')!r}, Model={rf.get('llm_model')!r}")
    mode_clean = mode.strip().lower()
    if mode_clean not in ("configured", "local", "agent"):
        return f"ERROR: Invalid mode {mode!r}. Must be 'configured', 'local', or 'agent'."
    rf.set_config("mcp_llm_mode", mode_clean)
    if provider or endpoint or model:
        rf.set_llm(provider=provider or None, endpoint=endpoint or None, model=model or None)
    if api_key and provider:
        rf.set_api_key(provider, api_key)
    return f"LLM execution mode set to {mode_clean!r}. (Provider={rf.get('llm_provider')}, Endpoint={rf.get('llm_endpoint')}, Model={rf.get('llm_model')})"


@mcp.tool()
def rf_save_artifact(session_id: str, relative_path: str, content: str) -> dict:
    """Save an agent-synthesized artifact into a session's canonical folder structure on disk.
    `relative_path` can be e.g. 'PATENT_LANDSCAPE.md', 'RELATED_WORK.md', 'GLOBAL_SUMMARY.md',
    or 'detailed_topic_reviews/MyTopic/MASTER_REPORT.md'."""
    try:
        from researchforge_api import _analyze
        model_root = _analyze._resolve_model_root(session_id, "")
        target_path = os.path.join(model_root, relative_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"path": target_path, "chars": len(content), "status": "saved", "session_id": session_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def rf_test_connection() -> str:
    """Test the LLM connection. Returns status message."""
    ok, msg = rf.test_connection()
    return f"{'OK' if ok else 'FAIL'}: {msg}"


@mcp.tool()
def rf_fetch_models() -> list:
    """Fetch available models from the current LLM provider."""
    return _safe(rf.fetch_models())


@mcp.tool()
def rf_discover_endpoints() -> list:
    """Scan localhost for running LM Studio and Ollama endpoints."""
    return _safe(rf.discover_endpoints())


@mcp.tool()
def rf_set_analysis_lens(similarity: bool = True, novelty: bool = True,
                         methodology: bool = False, gaps: bool = False) -> str:
    """Toggle analysis lenses for synthesis output."""
    rf.set_analysis_lens(similarity=similarity, novelty=novelty,
                         methodology=methodology, gaps=gaps)
    return _safe(rf.get_analysis_lenses())



@mcp.tool()
def rf_set_search_mode(mode: str = "academic") -> str:
    """Set search mode: 'academic' or 'general'."""
    rf.set_search_mode(mode)
    return f"Search mode: {rf.get_search_mode()}"


# ═══════════════════════════════════════════════════════════════
# PROMPTS
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_list_prompts() -> list:
    """List all 15 editable prompt templates."""
    return _safe(rf.list_prompts())


@mcp.tool()
def rf_get_prompt(key: str) -> str:
    """Get the full text of a prompt template. Keys: per_paper_prompt, topic_synthesis_prompt, global_synthesis_prompt, query_generation_prompt, rate_relevance_prompt, enhance_research_prompt, enhance_intent_prompt, related_work_prompt, introduction_prompt, analyze_own_paper_prompt, paper_review_prompt, paper_audit_prompt, section_preaudit_prompt, paper_review_synthesis_prompt, paper_audit_synthesis_prompt."""
    return rf.get_prompt(key)


@mcp.tool()
def rf_set_prompt(key: str, text: str) -> str:
    """Update a prompt template with new text."""
    rf.set_prompt(key, text)
    return f"Prompt '{key}' saved ({len(text)} chars)"


@mcp.tool()
def rf_reset_prompt(key: str) -> str:
    """Reset a single prompt to the bundled default."""
    return rf.reset_prompt(key)


@mcp.tool()
def rf_reset_all_prompts() -> str:
    """Reset ALL prompts to bundled defaults."""
    keys = rf.reset_all_prompts()
    return f"Reset {len(keys)} prompts: {', '.join(keys)}"


# ═══════════════════════════════════════════════════════════════
# SESSIONS
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_list_sessions() -> list:
    """List all saved research sessions."""
    return _safe(rf.list_sessions())


@mcp.tool()
def rf_load_session(session_id: str) -> dict:
    """Load a full session by ID (filename without .json)."""
    return _safe(rf.load_session(session_id))


@mcp.tool()
def rf_save_session(session_id: str, data_json: str) -> str:
    """Save session data. Pass data as a JSON string."""
    data = json.loads(data_json)
    path = rf.save_session(session_id, data)
    return f"Saved: {path}"


@mcp.tool()
def rf_delete_session(session_id: str) -> str:
    """Delete a session by ID."""
    rf.delete_session(session_id)
    return f"Deleted: {session_id}"


@mcp.tool()
def rf_get_session_queries(session_id: str) -> list:
    """Get the queries list from a session."""
    return _safe(rf.get_session_queries(session_id))


@mcp.tool()
def rf_add_query_to_session(session_id: str, query: str, max_results: int = 20,
                            name: str = "", sources: list = None,
                            topic: str = "General", lock_sources: bool = False) -> dict:
    """Add a search query to an existing session. `name` is the label shown for the
    query in the GUI (auto-derived from the query text if omitted).

    By default the GUI replaces a query's `sources` with the global Settings
    checkboxes at search time. Set `lock_sources=True` to pin this query to the
    `sources` given here — use it for a patent-targeted query (patentsview /
    epo_ops / pqai) so it is not broadcast to every academic source, and to keep
    academic queries off the patent providers."""
    return _safe(rf.add_query_to_session(session_id, query, max_results=max_results,
                                         name=name, sources=sources, topic=topic,
                                         lock_sources=lock_sources))


@mcp.tool()
def rf_remove_query_from_session(session_id: str, query_index: int) -> dict:
    """Remove a query from a session by index."""
    return _safe(rf.remove_query_from_session(session_id, query_index))


@mcp.tool()
def rf_update_session(session_id: str, fields_json: str = "", append_log: str = "") -> dict:
    """Update a session's working-state fields and save (GUI-loadable). Pass `fields_json`
    as a JSON object with any of: context, intent, focus_keywords, avoid_topics,
    score_threshold, scoring_depth, title_ok_only, title_filter_text,
    title_filter_enabled, source_filters, summ_chk_similarity/novelty/methodology/gaps,
    summ_selected_mode, paper_data, paper_path/pages/size_mb/titles/topic_name, name.
    Queries/results have their own tools. `append_log` adds a timestamped line to the
    session's agentic_log for a continuity trail."""
    fields = json.loads(fields_json) if fields_json else {}
    return _safe(rf.update_session(session_id, fields=fields, append_log=append_log))


@mcp.tool()
def rf_set_session_results(session_id: str, results_json: str,
                           query_key: str = "", append: bool = False) -> dict:
    """Register search results into a session so they show in the GUI results table.
    Pass results as a JSON array. `query_key` links results to a query by its name
    (defaults to the session's first query). `append=True` merges with existing
    results (dedup by id) instead of replacing."""
    results = json.loads(results_json) if isinstance(results_json, str) else results_json
    return _safe(rf.set_session_results(session_id, results, query_key=query_key, append=append))


# ═══════════════════════════════════════════════════════════════
# SEARCH
# ═══════════════════════════════════════════════════════════════

def _ensure_query_registered(session_id: str, query_name: str, query_text: str,
                             sources: list = None, max_results: int = 20,
                             after_date: str = "") -> None:
    """Ensure a query named ``query_name`` exists in the session's ``queries``
    array so the GUI's query panel shows it — not just the results table.

    rf_search previously registered results under query_name without adding the
    query itself, so the GUI's query table stayed empty even though results and
    summaries appeared. This upserts a normalized query (via add_query_to_session)
    when one with that name isn't already present; no-op on re-search to avoid
    duplicates.
    """
    try:
        existing = rf.get_session_queries(session_id) or []
    except Exception:
        existing = []
    if any((q.get("name") or "") == query_name for q in existing):
        return
    try:
        rf.add_query_to_session(
            session_id, query_text,
            sources=list(sources) if sources else None,
            max_results=max_results, after_date=after_date,
            name=query_name,
        )
    except Exception:
        # Best-effort: results still register below even if the query row can't be added.
        pass


@mcp.tool()
def rf_search(query: str, sources: list = None, max_results: int = 20,
              after_date: str = "", session_id: str = "", query_name: str = "",
              compact: bool = True, fields: list = None) -> dict:
    """Search academic paper databases. Sources: arxiv, semantic_scholar, pubmed, brave, web, openalex, crossref, europe_pmc, core. Default: arxiv, semantic_scholar, web, brave, pubmed.

    If `session_id` is given, the hits are also registered into that session
    (GUI-loadable, with FULL fields incl. abstracts) and linked to `query_name`
    (or a slug derived from the query). The query itself is added to the
    session's query list so the GUI's query panel shows it alongside the results.

    `compact` (default True) trims the RETURNED payload to id/title/url/year/source
    to stay under the MCP token cap — the session still stores full records. Pass
    `compact=False` for the full payload (e.g. to feed abstracts straight into
    rf_score_papers), or `fields=[...]` to choose the returned columns."""
    # Always fetch full results so the session gets complete records.
    out = _safe(rf.search(query, sources=sources, max_results=max_results, after_date=after_date))
    if session_id and isinstance(out, dict) and out.get("results"):
        try:
            # Resolve the effective query name: explicit > slug of the query text.
            qname = query_name or re.sub(r"[^0-9A-Za-z\s]", " ", query).split()
            qname = query_name or "_".join(qname[:6]).lower() or "query"
            _ensure_query_registered(session_id, qname, query, sources, max_results, after_date)
            rf.set_session_results(session_id, out["results"],
                                   query_key=qname, append=True)
            out["registered_to_session"] = session_id
        except Exception as e:
            out["session_save_error"] = str(e)
    # Trim the response only AFTER the session has stored the full records.
    if compact and isinstance(out, dict) and out.get("results"):
        out["results"] = rf.compact_results(out["results"], fields)
        out["compact"] = True
    return out


@mcp.tool()
def rf_lookup_by_title(titles: list, session_id: str = "") -> dict:
    """Look up specific papers by title. Returns found papers and not_found list.
    Pass session_id to persist results into the session (GUI Find-Papers parity):
    found papers register as results, not-found titles are added as quoted-title
    search queries."""
    return _safe(rf.lookup_by_title(titles, session_id=session_id))


@mcp.tool()
def rf_filter_papers(papers: list, title_filter: str = "",
                     score_threshold: int = 0, must_contain: list = None) -> list:
    """Filter a list of paper results by title keyword or relevance score."""
    return _safe(rf.filter_papers(papers, title_filter=title_filter,
                                   score_threshold=score_threshold,
                                   must_contain=must_contain))


# ═══════════════════════════════════════════════════════════════
# DOWNLOAD
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_download_paper(paper_json: str, output_dir: str = "") -> str:
    """Download a single paper PDF. Pass the paper dict as JSON (must have 'url', 'title', 'id')."""
    paper = json.loads(paper_json)
    path = rf.download_paper(paper, output_dir=output_dir)
    return path or "Download failed"


@mcp.tool()
def rf_download_papers(papers_json: str, output_dir: str = "") -> dict:
    """Download multiple paper PDFs. Pass papers as a JSON array."""
    papers = json.loads(papers_json)
    return _safe(rf.download_papers(papers, output_dir=output_dir))


@mcp.tool()
def rf_download_session(session_id: str, paper_ids: list = None) -> dict:
    """Download a session's registered results into the GUI folder layout
    (output_root/<query name>/) and persist file_exists/file_path/file_size_mb
    back onto the session. `paper_ids` limits which results to download (default: all).
    Use this so a later session reload knows which PDFs are downloaded and where."""
    return _safe(rf.download_session(session_id, paper_ids=paper_ids))


@mcp.tool()
def rf_refresh_session_downloads(session_id: str) -> dict:
    """Re-sync a session's results with what is already on disk: recompute
    file_exists/file_path/file_size_mb for every result and save. Call this when
    resuming a session to recover download state."""
    return _safe(rf.refresh_session_downloads(session_id))


@mcp.tool()
def rf_list_downloads(output_dir: str = "", session_id: str = "") -> list:
    """List all downloaded PDFs. Pass session_id to scope to one session's
    downloads (matches the GUI's Downloaded view); omit it for a flat walk of
    the whole output_root."""
    return _safe(rf.list_downloads(output_dir=output_dir, session_id=session_id))


@mcp.tool()
def rf_list_download_tree(output_dir: str = "", session_id: str = "") -> dict:
    """List the download directory as a topic → papers tree. With session_id
    the tree is rooted at that session's folder (topics = query folders,
    matching the GUI); without it the tree walks the flat output_root."""
    return _safe(rf.list_download_tree(output_dir=output_dir, session_id=session_id))


@mcp.tool()
def rf_is_downloaded(paper_id: str, output_dir: str = "") -> bool:
    """Check if a paper has already been downloaded."""
    return rf.is_downloaded(paper_id, output_dir=output_dir)


# ═══════════════════════════════════════════════════════════════
# SCORE
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_score_papers(papers_json: str, research_context: str = "",
                    intent: str = "", focus_keywords: str = "",
                    avoid_topics: str = "", scoring_depth: int = 1) -> list:
    """Score paper relevance 0-100 via LLM. Pass papers as JSON array. scoring_depth: 1=fast, 2=compare, 3=analyze."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    papers = json.loads(papers_json)
    return _safe(rf.score_papers(papers, research_context=research_context,
                                  intent=intent, focus_keywords=focus_keywords,
                                  avoid_topics=avoid_topics, scoring_depth=scoring_depth))


@mcp.tool()
def rf_score_session(session_id: str, paper_ids: list = None, scoring_depth: int = 1) -> dict:
    """Score a session's registered results against its own research context and
    persist relevance_score/score_reason back onto the session. `paper_ids` limits
    which results to score (default: all). scoring_depth: 1=fast, 2=compare, 3=analyze."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.score_session(session_id, paper_ids=paper_ids, scoring_depth=scoring_depth))


# ═══════════════════════════════════════════════════════════════
# ANALYZE & SYNTHESIZE
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_analyze_paper(pdf_path: str, prompt_key: str = "per_paper_prompt") -> dict:
    """Analyze a single PDF — extract method, contributions, results, limitations."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.analyze_paper(pdf_path, prompt_key=prompt_key))


@mcp.tool()
def rf_analyze_own_paper(pdf_path: str, session_id: str = "") -> dict:
    """Analyze your own paper to extract claims, results, comparisons for
    session creation. Pass session_id to persist the analysis into that session
    (paper_data/paper_path/paper_titles) so the GUI's 'Analyze My Paper' view of
    the session shows it; otherwise the analysis is returned only."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.analyze_own_paper(pdf_path, session_id=session_id))


def _check_llm_mode_guard():
    mode = (rf.get("mcp_llm_mode") or "").strip().lower()
    if not mode:
        return {
            "status": "CHOICE_REQUIRED",
            "message": "EXPLICIT USER CHOICE REQUIRED: No LLM execution mode selected.",
            "options": [
                "1. Configured cloud provider (DeepSeek / OpenAI) -> call rf_select_llm_mode('configured')",
                "2. Local model (LM Studio / Ollama) -> call rf_select_llm_mode('local', endpoint=...)",
                "3. Current Agent LLM -> call rf_select_llm_mode('agent')"
            ],
            "instruction": "Present these 3 options to the user and ask for their explicit choice before calling any synthesis tool."
        }
    return mode


@mcp.tool()
def rf_synthesize_topic(input_dir: str = "", output_dir: str = "",
                        session_id: str = "", topic_name: str = "") -> dict:
    """Synthesize all PDFs in one topic folder into a topic-level summary
    (per-paper analysis → topic synthesis). Pass session_id (+ optional
    topic_name) to write to the GUI's summary/<session>/<model>/ layout; or
    pass input_dir + output_dir explicitly for raw/ad-hoc use."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.synthesize_topic(input_dir=input_dir, output_dir=output_dir,
                                      session_id=session_id, topic_name=topic_name))


@mcp.tool()
def rf_synthesize_global(output_dir: str = "", session_id: str = "") -> dict:
    """Global cross-topic synthesis across all topic summaries in the model
    root. Pass session_id to target the GUI's summary/<session>/<model>/ folder."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.synthesize_global(output_dir=output_dir, session_id=session_id))


@mcp.tool()
def rf_generate_related_work(output_dir: str = "", session_id: str = "") -> dict:
    """Generate a Related Work section from existing summaries. Pass session_id
    to write to the GUI's summary/<session>/<model>/ folder."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.generate_related_work(output_dir=output_dir, session_id=session_id))


@mcp.tool()
def rf_generate_patent_landscape(output_dir: str = "", session_id: str = "") -> dict:
    """Analyse every patent in a session and write PATENT_LANDSCAPE.md beside
    RELATED_WORK.md. Groups patents by assignee, contrasts claimed scope, and maps
    white space. Requires a session containing results with doc_type == "patent"
    (i.e. searched via the patentsview / epo_ops / pqai sources). Per-patent
    analyses are cached under <model_root>/_patent_cache/, so re-running only pays
    for new patents. Pass session_id for GUI parity."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.generate_patent_landscape(output_dir=output_dir, session_id=session_id))


@mcp.tool()
def rf_analyze_patent(patent_json: str, context: str = "", intent: str = "") -> dict:
    """Analyse a single patent record (JSON with title/abstract/patent_meta) into
    problem, solution, what is claimed new, assignee, and relation to the research.
    Use rf_generate_patent_landscape for a whole session instead."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    import json as _json
    try:
        patent = _json.loads(patent_json)
    except Exception as e:
        return {"error": f"patent_json is not valid JSON: {e}"}
    return _safe(rf.analyze_patent(patent, context=context, intent=intent))


@mcp.tool()
def rf_generate_introduction(output_dir: str = "", session_id: str = "") -> dict:
    """Generate an Introduction section from existing summaries. Pass session_id
    to write to the GUI's summary/<session>/<model>/ folder."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.generate_introduction(output_dir=output_dir, session_id=session_id))



@mcp.tool()
def rf_generate_queries(research_description: str) -> dict:
    """Generate structured search queries from a natural-language research description."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.generate_queries(research_description))


@mcp.tool()
def rf_enhance_research(research_description: str) -> dict:
    """Enhance a research description — split into CONTRIBUTION and PROBLEM SPACE."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.enhance_research(research_description))


@mcp.tool()
def rf_run_full_pipeline(input_dir: str = "", output_dir: str = "",
                         session_id: str = "") -> dict:
    """Run the complete 3-pass pipeline: per-paper analysis → topic synthesis →
    global synthesis. Pass session_id to read from that session's downloads and
    write to its summary/<session>/<model>/ folder; or pass input_dir +
    output_dir explicitly for raw use."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.run_full_pipeline(input_dir=input_dir, output_dir=output_dir,
                                       session_id=session_id))


@mcp.tool()
def rf_create_session(name: str, research_description: str,
                      focus_keywords: str = "", topic: str = "General") -> dict:
    """Full session creation: enhance research → generate queries → save session.
    NOTE: This tool calls the LLM internally (for query generation and research enhancement).
    An explicit LLM mode must be selected first via rf_select_llm_mode()."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.create_session_full(name, research_description,
                                         focus_keywords=focus_keywords, topic=topic))


# ═══════════════════════════════════════════════════════════════
# SUMMARIES
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_list_summaries(summary_dir: str = "", session_id: str = "") -> list:
    """List generated summaries (global, topic, per-paper, related_work,
    introduction). Pass session_id to scope to one session's summary folder
    (matches the GUI's Check Summaries tab); omit it for a flat walk."""
    return _safe(rf.list_summaries(summary_dir=summary_dir, session_id=session_id))


@mcp.tool()
def rf_get_cached_analysis(cache_path: str) -> str:
    """Read the content of a cached analysis file by path."""
    return rf.get_cached_analysis(cache_path)


# ═══════════════════════════════════════════════════════════════
# AUDIT
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_audit_paper(pdf_path: str, mode: str = "section") -> dict:
    """Run a 10-dimension IEEE pre-submission audit on a paper. mode: 'section' (section-by-section) or 'full' (single call, needs large context model)."""
    guard = _check_llm_mode_guard()
    if isinstance(guard, dict):
        return guard
    return _safe(rf.audit_paper(pdf_path, mode=mode))


@mcp.tool()
def rf_detect_sections(pdf_path: str) -> dict:
    """Detect and return paper sections from a PDF."""
    return _safe(rf.detect_sections(pdf_path))


@mcp.tool()
def rf_get_section_text(pdf_path: str, section_name: str) -> str:
    """Get the text of a specific section (e.g. 'Introduction', 'Methods')."""
    return rf.get_section_text(pdf_path, section_name)


@mcp.tool()
def rf_save_audit_results(report: str, pdf_path: str = "", output_dir: str = "",
                          scores: dict = None, questions: list = None,
                          questions_text: str = "", model: str = "",
                          endpoint: str = "", paper_title: str = "",
                          source_type: str = "pdf", context_index: int = 0,
                          save_name: str = "") -> dict:
    """Save an audit report as a GUI-loadable bundle (.json + .md). The .json
    uses the same ``researchforge.audit/1`` schema as the GUI's Save Results,
    so it reappears in the GUI's Load Audit dialog. Returns
    ``{"json_path": ..., "md_path": ...}``. Pass the metadata from
    rf_audit_paper's result (scores, questions, mode) to populate the bundle."""
    return rf.save_audit_results(
        report, pdf_path=pdf_path, output_dir=output_dir,
        scores=scores, questions=questions, questions_text=questions_text,
        model=model, endpoint=endpoint, paper_title=paper_title,
        source_type=source_type, context_index=context_index, save_name=save_name,
    )


@mcp.tool()
def rf_get_audit_result(pdf_path: str) -> dict:
    """Retrieve the persisted result of an rf_audit_paper run.

    rf_audit_paper is long-running and often times out at the MCP client; the
    audit still completes server-side and writes its full result (report,
    scores, questions) to <audit_dir>/<slug>_audit.json. This tool reads that
    cached result back without re-running any LLM call. Call it after a timed-
    out rf_audit_paper to recover the result. Returns an error dict if no cached
    audit exists for the given pdf_path."""
    return _safe(rf.get_audit_result(pdf_path))


if __name__ == "__main__":
    mcp.run(transport="stdio")
