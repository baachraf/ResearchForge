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
                            topic: str = "General") -> dict:
    """Add a search query to an existing session. `name` is the label shown for the
    query in the GUI (auto-derived from the query text if omitted)."""
    return _safe(rf.add_query_to_session(session_id, query, max_results=max_results,
                                         name=name, sources=sources, topic=topic))


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

@mcp.tool()
def rf_search(query: str, sources: list = None, max_results: int = 20,
              after_date: str = "", session_id: str = "", query_name: str = "") -> dict:
    """Search academic paper databases. Sources: arxiv, semantic_scholar, pubmed, brave, web, openalex, crossref, europe_pmc, core. Default: arxiv, semantic_scholar, web, brave, pubmed.

    If `session_id` is given, the hits are also registered into that session
    (GUI-loadable) and linked to `query_name` (or the session's first query)."""
    out = _safe(rf.search(query, sources=sources, max_results=max_results, after_date=after_date))
    if session_id and isinstance(out, dict) and out.get("results"):
        try:
            rf.set_session_results(session_id, out["results"],
                                   query_key=query_name, append=True)
            out["registered_to_session"] = session_id
        except Exception as e:
            out["session_save_error"] = str(e)
    return out


@mcp.tool()
def rf_lookup_by_title(titles: list) -> dict:
    """Look up specific papers by title. Returns found papers and not_found list."""
    return _safe(rf.lookup_by_title(titles))


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
    papers = json.loads(papers_json)
    return _safe(rf.score_papers(papers, research_context=research_context,
                                  intent=intent, focus_keywords=focus_keywords,
                                  avoid_topics=avoid_topics, scoring_depth=scoring_depth))


@mcp.tool()
def rf_score_session(session_id: str, paper_ids: list = None, scoring_depth: int = 1) -> dict:
    """Score a session's registered results against its own research context and
    persist relevance_score/score_reason back onto the session. `paper_ids` limits
    which results to score (default: all). scoring_depth: 1=fast, 2=compare, 3=analyze."""
    return _safe(rf.score_session(session_id, paper_ids=paper_ids, scoring_depth=scoring_depth))


# ═══════════════════════════════════════════════════════════════
# ANALYZE & SYNTHESIZE
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def rf_analyze_paper(pdf_path: str, prompt_key: str = "per_paper_prompt") -> dict:
    """Analyze a single PDF — extract method, contributions, results, limitations."""
    return _safe(rf.analyze_paper(pdf_path, prompt_key=prompt_key))


@mcp.tool()
def rf_analyze_own_paper(pdf_path: str) -> dict:
    """Analyze your own paper to extract claims, results, comparisons for session creation."""
    return _safe(rf.analyze_own_paper(pdf_path))


@mcp.tool()
def rf_synthesize_topic(input_dir: str = "", output_dir: str = "",
                        session_id: str = "", topic_name: str = "") -> dict:
    """Synthesize all PDFs in one topic folder into a topic-level summary
    (per-paper analysis → topic synthesis). Pass session_id (+ optional
    topic_name) to write to the GUI's summary/<session>/<model>/ layout; or
    pass input_dir + output_dir explicitly for raw/ad-hoc use."""
    return _safe(rf.synthesize_topic(input_dir=input_dir, output_dir=output_dir,
                                      session_id=session_id, topic_name=topic_name))


@mcp.tool()
def rf_synthesize_global(output_dir: str = "", session_id: str = "") -> dict:
    """Global cross-topic synthesis across all topic summaries in the model
    root. Pass session_id to target the GUI's summary/<session>/<model>/ folder."""
    return _safe(rf.synthesize_global(output_dir=output_dir, session_id=session_id))


@mcp.tool()
def rf_generate_related_work(output_dir: str = "", session_id: str = "") -> dict:
    """Generate a Related Work section from existing summaries. Pass session_id
    to write to the GUI's summary/<session>/<model>/ folder."""
    return _safe(rf.generate_related_work(output_dir=output_dir, session_id=session_id))


@mcp.tool()
def rf_generate_introduction(output_dir: str = "", session_id: str = "") -> dict:
    """Generate an Introduction section from existing summaries. Pass session_id
    to write to the GUI's summary/<session>/<model>/ folder."""
    return _safe(rf.generate_introduction(output_dir=output_dir, session_id=session_id))


@mcp.tool()
def rf_generate_queries(research_description: str) -> dict:
    """Generate structured search queries from a natural-language research description."""
    return _safe(rf.generate_queries(research_description))


@mcp.tool()
def rf_enhance_research(research_description: str) -> dict:
    """Enhance a research description — split into CONTRIBUTION and PROBLEM SPACE."""
    return _safe(rf.enhance_research(research_description))


@mcp.tool()
def rf_run_full_pipeline(input_dir: str = "", output_dir: str = "",
                         session_id: str = "") -> dict:
    """Run the complete 3-pass pipeline: per-paper analysis → topic synthesis →
    global synthesis. Pass session_id to read from that session's downloads and
    write to its summary/<session>/<model>/ folder; or pass input_dir +
    output_dir explicitly for raw use."""
    return _safe(rf.run_full_pipeline(input_dir=input_dir, output_dir=output_dir,
                                       session_id=session_id))


@mcp.tool()
def rf_create_session(name: str, research_description: str,
                      focus_keywords: str = "", topic: str = "General") -> dict:
    """Full session creation: enhance research → generate queries → save session."""
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
