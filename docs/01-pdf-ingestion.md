# 01: PDF ingestion (`pdf_ingestion_qdrant.ipynb`)

Turns datasheet PDFs into a Qdrant collection built for agentic retrieval. Each chunk is self-describing (part, section, table, page) and searchable both by meaning and by exact identifiers. The notebook also stores whole pages, so the agent can widen the context it reads.

## 1. Why a custom pipeline

Datasheets defeat generic PDF-to-text pipelines:

| Problem | Example (LTC3310S, p.3) | Consequence with plain extraction |
|---|---|---|
| Stacked rows share one visual table row | "VIN Undervoltage Lockout" and "… Hysteresis" | Values land in the wrong MIN/TYP/MAX columns |
| Cells span rows | One CONDITIONS or UNITS cell for 2 parameters | Rows lose their conditions |
| Tables continue across pages | Electrical Characteristics, p.3 → p.4 | The second half has no title or test conditions |
| Meaning lives in graphs | "Switch On-Resistance vs Input Voltage" | Invisible to text extraction |
| Dot-leader lists | Absolute Maximum Ratings | Collapse into one run-on paragraph |

The notebook compared `pypdf`, `pymupdf4llm` and vision parsing on page 3. Only vision parsing rebuilt the table correctly.

## 2. Pipeline

```
PDF ─► (1) vision parse per page ─► parsed/<pdf>__<hash>__<cfg>/page_NNN.md   (cache)
            gpt-5.5 + page image (200 dpi) + PDF text layer
     ─► (2) document metadata (gpt-5.4-mini, structured output): title, manufacturer, part_numbers, category, summary
     ─► (3) structure-aware chunking: text / table / figure / pin / page
     ─► (4) dense embeddings (text-embedding-3-large) + BM25 sparse vectors
     ─► (5) Qdrant upsert (idempotent; replaces older versions of the same file)
```

### 2.1 Vision parsing (§3 of the notebook)

Each page is rendered to PNG and sent to `gpt-5.5` (Responses API, reasoning effort `low`) **with the PDF text layer**. The image provides structure, and the text layer provides exact characters (numbers, µ, Ω, ±). The prompt (`PARSE_PROMPT`) sets a strict output contract that the chunker depends on:

- `## ` section titles exactly as printed; `### ` sub-headings; never repeat the section title as `###`.
- A `Table: <title>` line before every table, or `Table: (continued)` on continuation pages. Any conditions note goes directly above that line.
- Each parameter is its own row. Spanned cells are **repeated** so every row stands alone. Group-label rows ("Input Supply") have only the first cell filled. Rows marked with ● get `FULL_TEMP = yes`.
- Dot-leader lists become `PARAMETER | RATING` tables, with the parent name copied into child rows.
- Pin descriptions stay as one paragraph per pin: `NAME (Pin N): …`.
- Figures become `<figure>` blocks with `Title`, `Type`, and a `Description` covering axes, ranges, curves and approximate key values (graphs), or components and connections (circuits).
- Page furniture (banner, revision, URL, page number) is dropped. No HTML tags or entities.

**Concurrency and cache.** Up to `PARSE_WORKERS = 6` pages are parsed in parallel. Results are cached in `parsed/`, keyed by **file hash + parser config hash** (model, effort, DPI, prompt). Re-runs are free, and editing the prompt triggers a re-parse automatically.

**Fallback.** If the vision call fails after retries, that page is parsed with `pymupdf4llm`. The page's `method` records which parser was used. Phoenix shows failures as span events.

### 2.2 Chunking (§5)

`build_chunks()` walks every page's Markdown in reading order and carries state across page boundaries: section, sub-section, table title, table header, table note, table id.

