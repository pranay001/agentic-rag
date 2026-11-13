# 04: TruLens course (`trulens_learning.ipynb`)

A self-paced, runnable course that takes an engineer from first contact with TruLens to designing, validating and operating a production evaluation program. It is self-contained: a toy datasheet RAG app and a toy tool-using agent are defined inline. No Qdrant or Phoenix is required, and a full run costs only a few cents of `gpt-5.4-mini` calls.

- **Version:** written and executed against **TruLens 2.16** in OpenTelemetry mode.
- **Database:** `evals/learning.sqlite`, reset at the start of every run.

## How to use it

1. `uv sync`, select the `.venv` kernel, make sure `OPENAI_API_KEY` is in `.env`.
2. Run top to bottom once. Every section builds on the earlier objects.
3. Then re-read section by section and change things: prompts, `top_k`, judges, thresholds.
4. Do the exercises in Part 5 on the real system (`trulens_evals.ipynb`).

## Syllabus

### Part 0: Mental model
How an evaluation flows: app → `@instrument` spans → records → metrics (provider + selectors) → EVAL spans → leaderboard, dashboard and comparisons. Includes a glossary of the core classes: `TruSession`, `TruApp`, `instrument`, `LLMProvider`, `Metric` (alias `Feedback`), `Selector`, `app_version`.

### Part 1: Beginner
| § | Topic | Key APIs |
|---|---|---|
| 1.1 | Session and storage (SQLite, Postgres, Snowflake) | `TruSession`, `DefaultDBConnector`, `reset_database` |
| 1.2 | Instrumenting an app | `@instrument(span_type=RETRIEVAL / GENERATION / RECORD_ROOT, attributes=…)` |
| 1.3 | Recording | `TruApp(...)`, `with tru_app as recording`, `force_flush`, `get_records_and_feedback` |
| 1.4 | Providers (judges): official packages, custom providers, `DummyProvider` | `LLMProvider`, `ResponsesJudge` |
| 1.5 | The RAG triad | `relevance`, `context_relevance`, `groundedness_measure_with_cot_reasons`, `.on_input_output()`, `Selector.select_context(collect_list)`, `.aggregate()` |
| 1.6 | Reading results and judge explanations | `get_leaderboard`, `retrieve_feedback_results`, `get_events` (EVAL_ROOT spans) |
| 1.7 | Dashboard | `run_dashboard` (Leaderboard / Records / Compare / Trends / Prompts) |

### Part 2: Intermediate
| § | Topic | Key APIs |
|---|---|---|
| 2.1 | Instrumentation in depth: span types, computed attributes (lambda), auto call attributes, instrumenting code you don't own, framework recorders | `instrument`, `instrument_method`, `TruChain` / `TruLlama` / `TruGraph` |
| 2.2 | Selectors: input, output, context, span type and attribute, function name and attribute, attribute processors, trace, conversation, dataset column; `collect_list`, `ignore_none_values` | `Selector(...)` |
| 2.3 | Metric catalog (generated live from the installed version) and quality/safety metrics | `coherence`, `conciseness`, `harmfulness`, `comprehensiveness`, … |
| 2.4 | Customizing judges | `criteria`, `additional_instructions`, `examples`, `min/max_score_val`, `generate_score_and_reasons` |
| 2.5 | Deterministic metrics | Python functions, regex, `SchemaValidator` |
| 2.6 | Ground truth | `GroundTruthAgreement`, `add_ground_truth_to_dataset`, `get_ground_truth`, retrieval recall vs expected chunks |
| 2.7 | Groundedness in depth | `GroundednessConfigs`, `…_consider_answerability` |
| 2.8 | Versions and experiments | Three app versions on one suite; leaderboard comparison |

