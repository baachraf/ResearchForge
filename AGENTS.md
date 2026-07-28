# ResearchForge MCP — Agent Guide

> **Read this first.** This file tells an AI agent how to drive ResearchForge via
> its 55 MCP tools (`rf_*`). The README covers installation; this covers usage.

## What ResearchForge does

Searches 9 academic databases, downloads PDFs, scores relevance, synthesizes
per-paper/topic/global summaries, generates Related Work + Introduction sections,
and runs a 10-dimension IEEE pre-submission audit on your own paper. All outputs
are visible in the desktop GUI and driven identically from MCP.

## Install (quick)

Register the MCP server in your client (full details in README §MCP Integration):

```json
// opencode (~/.config/opencode/opencode.json)
"researchforge": {
  "type": "local",
  "command": ["C:/path/to/ResearchForge.exe", "--mcp"],
  "enabled": true
}
```

## First-time setup via MCP

If the user has never opened the GUI, configure everything from MCP:

```
rf_set_llm(provider="DeepSeek", endpoint="https://api.deepseek.com/v1", model="deepseek-v4-flash")
rf_set_api_key(provider="deepseek", api_key="sk-...")
rf_set_config(key="output_root", value="D:/Papers/downloads")
rf_set_config(key="summary_output_dir", value="D:/Papers/summaries")
rf_set_config(key="audit_output_dir", value="D:/Papers/audits")
rf_test_connection()
```

All settings persist to `~/.ResearchForge/settings.json` — shared with the GUI.

---

## 🚨 MANDATORY RULE: Explicit LLM Execution Mode Selection (No Fallback)

Before triggering any synthesis operation (`rf_synthesize_topic`, `rf_synthesize_global`, `rf_generate_related_work`, `rf_generate_patent_landscape`, `rf_generate_introduction`), the agent **MUST** check `rf_select_llm_mode()` or present the **3-option choice menu** to the user:

1. **Option 1: Configured Cloud Provider** (DeepSeek, OpenAI, etc.) — `rf_select_llm_mode('configured')`
2. **Option 2: Local Model** (LM Studio or Ollama at `http://localhost:11434/v1`) — `rf_select_llm_mode('local', endpoint=...)`
3. **Option 3: Active Agent LLM** (In-context completion by the calling AI agent) — `rf_select_llm_mode('agent')`

**Strict Requirements:**
- **No Silent Fallbacks:** You must never assume a default provider or silently choose one. If `rf_select_llm_mode()` returns `CHOICE_REQUIRED`, ask the user explicitly.
- **Mid-Session Switching:** Whenever the user asks to *"change model"*, *"switch LLM"*, *"re-configure"*, or *"show model menu"*, immediately display the 3-option choice menu again and execute `rf_select_llm_mode(...)`.
- **Saving Agent Artifacts:** If Option 3 is selected, save your synthesized markdown directly to disk using `rf_save_artifact(session_id=..., relative_path=..., content=...)` to maintain full GUI parity.

---

## ⚠️ THE GOLDEN RULE: pass `session_id` for GUI parity

**Every tool that writes persistent data accepts a `session_id` parameter. Always pass it.**

Without `session_id`, results are returned to you but **not persisted into the session** —
the desktop GUI's Search tab, Downloaded tab, Check Summaries tab, and query panel will show
nothing. With `session_id`, every artifact (queries, results, downloads, scores, summaries,
reports, audits) lands in the exact on-disk layout the GUI reads.

| `session_id` given | Without `session_id` |
|---|---|
| Queries appear in GUI query panel | Query panel empty |
| Downloads nest under `output_root/<session>/<query>/` | Downloads flat or orphaned |
| Summaries at `summary/<session>/<model>/` | Summaries at wrong/flat location |
| GUI Check Summaries tab populates | GUI shows nothing |
| Audit saves GUI-loadable bundle | Audit result lost on timeout |

---

## Recommended workflow (session-driven)

This is the canonical end-to-end flow. **Run calls sequentially** — the MCP server is
single-threaded; parallel calls will corrupt the JSON-RPC stream and crash it.

