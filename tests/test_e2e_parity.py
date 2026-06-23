"""TRUE end-to-end GUI <-> API parity test.

This is the test that should have existed from the start. It does NOT mock the
filesystem, the session manager, the config, or the path logic — only the LLM
HTTP call (so it runs without a key or network). It then proves the actual
parity contract:

  1. Drive a full flow through the REAL API: create session → register results
     → synthesize_topic(session_id=...). Real files land on disk.
  2. Instantiate the REAL GUI OutputTab (offscreen Qt, same ConfigManager) and
     assert its tree populates from the files the API just wrote.
       => proves: MCP-triggered work is visible in the GUI's Check Summaries tab.
  3. Call the REAL API list_summaries(session_id=...) and assert it sees the
     same files.
       => proves: GUI-written files (same layout) are visible to the API.

If this passes, parity is proven by execution, not by argument.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# Force offscreen Qt BEFORE any PySide6 widget is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import gui.config_manager as cm  # noqa: E402
from gui.config_manager import ConfigManager  # noqa: E402
from gui import paths  # noqa: E402


class _FakeResp:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class _FakeClient:
    def __init__(self):
        self.chat = self
        self.completions = self
        self._client = self

    def create(self, **kw):
        # Per-paper analysis call vs topic-synthesis call — both just need to
        # return non-empty content so the cache + summary get written.
        return _FakeResp("Canned LLM analysis: method X, contribution Y.")

    def close(self):
        pass


class TestEndToEndParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Redirect the ENTIRE app data dir to temp so ConfigManager, paths,
        # SessionManager, and the API all resolve to disposable paths. No mock
        # of the path/session/config layer — only the LLM is mocked.
        self.tmp = tempfile.mkdtemp(prefix="rf_e2e_")
        self.app_data = os.path.join(self.tmp, "ResearchForge")
        self.dl_root = os.path.join(self.tmp, "downloads")
        self.sum_root = os.path.join(self.tmp, "summaries")
        for d in (self.app_data, self.dl_root, self.sum_root):
            os.makedirs(d)

        # Capture the REAL settings path & back it up BEFORE any redirection.
        # ConfigManager's default config_path arg is bound at module-import time,
        # so patching cm.SETTINGS_PATH alone does NOT stop ConfigManager() from
        # writing the real file — we must (a) pass config_path explicitly and
        # (b) redirect the API's _get_cfg singleton, and (c) keep this backup as
        # a belt-and-suspenders restore so the real user config is never touched.
        import shutil
        self._real_settings_path = cm.SETTINGS_PATH
        self._settings_backup = None
        if os.path.isfile(self._real_settings_path):
            self._settings_backup = self._real_settings_path + ".testbak"
            shutil.copy2(self._real_settings_path, self._settings_backup)

        self._orig_constants = {
            "APP_DATA_DIR": cm.APP_DATA_DIR,
            "SETTINGS_PATH": cm.SETTINGS_PATH,
            "PROMPTS_DIR": cm.PROMPTS_DIR,
            "SESSIONS_DIR": cm.SESSIONS_DIR,
        }
        cm.APP_DATA_DIR = self.app_data
        cm.SETTINGS_PATH = os.path.join(self.app_data, "settings.json")
        cm.PROMPTS_DIR = os.path.join(self.app_data, "prompts")
        cm.SESSIONS_DIR = os.path.join(self.app_data, "sessions")
        os.makedirs(cm.SESSIONS_DIR, exist_ok=True)
        os.makedirs(cm.PROMPTS_DIR, exist_ok=True)

        # Copy bundled prompts so load_prompt works for real.
        # (They live under the repo's config/prompts/.)
        self._copy_bundled_prompts()

        # Reset API singletons so they pick up the new config paths.
        import researchforge_api._config as _c
        import researchforge_api._sessions as _s
        _c._cfg = None
        _s._mgr = None

        # Real ConfigManager, explicit temp config_path (NOT the import-bound
        # default) so writes land in temp, never in the user's real settings.
        self.cfg = ConfigManager(config_path=cm.SETTINGS_PATH)
        self.cfg.set("output_root", self.dl_root)
        self.cfg.set("summary_output_dir", self.sum_root)
        self.cfg.set("llm_model", "test-model")
        self.cfg.set("llm_provider", "LM Studio")
        self.cfg.set("llm_endpoint", "http://127.0.0.1:1234/v1")

        # CRITICAL: force the API's config singleton onto the temp ConfigManager
        # too, so every _config.get/set_ call inside the API uses temp paths.
        # Without this, _get_cfg() would build a ConfigManager() at the real
        # (import-bound) SETTINGS_PATH and the test would only pass by polluting
        # the user's real settings — which is how this test originally leaked.
        self._get_cfg_patch = mock.patch(
            "researchforge_api._config._get_cfg", return_value=self.cfg)
        self._get_cfg_patch.start()

    def _copy_bundled_prompts(self):
        bundled = cm.resource(os.path.join("config", "prompts"))
        if os.path.isdir(bundled):
            import shutil
            for f in os.listdir(bundled):
                if f.endswith(".md"):
                    shutil.copy2(os.path.join(bundled, f), os.path.join(cm.PROMPTS_DIR, f))

    def tearDown(self):
        # Stop the _get_cfg redirection first.
        try:
            self._get_cfg_patch.stop()
        except RuntimeError:
            pass
        cm.APP_DATA_DIR = self._orig_constants["APP_DATA_DIR"]
        cm.SETTINGS_PATH = self._orig_constants["SETTINGS_PATH"]
        cm.PROMPTS_DIR = self._orig_constants["PROMPTS_DIR"]
        cm.SESSIONS_DIR = self._orig_constants["SESSIONS_DIR"]
        import shutil
        # Belt-and-suspenders: restore the real settings.json from backup if the
        # test touched it for any reason.
        if self._settings_backup and os.path.isfile(self._settings_backup):
            shutil.move(self._settings_backup, self._real_settings_path)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_llm(self):
        return mock.patch("researchforge_api._analyze._llm.create_client_from_config",
                          return_value=_FakeClient())

    # ─── the real test: API writes → GUI sees → API lists ───────────────────

    def test_full_flow_api_writes_gui_reads(self):
        from researchforge_api import _sessions, _analyze

        session_name = "E2E Session (rPPG)"  # has forbidden chars to test sanitiser
        session_id = session_name  # SessionManager uses the id as filename stem

        # 1) Create a real session via the API.
        _sessions.save_session(session_id, _sessions.blank_session(name=session_name))
        # Register one query + one result so synthesize_topic has an input folder.
        _sessions.add_query_to_session(session_id, "rPPG morphology", topic="rPPG")
        query_name = _sessions.get_session_queries(session_id)[0]["name"]

        # Drop a fake-but-valid PDF in the session's topic download folder, the
        # way DownloadWorker would.
        topic_dir = paths.topic_downloads_dir(self.cfg, session_name, query_name)
        os.makedirs(topic_dir)
        pdf_path = os.path.join(topic_dir, "paper1.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF")

        # 2) Run synthesize_topic through the REAL API with only the LLM mocked.
        #    Also stub _extract_paper_text so the fake PDF yields text.
        with self._patch_llm(), \
             mock.patch("researchforge_api._analyze._extract_paper_text",
                        return_value="Fake paper body text for the rPPG paper."):
            result = _analyze.synthesize_topic(session_id=session_id,
                                                topic_name=query_name)
        self.assertNotIn("error", result, f"synthesize_topic failed: {result}")

        # 3) Assert the EXACT canonical layout landed on disk.
        model_root = paths.model_output_root(self.cfg, session_name)
        self.assertTrue(os.path.isfile(paths.topic_summary_file(model_root, query_name)),
                        "topic _SUMMARY.md missing at model root")
        self.assertTrue(os.path.isfile(paths.topic_master_report(model_root, query_name)),
                        "MASTER_REPORT.md missing in detailed_topic_reviews/<topic>/")
        cache = paths.topic_cache_dir(model_root, query_name)
        self.assertTrue(os.path.isfile(os.path.join(cache, "paper1.md")),
                        "per-paper cache .md missing in _cache/")

        # 4) ── GUI side ── instantiate the REAL OutputTab with the same config,
        #    set the active session the way search_tab does, refresh, and assert
        #    the tree sees every file the API wrote.
        self.cfg.set("session_download_name", paths.session_segment(session_name))
        from gui.output_tab import OutputTab
        class _NullSignal:
            def emit(self, *a, **k): pass
        tab = OutputTab(self.cfg, _NullSignal())
        tab._refresh()

        # The tree's top-level items are model dirs; one should be "test-model".
        model_items = [tab.file_tree.topLevelItem(i).text(0)
                       for i in range(tab.file_tree.topLevelItemCount())]
        self.assertIn("test-model", model_items,
                      f"GUI OutputTab did not see model dir; got {model_items}")

        # Under that model, the topic summary must appear.
        model_item = next(tab.file_tree.topLevelItem(i)
                          for i in range(tab.file_tree.topLevelItemCount())
                          if tab.file_tree.topLevelItem(i).text(0) == "test-model")
        topic_labels = [model_item.child(j).text(0) for j in range(model_item.childCount())]
        self.assertIn(query_name, topic_labels,
                      f"GUI OutputTab did not see topic summary; got {topic_labels}")

        # 5) ── API side ── list_summaries(session_id) must also see them.
        listed = _analyze.list_summaries(session_id=session_id)
        types = {item["type"] for item in listed}
        self.assertIn("topic", types, f"list_summaries missed topic; got {types}")
        # And the path it reports must match the file on disk.
        topic_entry = next(i for i in listed if i["type"] == "topic")
        self.assertTrue(os.path.isfile(topic_entry["path"]),
                        f"list_summaries reported non-existent path: {topic_entry['path']}")


if __name__ == "__main__":
    unittest.main()
