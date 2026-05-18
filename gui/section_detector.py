"""
Detects top-level sections in an academic PDF.

Primary: fitz font-metadata (font size + bold flag).
Fallback: regex on plain text.
Returns (OrderedDict{canonical_name: text}, method_used).
method_used is one of: 'font', 'regex', 'fallback'.

IEEE abstract note: many IEEE papers write "Abstract—text…" inline on one line
(no separate header line). Both detectors handle this explicitly.
"""
import re
import statistics
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Tuple

# Sections that always appear at the END of a paper.
# When the same name is detected twice (spurious header mid-paper + real section at end),
# the last occurrence is authoritative — use last-wins semantics for these only.
_TERMINAL_SECTIONS = {"References", "Conclusion"}

# Canonical section names that can appear as inline bold subheadings inside an IEEE
# structured abstract (Objective / Methods / Results / Significance).  "Methods" and
# "Results" are in _CANONICAL, so they would be mis-detected as real section boundaries
# before Introduction is seen.  Block them until Introduction is confirmed.
_STRUCTURED_ABSTRACT_SUBHEADINGS = frozenset({"Methods", "Results", "Discussion", "Background"})

_CANONICAL = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "related work": "Related Work",
    "related works": "Related Work",
    "background": "Background",
    "literature review": "Related Work",
    "prior work": "Related Work",
    "previous work": "Related Work",
    "method": "Methods",
    "methods": "Methods",
    "methodology": "Methods",
    "proposed method": "Methods",
    "proposed approach": "Methods",
    "approach": "Methods",
    "our approach": "Methods",
    "system": "Methods",
    "framework": "Methods",
    "model": "Methods",
    "algorithm": "Methods",
    "experiment": "Experiments",
    "experiments": "Experiments",
    "experimental setup": "Experiments",
    "experimental results": "Results",
    "result": "Results",
    "results": "Results",
    "evaluation": "Results",
    "performance": "Results",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
    "conclusions": "Conclusion",
    "conclusion and future work": "Conclusion",
    "conclusions and future work": "Conclusion",
    "concluding remarks": "Conclusion",
    "summary": "Conclusion",
    "reference": "References",
    "references": "References",
    "bibliography": "References",
}

# Matches a section header alone on its own line (with optional leading
# roman-numeral or decimal number prefix).
_HEADER_RE = re.compile(
    r'(?m)^[ \t]*(?:(?:[IVXivx]+|[0-9]+(?:\.[0-9]+)?)[ \t]*[.)][ \t]*)?'
    r'(Abstract|Introduction|Related Works?|Background|Literature Review|'
    r'Methodology|Proposed (?:Method|Approach)|Methods?|(?:Our )?Approach|'
    r'Framework|System|Algorithm|Model|'
    r'Experiments?|Experimental (?:Setup|Results?)|Results?|Evaluation|Performance|'
    r'Discussion|Conclusions?(?:[ \t]+and[ \t]+Future[ \t]+Work)?|'
    r'Concluding Remarks|Summary|'
    r'References?|Bibliography)[ \t]*$',
    re.IGNORECASE,
)

# Matches the IEEE inline-abstract pattern: "Abstract—text…" or "Abstract: text…"
# The separator can be em-dash, en-dash, colon, hyphen, or just a space.
_INLINE_ABSTRACT_RE = re.compile(
    r'^[ \t]*(Abstract)[ \t]*[—–:\-][ \t]*',
    re.IGNORECASE,
)


def _normalise(raw: str):
    cleaned = re.sub(r'^(?:[IVXivx]+|[0-9]+(?:\.[0-9]+)*)[ \t]*[.)][ \t]*', '', raw.strip())
    return _CANONICAL.get(cleaned.lower().strip())


def _normalise_plain(text: str) -> str:
    """Pre-process plain-text to split inline abstracts onto their own line.

    Converts "Abstract—This paper…" → "Abstract\\nThis paper…"
    so _HEADER_RE can match it in the normal flow.
    """
    return re.sub(
        r'(?im)^([ \t]*Abstract)[ \t]*[—–:\-][ \t]*',
        r'\1\n',
        text,
    )


