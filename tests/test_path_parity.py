"""GUI ↔ API path parity + session-aware integration tests.

These prove that:
  1. The headless API resolves session-derived paths to the SAME layout the GUI
     uses (the bug class this test file exists to prevent).
  2. ``synthesize_topic`` / ``synthesize_global`` / ``generate_related_work`` /
     ``generate_introduction`` write to the canonical ``<summary>/<session>/
     <model>/`` layout when given a ``session_id``.
  3. ``list_downloads`` / ``list_download_tree`` / ``list_summaries`` scope
     correctly to a session.
  4. ``save_audit_results`` writes a GUI-loadable ``researchforge.audit/1``
     bundle with a name matching the GUI's slug convention.

LLM calls are mocked — these tests verify path/layout contracts, not model
output. They run without Qt and without network.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock


class _FakeResp:
    def __init__(self, text="OK"):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class _FakeClient:
    def __init__(self, text="analysis"):
        self._text = text
        self.chat = self
        self.completions = self
        self._client = self

    def create(self, **kw):
        return _FakeResp(self._text)

    def close(self):
        pass


def _patch_llm(text="analysis"):
    """Patch the API's LLM client factory with a no-network fake."""
    return mock.patch("researchforge_api._analyze._llm.create_client_from_config",
                      return_value=_FakeClient(text))


class TestApiSessionPathResolution(unittest.TestCase):
    """The API's ``_resolve_model_root`` must produce the GUI's layout when
    given a session_id."""

    def setUp(self):
        # Point both roots at a tmp tree so nothing escapes to ~/.ResearchForge.
        self.tmp = tempfile.mkdtemp(prefix="rf_test_")
        self.dl_root = os.path.join(self.tmp, "downloads")
        self.sum_root = os.path.join(self.tmp, "summaries")
        self.audit_root = os.path.join(self.tmp, "audit")
        os.makedirs(self.dl_root)
        os.makedirs(self.sum_root)
        os.makedirs(self.audit_root)
        # Reset module-level singletons so patches take effect cleanly.
        import researchforge_api._config as _c
        import researchforge_api._sessions as _s
        _c._cfg = None
        _s._mgr = None
        self._cfg_patch = mock.patch("researchforge_api._config._get_cfg")
        cfg_mock = self._cfg_patch.start()
        cfg_mock.return_value = type("C", (), {
            "get": self._cfg_get,
            "set": lambda self, k, v: None,
            "to_dict": lambda self: {},
        })()
        # Also a fake session for _sessions.load_session lookups.
        self._session_patch = mock.patch(
            "researchforge_api._sessions.SessionManager"
        )
        sm_cls = self._session_patch.start()
        self._fake_sessions = {}
        inst = sm_cls.return_value
        inst.load = self._fake_load
        inst.save = self._fake_save
        inst.list_sessions = lambda: list(self._fake_sessions.values())

    def tearDown(self):
        self._cfg_patch.stop()
        self._session_patch.stop()

    def _cfg_get(self, key, default=None):
        return {
            "output_root": self.dl_root,
            "summary_output_dir": self.sum_root,
            "audit_output_dir": self.audit_root,
            "llm_model": "deepseek-chat",
            "llm_endpoint": "http://x",
            "llm_provider": "DeepSeek",
        }.get(key, default)

    def _fake_load(self, session_id):
        return self._fake_sessions.get(session_id)

    def _fake_save(self, session_id, data):
        self._fake_sessions[session_id] = data
        return os.path.join(self.tmp, "sessions", session_id + ".json")

    def _register_session(self, session_id="s1", name="MySession"):
        from researchforge_api import _sessions
        self._fake_sessions[session_id] = _sessions.blank_session(name=name)

    # ── _resolve_model_root ─────────────────────────────────────────────────

    def test_model_root_from_session_matches_gui_layout(self):
        from researchforge_api import _analyze
        from gui import paths
        self._register_session()
        got = _analyze._resolve_model_root("s1", "")
        want = paths.model_output_root(_analyze._config, "MySession")
        self.assertEqual(got, want)
        self.assertEqual(got, os.path.join(self.sum_root, "MySession", "deepseek-chat"))

    def test_model_root_explicit_output_dir_overrides_session(self):
        from researchforge_api import _analyze
        got = _analyze._resolve_model_root("", "D:/custom/model_root")
        self.assertEqual(got, "D:/custom/model_root")

    def test_model_root_without_session_or_output_errors(self):
        from researchforge_api import _analyze
        with self.assertRaises(ValueError):
            _analyze._resolve_model_root("nonexistent", "")

    # ── synthesize_topic writes the full canonical layout ──────────────────

    def test_synthesize_topic_with_session_writes_canonical_layout(self):
        from researchforge_api import _analyze
        from gui import paths
        self._register_session()
        # Build a topic folder with one fake PDF under the session's downloads.
        topic = "morphology"
        topic_dir = paths.topic_downloads_dir(_analyze._config, "MySession", topic)
        os.makedirs(topic_dir)
        # Minimal valid-PDF-ish file (synthesize_topic only checks the header
        # bytes start with %PDF, then tries pypdf/section extraction which will
        # return empty text → written as .skipped).
        with open(os.path.join(topic_dir, "paper1.pdf"), "wb") as f:
            f.write(b"%PDF-1.4\nfake")

        with _patch_llm("fake analysis text"), \
             mock.patch("researchforge_api._analyze._prompts.get_prompt", return_value="PROMPT"):
            result = _analyze.synthesize_topic(session_id="s1", topic_name=topic)

        # Even with no extractable text, the layout dirs must be created and
        # a master report + skipped marker must land in the canonical spots.
        mr = paths.model_output_root(_analyze._config, "MySession")
        self.assertTrue(os.path.isdir(paths.topic_cache_dir(mr, topic)),
                        f"cache dir missing: {paths.topic_cache_dir(mr, topic)}")
        self.assertTrue(os.path.isfile(paths.topic_master_report(mr, topic)))
        # The skipped marker is the GUI-parity signal that the paper is permanently
        # unprocessable.
        skipped = os.path.join(paths.topic_cache_dir(mr, topic), "paper1.skipped")
        self.assertTrue(os.path.isfile(skipped),
                        f".skipped marker missing: {skipped}")
        # Result reports the model root so chained calls can find it.
        self.assertEqual(result["model_root"], mr)


