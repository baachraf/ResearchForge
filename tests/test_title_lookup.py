"""Tests for SemanticScholarSource.lookup_by_title()"""

import unittest
from unittest.mock import patch, MagicMock

from research_downloader.sources.semantic_scholar_source import SemanticScholarSource


def _make_s2_response(title, paper_id="abc123", open_access_pdf_url="https://arxiv.org/pdf/1234.pdf",
                      year=2023, authors=None, external_ids=None):
    """Build a mock Semantic Scholar API response payload."""
    if authors is None:
        authors = [{"name": "Alice Smith"}, {"name": "Bob Jones"}]
    if external_ids is None:
        external_ids = {"DOI": "10.1234/test"}

    item = {
        "paperId": paper_id,
        "title": title,
        "year": year,
        "abstract": "Test abstract.",
        "authors": authors,
        "externalIds": external_ids,
        "publicationLanguage": "en",
    }
    if open_access_pdf_url is not None:
        item["openAccessPdf"] = {"url": open_access_pdf_url}
    else:
        item["openAccessPdf"] = None

    return {"data": [item]}


class TestSemanticScholarLookupByTitle(unittest.TestCase):

    def _make_source(self):
        """Return a SemanticScholarSource with an API key so time.sleep is skipped."""
        return SemanticScholarSource(credentials={"api_key": "test-key"})

    # ------------------------------------------------------------------
    # Test 1: exact title match returns a paper dict
    # ------------------------------------------------------------------
    def test_exact_title_match_returns_paper(self):
        title = "Attention Is All You Need"
        payload = _make_s2_response(
            title=title,
            paper_id="paper001",
            open_access_pdf_url="https://arxiv.org/pdf/1706.03762.pdf",
            year=2023,
            authors=[{"name": "Alice Smith"}, {"name": "Bob Jones"}],
        )
        mock_response = MagicMock()
        mock_response.json.return_value = payload
        mock_response.raise_for_status.return_value = None

        with patch(
            "research_downloader.sources.semantic_scholar_source.requests.get",
            return_value=mock_response,
        ):
            source = self._make_source()
            result = source.lookup_by_title(title)

        self.assertIsNotNone(result)
        self.assertEqual(result["title"], title)
        self.assertEqual(result["source"], "SemanticScholar")
        self.assertEqual(result["url"], "https://arxiv.org/pdf/1706.03762.pdf")
        self.assertEqual(result["year"], 2023)
        self.assertIn("Alice Smith", result["authors"])

    # ------------------------------------------------------------------
    # Test 2: case-insensitive match
    # ------------------------------------------------------------------
    def test_case_insensitive_match(self):
        title = "Attention Is All You Need"
        payload = _make_s2_response(title=title)
        mock_response = MagicMock()
        mock_response.json.return_value = payload
        mock_response.raise_for_status.return_value = None

        with patch(
            "research_downloader.sources.semantic_scholar_source.requests.get",
            return_value=mock_response,
        ):
            source = self._make_source()
            result = source.lookup_by_title(title.upper())

        self.assertIsNotNone(result)

    # ------------------------------------------------------------------
    # Test 3: no match returns None
    # ------------------------------------------------------------------
    def test_no_match_returns_none(self):
        payload = _make_s2_response(title="A Completely Different Paper Title")
        mock_response = MagicMock()
        mock_response.json.return_value = payload
        mock_response.raise_for_status.return_value = None

        with patch(
            "research_downloader.sources.semantic_scholar_source.requests.get",
            return_value=mock_response,
        ):
            source = self._make_source()
            result = source.lookup_by_title("Attention Is All You Need")

        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Test 4: fallback DOI URL when no open-access PDF
    # ------------------------------------------------------------------
    def test_fallback_doi_url_when_no_open_pdf(self):
        title = "A Paper With Only A DOI"
        payload = _make_s2_response(
            title=title,
            paper_id="paper999",
            open_access_pdf_url=None,          # no open-access PDF
            external_ids={"DOI": "10.9999/x"},
        )
        mock_response = MagicMock()
        mock_response.json.return_value = payload
        mock_response.raise_for_status.return_value = None

        with patch(
            "research_downloader.sources.semantic_scholar_source.requests.get",
            return_value=mock_response,
        ):
            source = self._make_source()
            result = source.lookup_by_title(title)

        self.assertIsNotNone(result)
        self.assertEqual(result["url"], "https://doi.org/10.9999/x")
        self.assertTrue(result["needs_pdf_resolve"])


