"""Patents must be visible to the user, and must not pretend to download.

Two regressions:
1. Clicking Download on patents produced a folder holding only
   downloads_registry.db and no message — the download path has no doc_type
   awareness and patent hits carry an empty pdf_url.
2. Check Summaries hardcoded GLOBAL_SUMMARY / RELATED_WORK / INTRODUCTION, so
   PATENT_LANDSCAPE.md and the per-patent analyses were invisible in the app.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui import paths  # noqa: E402
from gui.output_tab import OutputTab  # noqa: E402
from gui.search_tab import SearchDownloadTab  # noqa: E402


class _NullSignal:
    def emit(self, *a, **k):
        pass


class _Cfg:
    def __init__(self, **overrides):
        self._d = dict(overrides)

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def load_prompt(self, key):
        return ""


PATENT = {"title": "A patent", "doc_type": "patent", "source": "EPO OPS"}
PAPER = {"title": "A paper", "doc_type": "paper", "source": "arXiv"}


class TestDownloadDropsPatents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _tab(self):
        return SearchDownloadTab(_Cfg(), _NullSignal())

    def test_all_patents_aborts_download(self):
        tab = self._tab()
        with mock.patch("gui.search_tab.QMessageBox.information") as box:
            self.assertIsNone(tab._drop_patents([dict(PATENT), dict(PATENT)]))
            self.assertTrue(box.called, "user was told nothing")
            body = " ".join(str(a) for a in box.call_args[0])
            self.assertIn("Patent Landscape", body,
                          "message must point at where to read them")

    def test_mixed_batch_keeps_papers_only(self):
        tab = self._tab()
        with mock.patch("gui.search_tab.QMessageBox.information") as box:
            rest = tab._drop_patents([dict(PATENT), dict(PAPER), dict(PATENT)])
        self.assertEqual(len(rest), 1)
        self.assertEqual(rest[0]["doc_type"], "paper")
        self.assertTrue(box.called)

    def test_papers_only_is_untouched_and_silent(self):
        tab = self._tab()
        batch = [dict(PAPER), dict(PAPER)]
        with mock.patch("gui.search_tab.QMessageBox.information") as box:
            rest = tab._drop_patents(batch)
        self.assertEqual(len(rest), 2)
        self.assertFalse(box.called, "no dialog when there are no patents")

    def test_missing_doc_type_treated_as_paper(self):
        """Legacy sessions have no doc_type — they must still download."""
        tab = self._tab()
        with mock.patch("gui.search_tab.QMessageBox.information") as box:
            rest = tab._drop_patents([{"title": "legacy"}])
        self.assertEqual(len(rest), 1)
        self.assertFalse(box.called)


class TestCheckSummariesShowsPatents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _tree_labels(self, model_root, session_root):
        tab = OutputTab(_Cfg(), _NullSignal())
        tab._get_active_root = lambda: session_root
        tab._refresh()
        labels = []

        def walk(item):
            for i in range(item.childCount()):
                c = item.child(i)
                labels.append(c.text(0))
                walk(c)

        for i in range(tab.file_tree.topLevelItemCount()):
            top = tab.file_tree.topLevelItem(i)
            labels.append(top.text(0))
            walk(top)
        return labels

    def test_landscape_and_per_patent_analyses_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_root = os.path.join(tmp, "MySession")
            model_root = os.path.join(session_root, "deepseek-v4-flash")
            cache = paths.patent_cache_dir(model_root)
            os.makedirs(cache)
            with open(paths.patent_landscape_file(model_root), "w",
                      encoding="utf-8") as f:
                f.write("# PATENT LANDSCAPE\n")
            for pn in ("EP4763065A1.md", "WO2026136374A1.md"):
                with open(os.path.join(cache, pn), "w", encoding="utf-8") as f:
                    f.write("## 1. PROBLEM\n")

            labels = self._tree_labels(model_root, session_root)

        self.assertIn("PATENT_LANDSCAPE.md", labels,
                      "the landscape report is invisible in Check Summaries")
        self.assertIn("Patents", labels)
        self.assertIn("EP4763065A1.md", labels)
        self.assertIn("WO2026136374A1.md", labels)

    def test_patents_only_session_does_not_crash(self):
        """No topic summaries and no global report — the tab must still render."""
        with tempfile.TemporaryDirectory() as tmp:
            session_root = os.path.join(tmp, "PatentsOnly")
            model_root = os.path.join(session_root, "m")
            cache = paths.patent_cache_dir(model_root)
            os.makedirs(cache)
            with open(os.path.join(cache, "US1.md"), "w", encoding="utf-8") as f:
                f.write("x")
            labels = self._tree_labels(model_root, session_root)
        self.assertIn("US1.md", labels)


if __name__ == "__main__":
    unittest.main()
