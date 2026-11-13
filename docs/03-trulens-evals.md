# 03: Performance evals with TruLens (`trulens_evals.ipynb`)

Measures how well `answer_question()` (the datasheet agent) performs on a golden set of 13 questions. It uses TruLens 2.16 LLM judges plus deterministic checks, records everything in `evals/trulens.sqlite`, and reports per metric, per category and per question, with links to the Phoenix trace of every agent run.

## 1. What is measured

| Metric | Implementation | Inputs (selectors) | Answers the question |
|---|---|---|---|
| **Correctness** | `GroundTruthAgreement(golden, judge).agreement_measure` | record input → expected answer; record output | Is the answer factually right? |
| **Groundedness** | `judge.groundedness_measure_with_cot_reasons_consider_answerability` | all retrieved contexts; output; input | Is every claim supported by what the agent read? A correct "not in the datasheet" counts as grounded. |
| **Answer relevance** | `judge.relevance_with_cot_reasons` | input, output | Does it answer the question asked? |
| **Context relevance** | `judge.context_relevance_with_cot_reasons`, averaged | input; each context (max 15) | How focused was retrieval? |
| **Page citation recall** | deterministic `page_citation_recall` | input, output | Share of expected pages cited as `[p.N]` |
| Latency, tokens, tool calls, verdict, revisions | from `AgentResult` | | Cost and behaviour |

The judge is `gpt-5.4-mini` (reasoning effort low) through `datasheet_rag/trulens_judge.py`. The judge model differs from the agent model (`gpt-5.5`), which limits self-preference bias.

**What counts as "retrieved contexts" for an agent.** The agent chooses its own retrieval, so there is no single retrieval step. Its contexts are every chunk, table and page its tools returned during the run (`AgentResult.contexts`): de-duplicated, with calculator output and image placeholders excluded. `DatasheetQAApp._retrieved_contexts()` publishes them as a TruLens RETRIEVAL span.

## 2. Golden dataset

13 questions, with expected answers and pages taken from the parsed datasheet:

| Category | # | Example | Tests |
|---|---|---|---|
| spec lookup | 4 | VFB tolerance; UVLO limits; top switch current limit; RT = VIN frequency | Guaranteed vs typical, conditions |
| variant | 1 | LTC3310S-1 output voltage | Variant awareness |
| ordering | 1 | Part marking for LTC3310HV-1#PBF | Exact identifier match |
| absolute max | 1 | VIN abs max + reflow temperature | Stress-rating caveat |
| pin + spec | 1 | PGOOD function + threshold | Combining pin text with a spec table |
| design calculation | 1 | RT for 1 MHz + minimum inductor | Multi-hop + calculator |
| graph reading | 1 | PMOS RDS(on) at 5 V | Figure description / page image |
| comparison | 1 | LTC3310S vs LTC3310 vs LTC3310S-1 | Synthesis across pages |
| not in datasheet | 2 | Unit price; a part not in the knowledge base | Honest refusal |

Edit `GOLDEN` in §2 of the notebook to add cases: `{"category", "query", "expected_response", "expected_pages"}`.

## 3. How the notebook works

| § | Step |
|---|---|
| 1 | `RUN_DEMO = False; %run -i agentic_rag.ipynb`: loads the agent, registers Phoenix tracing |
| 2 | Golden set |
| 3 | Judge provider (`ResponsesJudge`) + smoke test |
| 4 | `TruSession` on `evals/trulens.sqlite`. `DatasheetQAApp` is the instrumented app: `query()` is the recorded main method, and `RUN_LOG` collects agent statistics |
| 5 | Metric definitions |
| 6 | Runs every question for each entry of `VARIANTS` (each becomes a TruLens `app_version`) |
| 7 | Waits until all metrics are computed. Context relevance is skipped where the agent retrieved no contexts. |
| 8 | Leaderboard, per-category table, colour-coded per-question table, failure drill-down with Phoenix trace ids |
| 9 | Saves `evals/results_<timestamp>.csv` and `evals/leaderboard_<timestamp>.csv` |
| 10 | Optional TruLens dashboard |

**Comparing configurations.** Uncomment entries in `VARIANTS`, e.g. verifier off, or `AGENT_MODEL = "gpt-5.4-mini"`. The agent reads its module-level settings at call time, so no reload is needed. Each variant doubles run time and cost.

**History.** The database keeps every run, and the report filters to the current run (`RUN_STARTED`). Set `RESET_DB = True` to wipe it.

## 4. Results (latest run, `gpt-5.5` agent + verifier)

| Metric | Mean |
|---|---|
| Correctness | **0.99** |
| Groundedness | **0.90** |
| Answer relevance | 0.85 |
| Context relevance | 0.62 |
| Page citation recall | 0.96 |
| Verifier "supported" | 100 % (2 answers needed one revision) |
| Latency | 32 s / question (17–53 s) |
| Tokens | ~37k / question (6k–88k) |
| Tool calls | 5 / question (2–12) |

Per question:

