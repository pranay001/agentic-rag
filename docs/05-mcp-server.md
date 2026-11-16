# 05: Datasheet MCP server (`datasheet_mcp/`)

A Model Context Protocol (MCP) server that gives MCP clients **read-only retrieval access** to the datasheets ingested into Qdrant. Users sign in with **Microsoft Entra ID SSO**, and access is limited to assigned members of the organization. It runs as a Docker container on this machine. The primary client is **Claude Desktop**.

```
Claude Desktop ──stdio──► mcp-remote (Node, launched by Claude Desktop)
                              │  Streamable HTTP + OAuth 2.1 (PKCE)          browser ⇄ login.microsoftonline.com (SSO)
                              ▼
            http://localhost:8000/mcp   datasheet-mcp container (FastMCP 4.2)
              ├─ OAuth proxy ⇄ Entra ID: code exchange, token validation (JWKS, tenant, audience, scope)
              ├─ per-tool check: app role Datasheet.Reader
              ├─ 8 read-only tools → datasheet_rag.retrieval (same code as the agent and evals)
              └─ traces → Phoenix (project datasheet-mcp), tagged with the signed-in user
                              │ docker network
                 qdrant:6333 (vectors)      phoenix:6006 (traces)      ./pdfs (read-only, page images)
```

## 1. Tools

All tools are annotated **read-only, non-destructive, idempotent, closed-world**, return **structured output** (JSON schema), and include `source` and `page` on every result for citation. Text fields are capped at 12,000 characters, with truncation flagged.

| Tool | Parameters | Returns | Use for |
|---|---|---|---|
| `list_datasheets` | `part_number?` | `doc_id`, source, title, manufacturer, part numbers, category, summary, page count | What's available; getting `doc_id`s |
| `search_datasheets` | `query`, `content_types?` (`text` / `table` / `figure` / `pin`), `part_number?`, `doc_id?`, `top_k` (1–12, default 6) | Ranked chunks: text, score, type, page, section, table/figure/pin refs | Main entry point. Hybrid semantic + BM25, so exact part numbers, pin names and symbols match. |
| `get_spec` | `parameter`, `part_number?`, `max_rows` (≤ 20) | Spec-table rows with the column header (MIN/TYP/MAX/UNITS…) and the table's test-conditions note | "What is the X of part Y" |
| `get_table` | `doc_id`, `table_id` (`tNNN`) | Whole table across pages: Markdown, pages, title | A search hit that is one slice of a big table |
| `get_page` | `doc_id`, `page` | Parsed Markdown of the page | Equations, footnotes, surrounding context |
| `get_page_image` | `doc_id`, `page`, `dpi` (72–150, default 120) | **PNG image** of the original page | Graphs, schematics, package drawings |
| `get_pin` | `pin_name` (case-insensitive), `part_number?` | Pin-function description(s) | "What does PGOOD do?" |
| `get_neighbor_chunks` | `doc_id`, `chunk_index`, `window` (1–3) | Chunks before/after in reading order | Continue cut-off text |

The server's `instructions` describe the intended flow (list → search → expand → image) to the client model.

**Not exposed:** ingestion, deletion, the agent (`answer_question`), or anything that writes. The client's own model does the reasoning.

## 2. Files

| Path | Contents |
|---|---|
| `datasheet_mcp/server.py` | FastMCP app, tools, output models, entry point (`python -m datasheet_mcp`) |
| `datasheet_mcp/auth.py` | Auth mode, Entra `AzureProvider` (OAuth proxy), per-tool role check, caller identity |
| `datasheet_rag/retrieval.py` | Retrieval functions (shared). Added `get_pin`, `get_chunk_payload`, PDF lookup fallback (`DATASHEET_PDF_DIRS`). |
| `Dockerfile.mcp`, `.dockerignore` | Python 3.14 slim image, minimal `mcp` dependency group, non-root user, health check |
| `docker-compose.yml` → `datasheet-mcp` | Service on `127.0.0.1:8000`; mounts `./pdfs` read-only and the `mcp_oauth` volume |
| `tests/test_mcp_tools.py`, `tests/test_mcp_auth.py` | Functional and authorization tests (`uv run pytest`) |
| `docs/06-entra-setup.md` | Entra admin guide |

## 3. Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `MCP_AUTH_MODE` | `entra` | `entra` = SSO (production). `none` = no auth, **local development only** |
| `MCP_BASE_URL` | `http://localhost:8000` | URL clients use. The OAuth callback is `MCP_BASE_URL/auth/callback` and must match the Entra redirect URI. |
| `MCP_JWT_SIGNING_KEY` | generated | Signs the short-lived tokens the proxy issues to clients, and encrypts stored client registrations. Keep it stable (changing it forces everyone to sign in again) and secret. |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | from the admin | See [06-entra-setup.md](06-entra-setup.md) |
| `ENTRA_REQUIRED_SCOPE` | `Datasheets.Read` | Delegated scope requested and validated |
| `ENTRA_REQUIRED_ROLE` | `Datasheet.Reader` | App role required on every tool |
| `ENTRA_IDENTIFIER_URI` | `api://<client-id>` | Only if the admin used a different Application ID URI |
| `MCP_TRACING` / `MCP_PHOENIX_PROJECT` | `true` / `datasheet-mcp` | Phoenix tracing (never blocks startup) |
| `MCP_ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*` | Host-header allow-list (DNS-rebinding protection) |
| `MCP_HOST`, `MCP_PORT` | container: `0.0.0.0`, `8000` | Bind address inside the container. Compose publishes it on `127.0.0.1` only. |
| `DATASHEET_PDF_DIRS` | container: `/data/pdfs` | Where `get_page_image` finds PDFs when the ingestion-time path doesn't exist |

