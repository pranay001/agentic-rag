# Datasheet Agentic RAG: project documentation

A local system that ingests semiconductor datasheets (PDF) into a vector database and answers engineering questions about them with an agent. Every step is traced for observability, and answer quality is measured with TruLens evaluations.

## Documents

| Doc | Notebook | What it covers |
|---|---|---|
| [01 PDF ingestion](01-pdf-ingestion.md) | `pdf_ingestion_qdrant.ipynb` | Vision parsing of datasheets, structure-aware chunking, hybrid vectors, the Qdrant schema |
| [02 Agentic RAG](02-agentic-rag.md) | `agentic_rag.ipynb` | `answer_question()`: Microsoft Agent Framework agent, tools, verifier, tracing |
| [03 TruLens evals](03-trulens-evals.md) | `trulens_evals.ipynb` | Golden set, metrics, running and reading the evaluation, dashboard |
| [04 TruLens course](04-trulens-learning.md) | `trulens_learning.ipynb` | Beginner-to-expert learning material for TruLens |

## Architecture

```
                         ┌──────────────────────────── docker compose ────────────────────────────┐
                         │  Qdrant  127.0.0.1:6333/6334          Arize Phoenix  127.0.0.1:6006/4317 │
                         └───────────▲──────────────────────────────────────▲──────────────────────┘
                                     │ vectors + payload                     │ OTLP traces
 PDF ──► pdf_ingestion_qdrant.ipynb ─┤                                       │
         (gpt-5.5 vision parse,      │                                       │
          chunk, embed, upsert)      │                                       │
                                     │                                       │
 question ──► agentic_rag.ipynb ─────┴── tools: search / table / page / image / calculate
              answer_question()          Agent (gpt-5.5) ─► Verifier ─► structured answer
                                     ▲
 golden set ──► trulens_evals.ipynb ─┘── TruLens judges (gpt-5.4-mini) ─► evals/trulens.sqlite ─► dashboard :8501
```

Shared code lives in the `datasheet_rag/` package, which all notebooks import:

| Module | Contents |
|---|---|
| `config.py` | Settings from `.env` (Qdrant URL and collection, embedding model, Phoenix endpoint and project). Switches HTTPS verification to the OS certificate store (`truststore`). |
| `clients.py` | Shared `OpenAI` and `QdrantClient` instances |
| `tracing.py` | `setup_tracing()` for Phoenix + OpenInference (OpenAI or Agent Framework mode), `check_phoenix()`, `tracer`, helpers |
| `sparse.py` | BM25 tokenizer and sparse-vector builder, shared by ingestion and search |
| `retrieval.py` | `search_datasheets`, `get_table`, `get_page`, `get_neighbor_chunks`, `list_documents`, `render_page_image` |
| `trulens_judge.py` | `ResponsesJudge`: a TruLens LLM-judge provider on the OpenAI Responses API |

Other files:

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Qdrant and Phoenix containers. Both bind to localhost only. Phoenix runs with auth disabled. |
| `pyproject.toml`, `uv.lock` | Dependencies, managed with **uv** |
| `.env` / `.env.example` | `OPENAI_API_KEY`, endpoints. `.env` is git-ignored. |
| `parsed/` | Cache of page Markdown produced by the vision parser (git-ignored) |
| `evals/` | TruLens SQLite databases and result CSVs (git-ignored) |
| `trulens_dashboard.py` | Launches the TruLens dashboard on http://localhost:8501 |

## Quick start

```powershell
# 1. Infrastructure
docker compose up -d                      # Qdrant (6333) + Phoenix (6006)

# 2. Python environment (Python 3.14)
uv sync                                   # creates .venv; select it as the notebook kernel

# 3. Secrets
copy .env.example .env                    # then set OPENAI_API_KEY

# 4. Run the notebooks in order
#    pdf_ingestion_qdrant.ipynb  ->  agentic_rag.ipynb  ->  trulens_evals.ipynb
```

| UI | URL |
|---|---|
| Qdrant dashboard | http://localhost:6333/dashboard |
| Phoenix (traces) | http://localhost:6006, projects `datasheet-rag` (ingestion) and `datasheet-agent` (agent) |
| TruLens dashboard | `uv run python trulens_dashboard.py` → http://localhost:8501 |

## Models

| Role | Model | Where it's set |
|---|---|---|
| Page parsing (vision) | `gpt-5.5`, reasoning effort low | `PARSE_MODEL` in the ingestion notebook |
| Document metadata | `gpt-5.4-mini` | `META_MODEL` in the ingestion notebook |
| Embeddings | `text-embedding-3-large` (3072-d) | `datasheet_rag/config.py` |
| Agent | `gpt-5.5`, reasoning effort medium | `AGENT_MODEL` in the agent notebook |
| Verifier | `gpt-5.5`, reasoning effort low | `VERIFIER_MODEL` in the agent notebook |
| Eval judge | `gpt-5.4-mini`, reasoning effort low | `JUDGE_MODEL` in the eval notebook |

## Environment-specific notes

- **Corporate TLS inspection.** The network re-signs HTTPS with a certificate that Python's OpenSSL rejects ("EE certificate key too weak"). `truststore.inject_into_ssl()`, called in `datasheet_rag/config.py`, verifies against the Windows certificate store instead. Any new entry point must import `datasheet_rag.config` (or call `truststore` itself) before making HTTPS calls. uv needs `UV_SYSTEM_CERTS=1` for the same reason.
- **Dependency conflict.** `trulens-providers-openai` requires `openai<2`, while Microsoft Agent Framework requires `openai` 3.x. The project therefore does not install the TruLens OpenAI provider and uses `datasheet_rag/trulens_judge.py` instead.
- **Security.** The API key in `.env` is a secret, so don't commit it. Containers bind to `127.0.0.1`: neither Qdrant nor Phoenix (auth disabled) should be exposed on the network.
