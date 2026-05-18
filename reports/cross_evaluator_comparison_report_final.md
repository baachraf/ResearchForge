# Cross-Evaluator Model Comparison Report

A research topic with defined contributions and constraints was given to four AI models to synthesise an academic Introduction and Related Work section. The outputs were independently evaluated by seven AI judges (Claude Sonnet 4.6, DeepSeek V4 Flash, DeepSeek V4 Pro, Gemini 3 Flash, Gemini 3.1 Pro, GLM 5.1, and Grok) across four dimensions: Clarity, Readiness, Relevance, and Quality of Synthesis (0–100 each).

---

## 1. Per-Evaluator Scorecard

| Evaluator | DeepSeek V4 Flash | DeepSeek V4 Pro | Gemini 3 Flash | Gemini 3.1 Pro |
|---|:---:|:---:|:---:|:---:|
| Claude Sonnet 4.6 | 91.5 | 70.8 | 81.3 | 79.0 |
| DeepSeek V4 Flash | 90.5 | 70.0 | 70.8 | 55.0 |
| DeepSeek V4 Pro | 87.5 | 58.3 | 63.8 | 71.0 |
| Gemini 3 Flash | 97.3 | 70.3 | 80.0 | 91.5 |
| Gemini 3.1 Pro | 92.5 | 77.5 | 77.5 | 78.3 |
| GLM 5.1 | 91.3 | 77.0 | 83.3 | 79.0 |
| Grok | 92.3 | 80.0 | 76.8 | 67.0 |
| **Grand Average** | **🏆 91.8** | **72.0** | **76.2** | **74.4** |

---

## 2. Grand Average by Dimension

| Dimension | DeepSeek V4 Flash | DeepSeek V4 Pro | Gemini 3 Flash | Gemini 3.1 Pro |
|---|:---:|:---:|:---:|:---:|
| Clarity (Structure & Flow) | **91.0** | 78.1 | 82.9 | 80.3 |
| Readiness (No Artifacts) | **91.4** | 38.9 | 72.4 | 70.4 |
| Relevance (Topic Adherence) | **91.9** | 86.3 | 76.7 | 73.3 |
| Quality of Synthesis (Related Work) | **91.9** | 86.1 | 74.6 | 75.3 |
| **Grand Average** | **91.8** | **72.0** | **76.2** | **74.4** |

---

## 3. Evaluator Consensus Map

| Finding | Unanimous (7/7) | Majority (5–6/7) |
|---|:---:|:---:|
| DeepSeek V4 Flash is the best overall model | ✓ | — |
| DeepSeek V4 Flash is publication-ready or near publication-ready | ✓ | — |
| DeepSeek V4 Pro has a critical AI artifact leak | ✓ | — |
| DeepSeek V4 Pro is not publication-ready | ✓ | — |
| Gemini 3 Flash missing key quantitative constraints | — | ✓ |
| Gemini 3.1 Pro missing key quantitative benchmarks | — | ✓ |
| Gemini 3 Flash / Gemini 3.1 Pro have reference formatting issues | — | ✓ |

---

## 4. Model Conclusions

**DeepSeek V4 Flash — Grand Average: 91.8 🏆**
The unanimous winner across all seven evaluators. DeepSeek V4 Flash consistently produced the clearest structure, highest factual adherence to the given constraints, and the strongest thematic synthesis in the Related Work — grouping literature by ideas and critically connecting papers rather than listing them. Output was clean, professional, and free of any AI artefacts, making it the only model considered publication-ready without major revision.

**DeepSeek V4 Pro — Grand Average: 72.0**
DeepSeek V4 Pro demonstrated strong analytical depth and well-organised thematic coverage, often producing the most navigable Related Work structure among the four models. However, it committed a critical output failure by leaking its internal reasoning and self-correction notes directly into the final text, making the document unpublishable as-is. Its Readiness score collapsed as a result — averaging just 38.9 across all evaluators — despite the underlying content quality being competitive.

**Gemini 3 Flash — Grand Average: 76.2**
Gemini 3 Flash produced clean, artefact-free prose with a logical flow and good academic tone. It ranked second overall and was the most consistent of the Gemini models. Its main weakness was incomplete adherence to the specified constraints — it omitted several key quantitative details — and its synthesis style occasionally drifted toward listing references rather than critically connecting them. Reference numbering inconsistencies were also flagged by multiple evaluators.

**Gemini 3.1 Pro — Grand Average: 74.4**
Gemini 3.1 Pro showed strong analytical reasoning in places, particularly in its treatment of technical trade-offs, and its prose was concise and well-written. However, it had the weakest factual completeness of the four models, omitting multiple required benchmarks and metrics from its output. Several reference entries also contained incomplete metadata placeholders, which multiple evaluators flagged as unprofessional. Its score improved when evaluators weighted analytical quality more heavily, as seen in Gemini 3 Flash's notably higher rating for it.

---

*Evaluated models: DeepSeek V4 Flash, DeepSeek V4 Pro, Gemini 3 Flash, Gemini 3.1 Pro*
*Evaluating models: Claude Sonnet 4.6, DeepSeek V4 Flash, DeepSeek V4 Pro, Gemini 3 Flash, Gemini 3.1 Pro, GLM 5.1, Grok*

## 5. Interactive Charts

Open [benchmark_charts.html](benchmark_charts.html) in a browser to explore the benchmark results via interactive charts (Chart.js).
