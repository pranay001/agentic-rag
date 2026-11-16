"""Retrieval functions over the ingested datasheet collection (used by the agent's tools and by evals).

Every function emits an OpenInference span (RETRIEVER for search, TOOL for the rest) when tracing is set up.
"""
import functools
from pathlib import Path

from qdrant_client import models

from . import config
from .clients import oai, qdrant
from .sparse import sparse_query
from .tracing import as_json, tracer

_HIT_FIELDS = ("source", "doc_id", "content_type", "section", "subsection", "page_start", "page_end",
               "chunk_index", "table_id", "table_title", "figure_title", "pin_name", "text")


def _match(key, value):
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _hit(p) -> dict:
    out = {k: p.payload.get(k) for k in _HIT_FIELDS}
    s = getattr(p, "score", None)            # scroll Records have no score
    out["score"] = round(s, 4) if s is not None else None
    return out


def _traced_tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with tracer.start_as_current_span(fn.__name__, openinference_span_kind="tool") as span:
            span.set_attribute("tool.name", fn.__name__)
            span.set_input(as_json({"args": args, "kwargs": kwargs}), mime_type="application/json")
            out = fn(*args, **kwargs)
            if not isinstance(out, (bytes, bytearray)):
                span.set_output(as_json(out), mime_type="application/json")
            return out
    return wrapper


def search_datasheets(query: str, top_k: int = 6, content_types: list[str] | None = None,
                      sources: list[str] | None = None, part_number: str | None = None) -> list[dict]:
    """Hybrid search: dense (OpenAI) + sparse (BM25) candidates fused with Reciprocal Rank Fusion."""
    with tracer.start_as_current_span("search_datasheets", openinference_span_kind="retriever") as span:
        span.set_input(query)
        span.set_attributes({"retrieval.top_k": top_k, "retrieval.mode": "hybrid: dense + bm25, RRF fusion",
                             "retrieval.filters": as_json({"content_types": content_types, "sources": sources,
                                                           "part_number": part_number})})
        must = [models.FieldCondition(key="content_type", match=models.MatchAny(any=content_types))] if content_types else []
        if sources:
            must.append(models.FieldCondition(key="source", match=models.MatchAny(any=sources)))
        if part_number:
            must.append(_match("part_numbers", part_number))
        must_not = [] if content_types and "page" in content_types else [_match("content_type", "page")]
        flt = models.Filter(must=must, must_not=must_not)

        dense = oai.embeddings.create(model=config.EMBED_MODEL, input=[query]).data[0].embedding
        res = qdrant.query_points(
            config.COLLECTION,
            prefetch=[models.Prefetch(query=dense, using="dense", limit=top_k * 5, filter=flt),
                      models.Prefetch(query=sparse_query(query), using="bm25", limit=top_k * 5, filter=flt)],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k, with_payload=True,
        )
        hits = [_hit(p) for p in res.points]
        for i, h in enumerate(hits):
            pre = f"retrieval.documents.{i}.document"
            span.set_attributes({
                f"{pre}.id": f"{h['source']}#{h['content_type']}:{h['chunk_index']}",
                f"{pre}.score": h["score"],
                f"{pre}.content": h["text"],
                f"{pre}.metadata": as_json({k: v for k, v in h.items() if k not in ("text", "score") and v is not None}),
            })
        return hits


@_traced_tool
def get_table(doc_id: str, table_id: str) -> dict:
    """Rebuild a full table (all row groups, header, note) from its chunks."""
    pts, _ = qdrant.scroll(config.COLLECTION, limit=200, with_payload=True,
                           scroll_filter=models.Filter(must=[_match("doc_id", doc_id), _match("table_id", table_id)]))
    pts.sort(key=lambda p: p.payload["chunk_index"])
    if not pts:
        return {}
    first = pts[0].payload
    rows = "\n".join(p.payload["table_rows_md"] for p in pts)
    pages = sorted({p.payload["page_start"] for p in pts})
    note = next((p.payload["content"].split("\n", 1)[0] for p in pts if p.payload["content"].startswith("Table note:")), "")
    return {"source": first["source"], "table_title": first["table_title"], "section": first["section"], "pages": pages,
            "markdown": f"{note}\n{first['table_header_md']}\n{rows}".strip()}