if __name__ == "__main__":
    unittest.main()


from research_downloader.sources.arxiv_source import ArxivSource


class TestArxivLookupByTitle:
    def setup_method(self):
        self.src = ArxivSource(credentials={})

    def _make_arxiv_result(self, title):
        result = MagicMock()
        result.title = title
        result.entry_id = "http://arxiv.org/abs/2301.00001v1"
        result.pdf_url = "https://arxiv.org/pdf/2301.00001"
        result.published = MagicMock()
        result.published.year = 2023
        result.summary = "Test abstract text."
        author1 = MagicMock(); author1.name = "Alice Smith"
        author2 = MagicMock(); author2.name = "Bob Jones"
        result.authors = [author1, author2]
        return result

    @patch("research_downloader.sources.arxiv_source.arxiv.Client")
    def test_exact_title_match_returns_paper(self, mock_client_cls):
        title = "Blind Source Separation for rPPG Signal Extraction"
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.results.return_value = iter([self._make_arxiv_result(title)])

        src = ArxivSource(credentials={})
        result = src.lookup_by_title(title)
        assert result is not None
        assert result["title"] == title
        assert result["source"] == "arXiv"
        assert result["id"] == "2301.00001v1"

    @patch("research_downloader.sources.arxiv_source.arxiv.Client")
    def test_case_insensitive_match(self, mock_client_cls):
        title = "Blind Source Separation for rPPG Signal Extraction"
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.results.return_value = iter([self._make_arxiv_result(title)])

        src = ArxivSource(credentials={})
        result = src.lookup_by_title(title.lower())
        assert result is not None

    @patch("research_downloader.sources.arxiv_source.arxiv.Client")
    def test_no_match_returns_none(self, mock_client_cls):
        wrong_title = "Completely Different Paper Title"
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.results.return_value = iter([self._make_arxiv_result(wrong_title)])

        src = ArxivSource(credentials={})
        result = src.lookup_by_title("The Title I Actually Want")
        assert result is None


import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))

from gui.session_creator import _TitleLookupWorker


class TestTitleLookupWorker:
    """Verify worker emits correct signals for found/not-found titles."""

    def test_found_signal_emitted_when_ddg_matches(self):
        found_papers = []
        not_found_titles = []

        creds = {"arxiv": {"enabled": False}}
        worker = _TitleLookupWorker(["Exact Paper Title"], creds)
        worker.paper_found.connect(lambda idx, p: found_papers.append((idx, p)))
        worker.title_not_found.connect(lambda idx, t: not_found_titles.append((idx, t)))

        with patch("gui.session_creator.WebSource") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws_cls.return_value = mock_ws
            mock_ws.search.return_value = [{
                "id": "x", "title": "Exact Paper Title", "url": "https://x.com/p.pdf",
                "source": "DuckDuckGo", "year": 2023, "abstract": "", "authors": [],
            }]
            worker.run()

        assert len(found_papers) == 1
        assert found_papers[0][0] == 0
        assert found_papers[0][1]["title"] == "Exact Paper Title"
        assert len(not_found_titles) == 0

    def test_not_found_signal_emitted_when_no_source_matches(self):
        found_papers = []
        not_found_titles = []

        creds = {"arxiv": {"enabled": True}}
        worker = _TitleLookupWorker(["Ghost Paper"], creds)
        worker.paper_found.connect(lambda idx, p: found_papers.append((idx, p)))
        worker.title_not_found.connect(lambda idx, t: not_found_titles.append((idx, t)))

        with patch("gui.session_creator.WebSource") as mock_ws_cls, \
             patch("gui.session_creator.ArxivSource") as mock_ax_cls:
            mock_ws_cls.return_value.search.return_value = []
            mock_ax_cls.return_value.lookup_by_title.return_value = None
            worker.run()

        assert len(found_papers) == 0
        assert len(not_found_titles) == 1
        assert not_found_titles[0] == (0, "Ghost Paper")
