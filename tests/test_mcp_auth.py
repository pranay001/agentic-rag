"""Authorization tests over real HTTP.

1. Entra mode wiring: the server advertises OAuth protected-resource metadata pointing at the Entra tenant, and
   rejects unauthenticated MCP requests with 401 + WWW-Authenticate (what makes clients start the SSO flow).
2. Role enforcement: with a test token verifier swapped in, a token WITH the app role can call tools and a token
   WITHOUT it is refused - the same per-tool check that runs on real Entra tokens.
No real Entra tenant is contacted.
"""
import importlib
import os

import httpx
import pytest

pytestmark = pytest.mark.anyio

ENV = {
    "MCP_AUTH_MODE": "entra", "MCP_TRACING": "false",
    "ENTRA_TENANT_ID": "11111111-2222-3333-4444-555555555555",
    "ENTRA_CLIENT_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "ENTRA_CLIENT_SECRET": "test-secret", "MCP_BASE_URL": "http://localhost:8000",
    "MCP_JWT_SIGNING_KEY": "test-signing-key-0123456789abcdef0123456789abcdef",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def entra_server(monkeypatch, tmp_path):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))
    import datasheet_mcp.auth as auth_mod
    import datasheet_mcp.server as server_mod
    importlib.reload(auth_mod)
    yield importlib.reload(server_mod)
    monkeypatch.setenv("MCP_AUTH_MODE", "none")
    importlib.reload(auth_mod); importlib.reload(server_mod)


async def test_entra_metadata_and_401(entra_server):
    app = entra_server.mcp.http_app(path="/mcp")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as c:
            r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                             headers={"Accept": "application/json, text/event-stream"})
            assert r.status_code == 401
            assert "resource_metadata" in r.headers.get("www-authenticate", "")

            prm = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()
            asm = (await c.get("/.well-known/oauth-authorization-server")).json()
    assert prm["resource"] == "http://localhost:8000/mcp"
    assert prm["scopes_supported"] == ["Datasheets.Read"]       # the proxy maps it to api://<client-id>/Datasheets.Read upstream
    assert prm["authorization_servers"] == ["http://localhost:8000/"]               # OAuth proxy in front of Entra
    assert asm["authorization_endpoint"] == "http://localhost:8000/authorize"
    assert asm["registration_endpoint"] == "http://localhost:8000/register"         # DCR for Claude Desktop/mcp-remote
    assert asm["code_challenge_methods_supported"] == ["S256"]                      # PKCE enforced


async def test_role_is_required_on_every_tool(entra_server):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    entra_server.mcp.auth = StaticTokenVerifier(tokens={
        "with-role": {"client_id": "c1", "scopes": ["Datasheets.Read"], "roles": ["Datasheet.Reader"], "sub": "alice"},
        "no-role": {"client_id": "c2", "scopes": ["Datasheets.Read"], "sub": "mallory"},
    })
    app = entra_server.mcp.http_app(path="/mcp")

    async def call(token):
        import httpx2                                       # FastMCP's client is built on httpx2

        def transport_factory(*a, **k):                     # route the client's HTTP calls into the ASGI app
            k.pop("transport", None)
            return httpx2.AsyncClient(*a, transport=httpx2.ASGITransport(app=app), **k)
        t = StreamableHttpTransport("http://localhost:8000/mcp", auth=token, httpx_client_factory=transport_factory)
        async with Client(t) as c:
            names = {x.name for x in await c.list_tools()}
            res = await c.call_tool("list_datasheets", {}, raise_on_error=False)
            return names, res

    async with app.router.lifespan_context(app):
        names_ok, ok = await call("with-role")
        names_denied, denied = await call("no-role")
    assert "list_datasheets" in names_ok and not ok.is_error
    assert "list_datasheets" not in names_denied            # tools are hidden from callers without the role
    assert denied.is_error
