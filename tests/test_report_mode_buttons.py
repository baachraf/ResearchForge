"""Every Generate Reports mode must highlight its own button.

Regression: `_select_mode` carried a hardcoded copy of the mode-button list that
was missing `btn_patent`. Clicking "Patent Landscape" set `_selected_mode` and
persisted it, but highlighted nothing and cleared every other button — the mode
looked unselectable even though Start would have run it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.summarize_tab import SummarizeTab  # noqa: E402

# Every mode string `_on_process` dispatches on.
ALL_MODES = ["per_paper", "topic", "global", "related_work",
             "introduction", "patent_landscape", "all"]

SELECTED = "btn_done"


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


class TestModeButtons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _tab(self, **cfg):
        return SummarizeTab(_Cfg(**cfg), _NullSignal())

    def test_every_dispatched_mode_has_a_button(self):
        tab = self._tab()
        wired = {m for _, m in tab._mode_buttons}
        self.assertEqual(wired, set(ALL_MODES),
                         "a mode _on_process handles has no button, or vice versa")

    def test_each_mode_highlights_exactly_its_own_button(self):
        tab = self._tab()
        for mode in ALL_MODES:
            tab._select_mode(mode)
            selected = [m for btn, m in tab._mode_buttons
                        if btn.objectName() == SELECTED]
            self.assertEqual(selected, [mode],
                             f"selecting {mode!r} highlighted {selected}")

    def test_patent_landscape_button_highlights(self):
        """The reported bug, pinned directly."""
        tab = self._tab()
        tab._select_mode("patent_landscape")
        self.assertEqual(tab.btn_patent.objectName(), SELECTED)
        self.assertEqual(tab._selected_mode, "patent_landscape")

    def test_construction_does_not_clobber_the_saved_mode(self):
        """_setup_ui ended with a persisting _select_mode("per_paper"), which
        overwrote the saved setting before showEvent could read it — so mode
        persistence was dead for every mode, not just patents."""
        cfg = _Cfg(summ_selected_mode="patent_landscape")
        SummarizeTab(cfg, _NullSignal())
        self.assertEqual(cfg.get("summ_selected_mode"), "patent_landscape",
                         "constructing the tab overwrote the saved mode")

    def test_restoring_a_mode_does_not_rewrite_it(self):
        cfg = _Cfg(summ_selected_mode="patent_landscape")
        tab = SummarizeTab(cfg, _NullSignal())
        tab._select_mode("related_work", persist=False)
        self.assertEqual(cfg.get("summ_selected_mode"), "patent_landscape")
        self.assertEqual(tab.btn_related.objectName(), SELECTED)

    def test_patent_mode_persists_and_restores_highlighted(self):
        """Saved mode is re-applied on show; it must highlight, not go blank."""
        cfg = _Cfg()
        tab = SummarizeTab(cfg, _NullSignal())
        tab._select_mode("patent_landscape")
        self.assertEqual(cfg.get("summ_selected_mode"), "patent_landscape")

        restored = SummarizeTab(cfg, _NullSignal())
        restored._select_mode(cfg.get("summ_selected_mode"), persist=False)
        self.assertEqual(restored.btn_patent.objectName(), SELECTED)

    def test_switching_mode_clears_the_previous_one(self):
        tab = self._tab()
        tab._select_mode("patent_landscape")
        tab._select_mode("related_work")
        self.assertEqual(tab.btn_patent.objectName(), "")
        self.assertEqual(tab.btn_related.objectName(), SELECTED)


if __name__ == "__main__":
    unittest.main()
