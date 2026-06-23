"""Unit tests for the canonical path module (``gui.paths``).

These run with no Qt and no LLM — they assert the path contract that both the
GUI and the headless API rely on. Every helper is covered. If any of these
break, downloads, summaries, or audit outputs will silently land in the wrong
place and the two layers will stop interoperating.
"""
import os
import unittest
from datetime import datetime

from gui import paths


class _FakeCfg:
    """Minimal cfg-like object: a dict with .get(key, default)."""
    def __init__(self, **kw):
        self._d = kw

    def get(self, key, default=None):
        return self._d.get(key, default)


# ─── segment sanitisers ─────────────────────────────────────────────────────

class TestSessionSegment(unittest.TestCase):
    def test_strips_windows_forbidden(self):
        for bad in '\\/:*?"<>|':
            self.assertNotIn(bad, paths.session_segment(f"a{bad}b"))

    def test_keeps_dots(self):
        # Titles like "Vol.1" must survive (dots are valid in folder names).
        self.assertEqual(paths.session_segment("Vol.1 Final"), "Vol.1 Final")

    def test_idempotent(self):
        # Reading an already-sanitised name back from cfg must not change it.
        s = paths.session_segment("Dr. Foo's Study (rPPG)")
        self.assertEqual(paths.session_segment(s), s)

    def test_empty(self):
        self.assertEqual(paths.session_segment(""), "")
        self.assertEqual(paths.session_segment(None), "")

    def test_known_transformation(self):
        self.assertEqual(paths.session_segment('a/b:c?d'), "a_b_c_d")


class TestModelSegment(unittest.TestCase):
    def test_strips_dots(self):
        # Model versions must not produce dotted folder names.
        self.assertEqual(paths.model_segment("gpt-3.5-turbo"), "gpt-3_5-turbo")
        self.assertEqual(paths.model_segment("1.5b-instruct"), "1_5b-instruct")

    def test_strips_windows_forbidden(self):
        for bad in '\\/:*?"<>|.':
            self.assertNotIn(bad, paths.model_segment(f"x{bad}y"))

    def test_empty_becomes_default(self):
        self.assertEqual(paths.model_segment(""), "default")
        self.assertEqual(paths.model_segment(None), "default")

    def test_simple_model_unchanged(self):
        self.assertEqual(paths.model_segment("deepseek-chat"), "deepseek-chat")


# ─── downloads ───────────────────────────────────────────────────────────────

class TestDownloadsPaths(unittest.TestCase):
    def setUp(self):
        self.cfg = _FakeCfg(output_root="D:/papers", summary_output_dir="D:/summaries")

    def test_output_root_uses_cfg(self):
        self.assertEqual(paths.output_root(self.cfg), "D:/papers")

    def test_output_root_fallback(self):
        cfg = _FakeCfg()  # no output_root
        expected = os.path.join(os.path.expanduser("~"), ".ResearchForge", "downloads")
        self.assertEqual(paths.output_root(cfg), expected)

    def test_session_downloads_root_with_session(self):
        self.assertEqual(paths.session_downloads_root(self.cfg, "MySession"),
                         os.path.join("D:/papers", "MySession"))

    def test_session_downloads_root_sanitises(self):
        # Forbidden chars stripped, dots kept.
        self.assertEqual(paths.session_downloads_root(self.cfg, 'a/b:c'),
                         os.path.join("D:/papers", "a_b_c"))

    def test_session_downloads_root_empty_falls_to_output_root(self):
        self.assertEqual(paths.session_downloads_root(self.cfg, ""), "D:/papers")

    def test_topic_downloads_dir(self):
        self.assertEqual(paths.topic_downloads_dir(self.cfg, "Sess", "query1"),
                         os.path.join("D:/papers", "Sess", "query1"))

    def test_topic_downloads_dir_empty_folder(self):
        self.assertEqual(paths.topic_downloads_dir(self.cfg, "Sess", ""),
                         os.path.join("D:/papers", "Sess"))

    def test_registry_db(self):
        self.assertEqual(paths.registry_db("D:/papers/Sess/q"),
                         os.path.join("D:/papers/Sess/q", "downloads_registry.db"))