@_traced_tool
def get_page(doc_id: str, page: int) -> dict:
    """Full parsed Markdown of one page."""
    pts, _ = qdrant.scroll(config.COLLECTION, limit=1, with_payload=["content", "source"], scroll_filter=models.Filter(
        must=[_match("doc_id", doc_id), _match("content_type", "page"), _match("page_start", page)]))
    return {"source": pts[0].payload["source"], "page": page, "markdown": pts[0].payload["content"]} if pts else {}


@_traced_tool
def get_neighbor_chunks(doc_id: str, chunk_index: int, window: int = 1) -> list[dict]:
    """Chunks immediately before/after a hit (reading order)."""
    pts, _ = qdrant.scroll(config.COLLECTION, limit=2 * window + 1, with_payload=True, scroll_filter=models.Filter(
        must=[_match("doc_id", doc_id),
              models.FieldCondition(key="chunk_index", range=models.Range(gte=max(chunk_index - window, 0),
                                                                          lte=chunk_index + window))],
        must_not=[_match("content_type", "page")]))
    return [_hit(p) for p in sorted(pts, key=lambda p: p.payload["chunk_index"])]


@_traced_tool
def list_documents() -> list[dict]:
    """Datasheets in the collection with part numbers and summary."""
    docs, offset = {}, None
    while True:
        pts, offset = qdrant.scroll(config.COLLECTION, limit=1000, offset=offset,
                                    scroll_filter=models.Filter(must=[_match("content_type", "page")]),
                                    with_payload=["source", "doc_id", "title", "part_numbers", "device_category",
                                                  "doc_summary", "num_pages"])
        for p in pts:
            docs.setdefault(p.payload["doc_id"], p.payload)
        if offset is None:
            return list(docs.values())


@_traced_tool
def get_pin(pin_name: str, part_number: str | None = None) -> list[dict]:
    """Pin-function chunks for an exact pin name (case-insensitive), e.g. 'PGOOD', 'MODE/SYNC'."""
    must = [_match("content_type", "pin")]
    if part_number:
        must.append(_match("part_numbers", part_number))
    pts, _ = qdrant.scroll(config.COLLECTION, limit=500, with_payload=True, scroll_filter=models.Filter(must=must))
    want = pin_name.strip().casefold()
    return [_hit(p) for p in pts if str(p.payload.get("pin_name", "")).casefold() == want]


def get_chunk_payload(doc_id: str, chunk_index: int) -> dict | None:
    """Full stored payload of one non-page chunk (e.g. table_header_md / table_rows_md for spec lookups)."""
    pts, _ = qdrant.scroll(config.COLLECTION, limit=1, with_payload=True, scroll_filter=models.Filter(
        must=[_match("doc_id", doc_id), _match("chunk_index", chunk_index)],
        must_not=[_match("content_type", "page")]))
    return pts[0].payload if pts else None


def _resolve_pdf(source_path: str | None, source: str) -> Path:
    """Locate the PDF: the path stored at ingestion, else a file with the same name in DATASHEET_PDF_DIRS
    (os.pathsep-separated). The fallback matters in containers, where the host path does not exist."""
    import os

    if source_path and Path(source_path).exists():
        return Path(source_path)
    for d in filter(None, os.getenv("DATASHEET_PDF_DIRS", "").split(os.pathsep)):
        candidate = Path(d) / source
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"PDF {source!r} not found (stored path {source_path!r}; set DATASHEET_PDF_DIRS)")


@_traced_tool
def render_page_image(doc_id: str, page: int, dpi: int = 150) -> bytes:
    """Render the original PDF page to PNG (path stored at ingestion time as `source_path`)."""
    import pymupdf

    pts, _ = qdrant.scroll(config.COLLECTION, limit=1, with_payload=["source_path", "source", "num_pages"],
                           scroll_filter=models.Filter(must=[_match("doc_id", doc_id), _match("content_type", "page")]))
    if not pts:
        raise ValueError(f"unknown doc_id {doc_id!r}")
    path = _resolve_pdf(pts[0].payload.get("source_path"), pts[0].payload["source"])
    if not 1 <= page <= pts[0].payload["num_pages"]:
        raise ValueError(f"page must be 1..{pts[0].payload['num_pages']}")
    with pymupdf.open(path) as doc:
        return doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")
