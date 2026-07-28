"""A key-gated results-filter source (PatentsView / EPO OPS / PQAI) with no key
must stay unchecked + disabled — even after loading a session whose saved
source_filters mark it True. Before the fix, restore force-checked it, so it
showed permanently "selected" but greyed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.search_tab import SearchDownloadTab  # noqa: E402
from researchforge_api._sessions import _DEFAULT_SOURCE_FILTERS  # noqa: E402


class _Null:
    def emit(self, *a, **k):
        pass


class _Cfg:
    def __init__(self, **keys):
        self._d = dict(keys)

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v

    def load_prompt(self, k):
        return ""


class TestSourceFilterKeyGating(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_keyless_patent_sources_disabled_on_build(self):
        tab = SearchDownloadTab(_Cfg(), _Null())  # no keys at all
        for name in ("PatentsView", "PQAI"):
            act = tab._source_filters[name]
            self.assertFalse(act.isEnabled(), f"{name} should be disabled (no key)")
            self.assertFalse(act.isChecked(), f"{name} should be unchecked (no key)")

    def test_restore_does_not_recheck_keyless_source(self):
        tab = SearchDownloadTab(_Cfg(), _Null())  # no keys
        # A saved session with the canonical defaults marks all three True.
        data = {"name": "S", "source_filters": dict(_DEFAULT_SOURCE_FILTERS)}
        self.assertTrue(_DEFAULT_SOURCE_FILTERS["PatentsView"])  # precondition
        tab._restore_session(data, "sid")
        for name in ("PatentsView", "PQAI"):
            self.assertFalse(tab._source_filters[name].isChecked(),
                             f"{name} must NOT be force-checked on restore with no key")

    def test_restore_applies_saved_state_to_keyed_source(self):
        tab = SearchDownloadTab(_Cfg(epo_ops_key="k"), _Null())  # EPO keyed
        self.assertTrue(tab._source_filters["EPO OPS"].isEnabled())
        data = {"name": "S", "source_filters": {"EPO OPS": False, "arXiv": True}}
        tab._restore_session(data, "sid")
        self.assertFalse(tab._source_filters["EPO OPS"].isChecked(),
                         "a keyed source must honour the saved unchecked state")

    def test_enabled_academic_source_restores_normally(self):
        tab = SearchDownloadTab(_Cfg(), _Null())
        data = {"name": "S", "source_filters": {"arXiv": False}}
        tab._restore_session(data, "sid")
        self.assertFalse(tab._source_filters["arXiv"].isChecked())


if __name__ == "__main__":
    unittest.main()