class TestApiListingScope(unittest.TestCase):
    """list_downloads / list_download_tree / list_summaries must scope to the
    session when session_id is given."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rf_list_")
        self.dl_root = os.path.join(self.tmp, "downloads")
        self.sum_root = os.path.join(self.tmp, "summaries")
        os.makedirs(self.dl_root)
        os.makedirs(self.sum_root)
        import researchforge_api._config as _c
        import researchforge_api._sessions as _s
        _c._cfg = None
        _s._mgr = None
        self._cfg_patch = mock.patch("researchforge_api._config._get_cfg")
        cfg_mock = self._cfg_patch.start()
        cfg_mock.return_value = type("C", (), {
            "get": self._cfg_get, "set": lambda s, k, v: None,
        })()
        self._session_patch = mock.patch("researchforge_api._sessions.SessionManager")
        sm_cls = self._session_patch.start()
        self._fake_sessions = {}
        inst = sm_cls.return_value
        inst.load = self._fake_load
        inst.save = self._fake_save
        inst.list_sessions = lambda: list(self._fake_sessions.values())

    def tearDown(self):
        self._cfg_patch.stop()
        self._session_patch.stop()

    def _cfg_get(self, key, default=None):
        return {
            "output_root": self.dl_root,
            "summary_output_dir": self.sum_root,
            "llm_model": "m",
        }.get(key, default)

    def _fake_load(self, sid):
        return self._fake_sessions.get(sid)

    def _fake_save(self, sid, data):
        self._fake_sessions[sid] = data

    def _register(self, sid, name):
        from researchforge_api import _sessions
        self._fake_sessions[sid] = _sessions.blank_session(name=name)

    def test_list_downloads_scoped_to_session(self):
        from researchforge_api import _download
        self._register("s1", "Alpha")
        self._register("s2", "Beta")
        # Create PDFs under both sessions.
        for sess, topic in [("Alpha", "t1"), ("Beta", "t2")]:
            d = os.path.join(self.dl_root, sess, topic)
            os.makedirs(d)
            with open(os.path.join(d, "p.pdf"), "wb") as f:
                f.write(b"%PDF-1.4")
        # Flat (no session) sees both.
        flat = _download.list_downloads()
        self.assertEqual(len(flat), 2)
        # Session-scoped sees only Alpha's.
        scoped = _download.list_downloads(session_id="s1")
        self.assertEqual(len(scoped), 1)
        self.assertTrue(scoped[0]["path"].replace("\\", "/").endswith("Alpha/t1/p.pdf"))

    def test_list_download_tree_scoped_to_session_topics(self):
        from researchforge_api import _download
        self._register("s1", "Alpha")
        for t in ("topicA", "topicB"):
            d = os.path.join(self.dl_root, "Alpha", t)
            os.makedirs(d)
            with open(os.path.join(d, "x.pdf"), "wb") as f:
                f.write(b"%PDF")
        tree = _download.list_download_tree(session_id="s1")
        names = sorted(t["name"] for t in tree["topics"])
        self.assertEqual(names, ["topicA", "topicB"])
        self.assertEqual(tree["dir"], os.path.join(self.dl_root, "Alpha"))

    def test_list_summaries_scoped_to_session(self):
        from researchforge_api import _analyze
        self._register("s1", "Alpha")
        # Drop a fake topic summary in Alpha's model folder.
        from gui import paths
        mr = paths.model_output_root(_analyze._config, "Alpha")
        os.makedirs(mr)
        with open(os.path.join(mr, "topic1_SUMMARY.md"), "w") as f:
            f.write("summary")
        items = _analyze.list_summaries(session_id="s1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "topic")
        self.assertEqual(items[0]["topic"], "topic1")
        self.assertEqual(items[0]["model"], "m")


class TestSaveAuditBundle(unittest.TestCase):
    """save_audit_results writes a GUI-loadable bundle with the GUI's slug."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rf_audit_")
        self._cfg_patch = mock.patch("researchforge_api._config._get_cfg")
        cfg_mock = self._cfg_patch.start()
        cfg_mock.return_value = type("C", (), {
            "get": lambda s, k, d="": {"audit_output_dir": self.tmp,
                                       "llm_model": "m",
                                       "llm_endpoint": "e"}.get(k, d),
            "set": lambda s, k, v: None,
        })()

    def tearDown(self):
        self._cfg_patch.stop()

    def test_writes_both_json_and_md(self):
        from researchforge_api import _audit
        result = _audit.save_audit_results(
            "REPORT BODY", pdf_path="D:/paper.pdf",
            scores={"Abstract": 80}, questions=["q1", "q2"],
            paper_title="Spatial Artifact Coherence",
            source_type="latex", model="m", endpoint="e",
        )
        self.assertIn("json_path", result)
        self.assertIn("md_path", result)
        self.assertTrue(os.path.isfile(result["json_path"]))
        self.assertTrue(os.path.isfile(result["md_path"]))

    def test_bundle_schema_matches_gui(self):
        from researchforge_api import _audit
        result = _audit.save_audit_results(
            "REPORT", pdf_path="D:/p.pdf", paper_title="X Y Z W",
            save_name="my_audit", scores={"Abstract": 90}, questions=["why?"],
        )
        with open(result["json_path"], "r", encoding="utf-8") as f:
            bundle = json.load(f)
        # GUI's _load_audit checks for these exact keys.
        self.assertEqual(bundle["schema"], "researchforge.audit/1")
        self.assertIn("paper", bundle)
        self.assertIn("audit", bundle)
        self.assertEqual(bundle["paper"]["path"], "D:/p.pdf")
        self.assertEqual(bundle["audit"]["results"], "REPORT")
        self.assertEqual(bundle["audit"]["scores"], {"Abstract": 90})
        self.assertIn("why?", bundle["audit"]["questions"])

    def test_default_filename_uses_gui_slug_convention(self):
        from researchforge_api import _audit
        # Fixed timestamp via save_name pattern assertion would be fragile;
        # instead assert the prefix + source_type segments are present.
        result = _audit.save_audit_results(
            "R", paper_title="Spatial Artifact Coherence", source_type="latex",
        )
        base = os.path.basename(result["json_path"])
        self.assertTrue(base.startswith("audit_report_"), base)
        self.assertIn("_latex_", base)
        self.assertIn("Spatial_Artifact_Coherence", base)


