import json
import os
import re
import shutil
from typing import Any, Dict

from gui.app_info import resource

APP_NAME = "ResearchForge"
APP_DATA_DIR = os.path.join(os.path.expanduser("~"), f".{APP_NAME}")
CONFIG_DIR = APP_DATA_DIR
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
PROMPTS_DIR = os.path.join(APP_DATA_DIR, "prompts")
SESSIONS_DIR = os.path.join(APP_DATA_DIR, "sessions")

BUNDLED_DIR = resource("config")
BUNDLED_PROMPTS = resource(os.path.join("config", "prompts"))

DEFAULT_SETTINGS = {
    "llm_endpoint": "http://127.0.0.1:1234/v1",
    "llm_model": "",
    "output_root": os.path.join(APP_DATA_DIR, "downloads"),
    "summary_output_dir": os.path.join(APP_DATA_DIR, "summaries"),
    "prompt_dir": PROMPTS_DIR,
    "per_paper_prompt": os.path.join(PROMPTS_DIR, "per_paper.md"),
    "topic_synthesis_prompt": os.path.join(PROMPTS_DIR, "topic_synthesis.md"),
    "global_synthesis_prompt": os.path.join(PROMPTS_DIR, "global_synthesis.md"),
    "query_generation_prompt": os.path.join(PROMPTS_DIR, "query_generation.md"),
    "rate_relevance_prompt": os.path.join(PROMPTS_DIR, "rate_relevance.md"),
    "enhance_research_prompt": os.path.join(PROMPTS_DIR, "enhance_research.md"),
    "enhance_intent_prompt": os.path.join(PROMPTS_DIR, "enhance_intent.md"),
    "related_work_prompt": os.path.join(PROMPTS_DIR, "related_work.md"),
    "introduction_prompt": os.path.join(PROMPTS_DIR, "introduction.md"),
    "analyze_own_paper_prompt": os.path.join(PROMPTS_DIR, "analyze_own_paper.md"),
    "paper_review_prompt": os.path.join(PROMPTS_DIR, "paper_review_prompt.md"),
    "paper_audit_prompt":  os.path.join(PROMPTS_DIR, "paper_audit_prompt.md"),
    "section_preaudit_prompt": os.path.join(PROMPTS_DIR, "section_preaudit_prompt.md"),
    "paper_review_synthesis_prompt": os.path.join(PROMPTS_DIR, "paper_review_synthesis_prompt.md"),
    "paper_audit_synthesis_prompt": os.path.join(PROMPTS_DIR, "paper_audit_synthesis_prompt.md"),
    "per_patent_prompt": os.path.join(PROMPTS_DIR, "per_patent.md"),
    "patent_landscape_prompt": os.path.join(PROMPTS_DIR, "patent_landscape.md"),
    "llm_provider": "LM Studio",
    "brave_api_key": "",
    "semantic_scholar_api_key": "",
    "core_api_key": "",
    "patentsview_api_key": "",
    "epo_ops_key": "",
    "epo_ops_secret": "",
    "pqai_api_key": "",
    "pubmed_api_key": "",
    "contact_email": "",
    "deepseek_api_key": "",
    "gemini_api_key": "",
    "default_max_results": 100,
    "default_max_size_mb": 100.0,
    "default_after_date": "2020-01-01",
    "default_sources": ["arxiv", "semantic_scholar", "web", "brave", "pubmed"],
    "default_relevance_threshold": 2,
    "default_content_filter_enabled": True,
    "default_force_plus": False,
    "our_work_context": "",
    "session_intent": "",
    "summ_chk_similarity": True,
    "summ_chk_novelty": True,
    "summ_chk_methodology": False,
    "summ_chk_gaps": False,
    "summ_selected_mode": "per_paper",
    "score_threshold": 50,
    "summary_output_dir": "",
    "last_topic": "",
    "last_input_dir": "",
    "last_output_dir": "",
    "last_session": "",
    "queries": {},
    "search_mode": "academic",
    "first_run": True,
}

# Every prompt key (used by reset-all). Derived from DEFAULT_SETTINGS so it stays
# in sync automatically as prompts are added.
ALL_PROMPT_KEYS = [k for k in DEFAULT_SETTINGS if k.endswith("_prompt")]