### Part 3: Advanced
| § | Topic | Key APIs |
|---|---|---|
| 3.1 | Agent evaluation on full traces | `Selector(trace_level=True)`; `plan_quality`, `plan_adherence`, `tool_selection`, `tool_calling`, `logical_consistency`, `execution_efficiency` |
| 3.2 | Multi-turn conversations | `tru_app(conversation_id=…)`, `.on_conversation()`, `coherence_across_turns`, `conversation_helpfulness` |
| 3.3 | Evaluation modes and back-testing new metrics on stored traffic | `compute_feedbacks`, `compute_feedbacks_on_events`, `wait_for_*`, evaluator control |
| 3.4 | Offline batch evaluation of logs | `BatchEvaluator`, `Selector.from_column` |
| 3.5 | Runs and statistically sound regression testing: baseline vs candidate over a stored dataset | `RunConfig`, `add_run`, `start`, `compute_metrics`, `Run.compare` / `compare_runs` (per-item deltas, 95 % CI, permutation p-value) |
| 3.6 | Online evaluation in production | `configure_online_eval(sample_rate, throttle, cost_budget)` |
| 3.7 | Guardrails | `context_filter`, `block_input`, `block_output` |
| 3.8 | Curating production failures into datasets | `curate_records_to_dataset`, `TraceDatasetMapping` |
| 3.9 | Prompt registry | `create_prompt`, `create_prompt_version`, `set_prompt_label`, `get_prompt` |
| 3.10 | OpenTelemetry interop (OTLP export, coexisting with Phoenix, raw spans) | `otel_exporter="otlp"`, `get_events` |
| 3.11 | Cost and latency | `total_cost`, `eval_cost`, latency percentiles |

### Part 4: Expert, evaluating the evaluator
| § | Topic | Key APIs |
|---|---|---|
| 4.1 | Judge vs human agreement | `GroundTruthAggregator`: Cohen's κ, Pearson, AUC, Brier |
| 4.2 | Judge consistency (repeat variance) | |
| 4.3 | Juries (ensembles, reliability statistics) | `Jury`, `Jury.repeated` |
| 4.4 | Few-shot calibration | `FewShotOptimizer`, `LabeledExample` |
| 4.5 | Bias probes: verbosity, position, self-preference, authority, drift | |
| 4.6 | Designing an eval suite: failure mode → metric matrix, dataset design, process | |
| 4.7 | CI gating: absolute floors + no significant regression | `ci_gate()` pattern |
| 4.8 | Production checklist | Storage, judge hygiene, throughput, cost, privacy, OTLP, feedback loop |

### Part 5: Exercises
Seven exercises, from writing a selector-based metric to adding agent trace metrics and judge calibration to the real datasheet-agent evaluation.

## Key lessons

1. **Instrument first, evaluate second.** Metrics can only see what spans record. Use proper span types, and put anything a metric needs into an attribute.
2. **Selectors are the contract** between your app's traces and your metrics. Learn them well.
3. **Prefer `_with_cot_reasons`.** A score you can't explain can't be debugged.
4. **Mix judges with deterministic checks.** Deterministic metrics are free and exact. Judges cover semantics.
5. **Agents need trace-level metrics.** Answer scores don't explain *why* an agent failed.
6. **Validate the judge** (κ, consistency, bias) before you gate a release on it.
7. **Compare versions statistically.** Use paired deltas, CIs and p-values, not eyeballed means on 10 questions.
8. **Close the loop.** Production failures become curated dataset rows and then CI gates.

## Notes specific to this environment

- `trulens-providers-openai` isn't installed because of the `openai` version conflict. The course uses `datasheet_rag/trulens_judge.py` (`ResponsesJudge`), which supports every built-in metric.
- The dashboard for the course database runs on a different port: set `RUN_DASHBOARD = True` in §1.7 (port 8502).
- On Windows, `run_dashboard(force=True)` isn't supported. Stop the old dashboard first.
- **Runs on a local database** need `TruApp(..., connector=session.connector)`. Without it, `add_run` fails with "Run DAO not initialized". `compute_metrics` is asynchronous, so poll `run.get_records()` until the metric columns are filled.
- **Keep a reference to each `TruApp`** while its metrics are pending. A `TruApp` created inline in a `with` statement can be garbage-collected before its background evaluator runs, which shows up as `'NoneType' object has no attribute 'compute_feedbacks'` in the log.
- Passing `main_method=` already makes that method the record root, so TruLens logs "Cannot explicitly set 'record_root' span type" if you also decorate it with `RECORD_ROOT`. It is harmless.
- "Metric implementation … cannot be serialized: Module `__main__` is not importable" is harmless in notebooks. It only matters for the deferred evaluator mode, which re-imports metric code in another process.