def _font_detect(pdf_path: str) -> OrderedDict:
    import fitz

    doc = fitz.open(pdf_path)
    try:
        sizes = []
        pages_data = []

        for page in doc:
            page_width = page.rect.width or 1
            d = page.get_text("dict", flags=0)
            raw = []
            for blk in d.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                texts, szs, bolds = [], [], []
                for ln in blk.get("lines", []):
                    for sp in ln.get("spans", []):
                        t = sp.get("text", "").strip()
                        if not t:
                            continue
                        sz = sp.get("size", 10.0)
                        fn = sp.get("font", "")
                        fl = sp.get("flags", 0)
                        bold = bool(fl & 16) or "Bold" in fn or "bold" in fn
                        texts.append(t)
                        szs.append(sz)
                        bolds.append(bold)
                        sizes.append(sz)
                if texts:
                    bbox = blk.get("bbox", (0, 0, 0, 0))
                    raw.append((bbox[0], bbox[1], " ".join(texts), szs, bolds))

            # Re-sort by (column, y) so left-column content always precedes
            # right-column content on the same page — fixes two-column IEEE PDFs
            # where fitz returns a right-column section header before the tail
            # of the left-column paragraph that precedes it.
            half = page_width / 2
            raw.sort(key=lambda b: (0 if b[0] < half else 1, b[1]))

            pages_data.append([(txt, szs, bolds) for (_, _, txt, szs, bolds) in raw])

        if not sizes:
            return OrderedDict()

        median_sz = statistics.median(sizes)
        threshold = median_sz + 1.5

        sections: OrderedDict = OrderedDict()
        cur_name = None
        cur_body = []
        introduction_seen = False

        for pg in pages_data:
            for (text, szs, bolds) in pg:

                # ── Special case: inline abstract "Abstract—body…" ──────────
                # This is a long block so the normal len<100 check would miss it.
                m_abs = _INLINE_ABSTRACT_RE.match(text)
                if m_abs and "Abstract" not in sections and cur_name != "Abstract":
                    if cur_name and cur_body and cur_name not in sections:
                        sections[cur_name] = "\n".join(cur_body).strip()
                    body_text = text[m_abs.end():].strip()
                    # Accumulate rather than save immediately: IEEE structured abstracts
                    # have Methods / Results / Significance as separate subsequent blocks.
                    # Setting cur_name keeps them appended until Introduction is detected.
                    cur_name = "Abstract"
                    cur_body = [body_text] if body_text else []
                    continue

                # ── Normal header detection ──────────────────────────────────
                if (len(text) < 100
                        and (min(szs) >= threshold
                             or (all(bolds) and min(szs) >= median_sz - 0.5))):
                    canonical = _normalise(text)
                    if canonical:
                        if canonical == "Introduction":
                            introduction_seen = True
                        elif not introduction_seen and canonical in _STRUCTURED_ABSTRACT_SUBHEADINGS:
                            # This bold word is a structured-abstract subheading
                            # (e.g. "Methods", "Results" inside the IEEE abstract block),
                            # not a real paper section.  Keep the label as body text so
                            # the LLM sees the full abstract, and do not start a new section.
                            if cur_name is None:
                                cur_name = "Abstract"
                            cur_body.append(text)
                            continue
                        if cur_name and cur_body and cur_name not in sections:
                            sections[cur_name] = "\n".join(cur_body).strip()
                        cur_name = canonical
                        cur_body = []
                        continue

                if cur_name is not None:
                    cur_body.append(text)

        # Last-wins for terminal sections: a spurious mid-paper header (e.g., a bold
        # "References" in a comparison table) may have already inserted a short entry.
        # The real section is always the last one detected — allow it to overwrite.
        if cur_name and cur_body:
            if cur_name not in sections or cur_name in _TERMINAL_SECTIONS:
                sections[cur_name] = "\n".join(cur_body).strip()

        return sections
    finally:
        doc.close()


