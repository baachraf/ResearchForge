"""
llm_pdf_engine — Standalone CLI engine for batch paper analysis and synthesis.

Usage: python llm_pdf_engine.py <endpoint> <model_id> <prompt_path> <input_dir> <output_dir> [provider_name] [api_key]

Pipeline (3-pass):
  1. Per-paper: extract PDF text → LLM individual analysis → cache to _cache/
  2. Per-topic: merge cached analyses → LLM topic synthesis (TOPIC_SYNTHESIS_PROMPT)
  3. Global:    merge topic summaries   → LLM cross-topic synthesis (GLOBAL_SYNTHESIS_PROMPT)

This file has NO dependency on the GUI (PySide6). It uses the OpenAI client directly.

NOTE: The GUI (summarize_tab.py) uses SEPARATE prompt files from config/prompts/ for
topic/global synthesis, loaded via ConfigManager. The hardcoded prompts below are
ONLY for this CLI engine path.
"""
import os
import sys
import re
import pypdf
from tqdm import tqdm

PAPER_SEPARATOR = "\n\n" + "=" * 70 + "\n" + "*" * 70 + "\n" + "=" * 70 + "\n\n"

TOPIC_SYNTHESIS_PROMPT = """\
You are a senior IEEE researcher performing a rigorous cross-paper synthesis for a journal submission. \
You have received a MASTER REPORT containing individual structured analyses of multiple papers on the SAME TOPIC. \
Each paper analysis is clearly separated by a divider. Your job is to synthesize them into a single, \
high-quality topic-level review that a reviewer or co-author can use directly.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CITATION FORMAT RULES — enforce without exception
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- **In-text citation**: always "FirstAuthor et al. [X]" — first author's LAST NAME, lowercase "et al.", then number. Example: "Smith et al. [3]"
- **NEVER write "Author & Author [X]" or "Author and Author [X]"** — always "FirstAuthor et al. [X]" regardless of co-author count
- **NEVER write a bare number without an author** — "[1]" alone is wrong. Always "Author et al. [1]"
- **NEVER cite a venue name as an author** — "EMBC 2012 [5]" is WRONG. Use the first author's name
- **Reference list (Section 8)**: ALL authors in full — never truncate, never use "et al." in the reference list
- **Method/model acronyms**: preserve exactly as each paper's authors use them
- **Consistent numbering**: [1], [2], [3]... consecutively, no gaps. Same number for same paper everywhere

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BEFORE OUTPUT — CROSS-CHECK (MANDATORY, never skip)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. List every citation number used in the body text
2. Verify every number appears in the reference list
3. Verify numbering is [1], [2], [3]... consecutive with NO gaps or duplicates
4. Verify all in-text citations use "Author et al. [X]" — not "Author & Author", not bare "[X]", not "Venue [X]"
5. Fix all violations BEFORE outputting

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ABSOLUTE RULE — BUILD AND PRESERVE THE REFERENCE LIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Your first action: read every individual paper analysis and extract its CITATION DATA table. \
Assign sequential numbers [1], [2], [3]... in the order they appear. This is your reference registry.

Then apply without exception:
- Every paper mention = "FirstAuthor et al. [X]" inline citation
- Multiple papers on same claim = cite all: [X], [Y], [Z]
- Section 8 is MANDATORY — every paper from MASTER REPORT, all authors in full

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT STRUCTURE — complete every section, never skip any
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Never say "I cannot determine this" — always produce output based on available information.

## 1. TOPIC OVERVIEW
State the topic, how many papers analyzed, sub-themes covered, and time span.

## 2. COMMON THEMES & CONVERGENT FINDINGS
For each recurring theme across papers, describe what multiple papers agree on. Cite as "FirstAuthor et al. [X]".

## 3. HOW RESEARCHERS EXPLAIN THE PROBLEM
Summarize how this body of literature collectively defines and explains the core problem. Use "FirstAuthor et al. [X]" format.

## 4. COMPETING SOLUTIONS — DETAILED COMPARISON TABLE
Use the authors' exact acronyms:
| # | Method / Paper | Year | Core Idea | Problem Targeted | Root Cause Addressed? | Key Limitation |
|---|---|---|---|---|---|---|
| 1 | Smith et al. [1] | 2023 | | | | |

## 5. PROXIMITY TO OUR WORK
Identify the 3-5 most similar papers. For each, state the critical difference. Use "FirstAuthor et al. [X]" format.

## 6. GAPS — WHAT THIS TOPIC LEAVES UNANSWERED
List concrete, specific gaps as falsifiable claims.

## 7. RELATED WORKS NARRATIVE (Ready for Paper Use)
Polished IEEE-style prose. Pure narrative paragraphs. 2-3 sentences per paper covering: \
(a) what authors proposed (exact acronyms), (b) key result, (c) relation to our work. Always "FirstAuthor et al. [X]".

## 8. CONSOLIDATED REFERENCE LIST (MANDATORY)
Every paper from MASTER REPORT, numbered [1]-[N] consecutively — no gaps.
**ALL authors in full — never use "et al." in this section.**
Format: [X] All Authors, "Title," *Venue*, vol., no., pp., Year. DOI.
"""

