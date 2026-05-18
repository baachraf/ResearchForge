import contextlib
import os
import re
from typing import List, Optional, Any


def extract_year(sources: List[Any]) -> Optional[int]:
    """
    Tries to find a 4-digit year in a list of source strings or objects.
    Looks for years between 1980 and 2026.
    """
    # Regex for 4 digits between 1980 and 2026
    year_pattern = re.compile(r'\b(19[89]\d|20[012]\d)\b')
    
    found_years = []
    for s in sources:
        if not s:
            continue
        # Convert to string if it's not (like Brave snippets which are objects or dicts sometimes)
        s_str = str(s)
        matches = year_pattern.findall(s_str)
        for m in matches:
            found_years.append(int(m))
            
    if not found_years:
        return None
        
    # Pick the highest year that is not in the future (relative to the tool's 2026 context)
    valid_years = [y for y in found_years if y <= 2026]
    if not valid_years:
        return None
        
    return max(valid_years)
def passes_title_filter(title: str, must_contain: List[str], threshold: int = 1) -> bool:
    """
    PRE-DOWNLOAD FILTER.
    Returns True if at least `threshold` of the must_contain keywords appear in the title.
    Case-insensitive match. Hyphens are normalized to spaces for matching.
    """
    if not must_contain:
        return True

    title_normalized = title.lower().replace("-", " ")
    match_count = 0
    for keyword in must_contain:
        kw_normalized = keyword.lower().replace("-", " ")
        if kw_normalized in title_normalized:
            match_count += 1
            if match_count >= threshold:
                return True
    return False


def get_pdf_text_first_pages(filepath: str, num_pages: int = 3) -> Optional[str]:
    """
    Extracts text from the first N pages of a PDF using PyMuPDF.
    Returns None if extraction fails.
    """
    try:
        import fitz
        import os, sys
        with open(os.devnull, "w") as f, contextlib.redirect_stderr(f):
            doc = fitz.open(filepath)
        text = ""
        for i in range(min(num_pages, len(doc))):
            text += doc[i].get_text()
        doc.close()
        return text.lower()
    except Exception as e:
        print(f"Warning: Could not extract text from {filepath}: {e}")
        return None


def passes_content_filter(filepath: str, must_contain: List[str], threshold: int = 1) -> bool:
    """
    POST-DOWNLOAD FILTER.
    Extracts text from the first 3 pages of the PDF and checks
    how many of the must_contain keywords appear.
    Returns True if count >= threshold.
    """
    if not must_contain:
        return True  # No filter, accept all

    text = get_pdf_text_first_pages(filepath)
    if text is None:
        # If we can't read the PDF, give benefit of the doubt
        return True

    matched = sum(1 for kw in must_contain if kw.lower() in text)
    return matched >= threshold
