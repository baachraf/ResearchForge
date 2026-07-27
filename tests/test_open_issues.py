"""Regression tests for the "Open issues — next session" bugs found 2026-06-25
via the MCP layer (see CLAUDE.md). One test per issue, proving the fix by
execution rather than by argument. Only the LLM HTTP call is mocked; the config,
session manager, path logic, and download registry are all real (redirected to a
temp app-data dir, exactly like tests/test_e2e_parity.py).

Issue map:
  #1  rf_score_session actually scores + persists onto the session
  #2  refresh_session_downloads reports file_exists per real file, not per folder
  #3  score_session reload-merges so an interleaved write is not clobbered
  #4  search compact option trims the payload (id/title/url/year/source)
  #5  score_papers populates score_reason (was blank at depth 1)
"""
import os
import tempfile
import unittest
from unittest import mock

import gui.config_manager as cm
from gui.config_manager import ConfigManager
from gui import paths
from research_downloader.registry import Registry


class _FakeResp:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class _FakeClient:
    """Returns a canned scoring reply for every call."""
    def __init__(self, reply="85 - same problem, different approach"):
        self._reply = reply
        self.chat = self
        self.completions = self
        self._client = self

    def create(self, **kw):
        return _FakeResp(self._reply)

    def close(self):
        pass


