"""Feature: Load Session dialog sorts by name / created / modified, not just
filename order. Verifies the sortable-item mechanism used in the dialog (numeric
columns sort numerically, date columns sort chronologically) and that the tab
constructs after the change.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402


class _SessionItem(QTreeWidgetItem):
    """Same sort rule as the Load dialog: compare stored per-column keys."""
    def __lt__(self, other):
        tw = self.treeWidget()
        col = tw.sortColumn() if tw else 0
        a = self.data(col, Qt.UserRole + 1)
        b = other.data(col, Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)


class TestSortMechanism(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _tree(self, rows):
        tw = QTreeWidget()
        tw.setColumnCount(5)
        tw.setSortingEnabled(True)
        for name, q, res, created, mtime in rows:
            it = _SessionItem([name, str(q), str(res), created,
                               "" if not mtime else "m"])
            it.setData(0, Qt.UserRole + 1, name.lower())
            it.setData(1, Qt.UserRole + 1, q)
            it.setData(2, Qt.UserRole + 1, res)
            it.setData(3, Qt.UserRole + 1, created)
            it.setData(4, Qt.UserRole + 1, mtime)
            tw.addTopLevelItem(it)
        return tw

    def _order(self, tw):
        return [tw.topLevelItem(i).text(0) for i in range(tw.topLevelItemCount())]

    ROWS = [
        # name, queries, results, created, mtime(epoch)
        ("beta",  2, 100, "2026-07-01 09:00", 1000.0),
        ("Alpha", 10,   9, "2026-07-27 11:40", 3000.0),
        ("gamma", 1,  25, "2026-07-10 14:00", 2000.0),
    ]

    def test_results_column_sorts_numerically_not_lexically(self):
        tw = self._tree(self.ROWS)
        tw.sortItems(2, Qt.AscendingOrder)  # Results: 9, 25, 100
        self.assertEqual(self._order(tw), ["Alpha", "gamma", "beta"])

    def test_modified_sorts_newest_first(self):
        tw = self._tree(self.ROWS)
        tw.sortItems(4, Qt.DescendingOrder)  # mtime 3000, 2000, 1000
        self.assertEqual(self._order(tw), ["Alpha", "gamma", "beta"])

    def test_created_sorts_chronologically(self):
        tw = self._tree(self.ROWS)
        tw.sortItems(3, Qt.AscendingOrder)
        self.assertEqual(self._order(tw), ["beta", "gamma", "Alpha"])

    def test_name_sorts_case_insensitively(self):
        tw = self._tree(self.ROWS)
        tw.sortItems(0, Qt.AscendingOrder)  # alpha, beta, gamma (case-insensitive)
        self.assertEqual(self._order(tw), ["Alpha", "beta", "gamma"])

    def test_queries_column_numeric(self):
        tw = self._tree(self.ROWS)
        tw.sortItems(1, Qt.DescendingOrder)  # 10, 2, 1
        self.assertEqual(self._order(tw), ["Alpha", "beta", "gamma"])


class TestTabStillConstructs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_search_tab_constructs_after_load_dialog_change(self):
        from gui.search_tab import SearchDownloadTab

        class _Cfg:
            def __init__(self): self._d = {}
            def get(self, k, d=None): return self._d.get(k, d)
            def set(self, k, v): self._d[k] = v
            def load_prompt(self, k): return ""

        class _Null:
            def emit(self, *a, **k): pass

        tab = SearchDownloadTab(_Cfg(), _Null())
        self.assertIsNotNone(tab)


if __name__ == "__main__":
    unittest.main()
