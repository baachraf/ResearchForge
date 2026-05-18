"""
Task 3: Test that session_creator.py _setup_manual_tab has updated placeholder and tooltip.
Tests using regex on the source file, not instantiation (avoids Qt init).
"""
import sys, os, re

# Read the session_creator.py file
session_creator_path = os.path.join(os.path.dirname(__file__), "..", "gui", "session_creator.py")
with open(session_creator_path, 'r', encoding='utf-8') as f:
    content = f.read()


def test_placeholder_mentions_contribution():
    """research_text.setPlaceholderText must mention CONTRIBUTION"""
    # Look for the placeholder text in _setup_manual_tab
    match = re.search(
        r'self\.research_text\.setPlaceholderText\(\s*"(.+?)"\s*\)',
        content,
        re.DOTALL
    )
    assert match is not None, "Could not find research_text.setPlaceholderText"
    placeholder = match.group(1)
    assert "CONTRIBUTION" in placeholder, f"Placeholder does not mention CONTRIBUTION:\n{placeholder}"


def test_placeholder_mentions_problem_space():
    """research_text.setPlaceholderText must mention PROBLEM SPACE"""
    match = re.search(
        r'self\.research_text\.setPlaceholderText\(\s*"(.+?)"\s*\)',
        content,
        re.DOTALL
    )
    assert match is not None, "Could not find research_text.setPlaceholderText"
    placeholder = match.group(1)
    assert "PROBLEM SPACE" in placeholder, f"Placeholder does not mention PROBLEM SPACE:\n{placeholder}"


def test_enhance_tooltip_mentions_contribution():
    """btn_enhance.setToolTip must mention CONTRIBUTION and 'coined terms'"""
    match = re.search(
        r'self\.btn_enhance\.setToolTip\(\s*"(.+?)"\s*\)',
        content,
        re.DOTALL
    )
    assert match is not None, "Could not find btn_enhance.setToolTip"
    tooltip = match.group(1)
    assert "CONTRIBUTION" in tooltip, f"Tooltip does not mention CONTRIBUTION:\n{tooltip}"
    assert "coined terms" in tooltip, f"Tooltip does not mention 'coined terms':\n{tooltip}"


def test_enhance_tooltip_mentions_discovery():
    """btn_enhance.setToolTip must mention discovery or PROBLEM SPACE"""
    match = re.search(
        r'self\.btn_enhance\.setToolTip\(\s*"(.+?)"\s*\)',
        content,
        re.DOTALL
    )
    assert match is not None, "Could not find btn_enhance.setToolTip"
    tooltip = match.group(1)
    has_discovery = "discovery" in tooltip.lower()
    has_problem_space = "PROBLEM SPACE" in tooltip
    assert has_discovery or has_problem_space, f"Tooltip mentions neither discovery nor PROBLEM SPACE:\n{tooltip}"


def test_populate_suggested_shows_intent():
    """_populate_suggested_table must reference 'intent' in setToolTip"""
    # Find the _populate_suggested_table method
    match = re.search(
        r'def _populate_suggested_table\(self\):(.+?)(?=\n    def )',
        content,
        re.DOTALL
    )
    assert match is not None, "Could not find _populate_suggested_table method"
    method_body = match.group(1)
    assert "intent" in method_body, f"_populate_suggested_table does not reference 'intent'"


def test_populate_search_shows_intent():
    """_populate_search_table must reference 'intent' in setToolTip"""
    # Find the _populate_search_table method
    match = re.search(
        r'def _populate_search_table\(self\):(.+?)(?=\n    def )',
        content,
        re.DOTALL
    )
    assert match is not None, "Could not find _populate_search_table method"
    method_body = match.group(1)
    assert "intent" in method_body, f"_populate_search_table does not reference 'intent'"
