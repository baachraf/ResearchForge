# ResearchForge

<p align="center">
  <img src="design/app_icone.png" alt="ResearchForge Logo" width="120"/>
</p>

Desktop application that searches academic paper databases, downloads PDFs, and uses a local/remote LLM to analyze, summarize, and synthesize research findings.

---

## Features

- **Multi-source search**: arXiv, OpenAlex, Crossref, Europe PMC, PubMed, Semantic Scholar, CORE, Brave, DuckDuckGo — nine providers, most needing no API key
- **Session creator**: AI-enhanced research description with automatic query generation and "Analyze My Paper" mode
- **Local PDF injection**: add your own PDFs or entire folders; they auto-participate in relevance scoring and download workflows
- **Relevance scoring**: LLM rates each paper 0–100 against your research context, with keyword-based fallback
- **Automated PDF download**: batch download with duplicate detection and file validation
- **Summarization pipeline**: per-paper analysis, topic synthesis, global synthesis, related work section, introduction draft
- **Audit My Paper**: 10-dimension IEEE pre-submission self-audit on any PDF or LaTeX source — score panel, questions for the author, section-by-section mode; save/load standalone audit reports, independent of any session
- **13 editable prompts**: customize every stage of the LLM pipeline
- **Analysis lenses**: filter by similarity, novelty, methodology, research gaps
- **Session management**: save, load, and resume research sessions
- **Local and remote LLMs**: LM Studio, Ollama (local) / DeepSeek, Gemini (remote API)
- **Cross-platform**: Windows (tested on Windows 11 x64), Linux, macOS

---

## Screenshots

<p align="center">
  <img src="screenshot/search.png" alt="Search & Download" width="450"/>
</p>

<p align="center"><em>Search & Download: query management, multi-source search, relevance scoring, PDF management</em></p>

---

## Model Benchmark

Four models were given the same research topic to produce an academic Introduction and Related Work section. Seven AI judges independently scored each output (0-100) across Clarity, Readiness, Relevance, and Quality of Synthesis:

<p align="center">
  <img src="reports/reportGraph.png" alt="Model Benchmark" width="500"/>
</p>

| Model | Average | Verdict |
|-------|:-------:|---------|
| **DeepSeek V4 Flash** | **91.8** | Unanimous winner, publication-ready, clean output, strongest synthesis |
| Gemini 3 Flash | 76.2 | Clean prose, good structure, incomplete constraint adherence |
| Gemini 3.1 Pro | 74.4 | Strong analytics, weakest factual completeness, reference issues |
| DeepSeek V4 Pro | 72.0 | Good depth but leaked internal reasoning into output (unpublishable) |

**Key finding:** DeepSeek V4 Flash produced the best research writing unanimously across all seven evaluators. For synthesis-heavy tasks, remote Flash-class models significantly outperform smaller local models. While the tool supports local LLMs via Ollama and LM Studio, models below ~8B parameters produce messy, incomplete, and irrelevant output. 2-3B parameter models are not viable for academic synthesis.

[Full cross-evaluator report](reports/cross_evaluator_comparison_report_final.md) with per-evaluator scorecards, dimension breakdowns, and consensus map.

---

## Quick Start

### Prerequisites

- Python 3.9+
- A local or remote LLM

### Install

```bash
pip install -r requirements.txt
python main.py
```

### LLM Setup

ResearchForge supports both local and remote LLM providers:

| Provider | Type | Requires |
|----------|------|----------|
| **LM Studio** | Local | Running server with loaded model |
| **Ollama** | Local | Running Ollama instance |
| **DeepSeek** | Remote | API key |
| **Gemini** | Remote | API key |