GLOBAL_SYNTHESIS_PROMPT = """\
You are a senior IEEE researcher writing the Related Works section of a journal paper. \
You have received TOPIC SUMMARIES covering multiple distinct aspects of the research landscape. \
Produce a single, authoritative global overview — rigorous, well-cited, and clearly positioning our contribution.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CITATION FORMAT RULES — enforce without exception
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- **In-text citation**: always "FirstAuthor et al. [X]" — first author's LAST NAME, lowercase "et al.", then number
- **NEVER write "Author & Author [X]" or "Author and Author [X]"** — always "FirstAuthor et al. [X]" regardless of co-author count
- **NEVER write a bare number without an author** — "[1]" alone is wrong. Always "Author et al. [1]"
- **NEVER cite a venue name as an author** — "EMBC 2012 [5]" is WRONG. Use the first author's name
- **Reference list (Section 8)**: ALL authors in full — never truncate, never "et al." in the reference list
- **Method/model acronyms**: preserve exactly as each paper's authors use them
- **Consistent numbering**: deduplicate, renumber [1], [2], [3]... consecutively — no gaps

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BEFORE OUTPUT — CROSS-CHECK (MANDATORY, never skip)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. List every citation number used in the body text
2. Verify every one appears in the reference list
3. Verify numbering is [1], [2], [3]... consecutive with NO gaps or duplicates
4. Verify all in-text citations use "Author et al. [X]" — not "Author & Author", not bare "[X]", not "Venue [X]"
5. Fix all violations BEFORE outputting

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ABSOLUTE RULE — CONSOLIDATE AND PRESERVE ALL REFERENCES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
First action: scan every topic summary, collect ALL references from their lists. \
Merge into a single master registry, deduplicate by title, assign new consecutive numbers [1], [2], [3]...

Then apply without exception:
- Every claim = "FirstAuthor et al. [X]" inline citation
- Same paper in multiple topics = unify under one number
- Section 8 is MANDATORY — all papers, all authors, consecutive numbering

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT STRUCTURE — complete every section, never skip any
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 1. LANDSCAPE OVERVIEW
Map the research landscape: how many topics, total papers, major research threads, and how they relate. Use "FirstAuthor et al. [X]".

## 2. EVOLUTION OF THE FIELD
How thinking on this problem has evolved. Early work focus. Later corrections/improvements. "FirstAuthor et al. [X]" for all citations.

## 3. CROSS-TOPIC THEMES
Themes across multiple topics. Note contradictions. "FirstAuthor et al. [X]" for all citations.

## 4. UNIFIED COMPETING SOLUTIONS MAP
| # | Method / Paper | Research Thread | Year | Core Idea | Root Cause Addressed? | Fatal Limitation | Similarity to Our Work |
|---|---|---|---|---|---|---|---|
| 1 | Smith et al. [1] | | 2023 | | | | |

## 5. STATE OF THE ART
Current best-performing approaches. Metrics, benchmarks, performance ceiling. "FirstAuthor et al. [X]".

## 6. OUR CONTRIBUTION IN CONTEXT
3-5 sentences positioning our work. Be precise. "To the best of our knowledge, no existing work [specific claim], which is exactly what we address."

## 7. FULL RELATED WORKS SECTION
Polished, publication-ready IEEE prose. No bullet points. No tables. Structure by research thread (one paragraph per thread, 2-4 papers each). Per paper: (a) methodology (exact acronyms), (b) key result, (c) relevance to our work. Always "FirstAuthor et al. [X]".

## 8. MASTER REFERENCE LIST (MANDATORY)
All papers from all topic summaries, deduplicated, renumbered [1]-[N] consecutively — no gaps.
**ALL authors in full — never use "et al." in the reference list.**
Format: [X] All Authors, "Title," *Venue*, vol., no., pp., Year. DOI.
"""