class _IsolatedCase(unittest.TestCase):
    """Redirects the whole app-data dir to temp; mocks only the LLM."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rf_issues_")
        self.app_data = os.path.join(self.tmp, "ResearchForge")
        self.dl_root = os.path.join(self.tmp, "downloads")
        self.sum_root = os.path.join(self.tmp, "summaries")
        for d in (self.app_data, self.dl_root, self.sum_root):
            os.makedirs(d)

        import shutil
        self._real_settings_path = cm.SETTINGS_PATH
        self._settings_backup = None
        if os.path.isfile(self._real_settings_path):
            self._settings_backup = self._real_settings_path + ".issuesbak"
            shutil.copy2(self._real_settings_path, self._settings_backup)

        self._orig = {k: getattr(cm, k) for k in
                      ("APP_DATA_DIR", "SETTINGS_PATH", "PROMPTS_DIR", "SESSIONS_DIR")}
        cm.APP_DATA_DIR = self.app_data
        cm.SETTINGS_PATH = os.path.join(self.app_data, "settings.json")
        cm.PROMPTS_DIR = os.path.join(self.app_data, "prompts")
        cm.SESSIONS_DIR = os.path.join(self.app_data, "sessions")
        os.makedirs(cm.SESSIONS_DIR, exist_ok=True)
        os.makedirs(cm.PROMPTS_DIR, exist_ok=True)

        import researchforge_api._config as _c
        import researchforge_api._sessions as _s
        _c._cfg = None
        _s._mgr = None

        self.cfg = ConfigManager(config_path=cm.SETTINGS_PATH)
        self.cfg.set("output_root", self.dl_root)
        self.cfg.set("summary_output_dir", self.sum_root)
        self.cfg.set("llm_model", "test-model")
        self.cfg.set("llm_provider", "LM Studio")
        self.cfg.set("llm_endpoint", "http://127.0.0.1:1234/v1")

        self._cfg_patch = mock.patch(
            "researchforge_api._config._get_cfg", return_value=self.cfg)
        self._cfg_patch.start()

    def tearDown(self):
        try:
            self._cfg_patch.stop()
        except RuntimeError:
            pass
        for k, v in self._orig.items():
            setattr(cm, k, v)
        import shutil
        if self._settings_backup and os.path.isfile(self._settings_backup):
            shutil.move(self._settings_backup, self._real_settings_path)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mock_llm(self, reply="85 - same problem, different approach"):
        return mock.patch("researchforge_api._score._llm.create_client_from_config",
                          return_value=_FakeClient(reply))

    def _make_session(self, name, results):
        from researchforge_api import _sessions
        s = _sessions.blank_session(name=name, context="rPPG blood pressure from face video",
                                    focus_keywords="rPPG, morphology")
        s["results"] = results
        _sessions.save_session(name, s)
        return name


class TestIssue1And3ScoreSession(_IsolatedCase):

    def test_issue1_score_session_persists(self):
        """#1: score_session iterates the session's results, scores them, and
        persists relevance_score/score_reason back onto the session."""
        from researchforge_api import _score, _sessions
        sid = self._make_session("Sess1", [
            {"id": "p1", "title": "rPPG morphology paper", "abstract": "short abstract about rPPG."},
            {"id": "p2", "title": "another rPPG paper", "abstract": "short abstract two."},
        ])
        with self._mock_llm("85 - same problem, different approach"):
            out = _score.score_session(sid)

        self.assertTrue(out.get("saved"))
        self.assertEqual(out.get("scored"), 2, f"expected 2 scored, got {out}")

        reloaded = _sessions.load_session(sid)
        by_id = {r["id"]: r for r in reloaded["results"]}
        self.assertEqual(by_id["p1"]["relevance_score"], 85)
        self.assertEqual(by_id["p2"]["relevance_score"], 85)
        self.assertTrue(by_id["p1"]["score_reason"], "score_reason should not be blank")

    def test_issue3_interleaved_write_not_clobbered(self):
        """#3: if a result is added to the session on disk between score_session's
        load and its save (as a concurrent rf_search would), the reload-merge
        preserves it instead of clobbering it with the stale snapshot."""
        from researchforge_api import _score, _sessions
        sid = self._make_session("Sess3", [
            {"id": "p1", "title": "paper one", "abstract": "abs one"},
            {"id": "p2", "title": "paper two", "abstract": "abs two"},
        ])

        real_score_papers = _score.score_papers

        def _racing_score_papers(targets, **kw):
            # Score in place (what the real one does)...
            result = real_score_papers(targets, **kw)
            # ...then simulate a concurrent search writing a NEW result to disk.
            fresh = _sessions.load_session(sid)
            fresh["results"].append({"id": "p3", "title": "just-searched paper",
                                     "abstract": "arrived mid-scoring"})
            _sessions.save_session(sid, fresh)
            return result

        with self._mock_llm("90 - strong match"), \
             mock.patch("researchforge_api._score.score_papers", side_effect=_racing_score_papers):
            _score.score_session(sid)

        reloaded = _sessions.load_session(sid)
        ids = {r["id"] for r in reloaded["results"]}
        self.assertEqual(ids, {"p1", "p2", "p3"},
                         "the interleaved result p3 must survive the score save")
        by_id = {r["id"]: r for r in reloaded["results"]}
        self.assertEqual(by_id["p1"]["relevance_score"], 90)
        self.assertEqual(by_id["p2"]["relevance_score"], 90)


class TestIssue2RefreshDownloads(_IsolatedCase):

    def test_issue2_file_exists_reflects_disk_per_file(self):
        """#2: file_exists is set per actual file on disk, not per query folder.
        p1 downloaded (file present), p2 in registry but file deleted, p3 never
        downloaded -> only p1 counts as existing."""
        from researchforge_api import _download, _sessions
        session_name = "Sess2"
        folder = "rppg_query"
        results = [
            {"id": "p1", "title": "paper one", "output_folder": folder, "query_key": folder},
            {"id": "p2", "title": "paper two", "output_folder": folder, "query_key": folder},
            {"id": "p3", "title": "paper three", "output_folder": folder, "query_key": folder},
        ]
        sid = self._make_session(session_name, results)

        topic_dir = paths.topic_downloads_dir(self.cfg, session_name, folder)
        os.makedirs(topic_dir, exist_ok=True)
        reg = Registry(os.path.join(topic_dir, "downloads_registry.db"))

        # p1: real file + registry entry.
        p1_path = os.path.join(topic_dir, "p1.pdf")
        with open(p1_path, "wb") as f:
            f.write(b"%PDF-1.4 test")
        reg.record_download("p1", "paper one", "arxiv", p1_path, "http://x/p1")

        # p2: registry entry but the file is gone (stale registry).
        p2_path = os.path.join(topic_dir, "p2.pdf")
        reg.record_download("p2", "paper two", "arxiv", p2_path, "http://x/p2")
        # (never create p2_path)

        # p3: nothing.

        out = _download.refresh_session_downloads(sid)
        self.assertEqual(out["downloaded"], 1, f"only p1 is truly on disk: {out}")

        reloaded = _sessions.load_session(sid)
        by_id = {r["id"]: r for r in reloaded["results"]}
        self.assertTrue(by_id["p1"]["file_exists"])
        self.assertFalse(by_id["p2"]["file_exists"], "stale registry entry must not report exists")
        self.assertFalse(by_id["p3"]["file_exists"])


class TestIssue4Compact(_IsolatedCase):

    def test_issue4_compact_results_drops_bulk_fields(self):
        """#4: compact_results keeps only the small field set; abstracts/authors
        (the payload bulk) are dropped."""
        from researchforge_api import _search
        rows = [{
            "id": "a1", "title": "Paper A", "url": "http://x/a", "year": 2024,
            "query_source": "arxiv",
            "abstract": "x" * 5000, "authors": ["A"] * 50, "citations": list(range(100)),
        }]
        out = _search.compact_results(rows)
        self.assertEqual(set(out[0]), {"id", "title", "url", "year", "source", "query_source"})
        self.assertEqual(out[0]["source"], "arxiv")  # filled from query_source
        self.assertNotIn("abstract", out[0])
        self.assertNotIn("authors", out[0])

    def test_issue4_custom_fields(self):
        from researchforge_api import _search
        rows = [{"id": "a1", "title": "T", "url": "u", "abstract": "big"}]
        out = _search.compact_results(rows, fields=["id", "title"])
        self.assertEqual(set(out[0]), {"id", "title"})

    def test_issue4_search_papers_compact_flag(self):
        """search_papers(compact=True) trims results but keeps the full total."""
        from researchforge_api import _search

        class _FakeSource:
            def search(self, **kw):
                return [{"id": f"r{i}", "title": f"T{i}", "url": f"u{i}", "year": 2020,
                         "abstract": "y" * 3000} for i in range(5)]

        with mock.patch.object(_search, "ArxivSource", _FakeSource):
            out = _search.search_papers("rppg", sources=["arxiv"], compact=True)

        self.assertEqual(out["total"], 5)
        self.assertTrue(out["compact"])
        self.assertNotIn("abstract", out["results"][0])
        self.assertIn("title", out["results"][0])


class TestIssue5ScoreReason(_IsolatedCase):

    def test_issue5_reason_from_llm(self):
        """#5: a '<score> - <reason>' reply populates score_reason."""
        from researchforge_api import _score
        papers = [{"id": "p1", "title": "t", "abstract": "short abs"}]
        with self._mock_llm("72 - same domain, different sensor"):
            res = _score.score_papers(papers, research_context="rppg")
        self.assertEqual(res[0]["score"], 72)
        self.assertIn("different sensor", res[0]["reason"])

    def test_issue5_bare_integer_gets_band_reason(self):
        """#5: even if the model returns only an integer, score_reason falls back
        to a band label (never blank for a valid score)."""
        from researchforge_api import _score
        papers = [{"id": "p1", "title": "t", "abstract": "short abs"}]
        with self._mock_llm("40"):
            res = _score.score_papers(papers, research_context="rppg")
        self.assertEqual(res[0]["score"], 40)
        self.assertTrue(res[0]["reason"], "reason must not be blank")
        self.assertEqual(res[0]["reason"], "same broad domain, different specific problem")