def _regex_detect(pdf_path: str) -> OrderedDict:
    import fitz

    doc = fitz.open(pdf_path)
    try:
        plain = "\n".join(pg.get_text() for pg in doc)
    finally:
        doc.close()

    # Normalise inline abstract before running the header regex
    plain = _normalise_plain(plain)

    matches = list(_HEADER_RE.finditer(plain))
    if len(matches) < 3:
        return OrderedDict()

    # Drop structured-abstract subheadings ("Methods", "Results" …) that appear
    # before the Introduction match — they are inline bold labels inside the
    # abstract paragraph, not real section boundaries.
    intro_pos = next(
        (m.start() for m in matches if _normalise(m.group(1)) == "Introduction"),
        None,
    )
    if intro_pos is not None:
        matches = [
            m for m in matches
            if m.start() >= intro_pos
            or (_normalise(m.group(1)) not in _STRUCTURED_ABSTRACT_SUBHEADINGS)
        ]

    sections: OrderedDict = OrderedDict()
    for i, m in enumerate(matches):
        canonical = _normalise(m.group(1)) or m.group(1).title()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plain)
        text = plain[m.end():end].strip()
        if canonical not in sections or canonical in _TERMINAL_SECTIONS:
            sections[canonical] = text

    return sections


# ─────────────────────────────────────────────────────────────────────────────
#  LaTeX support
# ─────────────────────────────────────────────────────────────────────────────

def _strip_latex_for_audit(text: str) -> str:
    """Light LaTeX → readable-text conversion for LLM auditing.

    Removes float and math environments (replaced with placeholders),
    unwraps common text-formatting commands, and strips comments.
    Citation keys, \\ref{}, and structural markup are kept — the LLM
    understands them and they are useful for auditing claim traceability.
    """
    # Strip LaTeX comments FIRST (% not preceded by \), then convert \% → %.
    # Order matters: converting \% first would produce a bare % that the
    # comment stripper then treats as a comment, dropping the rest of the line.
    text = re.sub(r'(?<!\\)%[^\n]*', '', text)
    text = text.replace(r'\%', '%')
    # Float environments → placeholder
    for env in ('figure', 'table', 'algorithm', 'listing', 'tikzpicture', 'wrapfigure'):
        text = re.sub(
            r'\\begin\{' + env + r'\*?\}.*?\\end\{' + env + r'\*?\}',
            f'\n[{env}]\n', text, flags=re.DOTALL | re.IGNORECASE)
    # Display math environments → placeholder
    for env in ('equation', 'align', 'gather', 'multline', 'eqnarray', 'split', 'cases'):
        text = re.sub(
            r'\\begin\{' + env + r'\*?\}.*?\\end\{' + env + r'\*?\}',
            '\n[formula]\n', text, flags=re.DOTALL | re.IGNORECASE)
    # Remaining \begin / \end markers
    text = re.sub(r'\\(?:begin|end)\{[^}]*\}', '', text)
    # Display math \[ ... \]
    text = re.sub(r'\\\[.*?\\\]', '\n[formula]\n', text, flags=re.DOTALL)
    # Inline math $...$  — processed in ONE pass to avoid cross-boundary matches.
    # The old regex \$[^$\n]{40,}\$ could match from the closing $ of one short
    # formula to the closing $ of a later formula (e.g. $4x4$ ... $q<0.05$ →
    # $4x4[formula]q<0.05$).  Instead: replace every $...$ pair; keep content
    # when it is short and contains no LaTeX commands (readable as-is), otherwise
    # replace with [formula].
    def _inline_math(m):
        content = m.group(1)
        if len(content) <= 20 and '\\' not in content:
            return content   # e.g. "r = +0.969", "q < 0.05", "< 0.30"
        return '[formula]'
    text = re.sub(r'\$([^$\n]+)\$', _inline_math, text)
    # \label{...} → remove (not useful for auditing)
    text = re.sub(r'\\label\{[^}]*\}', '', text)
    # Unwrap common text-formatting commands: keep the argument
    text = re.sub(r'\\(?:textbf|textit|emph|underline|textrm|texttt|text)\{([^}]*)\}',
                  r'\1', text)
    # Collapse 3+ blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _mask_region(text: str, start: int, end: int) -> str:
    """Replace [start:end] with spaces, keeping string length and all other positions intact."""
    return text[:start] + ' ' * (end - start) + text[end:]