class LLMPdfEngine:
    """Standalone CLI engine for batch PDF analysis and cross-paper synthesis.

    Three-pass pipeline:
      1. Per-paper LLM analysis (uses the prompt template loaded from prompt_path)
      2. Per-topic synthesis via TOPIC_SYNTHESIS_PROMPT (hardcoded)
      3. Global cross-topic synthesis via GLOBAL_SYNTHESIS_PROMPT (hardcoded)

    Uses PYPDF for text extraction and the OpenAI-compatible client for LLM calls.
    Caches individual paper analyses in _cache/ to avoid re-processing.

    :param endpoint: OpenAI-compatible API base URL
    :param model_id: Model name string (e.g. "deepseek-chat", "gpt-4")
    :param prompt_path: Path to the per-paper analysis prompt template (.md file)
    :param input_path: Directory with topic subfolders containing PDFs
    :param output_path: Directory for output summaries
    :param provider_name: Provider identifier for client creation ("" for auto-detect)
    :param api_key: API key for remote providers ("not-needed" for local)
    """

    def __init__(self, endpoint: str, model_id: str, prompt_path: str,
                 input_path: str, output_path: str,
                 provider_name: str = "", api_key: str = "not-needed"):
        from gui.llm_provider import create_llm_client
        self.client = create_llm_client(endpoint, api_key, provider_name, timeout=120.0)
        self.model_id: str = model_id
        self.provider_name: str = provider_name
        self.api_key: str = api_key
        self.endpoint: str = endpoint
        self.input_path: str = input_path
        self.output_path: str = output_path

        self.safe_model_name: str = re.sub(r'[\\/*?:"<>|.]', '_', model_id)

        with open(prompt_path, 'r', encoding='utf-8') as f:
            self.prompt_template: str = f.read()

    def extract_text(self, pdf_path: str, max_pages: int = 12,
                     timeout_sec: int = 30) -> str | None:
        """Extract text from a PDF file with a timeout.

        Runs pypdf.PdfReader in a daemon thread to avoid blocking on corrupted files.

        :returns: Extracted text string, or None on failure/timeout.
        """
        import threading

        result = [None]
        error  = [None]

        def _read():
            try:
                with open(pdf_path, 'rb') as f:
                    reader = pypdf.PdfReader(f)
                    text = ""
                    for i in range(min(len(reader.pages), max_pages)):
                        page_text = reader.pages[i].extract_text()
                        if page_text:
                            text += page_text
                    result[0] = text if text.strip() else None
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=timeout_sec)

        if t.is_alive():
            print(f"\n  [WARN] PDF read timed out after {timeout_sec}s — skipping: {os.path.basename(pdf_path)}", flush=True)
            return None
        if error[0]:
            print(f"\n  [WARN] PDF read error ({error[0]}) — skipping: {os.path.basename(pdf_path)}", flush=True)
            return None
        return result[0]

    def ping(self) -> None:
        """Test LLM connection. Calls sys.exit(1) on failure."""
        from gui.llm_provider import check_provider_connection
        print(f"\n[PING] Testing {self.provider_name or 'LLM'} at {self.endpoint} ...", flush=True)
        ok, msg = check_provider_connection(self.provider_name, self.endpoint, self.api_key, self.model_id)
        if ok:
            print(f"[PING] {msg}", flush=True)
        else:
            print(f"[ERROR] {msg}", flush=True)
            sys.exit(1)

    def process(self) -> None:
        """Run the full 3-pass pipeline: per-paper → topic synthesis → global synthesis.

        Input directory structure expected:  input_path/<topic_name>/<paper.pdf>
        Output written to:                   output_path/<model>/...
        """
        # 0. Connection check
        self.ping()

        # 1. Setup paths
        print(f"\n{'='*60}", flush=True)
        print(f"[LLMPdfEngine] Starting", flush=True)
        print(f"  Model      : {self.model_id}", flush=True)
        print(f"  Input dir  : {self.input_path}", flush=True)
        print(f"  Output dir : {self.output_path}", flush=True)
        print(f"{'='*60}", flush=True)

        # Validate input directory
        if not os.path.exists(self.input_path):
            print(f"[ERROR] Input directory does not exist: {self.input_path}", flush=True)
            return
        if not os.path.isdir(self.input_path):
            print(f"[ERROR] Input path is not a directory: {self.input_path}", flush=True)
            return

        model_output_dir = os.path.join(self.output_path, self.safe_model_name)
        detailed_dir = os.path.join(model_output_dir, "detailed_topic_reviews")
        if not os.path.exists(detailed_dir): os.makedirs(detailed_dir)

        topic_summaries = []  # collect (folder_name, summary_path) for global pass

        # 2. Iterate topic folders
        subfolders = [f.path for f in os.scandir(self.input_path) if f.is_dir()]
        print(f"[INFO] Found {len(subfolders)} topic subfolder(s) in input dir", flush=True)
        if not subfolders:
            print(f"[WARN] No subfolders found. Expected: INPUT_DIR/<topic_name>/<paper.pdf>", flush=True)
            return

        for folder_path in subfolders:
            folder_name = os.path.basename(folder_path)
            pdf_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.pdf')]

            if not pdf_files:
                print(f"\n[SKIP] {folder_name} — no PDFs found", flush=True)
                continue

            # topic output folder: detailed_topic_reviews/<topic>/
            topic_dir = os.path.join(detailed_dir, folder_name)
            if not os.path.exists(topic_dir): os.makedirs(topic_dir)

            # individual paper cache lives in topic_dir/_cache/ (hidden from user)
            cache_dir = os.path.join(topic_dir, "_cache")
            if not os.path.exists(cache_dir): os.makedirs(cache_dir)

            # Count already cached
            done = [f for f in pdf_files if os.path.exists(os.path.join(cache_dir, os.path.splitext(f)[0] + ".md"))]
            todo = [f for f in pdf_files if f not in done]

            print(f"\n--- Topic: {folder_name} ---", flush=True)
            print(f"    PDFs total: {len(pdf_files)}  |  cached: {len(done)}  |  to process: {len(todo)}", flush=True)

            if not todo:
                print(f"    [SKIP] All papers cached — delete detailed_topic_reviews/{folder_name}/_cache/ to reprocess", flush=True)
            else:
                # Per-paper LLM loop
                for pdf_file in tqdm(todo, desc=f"  {folder_name}"):
                    cache_file = os.path.join(cache_dir, os.path.splitext(pdf_file)[0] + ".md")
                    text = self.extract_text(os.path.join(folder_path, pdf_file))
                    if not text:
                        print(f"\n  [WARN] Could not extract text: {pdf_file}", flush=True)
                        continue

                    truncated_text = text[:12000]
                    print(f"\n  [LLM] {pdf_file} ({len(truncated_text)} chars)", flush=True)
                    try:
                        res = self.client.chat.completions.create(
                            model=self.model_id,
                            messages=[
                                {"role": "system", "content": self.prompt_template.replace("{PDF_FILENAME}", pdf_file)},
                                {"role": "user",   "content": f"PROCESS TEXT:\n\n{truncated_text}"}
                            ],
                            temperature=0.0,
                        )
                        analysis = res.choices[0].message.content
                        if not analysis or not analysis.strip():
                            print(f"  [WARN] Empty response — finish_reason: {res.choices[0].finish_reason}", flush=True)
                            continue
                        with open(cache_file, 'w', encoding='utf-8') as f:
                            f.write(analysis)
                        print(f"  [OK] Cached ({len(analysis)} chars)", flush=True)
                    except Exception as e:
                        print(f"  [ERROR] {pdf_file}: {e}", flush=True)

            # Build MASTER_REPORT from cache (inside detailed_topic_reviews/<topic>/)
            master_path = self.build_master(topic_dir, cache_dir, folder_name, pdf_files)

            # Second LLM pass: topic summary (saved at model_output_dir root)
            summary_path = self.build_topic_summary(model_output_dir, folder_name, master_path)
            topic_summaries.append((folder_name, summary_path))

        # Third LLM pass: global synthesis across all topics (saved at model_output_dir root)
        self.build_global_summary(model_output_dir, topic_summaries)

    def build_master(self, topic_dir: str, cache_dir: str,
                     folder_name: str, pdf_files: list[str]) -> str:
        """Merge all cached per-paper analyses into MASTER_REPORT.md.

        :returns: Path to the generated MASTER_REPORT.md.
        """
        master_path = os.path.join(topic_dir, "MASTER_REPORT.md")
        sections = []
        for pdf in sorted(pdf_files):
            c_file = os.path.join(cache_dir, os.path.splitext(pdf)[0] + ".md")
            if not os.path.exists(c_file):
                continue
            with open(c_file, 'r', encoding='utf-8') as f:
                body = f.read().strip()
            header = f"### PAPER: {pdf}"
            sections.append(f"{header}\n\n{body}")

        full = (
            f"# MASTER REPORT: {folder_name}\n"
            f"Model: {self.model_id}\n"
            f"Papers: {len(sections)}\n"
        )
        full += PAPER_SEPARATOR.join([""] + sections)  # leading separator after header

        with open(master_path, 'w', encoding='utf-8') as f:
            f.write(full)
        print(f"\n  [MASTER] {len(sections)} papers → {master_path}", flush=True)
        return master_path

    def build_topic_summary(self, model_output_dir: str,
                            folder_name: str, master_path: str) -> str:
        """2nd LLM pass: cross-paper topic synthesis using TOPIC_SYNTHESIS_PROMPT.

        Reads MASTER_REPORT.md contents, sends to LLM with the hardcoded
        TOPIC_SYNTHESIS_PROMPT, writes output to <folder_name>_SUMMARY.md.

        :returns: Path to the generated summary file (may be incomplete on error).
        """
        summary_path = os.path.join(model_output_dir, f"{folder_name}_SUMMARY.md")

        with open(master_path, 'r', encoding='utf-8') as f:
            master_content = f.read()

        max_chars = 30000
        if len(master_content) > max_chars:
            master_content = master_content[:max_chars]
            print(f"  [WARN] Master truncated to {max_chars} chars for topic synthesis", flush=True)

        print(f"  [LLM] Topic synthesis: {folder_name} ...", flush=True)
        try:
            res = self.client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": TOPIC_SYNTHESIS_PROMPT},
                    {"role": "user",   "content": f"MASTER REPORT:\n\n{master_content}"}
                ],
                temperature=0.0,
            )
            summary = res.choices[0].message.content
            if not summary or not summary.strip():
                print(f"  [WARN] Empty topic summary — finish_reason: {res.choices[0].finish_reason}", flush=True)
                return summary_path
            with open(summary_path, 'w', encoding='utf-8') as f:
                f.write(f"# TOPIC SUMMARY: {folder_name}\nModel: {self.model_id}\n\n")
                f.write(summary)
            print(f"  [TOPIC SUMMARY] → {summary_path}", flush=True)
        except Exception as e:
            print(f"  [ERROR] Topic synthesis failed: {e}", flush=True)
        return summary_path

    def build_global_summary(self, model_output_dir: str,
                             topic_summaries: list[tuple[str, str]]) -> None:
        """3rd LLM pass: cross-topic global synthesis using GLOBAL_SYNTHESIS_PROMPT.

        Collects all topic summary files, merges them, sends to LLM, writes
        GLOBAL_SUMMARY.md to the model output directory.

        :param topic_summaries: List of (folder_name, summary_path) tuples.
        """
        if not topic_summaries:
            return

        global_path = os.path.join(model_output_dir, "GLOBAL_SUMMARY.md")

        # Collect all topic summaries into one input
        combined = ""
        for folder_name, summary_path in topic_summaries:
            if not os.path.exists(summary_path):
                continue
            with open(summary_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            combined += f"\n\n{'='*60}\n## TOPIC: {folder_name}\n{'='*60}\n\n{content}"

        if not combined.strip():
            print(f"\n[WARN] No topic summaries available for global synthesis", flush=True)
            return

        max_chars = 40000
        if len(combined) > max_chars:
            combined = combined[:max_chars]
            print(f"  [WARN] Combined summaries truncated to {max_chars} chars for global synthesis", flush=True)

        print(f"\n[LLM] Global synthesis across {len(topic_summaries)} topic(s) ...", flush=True)
        try:
            res = self.client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": GLOBAL_SYNTHESIS_PROMPT},
                    {"role": "user",   "content": f"TOPIC SUMMARIES:\n\n{combined}"}
                ],
                temperature=0.0,
            )
            global_summary = res.choices[0].message.content
            if not global_summary or not global_summary.strip():
                print(f"  [WARN] Empty global summary — finish_reason: {res.choices[0].finish_reason}", flush=True)
                return
            with open(global_path, 'w', encoding='utf-8') as f:
                f.write(f"# GLOBAL SUMMARY\nModel: {self.model_id}\nTopics: {len(topic_summaries)}\n\n")
                f.write(global_summary)
            print(f"  [GLOBAL SUMMARY] → {global_path}", flush=True)
        except Exception as e:
            print(f"  [ERROR] Global synthesis failed: {e}", flush=True)
