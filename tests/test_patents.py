"""Patent pipeline — schema, path contract, adapters, and prompt-fill safety.

No network and no LLM: the adapters are exercised keyless (which must return an
empty list, not raise) and via injected fake payloads.
"""
import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from gui import paths
from researchforge_api._sessions import normalize_result, ensure_full_schema
from research_downloader.sources.patentsview_source import PatentsViewSource
from research_downloader.sources.epo_ops_source import EpoOpsSource
from research_downloader.sources.pqai_source import PqaiSource


class TestSchemaBackCompat:
    """The doc_type addition must be invisible to everything that already exists."""

    def test_legacy_result_defaults_to_paper(self):
        r = normalize_result({"title": "A paper", "url": "http://x"})
        assert r["doc_type"] == "paper"
        assert r["patent_meta"] == {}

    def test_existing_fields_untouched(self):
        r = normalize_result({"title": "t", "relevance_score": 88, "score_reason": "why"})
        assert r["relevance_score"] == 88
        assert r["score_reason"] == "why"
        assert r["file_exists"] is False

    def test_patent_doc_type_survives_normalisation(self):
        r = normalize_result({
            "title": "A patent", "doc_type": "patent",
            "patent_meta": {"assignee": "Acme", "publication_number": "US1B2"},
        })
        assert r["doc_type"] == "patent"
        assert r["patent_meta"]["assignee"] == "Acme"

    def test_legacy_session_migrates_and_gains_doc_type(self):
        """results is a FLAT LIST in the real schema, not a per-query mapping."""
        sess = ensure_full_schema({"name": "old", "results": [{"title": "t"}]})
        assert sess["name"] == "old"
        assert sess["results"][0]["doc_type"] == "paper"
        assert sess["results"][0]["patent_meta"] == {}


class TestPathContract:
    def test_landscape_is_sibling_of_related_work(self):
        root = os.path.join("some", "model_root")
        assert paths.patent_landscape_file(root) == os.path.join(root, "PATENT_LANDSCAPE.md")
        assert os.path.dirname(paths.patent_landscape_file(root)) == \
               os.path.dirname(paths.related_work_file(root))

    def test_landscape_is_pure(self, tmp_path):
        paths.patent_landscape_file(str(tmp_path))
        assert not (tmp_path / "PATENT_LANDSCAPE.md").exists()


class TestAdaptersWithoutKeys:
    """A missing key must yield no results, never an exception."""

    @pytest.mark.parametrize("cls", [PatentsViewSource, EpoOpsSource, PqaiSource])
    def test_keyless_returns_empty(self, cls):
        assert cls().search("rppg") == []

    @pytest.mark.parametrize("cls", [PatentsViewSource, EpoOpsSource, PqaiSource])
    def test_keyless_does_not_hit_network(self, cls):
        with patch("requests.get") as g, patch("requests.post") as p:
            cls().search("rppg")
            g.assert_not_called()
            p.assert_not_called()


class TestPatentsViewAdapter:
    def _resp(self, payload):
        m = MagicMock()
        m.json.return_value = payload
        m.raise_for_status.return_value = None
        return m

    def test_marks_doc_type_and_extracts_meta(self):
        search = self._resp({"patents": [{
            "patent_id": "11111111",
            "patent_title": "Remote photoplethysmography apparatus",
            "patent_date": "2021-03-02",
            "patent_abstract": "An apparatus for measuring pulse from video.",
            "assignees": [{"assignee_organization": "Acme Health"}],
            "inventors": [{"inventor_name_first": "A", "inventor_name_last": "B"}],
            "cpc_current": [{"cpc_group_id": "A61B5/024"}],
        }]})
        claims = self._resp({"g_claims": [
            {"claim_sequence": 1, "claim_text": "A method comprising...", "claim_dependent": None},
            {"claim_sequence": 2, "claim_text": "The method of claim 1...", "claim_dependent": 1},
        ]})
        with patch("requests.get", side_effect=[search, claims]):
            out = PatentsViewSource(credentials={"api_key": "k"}).search("rppg")

        assert len(out) == 1
        r = out[0]
        assert r["doc_type"] == "patent"
        assert r["year"] == 2021
        meta = r["patent_meta"]
        assert meta["assignee"] == "Acme Health"
        assert meta["publication_number"] == "US11111111"
        assert "A method comprising" in meta["claims_text"]
        # Only the independent claim lands in independent_claims
        assert "A method comprising" in meta["independent_claims"]
        assert "The method of claim 1" not in meta["independent_claims"]

    def test_claims_failure_degrades_instead_of_dropping_hit(self):
        """The claims endpoint is beta upstream — a failure must not lose the patent."""
        search = self._resp({"patents": [{
            "patent_id": "222", "patent_title": "T", "patent_date": "2020-01-01",
            "patent_abstract": "abs", "assignees": [], "inventors": [], "cpc_current": [],
        }]})
        with patch("requests.get", side_effect=[search, Exception("beta endpoint 500")]):
            out = PatentsViewSource(credentials={"api_key": "k"}).search("x")

        assert len(out) == 1, "patent must survive a claims failure"
        assert out[0]["patent_meta"]["claims_text"] == ""
        assert out[0]["abstract"] == "abs"


class TestEpoOpsAdapter:
    def test_auth_failure_returns_empty_not_raise(self):
        with patch("requests.post", side_effect=Exception("401")):
            assert EpoOpsSource(
                credentials={"consumer_key": "k", "consumer_secret": "s"}).search("x") == []

    def test_as_list_normalises_ops_dict_or_list(self):
        s = EpoOpsSource()
        assert s._as_list(None) == []
        assert s._as_list({"a": 1}) == [{"a": 1}]
        assert s._as_list([1, 2]) == [1, 2]