```
1. rf_create_session(name="My Session", research_description="...")
   → auto-generates queries via LLM
   → returns session_id (= the session name)

2. rf_search(query="...", sources=["arxiv","semantic_scholar"],
             session_id="My Session", query_name="my_query_1")
   → registers results into the session under query_name (FULL records, incl. abstracts)
   → ALSO adds the query to the session's queries array (GUI parity)
   → the RETURNED payload is compact by default (id/title/url/year/source) to stay
     under the token cap; the session still holds the full records. Score/download
     in-session (steps 3–4) — do NOT feed this compact list into rf_score_papers.

3. (optional) rf_score_session(session_id="My Session", scoring_depth=2)
   → LLM scores each paper's relevance 0–100

4. rf_download_session(session_id="My Session",
                        paper_ids=["id1","id2"])          # or omit for all
   → PDFs land in output_root/<session>/<query_name>/

5. rf_synthesize_topic(session_id="My Session",
                        topic_name="my_query_1")
   → per-paper analysis + topic synthesis
   → writes to summary/<session>/<model>/<topic>_SUMMARY.md
                  + detailed_topic_reviews/<topic>/MASTER_REPORT.md + _cache/

6. (repeat 5 for each query that has downloaded papers)

7. rf_synthesize_global(session_id="My Session")
   → cross-topic GLOBAL_SUMMARY.md

8. rf_generate_related_work(session_id="My Session")
   → RELATED_WORK.md (fills {context}/{intent}/{topic_summaries} from cache+global)

9. rf_generate_introduction(session_id="My Session")
   → INTRODUCTION.md (fills {context}/{intent}/{paper_analyses} from per-paper cache)
```

All outputs land in `summary/<session>/<model>/` — the GUI's Check Summaries tab
reads this directory.

### Audit workflow

```
1. rf_audit_paper(pdf_path="D:/my_paper.pdf", mode="section")
   → section-by-section audit (DEFAULT, works with any model)
   → will likely TIME OUT at the MCP client (see below)
   → result persists to <audit_dir>/<slug>_audit.json

2. rf_get_audit_result(pdf_path="D:/my_paper.pdf")
   → retrieves the persisted result (report + scores)
   → NO LLM call — just reads the file
   → the file is GUI-loadable (Load Audit dialog accepts it)
```

**Always use `mode="section"`** (the default). `mode="full"` sends the entire paper
in one call and Flash-class models often skip the SCORES block under huge context.
Section mode splits into focused per-section calls → scores emitted reliably.

---

## Timeout / persistence pattern

Heavy operations exceed the MCP client's request timeout (~60s). They **persist
to disk server-side** regardless — the result is NOT lost. After a timeout, wait
for the server to finish, then retrieve:

| Tool | Typical duration | Times out? | Persists to | How to retrieve |
|---|---|---|---|---|
| `rf_search` | 5–30s | rarely | session results | `rf_load_session` |
| `rf_score_session` | 1–5 min (N papers) | **YES** | session scores | `rf_load_session` |
| `rf_download_session` | 10s–2min | sometimes | session `file_exists` | `rf_refresh_session_downloads` |
| `rf_synthesize_topic` | 2–5 min (N PDFs) | **YES** | `summary/<session>/<model>/` | `rf_list_summaries(session_id)` or check disk |
| `rf_synthesize_global` | 30–60s | sometimes | `GLOBAL_SUMMARY.md` | `rf_list_summaries` |
| `rf_generate_related_work` | 10–30s | rarely | `RELATED_WORK.md` | check `model_root` in return |
| `rf_generate_introduction` | 10–30s | rarely | `INTRODUCTION.md` | check `model_root` in return |
| `rf_audit_paper` (section) | 3–6 min (N sections) | **YES** | `<audit_dir>/<slug>_audit.json` | **`rf_get_audit_result`** |
| `rf_audit_paper` (full) | 30–90s | sometimes | same | same |

**Pattern:** call the tool → get "Request timed out" → wait 2–5 min → call the
retrieve tool → get the full result.

---

## Tool reference (53 tools)

### Config (7)
| Tool | Key params | Purpose |
|---|---|---|
| `rf_get_config` | `key` | Read one setting |
| `rf_get_all_config` | — | Read all settings (keys redacted by you) |
| `rf_set_config` | `key, value` | Set one setting |
| `rf_set_api_key` | `provider, api_key` | Set DeepSeek/Gemini/Brave/S2/CORE/PubMed key |
| `rf_set_llm` | `provider?, endpoint?, model?` | Configure LLM provider |
| `rf_set_analysis_lens` | `similarity?, novelty?, methodology?, gaps?` | Filter papers for synthesis |
| `rf_set_search_mode` | `mode` | "academic" or "general" |

### LLM (3)
| Tool | Purpose |
|---|---|
| `rf_test_connection` | Verify LLM endpoint + key work |
| `rf_fetch_models` | List available models from endpoint |
| `rf_discover_endpoints` | Scan localhost for LM Studio / Ollama |

