"""Feature: per-query enable/disable of the 'must contain' title filter.

The keywords are kept either way; the toggle only decides whether results are
gated by them. A session-level default seeds new queries; each query can be
flipped afterward in the search panel.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from gui.search_tab import QueryBuilderDialog  # noqa: E402
from researchforge_api import _sessions  # noqa: E402


class _Cfg:
    def __init__(self, **o):
        self._d = dict(o)

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v


class TestMustContainToggle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    # ── dialog round-trips the flag ──────────────────────────────────────────

    def test_dialog_default_from_config_on(self):
        d = QueryBuilderDialog({}, None, config_manager=_Cfg(default_must_contain_enabled=True))
        self.assertTrue(d.must_contain_enabled.isChecked())
        self.assertTrue(d.get_data()["must_contain_enabled"])

    def test_dialog_default_from_config_off(self):
        d = QueryBuilderDialog({}, None, config_manager=_Cfg(default_must_contain_enabled=False))
        self.assertFalse(d.must_contain_enabled.isChecked())
        self.assertFalse(d.get_data()["must_contain_enabled"])
        # field greys out but keeps whatever is typed
        self.assertFalse(d.must_contain.isEnabled())

    def test_dialog_loads_explicit_flag(self):
        d = QueryBuilderDialog(
            {"must_contain": ["rppg", "pulse"], "must_contain_enabled": False},
            None, config_manager=_Cfg(default_must_contain_enabled=True))
        self.assertFalse(d.must_contain_enabled.isChecked())
        out = d.get_data()
        self.assertEqual(out["must_contain"], ["rppg", "pulse"])   # terms kept
        self.assertFalse(out["must_contain_enabled"])              # gate off

    def test_toggle_disables_field_but_keeps_terms(self):
        d = QueryBuilderDialog(
            {"must_contain": ["rppg"], "must_contain_enabled": True},
            None, config_manager=_Cfg())
        self.assertTrue(d.must_contain.isEnabled())
        d.must_contain_enabled.setChecked(False)
        self.assertFalse(d.must_contain.isEnabled())
        self.assertEqual(d.get_data()["must_contain"], ["rppg"])

    # ── gating semantics (what the results filter actually does) ─────────────

    def test_gating_dict_drops_terms_when_disabled(self):
        """Mirror _populate_results_table's must_contains construction."""
        queries = [
            {"name": "on",  "must_contain": ["rppg"], "must_contain_enabled": True},
            {"name": "off", "must_contain": ["rppg"], "must_contain_enabled": False},
            {"name": "legacy", "must_contain": ["rppg"]},  # no flag → treated on
        ]
        must_contains = {
            q["name"]: (q.get("must_contain", []) if q.get("must_contain_enabled", True) else [])
            for q in queries
        }
        self.assertEqual(must_contains["on"], ["rppg"])
        self.assertEqual(must_contains["off"], [])          # gate skipped
        self.assertEqual(must_contains["legacy"], ["rppg"])  # back-compat: on

    # ── API/session schema parity ────────────────────────────────────────────

    def test_api_normalize_seeds_flag_from_default(self):
        import researchforge_api._config as _c
        orig = _c.get
        try:
            _c.get = lambda k, d=None: (False if k == "default_must_contain_enabled" else orig(k, d))
            q = _sessions.normalize_query({"query": "photoplethysmography"})
            self.assertIn("must_contain_enabled", q)
            self.assertFalse(q["must_contain_enabled"])
        finally:
            _c.get = orig

    def test_api_normalize_preserves_explicit_flag(self):
        q = _sessions.normalize_query({"query": "x", "must_contain_enabled": False})
        self.assertFalse(q["must_contain_enabled"])


if __name__ == "__main__":
    unittest.main()