Compose overrides `QDRANT_URL` and `PHOENIX_COLLECTOR_ENDPOINT` with container-network addresses, so `.env` keeps its `localhost` values for the notebooks.

## 4. Running

```powershell
# SSO mode (after docs/06-entra-setup.md is done and ENTRA_* are filled in .env)
docker compose up -d --build datasheet-mcp
docker logs -f datasheet-mcp              # expect: "Uvicorn running on http://0.0.0.0:8000"

# Development without SSO (localhost only)
$env:MCP_AUTH_MODE="none"; docker compose up -d datasheet-mcp; Remove-Item Env:MCP_AUTH_MODE

# Stop
docker compose stop datasheet-mcp
```

The PDFs must be in `./pdfs` for `get_page_image`. The LTC3310S PDF was copied there. Add new datasheets there too, then ingest them with the ingestion notebook.

With `MCP_AUTH_MODE=entra` but no `ENTRA_*` values, the container exits with "ENTRA_CLIENT_ID is required…" and restarts (compose `restart: unless-stopped`). Fill `.env`, or use dev mode, before starting it.

## 5. Connecting Claude Desktop

Claude Desktop's "custom connectors" (Settings → Connectors) connect **from Anthropic's cloud**, so they can't reach a server on `localhost`. Use a **local MCP server entry** in Claude Desktop's config file instead:

**File:** `%APPDATA%\Claude\claude_desktop_config.json` (Claude Desktop → Settings → Developer → Edit Config). Restart Claude Desktop after editing.

### 5.1 With SSO (target setup)

`mcp-remote` is a small Node bridge. Claude Desktop starts it over stdio. It talks Streamable HTTP to the server and runs the OAuth sign-in in your browser. Node.js 18+ is required (this machine has Node 24).

```json
{
  "mcpServers": {
    "datasheets": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "http://localhost:8000/mcp"],
      "env": { "NODE_OPTIONS": "--use-system-ca" }
    }
  }
}
```

- `NODE_OPTIONS=--use-system-ca` makes Node trust the Windows certificate store. It's needed on this corporate network, which re-signs HTTPS traffic, when `npx` downloads `mcp-remote`.
- If Claude Desktop reports that it can't start `npx`, use `"command": "cmd"` and `"args": ["/c", "npx", "-y", "mcp-remote@latest", "http://localhost:8000/mcp"]`.

**First connection:**
1. A browser tab opens on the server's consent page ("Datasheet MCP wants access"). Approve it.
2. You're sent to Microsoft sign-in. With SSO this is usually automatic, or MFA if your policies require it.
3. The browser shows success and Claude Desktop lists the 8 tools (hammer icon).
4. `mcp-remote` caches the tokens in `%USERPROFILE%\.mcp-auth`. Later starts are silent until the refresh token expires.

To sign out or switch user, delete `%USERPROFILE%\.mcp-auth` and restart Claude Desktop.

### 5.2 Without SSO (usable today, local only)

Runs the server directly as a stdio subprocess of Claude Desktop. It needs Qdrant running (`docker compose up -d qdrant`).

```json
{
  "mcpServers": {
    "datasheets-dev": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\Users\\venkatesh.chandragir\\Downloads\\te",
               "python", "-m", "datasheet_mcp", "--transport", "stdio"],
      "env": { "MCP_AUTH_MODE": "none" }
    }
  }
}
```

### 5.3 Try it

Ask Claude, for example:
- "Using the datasheets tools, what is the guaranteed top-switch current limit of the LTC3310S? Cite the page."
- "Show me the switch on-resistance graph for the LTC3310S and estimate the PMOS RDS(on) at 5 V."
- "What does the MODE/SYNC pin do?"

## 6. Security model