| Category | Correct | Grounded | Ans. rel. | Ctx. rel. | Cite rec. | Tools | s |
|---|---|---|---|---|---|---|---|
| spec lookup (VFB) | 1.0 | 1.00 | 1.0 | 0.60 | 1.0 | 3 | 25 |
| spec lookup (UVLO) | 1.0 | 0.67 | 1.0 | 0.52 | 1.0 | 4 | 41 |
| variant | 1.0 | 1.00 | 1.0 | 0.70 | 1.0 | 6 | 39 |
| ordering | 1.0 | 1.00 | 1.0 | 0.67 | 1.0 | 3 | 28 |
| absolute max | 1.0 | 1.00 | 1.0 | 0.50 | 1.0 | 3 | 18 |
| spec lookup (current limit) | 1.0 | 1.00 | 1.0 | 0.67 | 1.0 | 3 | 24 |
| spec lookup (RT = VIN) | 1.0 | 1.00 | 1.0 | 0.81 | 1.0 | 7 | 36 |
| pin + spec | 0.9 | 1.00 | 1.0 | 0.67 | 1.0 | 6 | 29 |
| design calculation | 1.0 | 0.89 | 1.0 | 0.48 | 0.5 | 6 | 39 |
| graph reading | 1.0 | 0.67 | 1.0 | 0.62 | 1.0 | 4 | 43 |
| comparison | 1.0 | 0.67 | 1.0 | 0.71 | 1.0 | 12 | 53 |
| not in datasheet (price) | 1.0 | 0.83 | 0.0 | 0.52 | 1.0 | 6 | 29 |
| not in datasheet (other part) | 1.0 | 1.00 | 0.0 | n/a | 1.0 | 2 | 17 |

### Interpreting the weak spots

The answers are right, so these low scores point at the metrics or the setup, not the agent:
- **Answer relevance 0.0 on correct refusals.** The relevance judge scores "the datasheet doesn't say" as not answering. Correctness (1.0) and groundedness confirm the behaviour is right. Either exclude refusal categories from this metric or use a refusal-aware criteria string (see the course, §2.4).
- **Groundedness 0.67 on graph reading.** The value (≈12.5 mΩ) was read from the **page image**, which the text-only judge can't see. The figure's text description gives approximate points (12 mΩ at 5.5 V), so the judge marks the exact value partially unsupported. This is expected for vision-derived answers.
- **Groundedness 0.67 on comparison and UVLO.** These answers draw on many pages, and the judge only receives the first 15 contexts (`MAX_CONTEXTS`). The comparison run made 12 tool calls. Some supporting evidence (e.g. bypass-capacitor details on p.10 and p.12) can fall outside the cap, or the answer adds a derived statement (the UVLO falling threshold 2.1 − 0.15 = 1.95 V). Raise `MAX_CONTEXTS` to confirm.
- **Context relevance ~0.6** is normal for an agent: it deliberately pulls whole tables and pages, and only part of each is relevant. Track it as an efficiency indicator, not a quality gate.
- **Citation recall 0.5 on the design calculation.** The answer cited the inductor equation page but not the RT table page. The fix is a prompt tweak ("cite every source page used").

Run-to-run variation is real. Between two runs, groundedness moved 0.89 → 0.90, answer relevance 0.90 → 0.85, and verifier pass rate 92 % → 100 %. With 13 questions, differences under about 0.05 are noise. Use the paired comparison in `trulens_learning.ipynb` §3.5 before concluding that a change helped.

## 5. Viewing results

- **In the notebook:** §8 shows a leaderboard, a per-category table and a colour-coded per-question table. Failures below `THRESHOLD = 0.7` print the expected and actual answers plus the Phoenix trace id.
- **TruLens dashboard:** `uv run python trulens_dashboard.py` → http://localhost:8501. The Leaderboard tab compares versions. Records shows each question's spans and every metric's reasoning.
- **Phoenix:** http://localhost:6006, project `datasheet-agent`. Search by the `phoenix_trace_id` column to replay exactly what the agent did: searches, documents, page images, LLM steps, verifier.
- **CSV:** `evals/results_<timestamp>.csv`.

## 6. Cost and time

A full run takes about 7–15 minutes: agent ≈ 32 s/question plus judge time. Agent tokens are about 0.5M of `gpt-5.5` (mostly cached input). The judge adds about 13 × (1 correctness + groundedness per statement + 1 relevance + up to 15 context relevance) calls of `gpt-5.4-mini`.

## 7. Tracing interplay (Phoenix + TruLens)

OpenTelemetry allows only one global tracer provider. The agent notebook registers Phoenix first. When `TruSession()` then tries to set its own provider, OTel refuses ("Overriding of current TracerProvider is not allowed") and TruLens attaches its span processor to the Phoenix provider instead. Both systems receive spans:
- Phoenix shows the agent's internal trace;
- TruLens records the eval app's record and RETRIEVAL spans and runs its metrics.

The agent runs on a worker thread (`answer_question` in Jupyter), so its spans are a separate Phoenix trace, linked through `phoenix_trace_id`.

## 8. Extending

- **More questions:** aim for 30–100, stratified by category, including negative ("not in datasheet") and adversarial cases.
- **Agent process metrics:** add trace-level metrics such as `tool_selection` and `execution_efficiency` (course §3.1) by instrumenting the agent's tool calls as TruLens TOOL spans.
- **Judge calibration:** label 30 judge decisions by hand and compute κ before using a metric as a CI gate (course §4.1).
- **CI:** run the notebook headlessly (`nbclient`) and assert floors, e.g. correctness ≥ 0.9 and groundedness ≥ 0.85, as in course §4.7.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: langchain` from `trulens.providers.openai` | Don't install `trulens-providers-openai` (its newer versions need `openai<2`). Use `ResponsesJudge`. |
| Wait loop never completes | A metric has no inputs (e.g. no contexts). The loop skips context relevance for those, so check the other metrics in `get_records_and_feedback()`. |
| `Styler.background_gradient requires matplotlib` | `uv sync` (matplotlib is a dependency) |
| "Metric implementation … cannot be serialized" warning | Harmless: functions defined in a notebook can't be imported by the deferred evaluator. Metrics are computed in-process. |
| `run_dashboard(force=True)` fails | Not supported on Windows. Stop the old dashboard first. |
