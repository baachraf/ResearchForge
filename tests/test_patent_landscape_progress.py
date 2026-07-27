"""generate_patent_landscape must report progress, and count metadata-only
patents correctly even when every analysis is served from cache.

Two symptoms the user hit on a 25-patent session:
- the UI showed "Analysing patents..." and nothing else for ~8 minutes, because
  the worker emitted a single message and then blocked with no per-patent tick;
- re-running (warm cache) would have dropped the "N without claims text" line
  from the header, because that count was derived from the analysis call and
  skipped whenever a patent was read from cache.
"""

import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui.session_manager as sm  # noqa: E402
import researchforge_api._sessions as _s  # noqa: E402
import researchforge_api._analyze as _a  # noqa: E402


def _patent(pubnum, claims=""):
    return {
        "title": f"Patent {pubnum}",
        "abstract": "An abstract.",
        "doc_type": "patent",
        "source": "EPO OPS",
        "patent_meta": {"publication_number": pubnum, "claims_text": claims},
    }


class _Cfg:
    def __init__(self, summary_dir):
        self._d = {"summary_output_dir": summary_dir, "llm_model": "test-model"}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value


class _FakeMsg:
    content = "ANALYSIS TEXT"


class _FakeChoice:
    message = _FakeMsg()


class _FakeResp:
    choices = [_FakeChoice()]


class _FakeClient:
    chat = types.SimpleNamespace(
        completions=types.SimpleNamespace(create=lambda **kw: _FakeResp()))
    _client = types.SimpleNamespace(close=lambda: None)


class TestLandscapeProgress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessions)
        # 2 with claims (EP/WO), 3 metadata-only (US/CN/KR) — same shape as rPPG.
        results = [
            _patent("EP1A", "1. A method ..."),
            _patent("WO2A", "1. A system ..."),
            _patent("US3A"),
            _patent("CN4A"),
            _patent("KR5A"),
        ]
        with open(os.path.join(self.sessions, "sess.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"name": "S", "results": results}, f)
        self._p = mock.patch.object(sm, "SESSIONS_DIR", self.sessions)
        self._p.start()
        _s._mgr = None
        self.cfg = _Cfg(os.path.join(self.tmp, "summary"))

    def tearDown(self):
        self._p.stop()
        _s._mgr = None

    def _run(self, on_progress=None):
        with mock.patch.object(_a, "_config", self.cfg), \
                mock.patch.object(_a._llm, "create_client_from_config",
                                  return_value=_FakeClient()), \
                mock.patch.object(_a, "_prompts") as mp:
            mp.get_prompt.return_value = "PROMPT {claims_text} {patent_analyses}"
            return _a.generate_patent_landscape(
                session_id="sess", on_progress=on_progress)

    def test_progress_is_reported_per_patent_plus_synthesis(self):
        ticks = []
        res = self._run(on_progress=lambda d, t, l: ticks.append((d, t, l)))
        self.assertNotIn("error", res)
        # `done` is COMPLETED count: 5 per-patent ticks (0..4) never collide with
        # the single synthesis tick (5, 5).
        per_patent = [t for t in ticks if t[0] < t[1]]
        self.assertEqual([t[0] for t in per_patent], [0, 1, 2, 3, 4])
        self.assertTrue(all(t[1] == 5 for t in ticks))
        synthesis = [t for t in ticks if t[0] >= t[1]]
        self.assertEqual(len(synthesis), 1, "exactly one synthesis tick expected")

    def test_metadata_only_count_correct_cold(self):
        res = self._run()
        self.assertEqual(res["patents"], 5)
        self.assertEqual(res["without_claims"], 3)

    def test_metadata_only_count_survives_a_warm_cache(self):
        first = self._run()
        self.assertEqual(first["without_claims"], 3)
        # Cache is now warm; a re-run reads every analysis from disk.
        second = self._run()
        self.assertEqual(second["patents"], 5)
        self.assertEqual(second["without_claims"], 3,
                         "warm-cache re-run lost the metadata-only count")

    def test_header_states_no_claims_count_on_warm_cache(self):
        self._run()
        res = self._run()
        with open(res["path"], encoding="utf-8") as f:
            head = f.read()
        # The count survives a warm cache and the wording no longer implies the
        # patent lacks claims — only that EPO OPS did not serve the text.
        self.assertIn("3 with no claims text available via EPO OPS", head)
        self.assertNotIn("has no claims", head.lower())

    def test_no_progress_callback_is_fine(self):
        res = self._run(on_progress=None)
        self.assertNotIn("error", res)

    def test_a_throwing_callback_does_not_break_the_run(self):
        def boom(*a):
            raise RuntimeError("ui gone")
        res = self._run(on_progress=boom)
        self.assertNotIn("error", res)
        self.assertEqual(res["patents"], 5)


if __name__ == "__main__":
    unittest.main()