### Sessions (9)
| Tool | Key params | Purpose |
|---|---|---|
| `rf_list_sessions` | — | List all saved sessions |
| `rf_load_session` | `session_id` | Load full session dict (queries, results, scores) |
| `rf_save_session` | `session_id, data_json` | Save session manually |
| `rf_delete_session` | `session_id` | Delete a session |
| `rf_get_session_queries` | `session_id` | List queries in the session |
| `rf_add_query_to_session` | `session_id, query, name?, sources?` | Add a query |
| `rf_remove_query_from_session` | `session_id, query_index` | Remove a query |
| `rf_set_session_results` | `session_id, results_json, query_key?` | Register results into session |
| `rf_update_session` | `session_id, fields_json?, append_log?` | Update context/intent/keywords/lens/etc. |

### Search (3)
| Tool | Key params | Purpose |
|---|---|---|
| `rf_search` | `query, sources?, max_results?, session_id?, query_name?, compact?, fields?` | Search databases; `session_id`+`query_name` registers FULL records into session + adds query to GUI panel. Returned payload is compact (id/title/url/year/source) by default — pass `compact=false` for full records (e.g. to feed `rf_score_papers`), or `fields=[…]` to pick columns |
| `rf_lookup_by_title` | `titles, session_id?` | Find papers by exact/fuzzy title; `session_id` persists found as results + not-found as queries |
| `rf_filter_papers` | `papers, title_filter?, score_threshold?` | Filter a result list |

### Download (7)
| Tool | Key params | Purpose |
|---|---|---|
| `rf_download_session` | `session_id, paper_ids?` | Download session's papers into `<session>/<query>/` (GUI layout) |
| `rf_refresh_session_downloads` | `session_id` | Re-sync `file_exists`/`file_path` from disk |
| `rf_download_paper` | `paper, output_dir?` | Download one paper (raw, no session) |
| `rf_download_papers` | `papers, output_dir?` | Download multiple (raw) |
| `rf_is_downloaded` | `paper_id, output_dir?` | Check if already downloaded |
| `rf_list_downloads` | `output_dir?, session_id?` | List PDFs; `session_id` scopes to one session |
| `rf_list_download_tree` | `output_dir?, session_id?` | Tree of topics → papers |

### Score (2)
| Tool | Key params | Purpose |
|---|---|---|
| `rf_score_papers` | `papers_json, research_context?, scoring_depth?` | Score a raw list (1=fast, 2=compare, 3=analyze). Needs abstracts — pass full records (from `rf_search` with `compact=false`), not the compact search payload. Returns `score` **and** a one-line `reason`. |
| `rf_score_session` | `session_id, paper_ids?, scoring_depth?` | Score session's own (full) results + persist `relevance_score`/`score_reason`. Preferred over `rf_score_papers` — the session always holds full records. |

### Analyze / Synthesize / Report (10)
| Tool | Key params | Purpose |
|---|---|---|
| `rf_create_session` | `name, research_description, focus_keywords?` | Create session + auto-generate queries |
| `rf_analyze_paper` | `pdf_path, prompt_key?` | One-shot analysis of a PDF (returns dict, no session write) |
| `rf_analyze_own_paper` | `pdf_path, session_id?` | Extract claims from user's paper; `session_id` persists to `paper_data` |
| `rf_synthesize_topic` | `session_id?, topic_name?, input_dir?, output_dir?` | Per-paper → topic synthesis; `session_id` writes to GUI layout |
| `rf_synthesize_global` | `session_id?, output_dir?` | Cross-topic synthesis |
| `rf_generate_related_work` | `session_id?, output_dir?` | Related Work section (fills placeholders from cache+global) |
| `rf_generate_introduction` | `session_id?, output_dir?` | Introduction section (fills placeholders from per-paper cache) |
| `rf_generate_queries` | `research_description` | Generate search queries from a description |
| `rf_enhance_research` | `research_description` | Split into CONTRIBUTION + PROBLEM SPACE |
| `rf_run_full_pipeline` | `session_id? or input_dir?, output_dir?` | Per-paper → topic → global in one call |

### Summaries (2)
| Tool | Key params | Purpose |
|---|---|---|
| `rf_list_summaries` | `session_id?, summary_dir?` | List all generated summaries; `session_id` scopes to one session |
| `rf_get_cached_analysis` | `cache_path` | Read a cached per-paper analysis by path |

### Audit (5)
| Tool | Key params | Purpose |
|---|---|---|
| `rf_audit_paper` | `pdf_path, mode("section"\|"full")` | 10-dimension IEEE audit; persists to `<audit_dir>/<slug>_audit.json`; use `mode="section"` |
| `rf_get_audit_result` | `pdf_path` | Retrieve persisted audit (after timeout); GUI-loadable shape with scores |
| `rf_detect_sections` | `pdf_path` | Detect paper sections (fast, local, no LLM) |
| `rf_get_section_text` | `pdf_path, section_name` | Get text of one section |
| `rf_save_audit_results` | `report, pdf_path?, scores?, ...` | Save a GUI-loadable audit bundle (.json + .md) |