# ─── summaries ───────────────────────────────────────────────────────────────

class TestSummaryPaths(unittest.TestCase):
    def setUp(self):
        self.cfg = _FakeCfg(summary_output_dir="D:/summaries", llm_model="deepseek-chat")

    def test_summary_root_uses_cfg_and_strips(self):
        self.assertEqual(paths.summary_root(_FakeCfg(summary_output_dir="  D:/s  ")), "D:/s")

    def test_summary_root_fallback(self):
        expected = os.path.join(os.path.expanduser("~"), ".ResearchForge", "summaries")
        self.assertEqual(paths.summary_root(_FakeCfg()), expected)

    def test_session_summary_root(self):
        self.assertEqual(paths.session_summary_root(self.cfg, "MySess"),
                         os.path.join("D:/summaries", "MySess"))

    def test_model_output_root_default_model_from_cfg(self):
        self.assertEqual(paths.model_output_root(self.cfg, "MySess"),
                         os.path.join("D:/summaries", "MySess", "deepseek-chat"))

    def test_model_output_root_explicit_model_sanitises_dots(self):
        got = paths.model_output_root(self.cfg, "MySess", "gpt-3.5")
        self.assertEqual(got, os.path.join("D:/summaries", "MySess", "gpt-3_5"))

    def test_model_output_root_empty_model_falls_to_cfg_then_default(self):
        # Empty model arg → read llm_model from cfg; if cfg has none → "default".
        cfg_with_model = _FakeCfg(summary_output_dir="D:/s", llm_model="my-model")
        self.assertTrue(paths.model_output_root(cfg_with_model, "S", "").endswith("my-model"))
        cfg_no_model = _FakeCfg(summary_output_dir="D:/s")  # no llm_model
        self.assertTrue(paths.model_output_root(cfg_no_model, "S", "").endswith("default"))


class TestTopicLayout(unittest.TestCase):
    def test_topic_reviews_parent(self):
        self.assertEqual(paths.topic_reviews_parent("M"),
                         os.path.join("M", "detailed_topic_reviews"))

    def test_topic_reviews_dir(self):
        self.assertEqual(paths.topic_reviews_dir("M", "topic1"),
                         os.path.join("M", "detailed_topic_reviews", "topic1"))

    def test_topic_cache_dir(self):
        self.assertEqual(paths.topic_cache_dir("M", "topic1"),
                         os.path.join("M", "detailed_topic_reviews", "topic1", "_cache"))

    def test_topic_master_report(self):
        self.assertEqual(paths.topic_master_report("M", "topic1"),
                         os.path.join("M", "detailed_topic_reviews", "topic1", "MASTER_REPORT.md"))

    def test_topic_summary_file_at_model_root(self):
        # Critical: the topic summary lives at the MODEL ROOT, not inside
        # detailed_topic_reviews/, so global synthesis can glob it.
        self.assertEqual(paths.topic_summary_file("M", "topic1"),
                         os.path.join("M", "topic1_SUMMARY.md"))

    def test_top_level_artefacts(self):
        self.assertEqual(paths.global_summary_file("M"), os.path.join("M", "GLOBAL_SUMMARY.md"))
        self.assertEqual(paths.related_work_file("M"), os.path.join("M", "RELATED_WORK.md"))
        self.assertEqual(paths.introduction_file("M"), os.path.join("M", "INTRODUCTION.md"))
        self.assertEqual(paths.relevance_scores_file("M"), os.path.join("M", "_relevance_scores.json"))


# ─── audit ───────────────────────────────────────────────────────────────────

