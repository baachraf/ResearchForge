"""Patent providers must appear in the results-table source filter.

Regression for the bug where "PatentsView" / "EPO OPS" / "PQAI" had no entry in
the Sources menu. `_apply_filters` hides a row whose Source cell text is not in
the set of ticked menu names, so a missing entry did not merely remove a filter
option — it hid EVERY patent result from the results table.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QTableWidgetItem  # noqa: E402

from researchforge_api._sessions import _DEFAULT_SOURCE_FILTERS  # noqa: E402
from gui.search_tab import SearchDownloadTab  # noqa: E402

PATENT_SOURCES = ["PatentsView", "EPO OPS", "PQAI"]


class _NullSignal:
    def emit(self, *a, **k):
        pass


class _Cfg:
    """Minimal ConfigManager stand-in — search_tab uses get/set/load_prompt only."""

    def __init__(self, **overrides):
        self._d = dict(overrides)

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def load_prompt(self, key):
        return ""


class TestPatentSourceFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _tab(self, **cfg):
        return SearchDownloadTab(_Cfg(**cfg), _NullSignal())

    # ── menu membership ──────────────────────────────────────────────────────

    def test_patent_sources_present_in_menu(self):
        tab = self._tab()
        for name in PATENT_SOURCES:
            self.assertIn(name, tab._source_filters,
                          f"{name} missing from the Sources menu")

    def test_menu_names_match_adapter_source_strings(self):
        """The menu key must equal the `source` string the adapter emits."""
        from research_downloader.sources import (
            patentsview_source, epo_ops_source, pqai_source)
        import inspect
        tab = self._tab()
        for mod, label in ((patentsview_source, "PatentsView"),
                           (epo_ops_source, "EPO OPS"),
                           (pqai_source, "PQAI")):
            src = inspect.getsource(mod)
            self.assertIn(f'"source": "{label}"', src,
                          f"{mod.__name__} no longer emits source={label!r}")
            self.assertIn(label, tab._source_filters)

    # ── key gating mirrors Brave/CORE ────────────────────────────────────────

    def test_gated_off_without_key(self):
        tab = self._tab()
        for name in PATENT_SOURCES:
            act = tab._source_filters[name]
            self.assertFalse(act.isEnabled(), f"{name} should be disabled with no key")
            self.assertFalse(act.isChecked())

    def test_enabled_and_checked_with_key(self):
        tab = self._tab(patentsview_api_key="k", epo_ops_key="k", pqai_api_key="k")
        for name in PATENT_SOURCES:
            act = tab._source_filters[name]
            self.assertTrue(act.isEnabled(), f"{name} should be enabled with a key")
            self.assertTrue(act.isChecked())

    # ── _source_visible: the bug class itself ────────────────────────────────

    def test_unknown_source_is_shown_not_hidden(self):
        """A source with no menu entry must never silently vanish."""
        tab = self._tab()
        self.assertTrue(tab._source_visible("Some Future Source", set()))

    def test_pin_prefix_does_not_break_matching(self):
        tab = self._tab()
        self.assertTrue(tab._source_visible("📌 arXiv", {"arXiv"}))
        self.assertFalse(tab._source_visible("📌 arXiv", set()))

    def test_known_source_still_filters(self):
        tab = self._tab()
        self.assertTrue(tab._source_visible("arXiv", {"arXiv"}))
        self.assertFalse(tab._source_visible("arXiv", {"PubMed"}))

    # ── end to end through the real table ────────────────────────────────────

    def test_patent_row_is_visible_in_results_table(self):
        tab = self._tab(epo_ops_key="k")
        t = tab.results_table
        t.setRowCount(1)
        t.setItem(0, 3, QTableWidgetItem("EPO OPS"))
        tab._apply_filters()
        self.assertFalse(t.isRowHidden(0),
                         "an EPO OPS result was hidden from the results table")

    def test_patent_row_hides_when_unticked(self):
        tab = self._tab(epo_ops_key="k")
        tab._source_filters["EPO OPS"].setChecked(False)
        t = tab.results_table
        t.setRowCount(1)
        t.setItem(0, 3, QTableWidgetItem("EPO OPS"))
        tab._apply_filters()
        self.assertTrue(t.isRowHidden(0))

    # ── API/session parity ───────────────────────────────────────────────────

    def test_default_source_filters_cover_patents(self):
        for name in PATENT_SOURCES:
            self.assertIn(name, _DEFAULT_SOURCE_FILTERS,
                          f"{name} missing from _DEFAULT_SOURCE_FILTERS")

    def test_menu_and_api_defaults_agree(self):
        tab = self._tab()
        self.assertEqual(set(tab._source_filters), set(_DEFAULT_SOURCE_FILTERS),
                         "GUI menu and _DEFAULT_SOURCE_FILTERS have drifted")


if __name__ == "__main__":
    unittest.main()
