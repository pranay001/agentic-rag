# 02: Agentic RAG (`agentic_rag.ipynb`)

`answer_question(question: str) -> AgentResult` answers one engineering question about the ingested datasheets. It is **agentic**: the model decides what to retrieve, how to widen context, when to look at the original page, and when to calculate. A separate verifier then checks the answer against the evidence before it is returned.

## 1. Agentic vs fixed RAG

| Fixed RAG | This agent |
|---|---|
| One search with the raw question, top-k, answer | Plans, issues several **targeted** searches (by content type, part number), and reformulates when hits are weak |
| Chunks are whatever came back | Widens fragments: full tables (`get_table`), pages (`get_page`), neighbouring text, the **original page image** |
| Can't compute | `calculate` tool for datasheet equations (RT, inductor sizing, …) |
| Answers from whatever it retrieved | **Verifier** checks every claim against the evidence. Failed answers get one revision round. |
| Free text | Structured `DatasheetAnswer`: conditions, evidence basis, citations, confidence, found/not-found |

## 2. Architecture

```
answer_question(question)                                  ── root span "answer_question" (Phoenix trace id returned)
│
├─ Agent  "datasheet_agent"   (Microsoft Agent Framework · OpenAIChatClient = Responses API · gpt-5.5, effort medium)
│    instructions: plan → retrieve → expand → compute → stop; datasheet-engineering answer rules
│    tools (AF @tool, typed → JSON schema):
│      list_documents · search_datasheets · get_table · get_page · get_neighbor_chunks · view_page_image · calculate
│    response_format = DatasheetAnswer (Pydantic, structured output)
│    FunctionInvocationConfiguration: max_iterations=15, max_consecutive_errors=3, detailed errors, parallel tool calls
│
├─ Evidence ledger (ContextVar): every tool result the agent saw
│
├─ Verifier "verifier" (separate Agent, no tools, gpt-5.5 effort low, response_format = Verdict)
│    input: question + draft answer JSON + evidence ledger
│    └─ verdict ≠ supported → feedback goes back into the SAME AgentSession (agent may search again) → re-verify
│       (at most MAX_REVISIONS = 1)
│
└─ AgentResult(answer, verdict, revisions, evidence, tool_calls, usage, latency_s, trace_id, draft_answer)
```

**Framework:** Microsoft Agent Framework 1.26. Only `agent-framework-core` and `agent-framework-openai` are installed, not the `agent-framework` meta-package, which pulls in about 30 cloud integrations. `OpenAIChatClient` targets the OpenAI **Responses API**.

## 3. Tools

| Tool | Wraps | When the agent should use it |
|---|---|---|
| `list_documents()` | `retrieval.list_documents` | Get `doc_id`s, check which parts are covered |
| `search_datasheets(query, content_types?, part_number?, top_k=6)` | Hybrid dense + BM25 search with RRF fusion | Focused queries in datasheet vocabulary. Target `table` (specs), `figure` (graphs/circuits), `pin`, `text` (applications info, equations). |
| `get_table(doc_id, table_id)` | Rebuilds every row group with header and note | A table hit may be missing rows or test conditions |
| `get_page(doc_id, page)` | Full parsed Markdown | Equations, footnotes, surrounding context |
| `get_neighbor_chunks(doc_id, chunk_index, window≤3)` | Reading-order neighbours | Continue cut-off text |
| `view_page_image(doc_id, page)` | Renders the PDF page (150 dpi) and returns it **as an image** to the model | Read a graph value precisely, check a schematic, resolve a garbled table |
| `calculate(expression, purpose)` | Safe AST evaluator: arithmetic + `sqrt log log10 log2 exp sin cos tan atan ceil floor abs min max round pi e`; no `eval`; exponents capped at 100 | Every arithmetic step |

Tool outputs are compact JSON: `ref` (doc_id, chunk_index, page, section, table_id/figure_title/pin_name, score) plus `text`. Each result is truncated to `TOOL_TEXT_LIMIT = 12,000` characters. `view_page_image` uses Agent Framework's `result_parser` to return `[text, image/png]` content. The model really sees the page, as the demos show.

## 4. Instructions (summary)

**Working method:**
1. **Plan:** split the question into sub-questions and identify the exact variant (LTC3310S vs LTC3310, `-1` fixed output, temperature grades). If the variants differ, answer for each.
2. **Retrieve:** focused, typed searches, in parallel where they're independent.
3. **Expand:** use `get_table`, `get_page`, neighbours or the page image.
4. **Compute:** use `calculate` with the formula, the inputs and their sources.
5. **Stop** when everything is evidenced. Otherwise say it isn't in the datasheet. No outside knowledge for specs.

**Answer rules:**
- Units and conditions on every number.
- Distinguish **guaranteed MIN/MAX**, **typical**, and **approximate graph readings**.
- Absolute maximum ratings are stress limits, not operating conditions.
- Inline `[p.N]` citations.
- Direct answer first.

The verifier checks each material claim (numbers, MIN/TYP/MAX type, conditions, variant, pin behaviour, arithmetic, not-found claims) against the evidence only. Approximate graph readings are accepted if they're consistent with the textual figure description. Style is ignored.

## 5. Output schema

```python
class DatasheetAnswer(BaseModel):
    answer: str                    # Markdown, direct answer first, inline [p.N]
    found_in_datasheet: bool
    conditions: str                # test conditions / assumptions
    basis: list[Literal["guaranteed spec", "typical spec", "graph reading", "calculation",
                        "descriptive text", "application circuit", "not found"]]
    citations: list[Citation]      # {source, page, reference}
    confidence: Literal["high", "medium", "low"]

class Verdict(BaseModel):
    verdict: Literal["supported", "partially_supported", "unsupported"]
    issues: list[str]
    feedback: str

@dataclass
class AgentResult:
    question; answer: DatasheetAnswer; verdict: Verdict | None; revisions: int
    evidence: list[dict]           # [{tool, args, content}] the ledger
    tool_calls: list[dict]; usage: dict; latency_s: float; trace_id: str
    draft_answer: DatasheetAnswer | None    # pre-revision answer, if revised
    contexts -> list[str]          # individual retrieved texts, de-duplicated (used by evals)
```

