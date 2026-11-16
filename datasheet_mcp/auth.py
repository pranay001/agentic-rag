"""Authentication / authorization for the datasheet MCP server.

MCP_AUTH_MODE
  none  - no authentication. Only for local development; the server refuses to bind to anything but
          127.0.0.1 in this mode (stdio transport is always local).
  entra - Microsoft Entra ID single sign-on via FastMCP's AzureProvider (OAuth proxy pattern):
          * MCP clients (Claude Desktop via mcp-remote) discover the server's OAuth metadata, register
            dynamically with the proxy, and are sent to the Microsoft sign-in page (SSO, MFA/Conditional Access).
          * The proxy exchanges the code with Entra using the app registration's client secret and validates
            the Entra access token (signature via JWKS, tenant issuer, audience, required scope).
          * Every tool additionally requires the app role ENTRA_REQUIRED_ROLE in the token's `roles` claim
            (defence in depth on top of "Assignment required" in Entra).
"""
import os

from fastmcp.server.auth import AuthCheck, require_roles

REQUIRED_ROLE = os.getenv("ENTRA_REQUIRED_ROLE", "Datasheet.Reader")
REQUIRED_SCOPE = os.getenv("ENTRA_REQUIRED_SCOPE", "Datasheets.Read")


def auth_mode() -> str:
    mode = os.getenv("MCP_AUTH_MODE", "entra").strip().lower()
    if mode not in ("none", "entra"):
        raise ValueError("MCP_AUTH_MODE must be 'none' or 'entra'")
    return mode


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required when MCP_AUTH_MODE=entra (see docs/06-entra-setup.md)")
    return value


def build_auth_provider():
    """Return the FastMCP auth provider for the configured mode (None = unauthenticated)."""
    if auth_mode() == "none":
        return None

    from fastmcp.server.auth.providers.azure import AzureProvider

    client_id = _require_env("ENTRA_CLIENT_ID")
    return AzureProvider(
        client_id=client_id,
        client_secret=_require_env("ENTRA_CLIENT_SECRET"),
        tenant_id=_require_env("ENTRA_TENANT_ID"),             # single tenant only: never "common"/"organizations"
        identifier_uri=os.getenv("ENTRA_IDENTIFIER_URI") or f"api://{client_id}",
        required_scopes=[REQUIRED_SCOPE],                     # unprefixed; provider prefixes with identifier_uri
        base_url=_require_env("MCP_BASE_URL"),                 # public URL of this server, e.g. http://localhost:8000
        jwt_signing_key=_require_env("MCP_JWT_SIGNING_KEY"),   # stable secret -> tokens survive restarts
        require_authorization_consent=True,                    # proxy shows a consent page per new MCP client
    )


def tool_auth() -> list[AuthCheck] | None:
    """Per-tool authorization: the Entra app role must be present (None in dev mode)."""
    if auth_mode() == "none":
        return None
    return [require_roles(REQUIRED_ROLE, extract=lambda claims: claims.get("roles", []))]


def current_user() -> dict:
    """Identity of the caller for audit logging/tracing (empty in dev mode)."""
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        return {}
    c = token.claims or {}
    return {k: v for k, v in {"oid": c.get("oid"), "upn": c.get("preferred_username") or c.get("upn"),
                              "name": c.get("name"), "tid": c.get("tid")}.items() if v}
