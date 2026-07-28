"""'All' must cover patents. A patents-only session used to fail: All ran the
paper pipeline (per-paper/topic/global) which found no papers, then Related Work
errored because no patent analyses existed yet. Now All runs Patent Landscape
first when the session has patents, then chains into Related Work.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

import gui.session_manager as sm  # noqa: E402
from gui.summarize_tab import SummarizeTab  # noqa: E402


class _Null:
    def emit(self, *a, **k):
        pass


class _Cfg:
    def __init__(self, **o):
        self._d = {"llm_endpoint": "http://x/v1", "llm_model": "m",
                   "llm_provider": "LM Studio", "last_session": "sid"}
        self._d.update(o)

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v

    def load_prompt(self, k):
        return ""


class TestAllCoversPatents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _tab(self):
        return SummarizeTab(_Cfg(), _Null())

    # ── _session_has_patents ─────────────────────────────────────────────────

    def test_detects_patents_in_session(self):
        tab = self._tab()
        with mock.patch.object(sm.SessionManager, "load",
                               return_value={"results": [{"doc_type": "patent"}]}):
            self.assertTrue(tab._session_has_patents())

    def test_no_patents_when_only_papers(self):
        tab = self._tab()
        with mock.patch.object(sm.SessionManager, "load",
                               return_value={"results": [{"doc_type": "paper"}]}):
            self.assertFalse(tab._session_has_patents())

    def test_no_patents_when_session_missing(self):
        tab = self._tab()
        with mock.patch.object(sm.SessionManager, "load", return_value=None):
            self.assertFalse(tab._session_has_patents())

    # ── landscape completion chains into Related Work during "All" ───────────

    def test_landscape_done_continues_all_into_related_work(self):
        tab = self._tab()
        tab._all_continue_after_patent = True
        with mock.patch.object(SummarizeTab, "_on_related_work") as rw, \
                mock.patch("gui.summarize_tab.QMessageBox.information") as box, \
                mock.patch("PySide6.QtCore.QTimer.singleShot",
                           side_effect=lambda ms, fn: fn()):
            tab._on_patent_landscape_done({"patents": 5, "without_claims": 3})
        self.assertTrue(rw.called, "All should continue into Related Work")
        self.assertFalse(box.called, "no interrupting popup mid-All")
        self.assertFalse(tab._all_continue_after_patent, "flag must reset")

    def test_standalone_landscape_still_pops_up(self):
        """A normal (non-All) Patent Landscape run keeps its completion dialog."""
        tab = self._tab()
        tab._all_continue_after_patent = False
        with mock.patch.object(SummarizeTab, "_on_related_work") as rw, \
                mock.patch("gui.summarize_tab.QMessageBox.information") as box:
            tab._on_patent_landscape_done({"patents": 5, "without_claims": 0})
        self.assertFalse(rw.called)
        self.assertTrue(box.called)

    def test_landscape_error_mid_all_does_not_chain(self):
        tab = self._tab()
        tab._all_continue_after_patent = True
        with mock.patch.object(SummarizeTab, "_on_related_work") as rw, \
                mock.patch("gui.summarize_tab.QMessageBox.warning") as warn:
            tab._on_patent_landscape_done({"error": "no patents"})
        self.assertFalse(rw.called, "an error must not push on into Related Work")
        self.assertTrue(warn.called)


if __name__ == "__main__":
    unittest.main()
