"""In a mixed paper+patent session, the paper-analysis passes (per-paper / topic /
global / counts) must ignore patent PDFs. A patent PDF is image-only and belongs
to Patent Landscape; feeding it to text extraction just fails and gets .skipped,
inflating the paper count. It is recognised by its <stem>.json sidecar.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from researchforge_api import _patents


class TestPaperPdfsExcludesPatents(unittest.TestCase):
    def _folder(self, d):
        # two papers (no sidecar) + two patents (with .json sidecar)
        for name in ("paperA.pdf", "paperB.pdf", "EP1A.pdf", "US2B.pdf"):
            open(os.path.join(d, name), "wb").write(b"%PDF-1.4")
        for pub in ("EP1A", "US2B"):
            open(os.path.join(d, pub + ".json"), "w").write("{}")
            open(os.path.join(d, pub + ".md"), "w").write("#")

    def test_paper_pdfs_returns_only_papers(self):
        with tempfile.TemporaryDirectory() as d:
            self._folder(d)
            got = sorted(_patents.paper_pdfs(d))
            self.assertEqual(got, ["paperA.pdf", "paperB.pdf"])

    def test_is_patent_pdf_flags_sidecar_files(self):
        with tempfile.TemporaryDirectory() as d:
            self._folder(d)
            self.assertTrue(_patents.is_patent_pdf(d, "EP1A.pdf"))
            self.assertFalse(_patents.is_patent_pdf(d, "paperA.pdf"))

    def test_all_papers_folder_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("p1.pdf", "p2.pdf"):
                open(os.path.join(d, name), "wb").write(b"%PDF")
            self.assertEqual(sorted(_patents.paper_pdfs(d)), ["p1.pdf", "p2.pdf"])

    def test_all_patents_folder_yields_no_papers(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "EP1A.pdf"), "wb").write(b"%PDF")
            open(os.path.join(d, "EP1A.json"), "w").write("{}")
            self.assertEqual(_patents.paper_pdfs(d), [])

    def test_missing_folder_is_empty(self):
        self.assertEqual(_patents.paper_pdfs("/no/such/folder/xyz"), [])


if __name__ == "__main__":
    unittest.main()
