"""A mixed paper+patent session must still produce GLOBAL_SUMMARY.md, a patent that
fails to analyse must not vanish silently, and an empty LLM completion must be retried.

Found live on 2026-08-06 driving the release exe over MCP: a patents-only query folder
correctly has no papers to analyse, but that was reported as an error, and
run_full_pipeline gated global synthesis on "no topic errored" — so every mixed session
lost its global summary with no message. The same run lost 2 of 18 patents to single
empty completions, including the only patent carrying claims text, while the report
header still read as complete.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from researchforge_api import _analyze


class _FakeMessage:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeCompletions:
    """Returns the queued contents in order, one per create() call."""

    def __init__(self, contents):
        self._contents = list(contents)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        content = self._contents.pop(0) if self._contents else ""
        return type("R", (), {"choices": [_FakeMessage(content)]})()


class _FakeClient:
    def __init__(self, contents):
        self.chat = type("C", (), {"completions": _FakeCompletions(contents)})()
        self._client = type("H", (), {"close": lambda self: None})()

    @property
    def calls(self):
        return self.chat.completions.calls


class _Patch:
    """Swap module attributes for the duration of a test."""

    def __init__(self, module, **attrs):
        self.module, self.attrs, self.saved = module, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.module, k)
            setattr(self.module, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(self.module, k, v)


class TestPatentsOnlyFolderIsSkippedNotFailed(unittest.TestCase):
    def test_no_papers_returns_skipped_not_error(self):
        with tempfile.TemporaryDirectory() as d:
            # One patent PDF (recognised by its .json sidecar), no papers.
            open(os.path.join(d, "EP1A.pdf"), "wb").write(b"%PDF-1.4")
            open(os.path.join(d, "EP1A.json"), "w").write("{}")
            out = _analyze.synthesize_topic(input_dir=d, output_dir=d, topic_name="pats")
            self.assertIn("skipped", out)
            self.assertNotIn("error", out)
            self.assertEqual(out["topic"], "pats")


class TestGlobalSynthesisGate(unittest.TestCase):
    """run_full_pipeline: skipped topics must not block global synthesis."""

    def _run(self, topic_results):
        seen = {}
        order = list(topic_results)

        def fake_topic(**kwargs):
            return order.pop(0)

        def fake_global(**kwargs):
            seen["called"] = True
            return {"path": "GLOBAL_SUMMARY.md"}

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out:
            for name in ("papers", "patents"):
                os.makedirs(os.path.join(root, name), exist_ok=True)
            with _Patch(_analyze, synthesize_topic=fake_topic,
                        synthesize_global=fake_global):
                res = _analyze.run_full_pipeline(input_dir=root, output_dir=out)
        return res, seen.get("called", False)

    def test_mixed_session_still_runs_global(self):
        res, called = self._run([
            {"topic": "papers", "processed": 2},
            {"skipped": "No papers in this folder", "topic": "patents"},
        ])
        self.assertTrue(called, "global synthesis must run when the only "
                                "non-analysed topic was skipped, not failed")
        self.assertIsNotNone(res["global"])

    def test_real_error_still_blocks_global(self):
        _, called = self._run([
            {"topic": "papers", "processed": 2},
            {"error": "LLM unreachable", "topic": "patents"},
        ])
        self.assertFalse(called, "a genuine topic error must still block global")

    def test_patents_only_session_does_not_run_global(self):
        res, called = self._run([
            {"skipped": "No papers in this folder", "topic": "papers"},
            {"skipped": "No papers in this folder", "topic": "patents"},
        ])
        self.assertFalse(called, "nothing to synthesise globally")
        self.assertIsNone(res["global"])


class TestAnalyzePatentRetriesEmpty(unittest.TestCase):
    PATENT = {"id": "WO1A", "title": "T", "abstract": "A",
              "patent_meta": {"publication_number": "WO1A", "claims_text": "1. A method."}}

    def _analyze_with(self, contents):
        client = _FakeClient(contents)
        with _Patch(_analyze, _prompts=type("P", (), {
                        "get_prompt": staticmethod(lambda k: "PATENT {title} {claims_text}")})(),
                    _llm=type("L", (), {
                        "create_client_from_config": staticmethod(lambda **kw: client)})(),
                    _config=type("C", (), {"get": staticmethod(lambda k, d="": "m")})()):
            return _analyze.analyze_patent(self.PATENT), client

    def test_empty_then_content_succeeds(self):
        out, client = self._analyze_with(["", "real analysis"])
        self.assertEqual(out.get("text"), "real analysis")
        self.assertEqual(client.calls, 2, "must retry exactly once")
        self.assertTrue(out["claims_available"])

    def test_two_empties_report_the_attempts(self):
        out, client = self._analyze_with(["", ""])
        self.assertIn("error", out)
        self.assertIn("2 attempts", out["error"])
        self.assertEqual(client.calls, 2, "must not retry more than once")

    def test_content_first_time_makes_one_call(self):
        out, client = self._analyze_with(["good"])
        self.assertEqual(out.get("text"), "good")
        self.assertEqual(client.calls, 1)


class TestLandscapeHeaderDisclosesFailures(unittest.TestCase):
    """A report missing patents must say so, in the document, not just the return value."""

    def _landscape(self, patents, analyse):
        with tempfile.TemporaryDirectory() as root:
            client = _FakeClient(["## 1. SUMMARY\n\nbody"])
            with _Patch(_analyze,
                        _patent_results=lambda sid: patents,
                        _resolve_model_root=lambda sid, od: root,
                        _session_context_intent=lambda sid: ("ctx", "int"),
                        analyze_patent=analyse,
                        _prompts=type("P", (), {
                            "get_prompt": staticmethod(lambda k: "L {patent_analyses}")})(),
                        _llm=type("L", (), {
                            "create_client_from_config": staticmethod(lambda **kw: client)})(),
                        _config=type("C", (), {"get": staticmethod(lambda k, d="": "m")})()):
                res = _analyze.generate_patent_landscape(session_id="S")
                with open(res["path"], encoding="utf-8") as f:
                    return res, f.read()

    def test_dropped_patent_named_in_the_report(self):
        patents = [
            {"id": "WO1A", "title": "kept", "patent_meta": {"publication_number": "WO1A",
                                                            "claims_text": "1. A method."}},
            {"id": "CN2B", "title": "lost", "patent_meta": {"publication_number": "CN2B",
                                                            "claims_text": ""}},
        ]

        def analyse(p, **kw):
            if p["id"] == "CN2B":
                return {"error": "LLM returned an empty analysis (2 attempts)"}
            return {"text": "ok", "claims_available": True}

        res, text = self._landscape(patents, analyse)
        self.assertEqual(res["patents"], 1)
        self.assertEqual(len(res["failed"]), 1)
        # The document itself must carry the loss, not only the return value.
        self.assertIn("1 of 2 patents analysed", text)
        self.assertIn("missing from this report", text)
        self.assertIn("CN2B", text)

    def test_clean_run_has_no_missing_notice(self):
        patents = [{"id": "WO1A", "title": "kept",
                    "patent_meta": {"publication_number": "WO1A", "claims_text": "1. A."}}]
        res, text = self._landscape(patents, lambda p, **kw: {"text": "ok",
                                                              "claims_available": True})
        self.assertEqual(res["failed"], [])
        self.assertIn("1 of 1 patents analysed", text)
        self.assertNotIn("missing from this report", text)


if __name__ == "__main__":
    unittest.main()
