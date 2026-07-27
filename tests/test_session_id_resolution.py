"""A session's id and its `name` are independent — callers may hold either.

Sessions are stored as `<id>.json`, but the `name` field is separate: the GUI
saves `session_20260727_114039.json` with name "rPPG systems". The Generate
Reports tab only ever holds the *name*, and passed it where an id was expected,
so the API could not load the session and reported:

    synthesize_* needs either an explicit output_dir or a valid session_id ...
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

SESSION_ID = "session_20260727_114039"
SESSION_NAME = "rPPG systems"


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


class TestSessionIdResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.sessions)
        with open(os.path.join(self.sessions, f"{SESSION_ID}.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"name": SESSION_NAME,
                       "results": [_patent("EP1A", "1. A method ..."),
                                   _patent("US2A")]}, f)
        self._p = mock.patch.object(sm, "SESSIONS_DIR", self.sessions)
        self._p.start()
        _s._mgr = None

    def tearDown(self):
        self._p.stop()
        _s._mgr = None

    # ── resolve_session_id ───────────────────────────────────────────────────

    def test_id_resolves_to_itself(self):
        self.assertEqual(_s.resolve_session_id(SESSION_ID), SESSION_ID)

    def test_name_resolves_to_id(self):
        self.assertEqual(_s.resolve_session_id(SESSION_NAME), SESSION_ID)

    def test_unknown_ref_resolves_to_empty(self):
        self.assertEqual(_s.resolve_session_id("no such session"), "")

    def test_empty_ref_resolves_to_empty(self):
        self.assertEqual(_s.resolve_session_id(""), "")

    def test_session_name_accepts_a_name(self):
        self.assertEqual(_a._session_name(SESSION_NAME), SESSION_NAME)
        self.assertEqual(_a._session_name(SESSION_ID), SESSION_NAME)

    # ── the reported failure ─────────────────────────────────────────────────

    def test_landscape_by_name_does_not_raise_the_session_id_error(self):
        cfg = _Cfg(os.path.join(self.tmp, "summary"))
        with mock.patch.object(_a, "_config", cfg), \
                mock.patch.object(_a._llm, "create_client_from_config") as mk, \
                mock.patch.object(_a, "_prompts") as mp:
            mp.get_prompt.return_value = "PROMPT {claims_text}"
            mk.return_value = _FakeClient()
            res = _a.generate_patent_landscape(session_id=SESSION_NAME)

        self.assertNotIn("error", res, res.get("error", ""))
        self.assertEqual(res["patents"], 2)
        self.assertEqual(res["without_claims"], 1)
        self.assertIn(os.path.join("rPPG systems", "test-model"), res["path"])
        self.assertTrue(os.path.isfile(res["path"]))

    def test_bad_session_error_names_what_failed(self):
        cfg = _Cfg(os.path.join(self.tmp, "summary"))
        with mock.patch.object(_a, "_config", cfg):
            res = _a.generate_patent_landscape(session_id="nope")
        self.assertIn("error", res)
        self.assertIn("nope", res["error"],
                      "the error must name the ref that failed to resolve")


class _FakeMsg:
    content = "ANALYSIS"


class _FakeChoice:
    message = _FakeMsg()


class _FakeResp:
    choices = [_FakeChoice()]


class _FakeCompletions:
    def create(self, **kw):
        return _FakeResp()


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()
    _client = types.SimpleNamespace(close=lambda: None)


if __name__ == "__main__":
    unittest.main()