def _load_bbl_file(tex_path: str):
    """Return stripped content of the companion .bbl file, or None if absent."""
    try:
        bbl = Path(tex_path).with_suffix('.bbl')
        if bbl.exists():
            return _strip_latex_for_audit(bbl.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return None


def detect_sections_latex(tex_path: str) -> Tuple[Dict[str, str], str]:
    """Split a .tex file into top-level sections for LLM auditing.

    Handles three constructs that are NOT \\section{} headers:

    * Abstract  — \\begin{abstract}...\\end{abstract} (precedes \\section{Introduction})
    * References — \\begin{thebibliography}...\\end{thebibliography}, or
                   \\bibliography{}/\\printbibliography + companion .bbl file
                   (typically embedded at the end of the Conclusion section)

    Strategy: extract these environments first, then mask their regions with
    spaces (preserving all string positions) before running \\section{} boundary
    detection — so Conclusion and other section bodies are not contaminated.
    """
    content = Path(tex_path).read_text(encoding='utf-8', errors='replace')

    body_m = re.search(r'\\begin\{document\}(.*?)(?:\\end\{document\}|$)',
                       content, re.DOTALL)
    body    = body_m.group(1) if body_m else content
    working = body   # will be masked in-place; positions stay valid throughout

    sections: OrderedDict = OrderedDict()

    # ── 1. Abstract environment ──────────────────────────────────────────────
    abs_m = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', body, re.DOTALL)
    if abs_m:
        sections['Abstract'] = _strip_latex_for_audit(abs_m.group(1))
        working = _mask_region(working, abs_m.start(), abs_m.end())

    # ── 2. Bibliography / References ─────────────────────────────────────────
    bib_text = None
    bib_env_m = re.search(
        r'\\begin\{thebibliography\}(.*?)\\end\{thebibliography\}', body, re.DOTALL)
    if bib_env_m:
        bib_text = _strip_latex_for_audit(bib_env_m.group(1))
        working  = _mask_region(working, bib_env_m.start(), bib_env_m.end())
    else:
        # biblatex / natbib external bibliography
        ext_m = re.search(
            r'\\(?:bibliography\{[^}]*\}|printbibliography(?:\[[^\]]*\])?)', body)
        if ext_m:
            working  = _mask_region(working, ext_m.start(), ext_m.end())
            bib_text = _load_bbl_file(tex_path)   # None when no .bbl present

    # ── 3. \section{} splits on masked body ─────────────────────────────────
    sec_re     = re.compile(r'\\section\s*\*?\s*\{([^}]+)\}')
    sec_matches = list(sec_re.finditer(working))

    for i, m in enumerate(sec_matches):
        raw_name = m.group(1).strip()
        raw_name = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', raw_name)
        raw_name = re.sub(r'\\[a-zA-Z]+', '', raw_name).strip()
        canonical = _CANONICAL.get(raw_name.lower(), raw_name.title())

        end  = sec_matches[i + 1].start() if i + 1 < len(sec_matches) else len(working)
        text = _strip_latex_for_audit(working[m.end():end])

        if canonical not in sections or canonical in _TERMINAL_SECTIONS:
            sections[canonical] = text

    # ── 4. References — always overwrites any empty placeholder ──────────────
    if bib_text is not None:
        sections['References'] = bib_text

    return _drop_short_sections(sections), "latex"


def extract_latex_text(tex_path: str, char_limit: int = None) -> str:
    """Return stripped plain text from a .tex file for single-call audit mode."""
    content = Path(tex_path).read_text(encoding='utf-8', errors='replace')
    body_m  = re.search(r'\\begin\{document\}(.*?)(?:\\end\{document\}|$)',
                        content, re.DOTALL)
    body = body_m.group(1) if body_m else content
    text = _strip_latex_for_audit(body)
    return text[:char_limit] if char_limit else text


def _drop_short_sections(sections: OrderedDict, min_chars: int = 100) -> OrderedDict:
    """Remove entries whose content is too short to be a real section.

    A spurious section (e.g. bold 'References' in a comparison table that was
    never overwritten) typically has < 100 stripped chars.  Real sections —
    even a 5-entry reference list — are always much longer.
    """
    return OrderedDict(
        (k, v) for k, v in sections.items() if len(v.strip()) >= min_chars
    )


def detect_sections(pdf_path: str) -> Tuple[Dict[str, str], str]:
    """Return (sections_dict, method) where method is 'font', 'regex', or 'fallback'."""
    try:
        s = _drop_short_sections(_font_detect(pdf_path))
        if len(s) >= 3:
            return s, "font"
    except Exception:
        pass

    try:
        s = _drop_short_sections(_regex_detect(pdf_path))
        if len(s) >= 3:
            return s, "regex"
    except Exception:
        pass

    return OrderedDict(), "fallback"