class TestPromptFillSafety:
    """The patent prompts contain literal {...} citation examples. str.format()
    would raise KeyError on them; the fill helper must not."""

    def test_fill_leaves_unknown_braces_intact(self):
        from researchforge_api._analyze import _fill
        tpl = 'Cite as `{assignee}, "{title}," {publication_number}.` and {unknown_key}'
        out = _fill(tpl, {"assignee": "Acme", "title": "T", "publication_number": "US1"})
        assert "Acme" in out and "US1" in out
        assert "{unknown_key}" in out, "unknown placeholders must survive, not raise"

    def test_format_would_have_raised(self):
        tpl = "text {unknown_key}"
        with pytest.raises(KeyError):
            tpl.format(assignee="a")

    def test_empty_values_become_not_available(self):
        from researchforge_api._analyze import _fill
        assert "Not available" in _fill("{assignee}", {"assignee": ""})

    def test_bundled_patent_prompts_exist_and_are_doc_first(self):
        from gui.app_info import resource
        for name in ("per_patent.md", "patent_landscape.md"):
            p = resource(os.path.join("config", "prompts", name))
            assert os.path.exists(p), f"{name} missing"
        # per_patent must keep PATENT before RESEARCH so load_prompt's staleness
        # guard does not swap it for a bundled template.
        text = open(resource(os.path.join("config", "prompts", "per_patent.md")),
                    encoding="utf-8").read()
        assert text.index("PATENT:") < text.index("RESEARCH:")


class TestLandscapeSelection:
    def test_only_patents_are_collected(self):
        from researchforge_api import _analyze
        fake = {"results": [
            {"title": "paper", "doc_type": "paper"},
            {"title": "legacy", "title_filter_ok": True},          # no doc_type at all
            {"title": "patent A", "doc_type": "patent", "patent_meta": {"publication_number": "US1"}},
            {"title": "patent B", "doc_type": "patent", "patent_meta": {"publication_number": "US2"}},
        ]}
        with patch("researchforge_api._sessions.load_session", return_value=fake):
            got = _analyze._patent_results("any")
        assert [g["title"] for g in got] == ["patent A", "patent B"]

    def test_no_patents_reports_error_not_crash(self):
        from researchforge_api import _analyze
        with patch("researchforge_api._sessions.load_session",
                   return_value={"results": [{"title": "p", "doc_type": "paper"}]}), \
             patch.object(_analyze, "_resolve_model_root", return_value="/tmp/x"):
            out = _analyze.generate_patent_landscape(session_id="s")
        assert "error" in out and "No patents" in out["error"]


class TestRelatedWorkIntegration:
    """Patents must reach Related Work / Introduction, and must arrive labelled."""

    def _cache(self, tmp_path, *names):
        d = tmp_path / "_patent_cache"
        d.mkdir()
        for n in names:
            (d / f"{n}.md").write_text(f"### {n}\n\nAcme claims a method.", encoding="utf-8")
        return str(tmp_path)

    def test_no_patents_returns_empty_string(self, tmp_path):
        assert paths.read_patent_analyses(str(tmp_path)) == ""

    def test_patent_analyses_are_gathered_and_labelled(self, tmp_path):
        root = self._cache(tmp_path, "US1", "US2")
        out = paths.read_patent_analyses(root)
        assert "US1" in out and "US2" in out
        assert "not peer-reviewed" in out, "patents must be labelled for the prompt"

    def test_related_work_source_appends_patents_to_papers(self, tmp_path):
        from researchforge_api import _analyze
        root = self._cache(tmp_path, "US1")
        with open(os.path.join(root, "GLOBAL_SUMMARY.md"), "w", encoding="utf-8") as f:
            f.write("Global synthesis of the papers.")
        out = _analyze._gather_related_work_source(root)
        assert "Global synthesis of the papers." in out
        assert "US1" in out, "patents must not be dropped when papers exist"

    def test_patents_only_session_still_produces_source(self, tmp_path):
        """A patents-only session is valid — Related Work from patents alone."""
        from researchforge_api import _analyze
        root = self._cache(tmp_path, "US1")
        out = _analyze._gather_related_work_source(root)
        assert "US1" in out

    def test_patent_cache_is_outside_the_paper_tree(self, tmp_path):
        """The paper gatherer must never pick patents up implicitly."""
        from researchforge_api import _analyze
        root = self._cache(tmp_path, "US1")
        # The paper gatherer walks detailed_topic_reviews/ only, so it sees nothing.
        assert _analyze._gather_per_paper_analyses(root) == ""
        # And the cache genuinely sits outside that tree, not merely unread.
        cache = os.path.realpath(paths.patent_cache_dir(root))
        reviews = os.path.realpath(paths.topic_reviews_parent(root))
        assert os.path.commonpath([cache, reviews]) == os.path.realpath(root)
        assert not cache.startswith(reviews + os.sep)


class TestPromptPatentRules:
    """The prompts carry the citation form and the hedge, or a patent gets cited
    like a peer-reviewed paper."""

    @pytest.mark.parametrize("fname", ["related_work.md", "introduction.md"])
    def test_prompt_has_patent_rules(self, fname):
        from gui.app_info import resource
        text = open(resource(os.path.join("config", "prompts", fname)), encoding="utf-8").read()
        assert "PATENTS" in text
        assert "has NO author" in text, "must forbid Author et al. for patents"
        assert "claims a method" in text, "must give the hedged form"
        assert "NEVER WRITE" in text, "must forbid asserting patents as results"