| Chunk type | Rule | Why |
|---|---|---|
| `text` | Paragraphs packed up to `TEXT_CHUNK_TOKENS = 450`; never crosses a heading | Coherent prose units |
| `pin` | Each `NAME (Pin N): …` paragraph is its own chunk with `pin_name` | "What does PGOOD do?" retrieves exactly one chunk |
| `table` | Split at group-label rows, packed up to `TABLE_CHUNK_TOKENS = 600`; every chunk repeats the **table note** (test conditions) and the **column header** | Each slice reads as a complete table; MIN/TYP/MAX keep their meaning |
| `figure` | One chunk per `<figure>` block | Graph and circuit descriptions are searchable |
| `page` | Full page Markdown (truncated to 7,500 tokens for embedding) | Coarse context for `get_page`; excluded from normal search |

Tables that continue across pages keep **one `table_id`**, decided as follows:
- **continued:** the page has no header, the title is `(continued)`, or the title and column names match the previous table;
- **new table:** a new `##` section starts.

A paragraph directly before a table becomes its note only if it is ≤ 120 tokens. Longer prose isn't a conditions note.

Every chunk's `text` (what gets embedded and shown to the LLM) starts with a contextual header:

```
[LTC3310S/LTC3310S-1/LTC3310 · ELECTRICAL CHARACTERISTICS · Table: ELECTRICAL CHARACTERISTICS · p.3]
Table note: The ● denotes the specifications which apply over the specified operating temperature range ...
| PARAMETER | CONDITIONS | FULL_TEMP | MIN | TYP | MAX | UNITS |
...
```

### 2.3 Vectors (§6)

- **Dense:** `text-embedding-3-large` (3072 dimensions, cosine), batches of 64.
- **Sparse (BM25):** computed locally in `datasheet_rag/sparse.py`. The tokenizer keeps identifiers whole *and* indexes their parts:
  - `ltc3310hv-1#pbf` → also `ltc3310hv`, `1`, `pbf`;
  - `rds(on)`, `3.3v`.

  Terms are hashed to 32-bit ids, and values use BM25 term-frequency saturation (k1 = 1.2, b = 0.75, avgdl = 120). Qdrant applies **IDF on the server** (`Modifier.IDF`), so weights stay correct as documents are added.

Why hybrid: part numbers, pin names and symbols need exact matching, while paraphrased questions need semantic matching.

## 3. Qdrant schema

Collection `datasheets` (configurable with `QDRANT_COLLECTION`):

| Vector | Config |
|---|---|
| `dense` | size 3072, cosine |
| `bm25` | sparse, `modifier=IDF` |

Payload (per point):

| Field | Type | Notes |
|---|---|---|
| `text` | str | Contextual header + content (embedded and shown to the LLM) |
| `content` | str | Content without header (for `page`: the full page Markdown) |
| `content_type` | keyword | `text` / `table` / `figure` / `pin` / `page` |
| `source`, `source_path` | keyword / str | File name and absolute path (`render_page_image` uses the path) |
| `doc_id` | keyword | SHA-256 of the file (identity and idempotency) |
| `title`, `manufacturer`, `part_numbers`, `device_category`, `doc_summary`, `num_pages` | | Document metadata |
| `section`, `subsection` | keyword / str | Heading path |
| `page_start`, `page_end` | integer | Page span (equal for tables and figures) |
| `chunk_index` | integer | Reading order. `page` points use `-page` |
| `table_id`, `table_title`, `table_groups`, `table_header_md`, `table_rows_md` | | Tables (used by `get_table` to rebuild a full table) |
| `figure_title`, `figure_type` | str | Figures |
| `pin_name` | keyword | Pins |
| `parse_method`, `parse_model`, `embedding_model`, `ingested_at` | | Provenance |

Payload indexes: `source`, `doc_id`, `content_type`, `section`, `table_id`, `part_numbers`, `pin_name` (keyword); `page_start`, `chunk_index` (integer).

Point ids are deterministic: `uuid5(NAMESPACE, f"{doc_id}:{content_type}:{chunk_index}")`.

## 4. Idempotency

- If a point with the file's `doc_id` (content hash) exists, the file is **skipped**.
- A changed file (new hash, same name): old points for that `source` are deleted, then the new ones are written.
- `RECREATE = True` (§7) drops and rebuilds the collection. Use it after changing chunking, embedding model or payload, since unchanged files are otherwise skipped. Parsing still comes from the cache.

