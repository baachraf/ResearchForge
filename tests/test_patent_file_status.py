"""A patent's downloaded-state must reflect ITS OWN PDF, not "some PDF exists in
the folder". A patent whose PDF fetch failed (metadata-only) shares a query
folder with other patents' PDFs — the loose `bool(pdf_files)` check wrongly
marked it downloaded, so it showed green with no PDF and could not be retried.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.search_tab import SearchDownloadTab  # noqa: E402
from gui import paths  # noqa: E402


class _Null:
    def emit(self, *a, **k):
        pass


class _Cfg:
    def __init__(self, root):
        self._d = {"output_root": root, "session_download_name": "S"}

    def get(self, k, d=None):
        return self._d.get(k, d)

    def set(self, k, v):
        self._d[k] = v

    def load_prompt(self, k):
        return ""


class TestPatentFileStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)  # SQLite db can linger on Win

    def test_patent_status_is_per_pdf_not_per_folder(self):
        tab = SearchDownloadTab(_Cfg(self.root), _Null())
        folder = os.path.join(paths.session_downloads_root(tab.cfg, "S"), "q1")
        os.makedirs(folder)
        # PatentA has its PDF; PatentB (same folder) does not.
        open(os.path.join(folder, "EP1A.pdf"), "wb").write(b"%PDF-1.4")

        a = {"id": "EP1A", "doc_type": "patent", "output_folder": "q1",
             "patent_meta": {"publication_number": "EP1A"}}
        b = {"id": "US2B", "doc_type": "patent", "output_folder": "q1",
             "patent_meta": {"publication_number": "US2B"}}
        tab._check_result_files_batch([a, b])

        self.assertTrue(a["file_exists"], "patent with its own PDF must be present")
        self.assertFalse(b["file_exists"],
                         "patent whose PDF failed must NOT be marked present "
                         "just because a sibling PDF exists")

    def test_paper_keeps_loose_folder_check(self):
        """Papers (unknown on-disk filename) keep the any-PDF-in-folder heuristic."""
        tab = SearchDownloadTab(_Cfg(self.root), _Null())
        folder = os.path.join(paths.session_downloads_root(tab.cfg, "S"), "q1")
        os.makedirs(folder)
        open(os.path.join(folder, "whatever.pdf"), "wb").write(b"%PDF-1.4")
        paper = {"id": "p1", "doc_type": "paper", "output_folder": "q1"}
        tab._check_result_files_batch([paper])
        self.assertTrue(paper["file_exists"])


if __name__ == "__main__":
    unittest.main()