class TestAuditPaths(unittest.TestCase):
    def test_audit_root_uses_cfg(self):
        self.assertEqual(paths.audit_root(_FakeCfg(audit_output_dir="D:/audits")), "D:/audits")

    def test_audit_root_strips_whitespace(self):
        self.assertEqual(paths.audit_root(_FakeCfg(audit_output_dir="  D:/a  ")), "D:/a")

    def test_audit_root_fallback(self):
        expected = os.path.join(os.path.expanduser("~"), ".ResearchForge", "audit_results")
        self.assertEqual(paths.audit_root(_FakeCfg()), expected)


class TestTitleToSlug(unittest.TestCase):
    def test_basic(self):
        # 5 words, all under max_chars (55) → all included.
        self.assertEqual(paths.title_to_slug("Spatial Artifact Coherence in rPPG"),
                         "Spatial_Artifact_Coherence_in_rPPG")

    def test_min_words_kept_even_past_max_chars(self):
        # At least min_words (4) words must appear before max_chars can cut.
        slug = paths.title_to_slug("supercalifragilisticexpialidocious word two three four", min_words=4)
        self.assertGreaterEqual(len(slug.split("_")), 4)

    def test_strips_short_words(self):
        # Words of length <= 1 are dropped.
        self.assertEqual(paths.title_to_slug("A B C real title here"),
                         "real_title_here")

    def test_empty(self):
        self.assertEqual(paths.title_to_slug(""), "")
        self.assertEqual(paths.title_to_slug(None), "")

    def test_strips_punctuation(self):
        self.assertEqual(paths.title_to_slug("Hello, World! (test)"),
                         "Hello_World_test")


class TestAuditFilename(unittest.TestCase):
    def test_format_with_title(self):
        now = datetime(2026, 5, 30, 7, 11, 40)  # Saturday 30 May 2026
        got = paths.audit_filename("Spatial Artifact Coherence", source_type="latex", now=now)
        self.assertEqual(got, "audit_report_Spatial_Artifact_Coherence_latex_Saturday_30_May_2026_071140")

    def test_format_without_title(self):
        now = datetime(2026, 5, 30, 7, 11, 40)
        got = paths.audit_filename("", source_type="pdf", now=now)
        self.assertEqual(got, "audit_report_pdf_Saturday_30_May_2026_071140")

    def test_pdf_source(self):
        now = datetime(2026, 1, 1, 0, 0, 0)
        got = paths.audit_filename("X Y Z W", source_type="pdf", now=now)
        self.assertIn("_pdf_", got)
        self.assertTrue(got.startswith("audit_report_"))


# ─── full layout contract (one assertion that documents the whole shape) ─────

class TestFullLayoutContract(unittest.TestCase):
    """A single test that pins down the complete on-disk layout. If any path
    helper changes its mind, this breaks loudly."""

    def test_downloads_layout(self):
        cfg = _FakeCfg(output_root="ROOT")
        self.assertEqual(
            paths.topic_downloads_dir(cfg, "Sess", "query1"),
            os.path.join("ROOT", "Sess", "query1"),
        )
        self.assertEqual(
            paths.registry_db(paths.topic_downloads_dir(cfg, "Sess", "query1")),
            os.path.join("ROOT", "Sess", "query1", "downloads_registry.db"),
        )

    def test_summary_layout(self):
        cfg = _FakeCfg(summary_output_dir="SUMM", llm_model="m")
        mr = paths.model_output_root(cfg, "Sess")
        self.assertEqual(mr, os.path.join("SUMM", "Sess", "m"))
        self.assertEqual(paths.topic_summary_file(mr, "t"),
                         os.path.join("SUMM", "Sess", "m", "t_SUMMARY.md"))
        self.assertEqual(paths.topic_master_report(mr, "t"),
                         os.path.join("SUMM", "Sess", "m", "detailed_topic_reviews", "t", "MASTER_REPORT.md"))
        self.assertEqual(paths.topic_cache_dir(mr, "t"),
                         os.path.join("SUMM", "Sess", "m", "detailed_topic_reviews", "t", "_cache"))
        self.assertEqual(paths.global_summary_file(mr),
                         os.path.join("SUMM", "Sess", "m", "GLOBAL_SUMMARY.md"))


if __name__ == "__main__":
    unittest.main()