## 5. Configuration

| Setting | Default | Effect |
|---|---|---|
| `PDF_PATHS` | the LTC3310S PDF + `pdfs/*.pdf` | Files to ingest |
| `PARSE_MODEL` / `PARSE_EFFORT` / `PARSE_DPI` | `gpt-5.5` / `low` / 200 | Vision parser. Changing any of them invalidates the cache. |
| `PARSE_WORKERS` | 6 | Concurrent page parses |
| `META_MODEL` | `gpt-5.4-mini` | Document metadata |
| `TEXT_CHUNK_TOKENS` / `TABLE_CHUNK_TOKENS` / `PAGE_MAX_TOKENS` | 450 / 600 / 7500 | Chunk budgets |
| `TRACE_PAGE_IMAGES` | False | Store page images in Phoenix traces (~1 MB/page) |
| `RECREATE` | False | Wipe the collection first |
| `QDRANT_URL`, `QDRANT_COLLECTION`, `PHOENIX_*` | `.env` | See `datasheet_rag/config.py` |

## 6. Observability (Phoenix project `datasheet-rag`)

```
ingest_pdf                 CHAIN  doc id/title, page & point counts, status
├─ parse_pdf               CHAIN  pages per parse method
│   └─ parse_page N        CHAIN  cache hit, method, fallback reason, #tables/#figures, output Markdown
│       └─ Response        LLM    gpt-5.5 prompt/output/tokens (auto-instrumented)
├─ extract_doc_meta        CHAIN
├─ build_chunks            CHAIN  counts per type, token max/mean, chunk previews
├─ embed_chunks            CHAIN  → CreateEmbeddings (EMBEDDING)
└─ qdrant_upsert           CHAIN
```

Page-parse threads inherit the trace context (`submit_traced`), and all spans carry `metadata.source` and the `ingestion` tag. The notebook calls `check_phoenix()` first, because the OTLP exporter drops rejected batches silently (e.g. a 401 when Phoenix auth is on).

## 7. Results on the LTC3310S datasheet

32 pages → 178 points: 56 text, 19 table, 59 figure, 12 pin, 32 page.

Parsing cost was about 32 × (≈5k input + ≈2k output tokens) on `gpt-5.5`, a few minutes wall-clock with 6 workers.

Retrieval sanity checks (§10):

| Query | Top hit |
|---|---|
| Part marking for LTC3310HV-1#PBF | Order Information table |
| PMOS on-resistance at 5 V | Switch On-Resistance graph description |
| What does the PGOOD pin do | PGOOD pin chunk |
| How to choose the inductor | Inductor-selection equations, p.15 |

## 8. Extending

- **More PDFs:** drop them into `pdfs/` and re-run. Each document gets its own `doc_id`. The agent's `list_documents` and `part_number` filter scale with that.
- **Scanned PDFs:** vision parsing already handles image-only pages, because the text layer is just empty.
- **Prompt tweaks:** edit `PARSE_PROMPT`. The cache key changes, so pages are re-parsed. Set `RECREATE = True` to rebuild vectors.
- **Different embedding model:** change `EMBED_MODEL` / `EMBED_DIM` in `datasheet_rag/config.py` and recreate the collection. The query side uses the same setting.

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `CERTIFICATE_VERIFY_FAILED … key too weak` | Corporate TLS inspection. Import `datasheet_rag.config` (truststore) before any HTTPS call. |
| `Phoenix has auth enabled` | Phoenix started with auth on. Use the provided compose file (auth off, localhost-only) or set `PHOENIX_API_KEY`. |
| A file is "already ingested, skipping" after a code change | Expected (content hash). Use `RECREATE = True`. |
| A page fell back to `pymupdf4llm` | Look at the `parse_page N` span in Phoenix (`parse.fallback_reason`), then delete that page's cache files and re-run. |
| Table split into two `table_id`s across pages | The model emitted a different title or columns on the continuation page. Inspect `parsed/…/page_NNN.md`, then tighten the prompt. |
