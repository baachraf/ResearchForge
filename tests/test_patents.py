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
    def _resp(self, payload, status=200):
        m = MagicMock()
        m.json.return_value = payload
        m.status_code = status
        return m

    def _src(self):
        s = PatentsViewSource(credentials={"api_key": "k"})
        s._MIN_INTERVAL = 0          # no pacing sleep in tests
        return s

    def test_marks_doc_type_and_extracts_meta(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            if url.endswith("/patent/"):
                return self._resp({"patents": [{
                    "patent_id": "11111111",
                    "patent_title": "Remote photoplethysmography apparatus",
                    "patent_date": "2021-03-02",
                    "patent_abstract": "An apparatus for measuring pulse from video.",
                    "assignees": [{"assignee_organization": "Acme Health"}],
                    "inventors": [{"inventor_name_first": "A", "inventor_name_last": "B"}],
                    "cpc_current": [{"cpc_group_id": "A61B5/024"}],
                }]})
            if "g_claim" in url:
                return self._resp({"g_claims": [
                    {"patent_id": "11111111", "claim_sequence": 1,
                     "claim_text": "A method comprising...", "claim_dependent": None},
                    {"patent_id": "11111111", "claim_sequence": 2,
                     "claim_text": "The method of claim 1...", "claim_dependent": 1},
                ]})
            return self._resp({})

        with patch("requests.get", side_effect=fake_get):
            out = self._src().search("rppg")

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
        def fake_get(url, params=None, headers=None, timeout=None):
            if url.endswith("/patent/"):
                return self._resp({"patents": [{
                    "patent_id": "222", "patent_title": "T", "patent_date": "2020-01-01",
                    "patent_abstract": "abs", "assignees": [], "inventors": [],
                    "cpc_current": [],
                }]})
            raise Exception("beta endpoint 500")

        with patch("requests.get", side_effect=fake_get):
            out = self._src().search("x")

        assert len(out) == 1, "patent must survive a claims failure"
        assert out[0]["patent_meta"]["claims_text"] == ""
        assert out[0]["abstract"] == "abs"

    def test_http_error_on_search_returns_empty(self):
        with patch("requests.get", side_effect=lambda *a, **k: self._resp({}, status=500)):
            assert self._src().search("x") == []


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


def _fixture(name):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class TestEpoOpsAgainstLivePayloads:
    """Parsing regressions, pinned to payloads captured from the live API
    on 2026-07-27. Every assertion here failed against the from-documentation
    adapter and returned empty rather than raising."""

    def _src(self):
        return EpoOpsSource(credentials={"consumer_key": "k", "consumer_secret": "s"})

    # -- CQL: quoting turns the search into an exact-phrase match --

    def test_multiword_query_is_not_phrase_quoted(self):
        """Live: the quoted form returned 1 hit where unquoted returned 925."""
        cql = self._src()._build_cql("photoplethysmography blood pressure")
        assert '"' not in cql
        assert cql == "ta=photoplethysmography blood pressure"

    def test_caller_can_still_force_a_phrase_search(self):
        assert self._src()._build_cql('"blood pressure"') == 'ta="blood pressure"'

    def test_stray_quotes_do_not_produce_invalid_cql(self):
        cql = self._src()._build_cql('rppg "signal')
        assert cql.count('"') in (0, 2)

    def test_empty_query_makes_no_request(self):
        with patch("requests.post") as p, patch("requests.get") as g:
            assert self._src().search("   ") == []
            g.assert_not_called()

    # -- Envelope shapes: one result is a dict, several are a list --

    def test_single_result_envelope_is_parsed(self):
        docs = self._src()._extract_documents(_fixture("epo_search_single_cn.json"))
        assert len(docs) == 1
        parsed = self._src()._parse_document(docs[0])
        assert parsed["id"] == "CN115911174A"

    def test_multi_result_envelope_is_parsed(self):
        src = self._src()
        docs = src._extract_documents(_fixture("epo_search_multi.json"))
        assert len(docs) == 2
        assert all(src._parse_document(d) for d in docs)

    # -- Language: OPS repeats title/abstract per language --

    def test_english_title_and_abstract_chosen_not_concatenated(self):
        docs = self._src()._extract_documents(_fixture("epo_search_single_cn.json"))
        p = self._src()._parse_document(docs[0])
        assert p["title"].startswith("Photoelectric integrated sensing chip")
        # The CN original must not be glued onto the English text.
        assert not any(ord(ch) > 0x2000 for ch in p["title"] + p["abstract"])

    # -- CPC lives under patent-classifications, not classifications-cpc --

    def test_cpc_is_extracted_from_patent_classifications(self):
        src = self._src()
        docs = src._extract_documents(_fixture("epo_search_multi.json"))
        cpc = src._parse_document(docs[0])["patent_meta"]["cpc"]
        assert cpc, "CPC came back empty — the classification path has drifted again"
        # Reassembled from section/class/subclass/main-group/subgroup components.
        assert "A61B5/1102" in cpc
        assert "A47C31/123" in cpc

    def test_ipc_codes_are_not_mixed_into_cpc(self):
        src = self._src()
        docs = src._extract_documents(_fixture("epo_search_multi.json"))
        for c in src._parse_document(docs[0])["patent_meta"]["cpc"]:
            assert "/" in c and " " not in c

    # -- Dates: priority date is not the publication date --

    def test_priority_date_is_the_priority_claim_not_publication(self):
        src = self._src()
        docs = src._extract_documents(_fixture("epo_search_single_cn.json"))
        meta = src._parse_document(docs[0])["patent_meta"]
        assert meta["publication_date"] == "20230404"
        assert meta["priority_date"] == "20220831"

    # -- Claims: two different live shapes --

    def test_ep_claims_one_entry_per_claim(self):
        src = self._src()
        with patch("requests.get", return_value=_mock_resp(_fixture("epo_claims_ep.json"))):
            text = src._fetch_claims("EP1000000A1", {})
        assert len(src._split_claims(text)) == 11
        assert src._independent_claims(text).startswith("1. Apparatus for manufacturing")

    def test_wo_claims_are_page_chunks_split_by_number(self):
        """US/WO return a few page-sized chunks, not one entry per claim, so
        claim boundaries come only from the numbering."""
        src = self._src()
        with patch("requests.get", return_value=_mock_resp(_fixture("epo_claims_wo.json"))):
            text = src._fetch_claims("WO2026136374A1", {})
        claims = src._split_claims(text)
        assert len(claims) > 50
        assert claims[0][0] == 1
        assert claims[0][1].startswith("A method for enhancing specificity")

    def test_running_page_header_is_stripped(self):
        src = self._src()
        with patch("requests.get", return_value=_mock_resp(_fixture("epo_claims_wo.json"))):
            text = src._fetch_claims("WO2026136374A1", {})
        assert "Atty. Dkt No. 10085-01-0191-PCT" not in text

    def test_independent_claims_are_a_subset_not_everything(self):
        src = self._src()
        with patch("requests.get", return_value=_mock_resp(_fixture("epo_claims_wo.json"))):
            text = src._fetch_claims("WO2026136374A1", {})
        indep = src._independent_claims(text)
        assert indep
        assert len(indep) < len(text) / 2
        assert "according to claim" not in indep.lower()

    def test_claims_language_blocks_are_not_concatenated(self):
        """An EP-B1 publishes EN/DE/FR; only one must be kept."""
        payload = _fixture("epo_claims_ep.json")
        doc = payload["ops:world-patent-data"]["ftxt:fulltext-documents"]["ftxt:fulltext-document"]
        en = doc["claims"]
        de = {"@lang": "DE", "claim": {"claim-text": [{"$": "1. Vorrichtung zur Herstellung."}]}}
        doc["claims"] = [en, de]
        with patch("requests.get", return_value=_mock_resp(payload)):
            text = self._src()._fetch_claims("EP1000000A1", {})
        assert "Vorrichtung" not in text
        assert "Apparatus for manufacturing" in text

    def test_missing_claims_yield_empty_not_exception(self):
        with patch("requests.get", return_value=_mock_resp({}, status=404)):
            assert self._src()._fetch_claims("US123A1", {}) == ""

    # -- Throttling: OPS rejects with 403 + X-Rejection-Reason, not only 429 --

    def test_throttle_rejection_is_retried(self):
        src = self._src()
        src._MIN_INTERVAL = 0
        throttled = _mock_resp({}, status=403, headers={"X-Rejection-Reason": "IndividualQuotaPerHour"})
        ok = _mock_resp(_fixture("epo_claims_ep.json"))
        with patch("requests.get", side_effect=[throttled, ok]), patch("time.sleep"):
            text = src._fetch_claims("EP1000000A1", {})
        assert "Apparatus for manufacturing" in text

    def test_plain_403_is_not_retried_as_throttling(self):
        """A 403 with no rejection reason is an authorisation failure; retrying
        it just burns quota."""
        src = self._src()
        src._MIN_INTERVAL = 0
        denied = _mock_resp({}, status=403, headers={})
        with patch("requests.get", side_effect=[denied]) as g, patch("time.sleep"):
            assert src._fetch_claims("EP1000000A1", {}) == ""
            assert g.call_count == 1

    def test_calls_are_paced(self):
        src = self._src()
        src._MIN_INTERVAL = 5.0
        with patch("requests.get", return_value=_mock_resp({}, status=404)), \
             patch("time.sleep") as slp:
            src._fetch_claims("EP1A", {})
            src._fetch_claims("EP2A", {})
        assert slp.called, "second call must wait out the minimum interval"


def _mock_resp(payload, status=200, headers=None):
    m = MagicMock()
    m.json.return_value = payload
    m.status_code = status
    m.headers = headers if headers is not None else {}
    m.raise_for_status.side_effect = None if status == 200 else Exception(str(status))
    return m


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


class TestPromptHasNoCitationContradiction:
    """A patent block further down the prompt is useless if the header still says
    the paper format has 'no exceptions' — the model sides with the absolute rule."""

    PROMPTS = ["related_work.md", "introduction.md"]

    def _text(self, fname):
        from gui.app_info import resource
        return open(resource(os.path.join("config", "prompts", fname)), encoding="utf-8").read()

    @pytest.mark.parametrize("fname", PROMPTS)
    def test_header_no_longer_claims_no_exceptions(self, fname):
        t = self._text(fname)
        assert "IN-TEXT CITATION FORMAT — no exceptions" not in t, (
            "header still forecloses the patent exception")

    @pytest.mark.parametrize("fname", PROMPTS)
    def test_patent_rule_is_stated_in_the_header_not_only_later(self, fname):
        """The exception must appear near the top, where the paper rule is stated."""
        t = self._text(fname)
        assert "FOR PATENTS" in t
        head = t[:t.index("FOR PATENTS")]
        assert "FOR PAPERS" in head, "paper/patent split must be in the same header block"
        assert t.index("FOR PATENTS") < len(t) // 2, "patent rule buried too deep"

    @pytest.mark.parametrize("fname", PROMPTS)
    def test_selfcheck_does_not_force_et_al_on_patents(self, fname):
        """The cross-check step used to 'correct' patent citations into the wrong form."""
        t = self._text(fname)
        assert 'Verify all in-text citations use "Author et al. [X]" format' not in t
        assert 'that is the error, not the correction' in t

    @pytest.mark.parametrize("fname", PROMPTS)
    def test_reference_list_shows_a_patent_example(self, fname):
        t = self._text(fname)
        assert "Publication Number" in t or "US11123456B2" in t
        assert "inventor names into the author position" in t


class TestRateLimitingAndBatching:
    """45 calls/min upstream. A search must cost a small constant number of calls,
    not 1 + 2N, or a 50-patent search throttles."""

    def _resp(self, payload, status=200):
        m = MagicMock()
        m.json.return_value = payload
        m.status_code = status
        return m

    def _search_payload(self, n):
        return {"patents": [{
            "patent_id": str(1000 + i), "patent_title": f"Patent {i}",
            "patent_date": "2021-01-01", "patent_abstract": "abs",
            "assignees": [{"assignee_organization": "Acme"}],
            "inventors": [], "cpc_current": [],
        } for i in range(n)]}

    def test_call_count_is_constant_not_per_patent(self):
        src = PatentsViewSource(credentials={"api_key": "k"})
        src._MIN_INTERVAL = 0
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append(url)
            if "patent/" in url and "citation" not in url:
                return self._resp(self._search_payload(20))
            return self._resp({})

        with patch("requests.get", side_effect=fake_get):
            out = src.search("rppg", max_results=20)

        assert len(out) == 20
        assert len(calls) <= 4, f"20 patents should cost ~4 calls, took {len(calls)}"

    def test_claims_are_grouped_back_to_the_right_patent(self):
        src = PatentsViewSource(credentials={"api_key": "k"})
        src._MIN_INTERVAL = 0

        def fake_get(url, params=None, headers=None, timeout=None):
            if url.endswith("/patent/"):
                return self._resp(self._search_payload(2))
            if "g_claim" in url:
                return self._resp({"g_claims": [
                    {"patent_id": "1000", "claim_sequence": 1,
                     "claim_text": "Claim for first", "claim_dependent": None},
                    {"patent_id": "1001", "claim_sequence": 1,
                     "claim_text": "Claim for second", "claim_dependent": None},
                ]})
            return self._resp({})

        with patch("requests.get", side_effect=fake_get):
            out = src.search("x", max_results=2)

        by_num = {r["patent_meta"]["publication_number"]: r for r in out}
        assert "Claim for first" in by_num["US1000"]["patent_meta"]["claims_text"]
        assert "Claim for second" in by_num["US1001"]["patent_meta"]["claims_text"]
        assert "Claim for second" not in by_num["US1000"]["patent_meta"]["claims_text"]

    def test_429_backs_off_then_succeeds(self):
        src = PatentsViewSource(credentials={"api_key": "k"})
        src._MIN_INTERVAL = 0
        seq = [self._resp({}, status=429), self._resp(self._search_payload(1))]

        with patch("requests.get", side_effect=lambda *a, **k: seq.pop(0)), \
             patch("time.sleep") as slept:
            out = src.search("x", max_results=1)

        assert len(out) == 1, "must retry past a 429 rather than return nothing"
        assert slept.called, "must actually back off"

    def test_internal_patent_id_never_leaks_to_the_session(self):
        src = PatentsViewSource(credentials={"api_key": "k"})
        src._MIN_INTERVAL = 0
        with patch("requests.get", side_effect=lambda *a, **k: self._resp(self._search_payload(1))):
            out = src.search("x", max_results=1)
        assert "_patent_id" not in out[0]["patent_meta"]


class TestCitationHarvesting:
    def _resp(self, payload):
        m = MagicMock(); m.json.return_value = payload; m.status_code = 200
        return m

    def test_cited_patents_and_literature_are_captured(self):
        src = PatentsViewSource(credentials={"api_key": "k"})
        src._MIN_INTERVAL = 0

        def fake_get(url, params=None, headers=None, timeout=None):
            if url.endswith("/patent/"):
                return self._resp({"patents": [{
                    "patent_id": "1000", "patent_title": "T", "patent_date": "2021-01-01",
                    "patent_abstract": "a", "assignees": [], "inventors": [], "cpc_current": [],
                }]})
            if "g_us_patent_citation" in url:
                return self._resp({"g_us_patent_citations": [
                    {"patent_id": "1000", "citation_patent_id": "999"}]})
            if "g_other_reference" in url:
                return self._resp({"g_other_references": [
                    {"patent_id": "1000",
                     "otherreference_text": "Verkruysse et al., Optics Express 2008"}]})
            return self._resp({})

        with patch("requests.get", side_effect=fake_get):
            out = src.search("x", max_results=1)

        meta = out[0]["patent_meta"]
        assert meta["cited_patents"] == ["US999"]
        assert "Verkruysse" in meta["cited_literature"][0]

    def test_citation_endpoint_failure_does_not_lose_the_patent(self):
        src = PatentsViewSource(credentials={"api_key": "k"})
        src._MIN_INTERVAL = 0

        def fake_get(url, params=None, headers=None, timeout=None):
            if url.endswith("/patent/"):
                return self._resp({"patents": [{
                    "patent_id": "1000", "patent_title": "T", "patent_date": "2021-01-01",
                    "patent_abstract": "a", "assignees": [], "inventors": [], "cpc_current": [],
                }]})
            raise Exception("citation endpoint down")

        with patch("requests.get", side_effect=fake_get):
            out = src.search("x", max_results=1)

        assert len(out) == 1
        assert out[0]["patent_meta"]["cited_patents"] == []
        assert out[0]["patent_meta"]["claims_text"] == ""