| Layer | Control |
|---|---|
| Network | Published on `127.0.0.1:8000` only. Qdrant and Phoenix are localhost-only too. Host-header allow-list. |
| Authentication | OAuth 2.1 authorization code + **PKCE (S256)**, via FastMCP's OAuth proxy to Entra ID. Single-tenant issuer. Entra access tokens are validated (signature via JWKS, issuer, audience `api://<client-id>`, scope `Datasheets.Read`, expiry) on **every** request. |
| Who can sign in | Entra enterprise app with **Assignment required** + the **Datasheet MCP Users** group (managed by your admin) |
| Authorization | Every tool requires app role `Datasheet.Reader` in the token's `roles` claim. Tools are hidden from callers without it (tested). |
| Client registration | Dynamic client registration is handled by the proxy. Registrations are stored encrypted (Fernet, key derived from `MCP_JWT_SIGNING_KEY`) in the `mcp_oauth` volume. A consent page is shown for each new client. |
| Token handling | Clients receive short-lived proxy tokens. The Entra token never leaves the server and is never forwarded to Qdrant or OpenAI (no token passthrough). |
| Least privilege | Tools are read-only. Inputs are validated (`doc_id` format, page and dpi ranges, `top_k` caps). Output sizes are capped. Error details are masked (`mask_error_details=True`), and tool errors are clear messages. |
| Container | Non-root user, minimal dependency set, PDFs mounted read-only |
| Secrets | `.env` (git-ignored, excluded from the image). `OPENAI_API_KEY`, `ENTRA_CLIENT_SECRET` and `MCP_JWT_SIGNING_KEY` are injected at runtime. |
| Audit | Each tool span in Phoenix carries `user.upn`, `user.oid`, `user.name`, `mcp.tool`, `mcp.arg.*`. Entra sign-in logs record each sign-in. |
| Prompt injection | The server instructions state that datasheet text is reference data, not instructions. Tools only read. |

## 7. Testing

```powershell
docker compose up -d qdrant          # tools tests use the live collection (+ OPENAI_API_KEY for query embeddings)
uv run pytest                        # 9 tests
```

| Test | Verifies |
|---|---|
| `test_tools_are_listed_and_read_only` | 8 tools, read-only annotations, output schemas |
| `test_list_datasheets_and_part_filter` | Listing and part filter |
| `test_search_exact_part_number` | Hybrid search finds `LTC3310HV-1#PBF` → Order Information, marking LHMP |
| `test_get_spec_returns_min_typ_max_with_conditions` | VFB 495 / 500 / 505 mV row with header and conditions note |
| `test_get_pin_and_table_and_page` | Pin lookup (PGOOD, p.10), full table, page Markdown |
| `test_page_image_is_png` | Image content from the original PDF |
| `test_input_validation_errors_are_clean` | Bad `doc_id`, missing page, out-of-range dpi give clean errors |
| `test_entra_metadata_and_401` | Unauthenticated → 401 + `WWW-Authenticate` resource metadata; protected-resource and authorization-server metadata; DCR endpoint; PKCE S256 |
| `test_role_is_required_on_every_tool` | Token **with** `Datasheet.Reader` can list and call tools. Token **without** it sees no tools and is denied. |

Also verified manually: the container in dev mode answering all tools over HTTP (including the page image from the mounted PDF), traces arriving in Phoenix, the container in Entra mode returning the 401 challenge, and the stdio mode.

**Not yet verified:** the live Microsoft sign-in round trip. That needs the app registration from [06-entra-setup.md](06-entra-setup.md). After it's done:
1. Fill `.env`.
2. `docker compose up -d --build datasheet-mcp`.
3. Connect Claude Desktop (§5.1).
4. Confirm a non-member gets `AADSTS50105`.

## 8. Giving teammates access (later)

Today the server listens on `localhost` only, so only you can use it. To serve teammates from this machine:
1. **HTTPS is mandatory.** Entra allows `http` redirect URIs only for `localhost`, and bearer tokens must not cross the network unencrypted. Put a TLS reverse proxy (e.g. Caddy or IIS) in front of the container with a certificate for this machine's DNS name, issued by your corporate CA so teammates' machines trust it.
2. Set `MCP_BASE_URL=https://<host-name>` and `MCP_ALLOWED_HOSTS=<host-name>:*`. Add the redirect URI `https://<host-name>/auth/callback` to the app registration (platform Web).
3. Publish the proxy port on the LAN (Windows Firewall rule restricted to the corporate network). Keep the container itself on `127.0.0.1`.
4. Teammates use the §5.1 config with `https://<host-name>/mcp`. Access is controlled by the Entra group.
5. Consider uptime: the server runs only while this computer is on. A small Azure Container App, or an internal VM, is the next step for a team service.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| Container restarts with "ENTRA_… is required" | Fill `ENTRA_*` in `.env` (see 06), or start in dev mode |
| Claude Desktop shows no tools | Check the Claude Desktop MCP log (Settings → Developer). Make sure `docker ps` shows `datasheet-mcp` healthy and `http://localhost:8000/mcp` answers 401 (SSO mode). |
| `npx` fails with certificate errors | Keep `NODE_OPTIONS=--use-system-ca` in the config (corporate TLS inspection) |
| Browser shows an `AADSTS…` error | See the table in [06-entra-setup.md §11](06-entra-setup.md#11-troubleshooting-sign-in-errors) |
| Signed in, but every tool fails with an authorization error | The user lacks the `Datasheet.Reader` role assignment, or `ENTRA_REQUIRED_ROLE` doesn't match the role value |
| Everyone must sign in again after a restart | `MCP_JWT_SIGNING_KEY` changed, or the `mcp_oauth` volume was deleted |
| `get_page_image`: "PDF … not found" | Copy the PDF into `./pdfs` (same file name as ingested) |
| Search returns nothing | Qdrant not running, or the collection isn't ingested (`pdf_ingestion_qdrant.ipynb`) |