class TestIssue4MCPWrapper(_IsolatedCase):
    """Integration test for the actual MCP tool rf_search (not just the API
    helper): the session must keep FULL records while the returned payload is
    compact — i.e. compaction happens AFTER session registration."""

    def _call_rf_search(self, **kwargs):
        import mcp_server_researchforge as m
        # rf_search is wrapped by FastMCP; call the underlying function.
        fn = getattr(m.rf_search, "fn", m.rf_search)
        # Mock the network search to return full records (with abstracts).
        full = [{"id": f"r{i}", "title": f"Paper {i}", "url": f"http://x/{i}",
                 "year": 2021, "query_source": "arxiv",
                 "abstract": "A" * 2000} for i in range(3)]
        with mock.patch.object(m.rf, "search",
                               return_value={"results": full, "sources": {"arxiv": 3}, "total": 3}):
            return fn(**kwargs)

    def test_session_keeps_full_records_response_is_compact(self):
        from researchforge_api import _sessions
        sid = self._make_session("MCPSearchSess", [])

        out = self._call_rf_search(query="rppg", session_id=sid, query_name="q1", compact=True)

        # Returned payload is compact (no abstracts) ...
        self.assertTrue(out.get("compact"))
        self.assertNotIn("abstract", out["results"][0])
        self.assertEqual(set(out["results"][0]) & {"id", "title", "url", "year", "source"},
                         {"id", "title", "url", "year", "source"})

        # ... but the SESSION stored the full records (abstracts intact).
        reloaded = _sessions.load_session(sid)
        self.assertEqual(len(reloaded["results"]), 3)
        self.assertTrue(all(r.get("abstract") for r in reloaded["results"]),
                        "session must keep full records with abstracts")

    def test_compact_false_returns_full_payload(self):
        out = self._call_rf_search(query="rppg", session_id="", compact=False)
        self.assertIn("abstract", out["results"][0])