**Local setup** (LM Studio or Ollama):
1. Install [LM Studio](https://lmstudio.ai/) or [Ollama](https://ollama.ai/)
2. Load a model (recommended: 7B+ parameters)
3. Start the local server

> Small local models (<7B) can handle basic summarization but struggle with synthesis tasks: finding nuance, drawing cross-paper similarities, identifying gaps. For topic/global synthesis and related-work generation, remote models like DeepSeek V4 Flash or Gemini Flash produce significantly better results.

**Remote setup** (DeepSeek or Gemini):
1. Get an API key from [DeepSeek](https://platform.deepseek.com/) or [Google AI Studio](https://aistudio.google.com/)
2. Enter the key in Settings & Prompts, API Keys

Then in ResearchForge: Settings & Prompts, select your provider, click the refresh button to detect models.

---

## How It Works

### 1. Create a Session

Describe your research topic. The LLM enhances your description and can:
- **Generate Queries**: generate search queries from your description
- **Analyze My Paper**: upload your own PDF for structured claim extraction (methods, contributions, results, comparisons)

### 2. Search & Download

Run queries across multiple academic databases. You can also inject your own PDFs:
- **+ Add PDF(s)**: select one or more local PDF files; they appear in the results table and participate in relevance scoring
- **+ Add Folder**: import all PDFs from a folder, preserving structure as topics (depth control, split/flatten options)

Use the title filter, source filters, and LLM relevance scoring to narrow results.

### 3. Download

Download selected papers as PDFs. Duplicates are automatically detected and invalid files are skipped. Three download modes: Selected, by Score threshold, or All visible. Use **Delete** to remove the checked results from the list — prune a search and re-query without starting a new session (downloaded files are kept).

### 4. Summarize Pipeline

Run the LLM pipeline in stages:

| Stage | Output |
|-------|--------|
| Per-Paper Analysis | Detailed analysis of each PDF with citation key, evidence quality flags |
| Topic Synthesis | Coherent review per query topic, cross-paper tensions |
| Global Synthesis | Cross-topic research overview |
| Related Work | Publication-ready related work section (800–1200 words) |
| Introduction | Motivation-first introduction draft grounded in the synthesized evidence |

### 5. Audit My Paper

Upload your paper (PDF or `.tex`) and run a structured pre-submission self-audit before submitting to a journal.

**What is checked — 10 IEEE dimensions:**

| # | Dimension | What is evaluated |
|---|-----------|-------------------|
| 1 | Abstract | Self-contained, four subheadings, quantitative results |
| 2 | Contributions | Positive findings, falsifiable, traceable to results |
| 3 | Claims | Every claim cited or your own result, correct hedging language |
| 4 | References | Peer-reviewed venues, correct years, no duplicates |
| 5 | Transitions | Topic sentences, gap statement, forward pointers |
| 6 | Discussion | Answer-first, limitations named and scoped |
| 7 | Conclusion | Synthesizes rather than summarizes, forward-looking final sentence |
| 8 | Reproducibility | All loss coefficients, hyperparameters, derived numbers |
| 9 | Statistical Reporting | Uncertainty estimates, effect sizes, sourced population claims |
| 10 | Style | No em-dashes as separators, no internal codes, consistent notation |

**Key capabilities:**

- **Section-by-section mode** *(default)*: detects paper sections locally using font metadata and regex, audits each in a separate LLM call, synthesizes into a structured report. Works with any model size — no large context window required.
- **Single-call modes**: full paper, ~15 pages (~40k chars), or ~5 pages (~14k chars)
- **LaTeX source**: accepts `.tex` files directly — extracts abstract, bibliography, and body sections
- **Score panel**: visual `81/100` overall score + colored chip per dimension (green ≥80, amber 65–79, orange 40–64, red <40), computed from a weighted mean (Contributions ×1.5, Claims ×1.5, Reproducibility ×1.3, others ×1.0)
- **Questions for the Author**: button shows how many genuine unanswered questions the LLM found — things whose answer is absent from the paper. Click to open the list in a dialog.
- **Section inspector**: `[ inspect ]` link lets you preview the exact text of each detected section before running the audit
- **Save / Load / Unload** *(session-independent)*: **Save** writes a self-contained `.json` bundle (paper, mode, report, scores, questions) plus a readable `.md` copy to the audit folder; **Load** reopens any saved `.json` back into the tab (report, score chips, questions, paper); **Unload** clears the tab (saved files are kept). The tab isn't tied to any research session and remembers your last audit across restarts.
- **Auto-named files**: `TitleSlug_DayName_DDMonYYYY_HHMMSS`

---

## Project Files

| File / Directory | Purpose |
|------------------|---------|
| `main.py` | Entry point: launches QApplication + MainWindow |
| `llm_pdf_engine.py` | Standalone CLI engine (no GUI dependency), hardcoded fallback prompts |

### `gui/`: PySide6 UI

| File | Purpose |
|------|---------|
| `main_window.py` | QMainWindow, 5-tab layout, close-event guard, log redirector |
| `settings_tab.py` | LLM provider/model, API keys, directories, query defaults |
| `prompt_tab.py` | 11 editable system prompts in a QTabWidget |
| `search_tab.py` | Query table + results table, multi-source search, relevance scoring, downloads, local PDF injection |
| `session_creator.py` | Dialog: research description, query generation, paper analysis |
| `query_assistant.py` | Dialog: generate queries from natural-language description |
| `folder_import.py` | Dialog: import PDF folders with topic structure, live preview, depth control |
| `summarize_tab.py` | LLM pipeline: per-paper, topic, global, related work, introduction |
| `audit_tab.py` | Audit My Paper — 10-dimension self-audit, score panel, questions dialog |
| `section_detector.py` | PDF/LaTeX section splitter (font metadata → regex → LaTeX fallback) |
| `output_tab.py` | Browse cached summaries in expandable tree view |
| `logs_tab.py` | Timestamped scrollable log viewer |
| `workers.py` | QThread workers: Search, RelevanceScoring, Download, LLMProcess |
| `config_manager.py` | JSON settings at `~/.ResearchForge/settings.json`; auto-detects stale prompt overrides |
| `style.py` | Global QSS stylesheet (`refresh_qss`) |
| `theme_manager.py` | JSON-based theming (default.json, dark.json) |
| `discovery.py` | Endpoint auto-detection for LM Studio / Ollama |
| `llm_provider.py` | Provider abstraction + `ensure_llm_available()` guard |
| `app_info.py` | APP_NAME/ID/VERSION + `resource()` path resolver (frozen-safe) |
| `tour.py` | Guided tour overlay with live widget highlighting |

### `research_downloader/`: Paper backends

| Source | Key | Description |
|--------|-----|-------------|
| arXiv | — | arXiv.org API |
| OpenAlex | — | OpenAlex works API (~250M records); optional contact email for the polite pool |
| Crossref | — | Crossref DOI metadata (~150M records); optional contact email for the polite pool |
| Europe PMC | — | Europe PMC biomedical literature + open full text |
| PubMed | optional | NCBI PubMed via Entrez; optional API key + email raise the rate limit |
| Semantic Scholar | optional | S2 academic graph API; optional key raises the rate limit |
| CORE | required | CORE open-access aggregator (~290M records); needs a free API key |
| Brave | required | Brave Search API |
| DuckDuckGo | — | Web search fallback |

All API keys (and the shared contact email) are entered via **Set Keys** on the Settings tab; keyless providers work out of the box. Providers that need a key stay greyed-out until the key is set.

### `config/`: Bundled defaults

| Item | Purpose |
|------|---------|
| `settings.json` | Default settings (clean, no API keys) |
| `prompts/` | 11 default prompt templates (.md). User edits saved to `~/.ResearchForge/prompts/` |
| `sessions/` | Empty. Bundled for build, user sessions at `~/.ResearchForge/sessions/` |

### `design/`: App icons and assets

### `themes/`: JSON theme files (`default.json`, `dark.json`)

### `build_exe.bat`: Nuitka MSVC 2022 onefile compiler script

---

## Citation Format

ResearchForge enforces a consistent citation format across all outputs:

- **In-text**: `FirstAuthor et al. [X]`
- **Reference list**: All authors listed in full, consecutive [1]-[N] numbering
- **Citation keys**: Automatically extracted per paper for downstream use

---

## Analysis Lenses

Control which papers are included in the related work section:

| Lens | Default | Purpose |
|------|---------|---------|
| Similarity | On | Papers sharing methods, goals, or domain |
| Novelty | On | Highlight genuinely new contributions |
| Methodology | Off | Compare methods and datasets |
| Gaps | Off | Identify open problems our work could address |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| PySide6 | Qt GUI framework |
| openai | LLM API client |
| pypdf | PDF text extraction |
| pymupdf | Advanced PDF parsing |
| arxiv | arXiv API client |
| ddgs | DuckDuckGo search |
| requests | HTTP client |
| tqdm | Progress bars |

---

## Build (Windows standalone .exe)

If you want to modify the source code and rebuild a standalone `.exe`, use:

```
build_exe.bat
```

This runs Nuitka with `--onefile` to produce `C:\ResearchForge_build\ResearchForge.exe`. Requires Python 3.11 and Visual Studio Build Tools 2022.

## License

This project is licensed under the MIT License.
