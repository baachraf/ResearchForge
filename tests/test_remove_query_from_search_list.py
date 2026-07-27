"""Removing a query from the session-creator Search Queries list after it was
added from Suggested.

The button existed but only acted on TICKED checkboxes — selecting a row and
clicking it did nothing, silently. Now it acts on selected rows too, gives
feedback when nothing is chosen, and there's a right-click Remove.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from gui.session_creator import SessionCreatorDialog  # noqa: E402


class _Cfg:
    def __init__(self, **o):
        self._d = dict(o)

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v


class TestRemoveFromSearchList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dlg_with_queries(self):
        dlg = SessionCreatorDialog(_Cfg())
        dlg._search_queries = [
            {"name": "q1", "query": "rppg", "must_contain": [], "must_not": []},
            {"name": "q2", "query": "pulse", "must_contain": [], "must_not": []},
            {"name": "q3", "query": "notch", "must_contain": [], "must_not": []},
        ]
        dlg._populate_search_table()
        return dlg

    def test_remove_by_selection(self):
        """Highlight a row and remove — the reported flow."""
        dlg = self._dlg_with_queries()
        dlg.search_table.selectRow(1)  # q2
        dlg._remove_selected_queries()
        names = [q["name"] for q in dlg._search_queries]
        self.assertEqual(names, ["q1", "q3"])
        # removed query is recoverable from Suggested
        self.assertIn("q2", [q["name"] for q in dlg._suggested_queries])

    def test_checkbox_alone_does_not_remove(self):
        """The checkbox means 'include in search', not 'remove'. Ticking one and
        clicking remove with no row selected must NOT delete anything."""
        dlg = self._dlg_with_queries()
        dlg.search_table.item(0, 0).setCheckState(Qt.Checked)  # already checked
        dlg.search_table.clearSelection()
        with mock.patch("gui.session_creator.QMessageBox.information"):
            dlg._remove_selected_queries()
        self.assertEqual(len(dlg._search_queries), 3)  # nothing removed

    def test_remove_multiple_selected(self):
        dlg = self._dlg_with_queries()
        from PySide6.QtCore import QItemSelectionModel
        sm = dlg.search_table.selectionModel()
        flag = QItemSelectionModel.Select | QItemSelectionModel.Rows
        sm.select(dlg.search_table.model().index(0, 0), flag)
        sm.select(dlg.search_table.model().index(2, 0), flag)
        dlg._remove_selected_queries()
        self.assertEqual([q["name"] for q in dlg._search_queries], ["q2"])

    def test_nothing_selected_gives_feedback_and_no_change(self):
        dlg = self._dlg_with_queries()
        dlg.search_table.clearSelection()
        with mock.patch("gui.session_creator.QMessageBox.information") as box:
            dlg._remove_selected_queries()
        self.assertTrue(box.called, "must tell the user nothing was selected")
        self.assertEqual(len(dlg._search_queries), 3)  # unchanged

    def test_context_menu_action_exists(self):
        dlg = self._dlg_with_queries()
        self.assertTrue(hasattr(dlg, "_search_context_menu"))


if __name__ == "__main__":
    unittest.main()
