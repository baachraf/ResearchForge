"""
Regression test: verify the enhance prompt instructs two-section output.
Tests the fallback constant, not the file (file may be user-customised).
"""
import sys, os, re

# Read the session_creator.py file to extract FALLBACK_ENHANCE_RESEARCH
session_creator_path = os.path.join(os.path.dirname(__file__), "..", "gui", "session_creator.py")
with open(session_creator_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Extract the FALLBACK_ENHANCE_RESEARCH constant
match = re.search(r'FALLBACK_ENHANCE_RESEARCH = """(.+?)"""', content, re.DOTALL)
assert match is not None, "Could not extract FALLBACK_ENHANCE_RESEARCH — regex broke or constant was renamed"
FALLBACK_ENHANCE_RESEARCH = match.group(1)


def test_contribution_section_required():
    assert "CONTRIBUTION:" in FALLBACK_ENHANCE_RESEARCH


def test_problem_space_section_required():
    assert "PROBLEM SPACE:" in FALLBACK_ENHANCE_RESEARCH


def test_no_single_prose_instruction():
    # The old prompt said "Output ONLY the reformulated text" as a single block.
    # The new prompt must not say that — it must require two sections.
    assert "Output ONLY the reformulated text" not in FALLBACK_ENHANCE_RESEARCH


def test_contribution_before_problem_space():
    ci = FALLBACK_ENHANCE_RESEARCH.index("CONTRIBUTION:")
    pi = FALLBACK_ENHANCE_RESEARCH.index("PROBLEM SPACE:")
    assert ci < pi
