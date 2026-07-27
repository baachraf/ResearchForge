"""Patents must be visible to the user, and download as PDF + metadata sidecars.

- A patent download writes <pubnum>.pdf (EPO original document) plus
  <pubnum>.json / <pubnum>.md metadata into the query folder — the folder, not
  the session, holds the metadata.
- Check Summaries lists PATENT_LANDSCAPE.md and the per-patent analyses.
"""

import json
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
from researchforge_api import _patents  # noqa: E402


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


PATENT = {
    "title": "A PPG patent", "doc_type": "patent", "source": "EPO OPS",
    "id": "EP4763065A1", "abstract": "A wearable device.",
    "url": "https://worldwide.espacenet.com/patent/search?q=EP4763065A1",
    "patent_meta": {"publication_number": "EP4763065A1", "assignee": "ACME NV",
                    "cpc": ["A61B5/024"], "priority_date": "20241220",
                    "publication_date": "20260624",
                    "claims_text": "1. A wearable device ..."},
}


class TestPatentDownloadWritesFolder(unittest.TestCase):
    """Downloading a patent writes metadata (always) + the EPO PDF (when served)
    into the query folder."""

    def test_metadata_sidecars_written_even_without_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            # No EPO creds → _epo_source() is None → no PDF, but metadata must land.
            with mock.patch.object(_patents, "_epo_source", return_value=None):
                res = _patents.download_patent(dict(PATENT), d)
            self.assertTrue(os.path.isfile(res["json"]))
            self.assertTrue(os.path.isfile(res["md"]))
            self.assertEqual(res["pdf"], "")
            rec = json.load(open(res["json"], encoding="utf-8"))
            self.assertEqual(rec["patent_meta"]["assignee"], "ACME NV")
            self.assertIn("EP4763065A1", open(res["md"], encoding="utf-8").read())

    def test_pdf_written_when_epo_serves_it(self):
        with tempfile.TemporaryDirectory() as d:
            fake = mock.Mock()

            def _dl(pub, path, on_page=None):
                with open(path, "wb") as f:
                    f.write(b"%PDF-1.4 fake")
                return True
            fake.download_original_pdf.side_effect = _dl
            with mock.patch.object(_patents, "_epo_source", return_value=fake):
                res = _patents.download_patent(dict(PATENT), d)
            self.assertTrue(res["pdf_ok"])
            self.assertTrue(os.path.isfile(res["pdf"]))
            self.assertGreater(res["size_mb"], 0)

    def test_load_metadata_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(_patents, "_epo_source", return_value=None):
                _patents.download_patent(dict(PATENT), d)
            rec = _patents.load_metadata(d, "EP4763065A1")
            self.assertEqual(rec["patent_meta"]["cpc"], ["A61B5/024"])

    def test_non_epo_patent_gets_metadata_no_pdf(self):
        pv = dict(PATENT); pv["source"] = "PatentsView"
        with tempfile.TemporaryDirectory() as d:
            res = _patents.download_patent(pv, d)
            self.assertTrue(os.path.isfile(res["json"]))
            self.assertEqual(res["pdf"], "")  # only EPO is wired for PDF


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