### Prompts (5)
| Tool | Purpose |
|---|---|
| `rf_list_prompts` | List all 15 editable prompt templates |
| `rf_get_prompt` | Read one prompt template |
| `rf_set_prompt` | Update a prompt template |
| `rf_reset_prompt` | Reset one prompt to bundled default |
| `rf_reset_all_prompts` | Reset all to defaults |

---

## GUI ↔ MCP parity

Everything is shared via `~/.ResearchForge/`:

```
~/.ResearchForge/
├── settings.json          ← shared config (API keys, model, dirs)
├── prompts/               ← shared prompt templates (15 .md files)
├── sessions/              ← shared session JSONs (queries, results, scores)
└── (output_root, summary_output_dir, audit_output_dir point to user-chosen dirs)
```

**MCP → GUI:** anything the agent creates (sessions, downloads, summaries, reports,
audits) appears in the GUI's respective tabs when the user opens the app.

**GUI → MCP:** anything the user configures or creates in the GUI is readable by
the agent via `rf_load_session`, `rf_get_config`, `rf_list_summaries`, etc.

Sessions are indistinguishable — no origin marker. A session built entirely via
MCP looks identical to one built via the GUI.

---

## Common pitfalls

1. **Parallel calls crash the server.** The FastMCP stdio server is single-threaded.
   Send one `rf_*` call at a time. Never batch in parallel.
2. **Missing `session_id` → GUI shows nothing.** Always pass it for persistent ops.
3. **Full-paper audit skips scores.** Use `mode="section"` (the default).
4. **Timeouts are normal for heavy ops.** Score/synthesize/audit take minutes.
   The result persists to disk — retrieve it, don't re-run.
5. **Semantic Scholar + PubMed return 0 sometimes.** Transient rate limits. Retry
   or use arXiv (keyless, reliable).


## Patents

Patent providers (`patentsview`, `epo_ops`, `pqai`) are ordinary `rf_search` sources —
tick them like any other. What differs is downstream.

**Patent analysis runs on metadata, not a parsed PDF.** The text used for the summary
arrives with the search hit and lives in `result["patent_meta"]` (`claims_text`,
`assignee`, `publication_number`, `priority_date`, `cpc`). A patent analyses fully without
any download.

**Patents CAN be downloaded** (added 2026-07-27). `rf_download_session` now fetches the EPO
**original document** as a PDF (via the images service — broad coverage incl. US/CN/KR/JP,
this is the "Original document" you see on Espacenet) and writes the metadata beside it in
the query folder: `<pubnum>.pdf`, `<pubnum>.json`, `<pubnum>.md`. The PDF is page images (a
reading copy) and is **never parsed for text** — no OCR; the summary still uses the
metadata. When a patent has been downloaded, `generate_patent_landscape` reads its metadata
from the on-disk `<pubnum>.json` (authoritative), falling back to the session copy
otherwise. Only EPO OPS patents get a PDF (the sole tested provider); others still get the
metadata sidecars.

**Every result now carries `doc_type`** (`"paper"` or `"patent"`). It defaults to
`"paper"`, so pre-existing sessions are unaffected.

```
1. rf_search(query="...", sources=["patentsview","epo_ops"], session_id="S", query_name="q1")
2. rf_generate_patent_landscape(session_id="S")
   → writes PATENT_LANDSCAPE.md beside RELATED_WORK.md
```

`rf_generate_patent_landscape` analyses each patent (problem / solution / what is claimed
new / assignee / relation to the research), caches each under
`<model_root>/_patent_cache/`, then synthesises the cross-patent view: grouping by
assignee, contrasting claimed scope, and mapping white space. Re-running only pays for
patents not yet analysed.

A patents-only session produces the landscape report and nothing else — that is the
supported "patents are the target" case, not an error.

**Claims text may be unavailable — which is NOT the same as "no claims."** Every patent
has public claims. The limit is EPO OPS coverage: full text is held mainly for EP/WO, so
national publications (US, CN, KR, JP) 404 on the claims endpoint and arrive as abstract
only. When that happens the per-patent analysis says
`CLAIMS TEXT NOT AVAILABLE via EPO OPS` and the landscape report excludes that patent
from scope conclusions rather than silently inflating them; the header states how many
patents this applies to. If a user asks why claims are "missing," explain it is EPO
coverage (retrievable at the national office / Espacenet), not a hidden or broken field,
and not fixable via a prompt. **Only EPO OPS is live-tested** — PatentsView and PQAI are
unverified.

**Never treat the output as legal advice.** The prompts describe claim scope and refuse
infringement, validity, and freedom-to-operate conclusions by design. If a user asks for
those, say it requires a patent attorney.
