"""
Test query generation prompt format: verifies two-section structure detection and intent labeling.
Tests the fallback constant, not the file (file may be user-customised).
"""
import sys, os, re

# Read the session_creator.py file to extract FALLBACK_QUERY_GENERATOR
session_creator_path = os.path.join(os.path.dirname(__file__), "..", "gui", "session_creator.py")
with open(session_creator_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Extract the FALLBACK_QUERY_GENERATOR constant
match = re.search(r'FALLBACK_QUERY_GENERATOR = """(.+?)"""', content, re.DOTALL)
assert match is not None, "Could not extract FALLBACK_QUERY_GENERATOR — regex broke or constant was renamed"
FALLBACK_QUERY_GENERATOR = match.group(1)


def test_structured_input_detection_present():
    assert "STRUCTURED INPUT DETECTION" in FALLBACK_QUERY_GENERATOR


def test_contribution_text_label():
    assert "CONTRIBUTION TEXT" in FALLBACK_QUERY_GENERATOR


def test_problem_space_text_label():
    assert "PROBLEM SPACE TEXT" in FALLBACK_QUERY_GENERATOR


def test_prior_art_intent():
    assert "prior_art" in FALLBACK_QUERY_GENERATOR


def test_discovery_intent():
    assert "discovery" in FALLBACK_QUERY_GENERATOR


def test_intent_in_json_format():
    assert '"intent"' in FALLBACK_QUERY_GENERATOR


def test_graceful_degradation():
    # If no structure found, treat as PROBLEM SPACE only
    assert "PROBLEM SPACE" in FALLBACK_QUERY_GENERATOR
    assert "leave CONTRIBUTION TEXT empty" in FALLBACK_QUERY_GENERATOR