class TestConcurrentSettingsWrite(_IsolatedCase):
    """settings.json is shared by the GUI and one `--mcp` server per registered
    client, all long-lived. A save must not write back a snapshot taken at
    startup, or the last process to touch any setting erases every key the
    others saved in the meantime.

    Observed live 2026-07-27: EPO OPS credentials written to the file vanished
    between two runs while six `ResearchForge.exe --mcp` servers were resident.
    """

    def test_save_does_not_clobber_another_processs_key(self):
        path = os.path.join(self.app_data, "shared_settings.json")
        stale = ConfigManager(config_path=path)      # long-lived process
        stale.set("llm_model", "model-a")

        other = ConfigManager(config_path=path)      # e.g. the GUI's Set Keys dialog
        other.set("epo_ops_key", "SECRET-KEY")

        stale.set("llm_model", "model-b")            # any unrelated setting

        import json as _json
        with open(path, encoding="utf-8") as f:
            on_disk = _json.load(f)
        self.assertEqual(on_disk["epo_ops_key"], "SECRET-KEY",
                         "a stale process erased a key written by another process")
        self.assertEqual(on_disk["llm_model"], "model-b")

    def test_own_edits_still_win_over_disk(self):
        path = os.path.join(self.app_data, "own_edit.json")
        a = ConfigManager(config_path=path)
        a.set("llm_model", "old")
        b = ConfigManager(config_path=path)
        b.set("llm_model", "new")
        self.assertEqual(ConfigManager(config_path=path).get("llm_model"), "new")

    def test_saver_picks_up_concurrent_values_in_memory(self):
        path = os.path.join(self.app_data, "pickup.json")
        a = ConfigManager(config_path=path)
        a.set("llm_model", "m")
        b = ConfigManager(config_path=path)
        b.set("epo_ops_secret", "S")
        a.set("llm_model", "m2")
        self.assertEqual(a.get("epo_ops_secret"), "S")


class TestQuerySourceRouting(_IsolatedCase):
    """The GUI replaces every query's `sources` with the global Settings
    checkboxes at search time, so ticking a patent provider sent every academic
    query to it. A query may now pin its own routing via lock_sources."""

    def test_locked_query_keeps_its_own_sources(self):
        from researchforge_api import _sessions
        sid = self._make_session("RoutingSess", [])
        _sessions.add_query_to_session(sid, "rppg blood pressure",
                                       sources=["epo_ops"], lock_sources=True,
                                       name="patents")
        _sessions.add_query_to_session(sid, "rppg morphology",
                                       sources=["arxiv"], name="papers")
        qs = _sessions.load_session(sid)["queries"]
        locked = [q for q in qs if q["name"] == "patents"][0]
        plain = [q for q in qs if q["name"] == "papers"][0]
        self.assertTrue(locked["lock_sources"])
        self.assertEqual(locked["sources"], ["epo_ops"])
        self.assertFalse(plain["lock_sources"])

    def test_gui_override_skips_locked_queries(self):
        """Reproduces gui/search_tab.py::_do_search on plain dicts."""
        from researchforge_api._sessions import normalize_query
        live = ["arxiv", "epo_ops"]
        queries = [
            normalize_query({"query": "papers", "sources": ["arxiv"]}),
            normalize_query({"query": "patents", "sources": ["epo_ops"],
                             "lock_sources": True}),
        ]
        for q in queries:
            if q.get("lock_sources") and q.get("sources"):
                continue
            q["sources"] = live
        self.assertEqual(queries[0]["sources"], live)
        self.assertEqual(queries[1]["sources"], ["epo_ops"],
                         "a locked patent query must not inherit academic sources")

    def test_legacy_query_without_flag_still_follows_global(self):
        from researchforge_api._sessions import normalize_query
        q = normalize_query({"query": "legacy", "sources": ["arxiv"]})
        self.assertIn("lock_sources", q)
        self.assertFalse(q["lock_sources"])


if __name__ == "__main__":
    unittest.main()
