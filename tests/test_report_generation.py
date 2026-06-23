"""Tests for report-generation placeholder filling (GUI↔API parity).

Verifies that ``generate_introduction`` and ``generate_related_work`` in the API
layer gather the per-paper analyses / global summary and fill the
``{paper_analyses}`` / ``{topic_summaries}`` / ``{context}`` / ``{intent}``
placeholders — instead of sending the template with literal placeholders to the
LLM (the bug that produced stub/empty Introduction sections via MCP).

The LLM HTTP call is mocked; the test captures the exact prompt sent and asserts
the gathered content is present and the literal placeholders are gone.
"""
import os
import tempfile
import unittest
from unittest import mock


class _FakeResp:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class _RecordingClient:
    """Fake OpenAI client that records the prompt it receives and returns canned text."""
    def __init__(self, reply="GENERATED"):
        self.received = None
        self._reply = reply
        self.chat = self
        self.completions = self
        self._client = self

    def create(self, *, messages, **kw):
        self.received = messages[0]["content"]
        return _FakeResp(self._reply)

    def close(self):
        pass


def _seed_model_root(model_root: str, *, topic: str = "T1",
                     paper_text: str = "PAPER ANALYSIS CONTENT HERE.",
                     global_text: str = "GLOBAL SYNTHESIS CONTENT."):
    """Populate a model_root with one topic's per-paper cache + a global summary."""
    cache = os.path.join(model_root, "detailed_topic_reviews", topic, "_cache")
    os.makedirs(cache, exist_ok=True)
    with open(os.path.join(cache, "paper1.md"), "w", encoding="utf-8") as f:
        f.write(paper_text)
    with open(os.path.join(model_root, "GLOBAL_SUMMARY.md"), "w", encoding="utf-8") as f:
        f.write(global_text)


class TestReportGenerationPlaceholders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rf_report_")
        # Point summary_output_dir at tmp so session-derived paths resolve here.
        self._cfg_patch = mock.patch("researchforge_api._config._get_cfg")
        cfg_mock = self._cfg_patch.start()
        cfg_mock.return_value = type("C", (), {
            "get": lambda s, k, d="": {
                "summary_output_dir": self.tmp, "llm_model": "m", "llm_endpoint": "e",
                "our_work_context": "OUR WORK CONTEXT",
            }.get(k, d),
            "set": lambda s, k, v: None,
        })()

    def tearDown(self):
        self._cfg_patch.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_llm(self, reply="GENERATED"):
        rc = _RecordingClient(reply=reply)
        return rc, mock.patch("researchforge_api._analyze._llm.create_client_from_config",
                              return_value=rc)

    # ── introduction ────────────────────────────────────────────────────────

    def test_introduction_fills_paper_analyses_placeholder(self):
        from researchforge_api import _analyze
        model_root = os.path.join(self.tmp, "sess", "m")
        _seed_model_root(model_root, paper_text="UNIQUE_PER_PAPER_MARKER_123")
        rc, llm_patch = self._patch_llm()
        tmpl = "Context: {context}\nIntent: {intent}\nAnalyses: {paper_analyses}\nWrite intro."
        with llm_patch, mock.patch("researchforge_api._analyze._prompts.get_prompt", return_value=tmpl):
            result = _analyze.generate_introduction(output_dir=model_root)
        self.assertNotIn("error", result, result)
        # The literal placeholder MUST be gone from the sent prompt...
        self.assertNotIn("{paper_analyses}", rc.received)
        self.assertNotIn("{context}", rc.received)
        self.assertNotIn("{intent}", rc.received)
        # ...and the gathered per-paper content + context MUST be present.
        self.assertIn("UNIQUE_PER_PAPER_MARKER_123", rc.received)
        self.assertIn("OUR WORK CONTEXT", rc.received)
        # Output file written with the LLM reply.
        with open(result["path"], "r", encoding="utf-8") as f:
            self.assertIn("GENERATED", f.read())

    def test_introduction_errors_when_no_analyses(self):
        from researchforge_api import _analyze
        model_root = os.path.join(self.tmp, "empty", "m")
        os.makedirs(model_root, exist_ok=True)
        with mock.patch("researchforge_api._analyze._prompts.get_prompt", return_value="{paper_analyses}"):
            result = _analyze.generate_introduction(output_dir=model_root)
        self.assertIn("error", result)
        self.assertIn("No per-paper analyses", result["error"])

    # ── related work ─────────────────────────────────────────────────────────

    def test_related_work_prefers_global_summary(self):
        from researchforge_api import _analyze
        model_root = os.path.join(self.tmp, "sess2", "m")
        _seed_model_root(model_root,
                         paper_text="PER_PAPER_FALLBACK_MARKER",
                         global_text="UNIQUE_GLOBAL_MARKER_456")
        rc, llm_patch = self._patch_llm()
        tmpl = "{context}\n{intent}\n{topic_summaries}\nWrite related work."
        with llm_patch, mock.patch("researchforge_api._analyze._prompts.get_prompt", return_value=tmpl):
            result = _analyze.generate_related_work(output_dir=model_root)
        self.assertNotIn("error", result, result)
        self.assertNotIn("{topic_summaries}", rc.received)
        # Global summary is preferred over per-paper cache.
        self.assertIn("UNIQUE_GLOBAL_MARKER_456", rc.received)
        self.assertNotIn("PER_PAPER_FALLBACK_MARKER", rc.received)

    def test_related_work_falls_back_to_per_paper_when_no_global(self):
        from researchforge_api import _analyze
        model_root = os.path.join(self.tmp, "sess3", "m")
        # Seed cache only, NO global summary.
        cache = os.path.join(model_root, "detailed_topic_reviews", "T", "_cache")
        os.makedirs(cache, exist_ok=True)
        with open(os.path.join(cache, "p.md"), "w", encoding="utf-8") as f:
            f.write("FALLBACK_PER_PAPER_MARKER_789")
        rc, llm_patch = self._patch_llm()
        with llm_patch, mock.patch("researchforge_api._analyze._prompts.get_prompt",
                                   return_value="{topic_summaries}"):
            result = _analyze.generate_related_work(output_dir=model_root)
        self.assertNotIn("error", result, result)
        self.assertIn("FALLBACK_PER_PAPER_MARKER_789", rc.received)

    def test_related_work_errors_when_no_source(self):
        from researchforge_api import _analyze
        model_root = os.path.join(self.tmp, "empty2", "m")
        os.makedirs(model_root, exist_ok=True)
        with mock.patch("researchforge_api._analyze._prompts.get_prompt", return_value="{topic_summaries}"):
            result = _analyze.generate_related_work(output_dir=model_root)
        self.assertIn("error", result)
        self.assertIn("No summaries", result["error"])


if __name__ == "__main__":
    unittest.main()