class ConfigManager:
    """JSON-backed persistent settings at ~/.ResearchForge/settings.json.

    Two-tier storage: DEFAULT_SETTINGS (bundled fallbacks) + user overrides in JSON.
    Manages: LLM config, API keys, directories, prompt paths, session state, defaults.

    Prompt loading (load_prompt): user override in ~/.ResearchForge/prompts/ checked
    first; falls back to bundled config/prompts/. For rate_relevance_prompt, stale
    user prompts (RESEARCH before PAPER) are silently replaced with the bundled version.

    Key methods:
      get(key) / set(key, value) — Read/write settings
      load_prompt(key) / save_prompt(key, content) — Prompt file management
      load() / save() — Persist to disk
      is_first_run() / mark_first_run_done() — First-run detection
    """
    def __init__(self, config_path: str = SETTINGS_PATH):
        self.config_path = config_path
        self._data: Dict[str, Any] = {}
        self._dirty: set = set()   # keys changed by THIS instance since the last save
        self._ensure_dirs()
        self._migrate_bundled()
        self.load()

    def _ensure_dirs(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(PROMPTS_DIR, exist_ok=True)
        os.makedirs(SESSIONS_DIR, exist_ok=True)

    def _migrate_bundled(self):
        if os.path.isdir(BUNDLED_PROMPTS):
            for fname in os.listdir(BUNDLED_PROMPTS):
                if fname.endswith(".md"):
                    src = os.path.join(BUNDLED_PROMPTS, fname)
                    dst = os.path.join(PROMPTS_DIR, fname)
                    if not os.path.exists(dst):
                        shutil.copy2(src, dst)
        old_settings = os.path.join(BUNDLED_DIR, "settings.json")
        if os.path.isfile(old_settings) and not os.path.isfile(self.config_path):
            shutil.copy2(old_settings, self.config_path)
        migration_flag = os.path.join(CONFIG_DIR, ".sessions_migrated")
        if not os.path.isfile(migration_flag):
            old_sessions = os.path.join(BUNDLED_DIR, "sessions")
            if os.path.isdir(old_sessions):
                for fname in os.listdir(old_sessions):
                    if fname.endswith(".json"):
                        src = os.path.join(old_sessions, fname)
                        dst = os.path.join(SESSIONS_DIR, fname)
                        if not os.path.exists(dst):
                            shutil.copy2(src, dst)
            with open(migration_flag, "w") as f:
                f.write("")

    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        for k, v in DEFAULT_SETTINGS.items():
            if k not in self._data:
                self._data[k] = v
        self._data["prompt_dir"] = PROMPTS_DIR
        for key in ("per_paper_prompt", "topic_synthesis_prompt", "global_synthesis_prompt",
                     "query_generation_prompt", "rate_relevance_prompt",
                     "enhance_research_prompt", "enhance_intent_prompt", "related_work_prompt",
                     "analyze_own_paper_prompt", "per_patent_prompt", "patent_landscape_prompt"):
            fname = os.path.basename(DEFAULT_SETTINGS.get(key, f"{key}.md"))
            self._data[key] = os.path.join(PROMPTS_DIR, fname)

    def save(self):
        """Write settings, merging onto whatever is on disk now.

        `_data` is a snapshot taken at construction, and this file is shared by
        long-lived processes: the GUI plus one `--mcp` server per registered
        client. Writing the whole snapshot back makes the last writer win, so a
        server started this morning silently erases an API key the GUI saved this
        afternoon — observed 2026-07-27, EPO OPS credentials wiped between two
        runs while six MCP servers were live.

        Only keys this instance actually changed are written over the on-disk
        copy; concurrent edits to other keys survive. Same reload-merge rule the
        session writer already uses.
        """
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        on_disk = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    on_disk = json.load(f)
            except Exception:
                on_disk = {}
        if not isinstance(on_disk, dict):
            on_disk = {}

        merged = dict(on_disk)
        for k in self._dirty:
            merged[k] = self._data[k]
        for k, v in self._data.items():
            merged.setdefault(k, v)

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        self._data = merged
        self._dirty.clear()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value
        self._dirty.add(key)
        self.save()

    def update(self, d: Dict[str, Any]):
        self._data.update(d)
        self._dirty.update(d.keys())
        self.save()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    def export_to_file(self, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def import_from_file(self, filepath: str):
        with open(filepath, "r", encoding="utf-8") as f:
            incoming = json.load(f)
        self.update(incoming)

    def get_prompt_path(self, key: str) -> str:
        path = self._data.get(key, DEFAULT_SETTINGS.get(key, ""))
        if path and os.path.isfile(path):
            return path
        default = DEFAULT_SETTINGS.get(key, "")
        if default and os.path.isfile(default):
            return default
        return ""

    def is_first_run(self) -> bool:
        return self._data.get("first_run", True)

    def mark_first_run_done(self):
        self.set("first_run", False)

    def load_prompt(self, key: str) -> str:
        path = self.get_prompt_path(key)
        if path:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # ── staleness: rate_relevance_prompt ─────────────────────────────
            if key == "rate_relevance_prompt":
                idx_paper = content.upper().find("PAPER:")
                idx_research = content.upper().find("RESEARCH:")
                if idx_research >= 0 and (idx_paper < 0 or idx_research < idx_paper):
                    print(f"[CONFIG] Stale rate_relevance_prompt detected — "
                          f"RESEARCH before PAPER in {path} → falling back to bundled template")
                    bundled = os.path.join(BUNDLED_PROMPTS, os.path.basename(path))
                    if os.path.isfile(bundled) and bundled != path:
                        try:
                            with open(bundled, "r", encoding="utf-8") as bf:
                                content = bf.read()
                        except Exception:
                            pass

            # ── staleness: audit prompts must contain SCORES block ────────────
            if key in ("paper_review_prompt", "paper_review_synthesis_prompt"):
                if "END_SCORES" not in content.upper():
                    bundled = os.path.join(BUNDLED_PROMPTS, os.path.basename(path))
                    if os.path.isfile(bundled) and bundled != path:
                        try:
                            with open(bundled, "r", encoding="utf-8") as bf:
                                new_content = bf.read()
                            if "END_SCORES" in new_content.upper():
                                print(f"[CONFIG] Stale {key} — no SCORES block"
                                      f" → auto-updating {path}")
                                shutil.copy2(bundled, path)
                                content = new_content
                        except Exception:
                            pass

            return content
        return ""

    def save_prompt(self, key: str, content: str):
        """Persist ``content`` as the user's ACTIVE prompt for ``key`` — this is
        the file that loads every session and that the pipeline reads. i.e.
        'set as my default'."""
        path = self.get_prompt_path(key) or self._active_prompt_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # ── default / bundled restore ───────────────────────────────────────────

    def _prompt_filename(self, key: str) -> str:
        """Canonical .md filename for a prompt key (from DEFAULT_SETTINGS)."""
        default = DEFAULT_SETTINGS.get(key, "")
        if default:
            return os.path.basename(default)
        return f"{key.split('_prompt')[0]}.md"

    def _active_prompt_path(self, key: str) -> str:
        """Where the user's active copy of this prompt lives."""
        return os.path.join(PROMPTS_DIR, self._prompt_filename(key))

    def bundled_prompt_path(self, key: str) -> str:
        """The original shipped prompt — never written to, always available."""
        return os.path.join(BUNDLED_PROMPTS, self._prompt_filename(key))

    def reset_prompt_to_default(self, key: str) -> str:
        """Restore the ORIGINAL bundled prompt for ``key`` as the active prompt.
        Returns the restored content (empty string if no bundled file)."""
        src = self.bundled_prompt_path(key)
        dst = self.get_prompt_path(key) or self._active_prompt_path(key)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            with open(dst, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def reset_all_prompts_to_default(self) -> None:
        """Restore every prompt to its original bundled version."""
        for key in ALL_PROMPT_KEYS:
            self.reset_prompt_to_default(key)

    # ── named presets (saved variants, applied only on manual load) ──────────

    def _preset_dir(self, key: str) -> str:
        return os.path.join(PROMPTS_DIR, "presets", key)

    @staticmethod
    def _safe_preset_name(name: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "_", (name or "").strip()) or "preset"

    def save_prompt_preset(self, key: str, name: str, content: str) -> str:
        """Save ``content`` as a NAMED preset for ``key``. Presets are NOT loaded
        automatically — they only apply when loaded via ``load_prompt_preset``."""
        d = self._preset_dir(key)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, self._safe_preset_name(name) + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def list_prompt_presets(self, key: str) -> list:
        d = self._preset_dir(key)
        if not os.path.isdir(d):
            return []
        return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".md"))

    def load_prompt_preset(self, key: str, name: str) -> str:
        path = os.path.join(self._preset_dir(key), self._safe_preset_name(name) + ".md")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def delete_prompt_preset(self, key: str, name: str) -> bool:
        path = os.path.join(self._preset_dir(key), self._safe_preset_name(name) + ".md")
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False
