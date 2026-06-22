"""Prompt layer — delegates to ConfigManager.load_prompt / save_prompt.

Uses the EXACT same file resolution and staleness checks as the GUI:
- User override in ~/.ResearchForge/prompts/ checked first
- Bundled config/prompts/ as fallback
- rate_relevance staleness (RESEARCH before PAPER) auto-corrected
- paper_review staleness (missing END_SCORES) auto-corrected
"""
from gui.config_manager import ConfigManager
from researchforge_api import _config

_prompt_keys = [
    "per_paper_prompt", "topic_synthesis_prompt", "global_synthesis_prompt",
    "query_generation_prompt", "rate_relevance_prompt", "enhance_research_prompt",
    "enhance_intent_prompt", "related_work_prompt", "introduction_prompt",
    "analyze_own_paper_prompt", "paper_review_prompt", "paper_audit_prompt",
    "section_preaudit_prompt", "paper_review_synthesis_prompt",
    "paper_audit_synthesis_prompt",
]

_prompt_names = {
    "per_paper_prompt": "Per-Paper Analysis",
    "topic_synthesis_prompt": "Topic Synthesis",
    "global_synthesis_prompt": "Global Synthesis",
    "query_generation_prompt": "Query Generation",
    "rate_relevance_prompt": "Rate Relevance",
    "enhance_research_prompt": "Enhance Research",
    "enhance_intent_prompt": "Enhance Intent",
    "related_work_prompt": "Related Work",
    "introduction_prompt": "Introduction",
    "analyze_own_paper_prompt": "Analyze Own Paper",
    "paper_review_prompt": "Paper Review (Single Call)",
    "paper_audit_prompt": "Paper Full Audit",
    "section_preaudit_prompt": "Section Pre-Audit",
    "paper_review_synthesis_prompt": "Review Synthesis",
    "paper_audit_synthesis_prompt": "Audit Synthesis",
}


def list_prompts() -> list[dict]:
    cfg = _config._get_cfg()
    result = []
    for key in _prompt_keys:
        path = cfg.get_prompt_path(key)
        entry = {
            "key": key,
            "name": _prompt_names.get(key, key),
            "has_user_override": bool(path),
        }
        result.append(entry)
    return result


def get_prompt(key: str) -> str:
    return _config._get_cfg().load_prompt(key)


def set_prompt(key: str, text: str):
    _config._get_cfg().save_prompt(key, text)


def reset_prompt(key: str) -> str:
    """Delete user override, reload from bundled default."""
    import os
    cfg = _config._get_cfg()
    path = cfg.get_prompt_path(key)
    if path and os.path.isfile(path):
        os.remove(path)
    # Force reload of bundled via fresh ConfigManager
    cfg2 = ConfigManager()
    return cfg2.load_prompt(key)


def reset_all_prompts() -> list[str]:
    """Reset all prompts to bundled defaults. Returns list of reset keys."""
    reset_keys = []
    for key in _prompt_keys:
        reset_prompt(key)
        reset_keys.append(key)
    return reset_keys
