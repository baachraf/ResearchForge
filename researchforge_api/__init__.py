"""ResearchForge API — headless access to the ResearchForge paper search and synthesis pipeline.

Usage:
    import researchforge_api as rf

    # Settings
    rf.set_api_key("deepseek", "sk-...")
    rf.set_config("llm_model", "deepseek-chat")
    rf.set_config("llm_endpoint", "https://api.deepseek.com/v1")
    rf.set_config("llm_provider", "DeepSeek")

    # Test connection
    ok, msg = rf.test_connection()
    print(msg)

    # Search
    result = rf.search("remote photoplethysmography rPPG", sources=["arxiv", "semantic_scholar"])
    for paper in result["results"]:
        print(paper["title"])

    # Download
    rf.download_paper(result["results"][0])

    # Analyze
    analysis = rf.analyze_paper("path/to/paper.pdf")
    print(analysis["analysis"])

    # Audit
    audit = rf.audit_paper("path/to/paper.pdf", mode="section")
    print(audit["report"])

    # Generate queries
    queries = rf.generate_queries("I want to research AI-assisted blood pressure estimation...")
    for q in queries["queries"]:
        print(q["query"])
"""

from researchforge_api._config import (
    get, set_ as set_config, get_all, set_api_key, get_api_key,
    set_analysis_lens, get_analysis_lenses, set_search_mode, get_search_mode, set_llm,
)
from researchforge_api._llm import check_connection as test_connection, fetch_models, create_client_from_config
from researchforge_api._prompts import list_prompts, get_prompt, set_prompt, reset_prompt, reset_all_prompts
from researchforge_api._sessions import (
    list_sessions, load_session, save_session, delete_session,
    get_session_queries, add_query_to_session, remove_query_from_session, set_session_results,
)
from researchforge_api._search import (
    search_papers as search, discover_endpoints, lookup_by_title, filter_papers,
)
from researchforge_api._download import (
    download_paper, download_papers, is_downloaded, get_download_path,
    list_downloads, list_download_tree,
)
from researchforge_api._score import score_papers
from researchforge_api._analyze import (
    analyze_paper, analyze_own_paper, synthesize_topic, synthesize_global,
    generate_queries, enhance_research, generate_related_work, generate_introduction,
    list_summaries, get_cached_analysis, run_full_pipeline, create_session_full,
)
from researchforge_api._audit import audit_paper, detect_sections, get_section_text, save_audit_results

__all__ = [
    "get", "set_config", "get_all", "set_api_key", "get_api_key",
    "set_analysis_lens", "get_analysis_lenses", "set_search_mode", "get_search_mode", "set_llm",
    "test_connection", "fetch_models", "create_client_from_config",
    "list_prompts", "get_prompt", "set_prompt", "reset_prompt", "reset_all_prompts",
    "list_sessions", "load_session", "save_session", "delete_session",
    "get_session_queries", "add_query_to_session", "remove_query_from_session", "set_session_results",
    "search", "discover_endpoints", "lookup_by_title", "filter_papers",
    "download_paper", "download_papers", "is_downloaded", "get_download_path",
    "list_downloads", "list_download_tree",
    "score_papers",
    "analyze_paper", "analyze_own_paper", "synthesize_topic", "synthesize_global",
    "generate_queries", "enhance_research", "generate_related_work", "generate_introduction",
    "list_summaries", "get_cached_analysis", "run_full_pipeline", "create_session_full",
    "audit_paper", "detect_sections", "get_section_text", "save_audit_results",
]
