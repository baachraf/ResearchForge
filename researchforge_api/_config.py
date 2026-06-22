"""Config layer — delegates to gui.config_manager.ConfigManager (PySide6-free).

The API sits ON TOP of the existing application. ConfigManager is the single
source of truth for settings.json, prompt paths, and prompt staleness checks.
This module wraps it with a simpler function-based interface for MCP use.
"""
from gui.config_manager import ConfigManager, DEFAULT_SETTINGS

_cfg = None


def _get_cfg() -> ConfigManager:
    global _cfg
    if _cfg is None:
        _cfg = ConfigManager()
    return _cfg


def get_all() -> dict:
    return _get_cfg().to_dict()


def get(key: str, default=None):
    return _get_cfg().get(key, default)


def set_(key: str, value):
    _get_cfg().set(key, value)


def set_api_key(provider: str, key_value: str):
    key_map = {
        "deepseek": "deepseek_api_key",
        "gemini": "gemini_api_key",
        "brave": "brave_api_key",
        "semantic_scholar": "semantic_scholar_api_key",
        "core": "core_api_key",
        "pubmed": "pubmed_api_key",
    }
    mapped = key_map.get(provider.lower())
    if not mapped:
        raise ValueError(f"Unknown provider: {provider}. Valid: {list(key_map)}")
    set_(mapped, key_value)


def get_api_key(provider: str) -> str:
    key_map = {
        "deepseek": "deepseek_api_key",
        "gemini": "gemini_api_key",
        "brave": "brave_api_key",
        "semantic_scholar": "semantic_scholar_api_key",
        "core": "core_api_key",
        "pubmed": "pubmed_api_key",
    }
    mapped = key_map.get(provider.lower())
    return get(mapped, "") if mapped else "not-needed"


def get_prompt_dir() -> str:
    from gui.config_manager import PROMPTS_DIR
    return PROMPTS_DIR


def get_sessions_dir() -> str:
    from gui.config_manager import SESSIONS_DIR
    return SESSIONS_DIR


def get_settings_path() -> str:
    from gui.config_manager import SETTINGS_PATH
    return SETTINGS_PATH


def get_app_data_dir() -> str:
    from gui.config_manager import APP_DATA_DIR
    return APP_DATA_DIR


def set_analysis_lens(similarity: bool = None, novelty: bool = None,
                      methodology: bool = None, gaps: bool = None):
    for key, val in [("summ_chk_similarity", similarity), ("summ_chk_novelty", novelty),
                      ("summ_chk_methodology", methodology), ("summ_chk_gaps", gaps)]:
        if val is not None:
            set_(key, val)


def get_analysis_lenses() -> dict:
    return {k: get(k, True) for k in
            ["summ_chk_similarity", "summ_chk_novelty", "summ_chk_methodology", "summ_chk_gaps"]}


def set_search_mode(mode: str):
    set_("search_mode", mode)


def get_search_mode() -> str:
    return get("search_mode", "academic")


def set_llm(provider: str = None, endpoint: str = None, model: str = None):
    for key, val in [("llm_provider", provider), ("llm_endpoint", endpoint), ("llm_model", model)]:
        if val is not None:
            set_(key, val)
