"""Functional tests of the MCP tools against the live Qdrant collection (needs `docker compose up -d`, the
collection ingested, and OPENAI_API_KEY for query embeddings). Runs the server in-process, no auth."""
import json
import os

import pytest

os.environ["MCP_AUTH_MODE"] = "none"
os.environ["MCP_TRACING"] = "false"

from fastmcp import Client  # noqa: E402

from datasheet_mcp.server import mcp  # noqa: E402
from datasheet_rag import retrieval  # noqa: E402

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {"list_datasheets", "search_datasheets", "get_spec", "get_table", "get_page", "get_page_image",
                  "get_pin", "get_neighbor_chunks"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def data(result):
    """Structured tool output (FastMCP wraps list outputs as {'result': [...]})."""
    sc = result.structured_content
    return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc


async def test_tools_are_listed_and_read_only():
    async with Client(mcp) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) == EXPECTED_TOOLS
    for t in tools.values():
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False, t.name
        assert t.output_schema is not None or t.name == "get_page_image", t.name


async def test_list_datasheets_and_part_filter():
    async with Client(mcp) as c:
        docs = data(await c.call_tool("list_datasheets", {}))
        none = data(await c.call_tool("list_datasheets", {"part_number": "NOPE123"}))
    assert any("LTC3310S" in d["part_numbers"] for d in docs)
    assert none == []


async def test_search_exact_part_number():
    async with Client(mcp) as c:
        hits = data(await c.call_tool("search_datasheets", {"query": "part marking LTC3310HV-1#PBF", "top_k": 3}))
    assert hits[0]["content_type"] == "table" and "ORDER INFORMATION" in (hits[0]["table_title"] or "")
    assert "LHMP" in hits[0]["text"]


async def test_get_spec_returns_min_typ_max_with_conditions():
    async with Client(mcp) as c:
        rows = data(await c.call_tool("get_spec", {"parameter": "regulated feedback voltage", "part_number": "LTC3310S"}))
    row = next(r for r in rows if "Feedback Voltage" in r["row"][0])
    assert {"495", "500", "505"} <= set(row["row"]) and row["page"] == 3
    assert "MIN" in row["header"] and row["conditions_note"]


async def test_get_pin_and_table_and_page():
    async with Client(mcp) as c:
        pins = data(await c.call_tool("get_pin", {"pin_name": "pgood"}))
        assert pins and pins[0]["pin_name"] == "PGOOD" and pins[0]["page"] == 10
        doc_id = pins[0]["doc_id"]
        hit = data(await c.call_tool("search_datasheets",
                                     {"query": "oscillator switching frequency", "content_types": ["table"], "top_k": 1}))[0]
        table = data(await c.call_tool("get_table", {"doc_id": doc_id, "table_id": hit["table_id"]}))
        page = data(await c.call_tool("get_page", {"doc_id": doc_id, "page": 3}))
    assert table["pages"] and "| PARAMETER" in table["markdown"]
    assert "ELECTRICAL CHARACTERISTICS" in page["markdown"]


async def test_page_image_is_png():
    async with Client(mcp) as c:
        doc_id = data(await c.call_tool("list_datasheets", {}))[0]["doc_id"]
        res = await c.call_tool("get_page_image", {"doc_id": doc_id, "page": 6, "dpi": 72})
    img = res.content[0]
    assert img.type == "image" and img.mime_type == "image/png" and len(img.data) > 1000


async def test_input_validation_errors_are_clean():
    async with Client(mcp) as c:
        bad = await c.call_tool("get_page", {"doc_id": "not-a-doc", "page": 1}, raise_on_error=False)
        missing = await c.call_tool("get_page", {"doc_id": "0" * 64, "page": 1}, raise_on_error=False)
        oob = await c.call_tool("get_page_image", {"doc_id": "0" * 64, "page": 1, "dpi": 9999}, raise_on_error=False)
    assert bad.is_error and "64-character" in bad.content[0].text
    assert missing.is_error and "not found" in missing.content[0].text
    assert oob.is_error                                     # dpi above the 150 cap is rejected by the schema


async def test_each_pdf_is_a_resource_named_by_file_name():
    """Typing @ in a client lists these: one resource per ingested PDF, named after the file, and nothing else."""
    async with Client(mcp) as c:
        caps = c.server_capabilities
        tools = {t.name for t in await c.list_tools()}
        resources = await c.list_resources()
        detail = json.loads((await c.read_resource(str(resources[0].uri)))[0].text)
        with pytest.raises(Exception):
            await c.read_resource("datasheets://pdfs/no-such-file.pdf")
        with pytest.raises(Exception):
            await c.read_resource("datasheets://pdfs")                              # no list resource
    docs = retrieval.list_documents()
    assert caps.resources is not None                                              # MCP resources capability
    assert tools == EXPECTED_TOOLS                                                 # not exposed as tools
    assert all(str(r.uri).startswith("datasheets://pdfs/") for r in resources)
    assert [r.name for r in resources] == sorted((d["source"] for d in docs), key=str.casefold)
    assert all(r.mime_type == "application/json" for r in resources)
    assert any("ad4695_4696%20%281%29.pdf" in str(r.uri) for r in resources)       # spaces/parens are URI-encoded
    assert detail["file_name"] == resources[0].name and len(detail["doc_id"]) == 64
    assert detail["num_pages"] > 0 and isinstance(detail["part_numbers"], list)