## 6. Usage

```python
result = answer_question("What is the guaranteed top switch current limit?")
show(result)                       # renders answer, conditions, basis, verdict, citations, tool path, tokens, trace id
result.answer.model_dump()         # structured answer
result.evidence                    # what the agent read
```

`answer_question` is synchronous and works in scripts and in Jupyter: inside an already-running event loop it runs the async implementation in a worker thread. `answer_question_async` is available for async callers.

From another notebook: `RUN_DEMO = False` then `%run -i agentic_rag.ipynb`. The eval notebook loads it this way.

## 7. Configuration

| Setting | Default | Notes |
|---|---|---|
| `AGENT_MODEL` / `AGENT_EFFORT` | `gpt-5.5` / `medium` | Planning and tool use |
| `VERIFIER_MODEL` / `VERIFIER_EFFORT` | `gpt-5.5` / `low` | |
| `ENABLE_VERIFIER` | True | Off: faster and cheaper, no grounding check |
| `MAX_REVISIONS` | 1 | Revision rounds after a failed verification |
| `MAX_ITERATIONS` | 15 | Model ↔ tool round trips per agent run |
| `TOOL_TEXT_LIMIT` | 12,000 chars | Per tool result |
| `PHOENIX_PROJECT` | `datasheet-agent` | |
| `RUN_DEMO` | True | The eval notebook sets it to False |

All of these are module-level globals read at call time, so evals can switch variants without reloading.

## 8. Observability (Phoenix project `datasheet-agent`)

```
answer_question                         CHAIN   question → final JSON; agent.revisions, agent.tool_calls, agent.verdict, …
├─ invoke_agent datasheet_agent         AGENT
│   ├─ chat gpt-5.5                     LLM     messages, tool calls, tokens
│   ├─ execute_tool search_datasheets   TOOL
│   │    └─ search_datasheets           RETRIEVER  query + ranked documents (content, score, metadata)
│   ├─ execute_tool view_page_image     TOOL → render_page_image TOOL
│   └─ chat gpt-5.5 …                   LLM     final structured answer
├─ invoke_agent verifier                AGENT → LLM
└─ (revision + re-verification)
```

Wiring details, in `datasheet_rag/tracing.py` → `setup_tracing(agent_framework=True)`:
- Agent Framework emits OTel GenAI spans. The OpenInference `AgentFrameworkToOpenInferenceProcessor` converts them to AGENT, LLM and TOOL kinds.
- The stock processor rewrites **every** span, which turned our RETRIEVER and TOOL spans into CHAIN. A subclass restricts it to spans whose instrumentation scope starts with `agent_framework`.
- Phoenix's `add_span_processor` replaces the default exporter by default. The converter is therefore added first, followed by a fresh `BatchSpanProcessor` exporter, so conversion happens before export.
- The OpenAI instrumentor is **not** enabled in agent mode. It would record every model call a second time and double-count tokens.

## 9. Measured behaviour

Demo questions (latest run):

| Question | Result | Tools | Time / tokens |
|---|---|---|---|
| UVLO min/max + hysteresis | 2.0 / 2.1 / 2.2 V, 150 mV typ, with conditions, verified | list → search → get_table | ~30 s / ~21k |
| 1 MHz RT + inductor for 5 V → 1.2 V | RT = 549 kΩ; L ≥ 0.304 µH (calculated from the datasheet equation), verified | list → search ×2 → calculate ×2 | ~30 s / ~27k |
| PMOS RDS(on) at 5 V vs table | ≈12.5 mΩ (graph, read from the page image) vs 16 mΩ typ at 3.3 V, verified | … → view_page_image → calculate → get_table → get_page | ~40–60 s / ~68–75k |
| Unit price | "Not in the datasheet", found_in_datasheet = false, verified | list → search ×3 → get_page → get_table | ~33 s / ~42k |

Full evaluation results are in [03-trulens-evals.md](03-trulens-evals.md).

## 10. Extending

- **New tool:** write a typed function with `@tool(description=…)` that returns a string (call `_record(...)` so the verifier and evals see it), then add it to `TOOLS`. Mention when to use it in `AGENT_INSTRUCTIONS`.
- **Multi-turn:** create the session once and reuse it across calls (`agent.create_session()`). It was left out by design, since the current scope is one question at a time.
- **Cheaper configuration:** `AGENT_MODEL = "gpt-5.4-mini"`, or `ENABLE_VERIFIER = False`. Measure the effect with the eval notebook's `VARIANTS`.

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No spans in Phoenix | Phoenix not running, or auth on. `check_phoenix()` raises a clear error. |
| Retriever spans show as CHAIN | Converter not restricted. Make sure `setup_tracing(..., agent_framework=True)` from `datasheet_rag` is used. |
| `Event loop is closed` warnings | Fixed by creating and closing a per-run `AsyncOpenAI` inside the loop. Don't share clients across `asyncio.run` calls. |
| `unknown doc_id … or no source_path` from `view_page_image` | Collection ingested before `source_path` existed. Re-ingest with `RECREATE = True`. |
| Answer `found_in_datasheet=false` but the info exists | Check the trace: which searches ran and what they returned. Usually it needs better query vocabulary in the instructions, or a chunking issue. |