class TestGuiAuditHelpersDelegation(unittest.TestCase):
    """The GUI's _title_to_slug / _make_audit_filename must delegate to paths
    so both layers produce identical slugs. Importing audit_tab pulls in PySide6
    but never constructs a widget here, so it's safe in headless test envs."""

    @classmethod
    def setUpClass(cls):
        try:
            from gui import audit_tab
            cls.audit_tab = audit_tab
        except Exception:
            cls.audit_tab = None

    def test_title_to_slug_matches_paths(self):
        if self.audit_tab is None:
            self.skipTest("PySide6 not importable in this env")
        from gui import paths
        for title in ["Spatial Artifact Coherence", "Hello, World!", "", "a b c d e f g h"]:
            self.assertEqual(self.audit_tab._title_to_slug(title),
                             paths.title_to_slug(title),
                             f"mismatch for {title!r}")

    def test_make_audit_filename_matches_paths_format(self):
        if self.audit_tab is None:
            self.skipTest("PySide6 not importable in this env")
        from gui import paths
        # Both produce the same shape; only the timestamp differs at runtime,
        # so compare the slug + source_type segments via prefix structure.
        g = self.audit_tab._make_audit_filename("Spatial Coherence", "latex")
        p = paths.audit_filename("Spatial Coherence", source_type="latex")
        # Same prefix up to the timestamp.
        self.assertEqual(g.split("_Saturday_")[0] if "_Saturday_" in g else g.split("_")[0:4],
                         p.split("_Saturday_")[0] if "_Saturday_" in p else p.split("_")[0:4])


if __name__ == "__main__":
    unittest.main()
