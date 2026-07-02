"""Tests for the prompt-management model (Settings & Prompts tab).

Behaviour under test:
  - Fresh install uses bundled defaults; bundled files are never modified.
  - Save = 'set as my default' (active prompt the pipeline reads) OR a named
    preset (applied only when loaded).
  - Reset to Default restores the original bundled prompt (per prompt / all).
  - The editor tracks unsaved edits (dirty) so the app can warn before they are
    lost — because unsaved edits are NOT used by the pipeline (it reads disk).

Storage tests are headless (ConfigManager only). UI tests use offscreen Qt,
same isolation pattern as tests/test_e2e_parity.py.
"""
import os
import tempfile
import unittest

import gui.config_manager as cm
from gui.config_manager import ConfigManager

KEY = "per_paper_prompt"  # bundled per_paper.md exists, no staleness auto-replace


class _TempAppData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rf_prompts_")
        self.app_data = os.path.join(self.tmp, "ResearchForge")
        os.makedirs(self.app_data)
        import shutil
        self._real_settings = cm.SETTINGS_PATH
        self._bak = None
        if os.path.isfile(self._real_settings):
            self._bak = self._real_settings + ".promptsbak"
            shutil.copy2(self._real_settings, self._bak)
        self._orig = {k: getattr(cm, k) for k in
                      ("APP_DATA_DIR", "SETTINGS_PATH", "PROMPTS_DIR", "SESSIONS_DIR")}
        cm.APP_DATA_DIR = self.app_data
        cm.SETTINGS_PATH = os.path.join(self.app_data, "settings.json")
        cm.PROMPTS_DIR = os.path.join(self.app_data, "prompts")
        cm.SESSIONS_DIR = os.path.join(self.app_data, "sessions")
        os.makedirs(cm.PROMPTS_DIR, exist_ok=True)
        os.makedirs(cm.SESSIONS_DIR, exist_ok=True)
        self.cfg = ConfigManager(config_path=cm.SETTINGS_PATH)  # migrates bundled -> temp

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(cm, k, v)
        import shutil
        if self._bak and os.path.isfile(self._bak):
            shutil.move(self._bak, self._real_settings)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestStorage(_TempAppData):

    def test_fresh_install_loads_bundled(self):
        bundled = open(self.cfg.bundled_prompt_path(KEY), encoding="utf-8").read()
        self.assertEqual(self.cfg.load_prompt(KEY), bundled)

    def test_save_sets_default(self):
        self.cfg.save_prompt(KEY, "MY CUSTOM PROMPT")
        self.assertEqual(self.cfg.load_prompt(KEY), "MY CUSTOM PROMPT")

    def test_reset_restores_bundled_and_bundle_stays_intact(self):
        bundled_path = self.cfg.bundled_prompt_path(KEY)
        bundled_before = open(bundled_path, encoding="utf-8").read()

        self.cfg.save_prompt(KEY, "MODIFIED")
        self.assertEqual(self.cfg.load_prompt(KEY), "MODIFIED")

        restored = self.cfg.reset_prompt_to_default(KEY)
        self.assertEqual(self.cfg.load_prompt(KEY), restored)
        self.assertNotEqual(restored, "MODIFIED")
        self.assertEqual(restored, bundled_before)
        # the ORIGINAL bundled file must never be written to
        self.assertEqual(open(bundled_path, encoding="utf-8").read(), bundled_before)

    def test_presets_roundtrip_and_do_not_touch_active(self):
        self.cfg.save_prompt(KEY, "ACTIVE DEFAULT")
        self.cfg.save_prompt_preset(KEY, "aggressive v2", "PRESET BODY")

        self.assertIn("aggressive v2", self.cfg.list_prompt_presets(KEY))
        self.assertEqual(self.cfg.load_prompt_preset(KEY, "aggressive v2"), "PRESET BODY")
        # saving a preset must NOT change what the pipeline uses
        self.assertEqual(self.cfg.load_prompt(KEY), "ACTIVE DEFAULT")

        self.assertTrue(self.cfg.delete_prompt_preset(KEY, "aggressive v2"))
        self.assertEqual(self.cfg.list_prompt_presets(KEY), [])

    def test_reset_all(self):
        k2 = "topic_synthesis_prompt"
        b1 = open(self.cfg.bundled_prompt_path(KEY), encoding="utf-8").read()
        b2 = open(self.cfg.bundled_prompt_path(k2), encoding="utf-8").read()
        self.cfg.save_prompt(KEY, "X")
        self.cfg.save_prompt(k2, "Y")
        self.cfg.reset_all_prompts_to_default()
        self.assertEqual(self.cfg.load_prompt(KEY), b1)
        self.assertEqual(self.cfg.load_prompt(k2), b2)


# ── UI tests (offscreen Qt) ──────────────────────────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from gui.prompt_tab import PromptEditorTab  # noqa: E402


class _NullSignal:
    def emit(self, *a, **k):
        pass


class TestPromptTabUI(_TempAppData):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _tab(self):
        return PromptEditorTab(self.cfg, _NullSignal())

    def test_starts_clean(self):
        tab = self._tab()
        self.assertFalse(tab.has_unsaved_changes())
        idx = tab._key_index[KEY]
        self.assertNotIn("*", tab.prompt_tabs.tabText(idx))

    def test_edit_marks_dirty(self):
        tab = self._tab()
        tab._editors[KEY].setPlainText("edited but not saved")
        self.assertTrue(tab.has_unsaved_changes())
        self.assertIn("*", tab.prompt_tabs.tabText(tab._key_index[KEY]))
        # unsaved edits are NOT persisted -> pipeline still sees the old prompt
        self.assertNotEqual(self.cfg.load_prompt(KEY), "edited but not saved")

    def test_save_all_dirty_clears_and_persists(self):
        tab = self._tab()
        tab._editors[KEY].setPlainText("now my default")
        tab.save_all_dirty_as_default()
        self.assertFalse(tab.has_unsaved_changes())
        self.assertEqual(self.cfg.load_prompt(KEY), "now my default")

    def test_star_clears_after_save(self):
        """Regression: the dirty '*' must disappear once the prompt is saved."""
        tab = self._tab()
        idx = tab._key_index[KEY]
        tab._editors[KEY].setPlainText("changed text")
        self.assertIn("*", tab.prompt_tabs.tabText(idx))
        tab._mark_saved(KEY)   # what every save path calls
        self.assertNotIn("*", tab.prompt_tabs.tabText(idx))
        self.assertFalse(tab.has_unsaved_changes())

    def test_load_preset_activates_it(self):
        self.cfg.save_prompt_preset(KEY, "p1", "PRESET ACTIVE NOW")
        tab = self._tab()
        tab.prompt_tabs.setCurrentIndex(tab._key_index[KEY])
        tab._load_preset("p1")
        self.assertEqual(tab._editors[KEY].toPlainText(), "PRESET ACTIVE NOW")
        self.assertEqual(self.cfg.load_prompt(KEY), "PRESET ACTIVE NOW")
        self.assertFalse(tab.has_unsaved_changes())


if __name__ == "__main__":
    unittest.main()
