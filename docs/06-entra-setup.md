# 06: Microsoft Entra ID setup for the Datasheet MCP server

This guide creates everything in Microsoft Entra ID (formerly Azure AD) that the Datasheet MCP server needs for **single sign-on**, restricted to **specific members of the organization**. Follow it in the Azure / Entra portal. An Azure CLI alternative is in [Appendix A](#appendix-a-azure-cli-alternative).

**Time:** about 20 minutes.

**What you will hand back:** three values (tenant ID, client ID, client secret), which go into the project's `.env`.

---

## 0. What is being created, and why

| Object | Purpose |
|---|---|
| **App registration "Datasheet MCP"** | The server's identity. It **exposes an API scope** (`Datasheets.Read`) that sign-ins request, **defines an app role** (`Datasheet.Reader`) that the server requires, and **holds a client secret**, which the server uses to complete sign-ins. |
| **Enterprise application "Datasheet MCP"** (created automatically) | Controls **who** may sign in. "Assignment required = Yes" means only assigned users or groups get a token. Everyone else is refused by Microsoft at sign-in. |
| **Security group "Datasheet MCP Users"** | The people allowed to use the server. Membership is the access list. |

How sign-in works for a user:

```
Claude Desktop ─► mcp-remote ─► Datasheet MCP server (http://localhost:8000)
                                   │ 401 + OAuth metadata
                 browser opens ◄───┘
                 login.microsoftonline.com  (SSO: usually no prompt; MFA if your policies require it)
                 ✔ user is assigned to the app?  ✔ Conditional Access satisfied?
                 └─► redirect to http://localhost:8000/auth/callback with an authorization code
Datasheet MCP server exchanges the code (client secret) → Entra access token, then validates:
  issuer = your tenant · audience = this app · scope Datasheets.Read · app role Datasheet.Reader
```

Two independent controls enforce "specific members only":
1. **Entra** won't issue a token to anyone who isn't assigned to the application.
2. **The server** rejects any token that lacks the `Datasheet.Reader` role, even if assignment were later switched off by mistake.

### Prerequisites

| Requirement | Notes |
|---|---|
| Permission to create app registrations | Role **Application Developer** or higher. Some tenants disable "Users can register applications". |
| Permission to grant admin consent and manage enterprise app assignments | **Cloud Application Administrator** or **Application Administrator** (or Global Administrator) |
| Group-based assignment | Requires **Entra ID P1 or P2**. Without it, assign individual users instead (step 7). |
| The server URL | Default `http://localhost:8000`. If you change the port or host, use the new value everywhere `localhost:8000` appears below. |

---

## 1. Register the application

1. Go to **https://entra.microsoft.com** → **Identity** → **Applications** → **App registrations** → **+ New registration**.
2. Fill in:
   - **Name:** `Datasheet MCP`
   - **Supported account types:** **Accounts in this organizational directory only (Single tenant)**
   - **Redirect URI:** platform **Web**, URI `http://localhost:8000/auth/callback`
3. Click **Register**.
4. On the **Overview** page, copy:
   - **Application (client) ID** → this is `ENTRA_CLIENT_ID`
   - **Directory (tenant) ID** → this is `ENTRA_TENANT_ID`

### 1a. Check the redirect URI (add it if missing)

The registration form doesn't always save the redirect URI, for example when the platform dropdown was left empty. Without it, sign-in fails with `AADSTS500113: No reply address is registered`. Always check it after registering:

1. In the app registration, open **Authentication**.
2. **New experience ("Authentication (Preview)")**, which has tabs *Redirect URI configuration / Supported accounts / Settings*:
   1. On the **Redirect URI configuration** tab, click **Add Redirect URI**.
   2. Choose the platform **Web**. Don't choose *Single-page application*, *Mobile and desktop applications* or a public client: the server signs in with a client secret, which needs the Web platform.
   3. Enter `http://localhost:8000/auth/callback` exactly: no trailing slash, `http`, port `8000`.
   4. Leave the implicit-grant / access-token / ID-token boxes unticked. Click **Configure** or **Save**.
   5. The list now shows the URI with type **Web**.
3. **Classic experience** (or click *"To switch to the old experience, please click here"* in the blue banner): **Add a platform** → **Web** → enter the same URI → leave the implicit-grant boxes unticked → **Configure**.

The URI must equal `MCP_BASE_URL` + `/auth/callback`. Changes can take a minute to apply.

> Entra accepts `http://` redirect URIs only for `localhost`. That fits this single-machine setup. Serving teammates from another machine needs HTTPS (see [05-mcp-server.md §8](05-mcp-server.md#8-giving-teammates-access-later)).

## 2. Expose the API scope

1. In the app → **Expose an API**.
2. Next to **Application ID URI** click **Add** and accept the default `api://<client-id>` → **Save**.
3. **+ Add a scope:**
   - **Scope name:** `Datasheets.Read`
   - **Who can consent:** **Admins only**
   - **Admin consent display name:** `Read datasheets`
   - **Admin consent description:** `Allows the app to search and read ingested datasheets on behalf of the signed-in user.`
   - **State:** Enabled
   - **Add scope**
4. Confirm the full scope reads `api://<client-id>/Datasheets.Read`.

## 3. Use v2 access tokens

The server validates tokens against the v2 endpoint's issuer, so the app must issue **v2** access tokens.

1. In the app → **Manifest**.
2. Find `"requestedAccessTokenVersion"` (inside the `"api"` object in the Microsoft Graph manifest format) and set it to **`2`**:
   ```json
   "api": {
       "requestedAccessTokenVersion": 2,
       ...
   }
   ```
3. **Save**.

## 4. Create the app role

1. In the app → **App roles** → **+ Create app role**:
   - **Display name:** `Datasheet Reader`
   - **Allowed member types:** **Users/Groups**
   - **Value:** `Datasheet.Reader` (exactly this; the server checks it)
   - **Description:** `Can use the Datasheet MCP server`
   - **Enable this app role:** checked
2. **Apply**.

## 5. Create the client secret

1. In the app → **Certificates & secrets** → **Client secrets** → **+ New client secret**.
2. **Description:** `datasheet-mcp-server`. **Expires:** 6 or 12 months, per your policy.
3. **Add**, then **immediately copy the Value** (not the Secret ID). It is shown only once → this is `ENTRA_CLIENT_SECRET`.
4. Put a reminder in your calendar to rotate it before it expires ([§10](#10-maintenance)).

## 6. API permissions and admin consent

1. In the app → **API permissions**. `Microsoft Graph → User.Read` (delegated) is present by default. Keep it.
2. **+ Add a permission** → **APIs my organization uses** (or **My APIs**) → **Datasheet MCP** → **Delegated permissions** → tick **Datasheets.Read** → **Add permissions**.
3. Click **Grant admin consent for \<your organization\>** → **Yes**. Both permissions should show a green **Granted** status.

Admin consent means users are never asked to approve the app themselves. Users can't consent anyway, because the scope is admin-only.

## 7. Restrict access to specific members

**7a. Create the group** (skip if you use an existing one):
1. **Identity** → **Groups** → **All groups** → **+ New group**.
2. **Group type:** Security. **Name:** `Datasheet MCP Users`. **Membership type:** Assigned.
3. **Members:** add the people who should have access (include yourself) → **Create**.

**7b. Require assignment:**
1. **Identity** → **Applications** → **Enterprise applications** → search **Datasheet MCP** → open it.
2. **Properties:**
   - **Enabled for users to sign-in?** Yes
   - **Assignment required?** **Yes**
   - **Visible to users?** No (it's not a portal app)
   - **Save**

**7c. Assign the group to the role:**
1. Same enterprise application → **Users and groups** → **+ Add user/group**.
2. **Users and groups:** select **Datasheet MCP Users** (or individual users if you don't have Entra ID P1/P2).
3. **Select a role:** **Datasheet Reader**.
4. **Assign**.

From now on, granting or revoking access is just adding or removing group members. Changes apply at each user's next token refresh (at most about an hour).

## 8. (Recommended) Conditional Access

If your organization uses Conditional Access, the app can be included in an existing policy, or given a new one:
- **Entra** → **Protection** → **Conditional Access** → **+ New policy**.
- **Target resources** → **Datasheet MCP**. **Users** → **Datasheet MCP Users**.
- **Grant:** require multifactor authentication and/or a compliant device.

SSO still applies. Users who already satisfied MFA in their Windows or Edge session usually see no prompt.

## 9. Hand-over checklist

Send these to the person running the server, through a secure channel (not email or chat in plain text). They go into `.env`:

| `.env` key | Value | From |
|---|---|---|
| `ENTRA_TENANT_ID` | Directory (tenant) ID | Step 1 |
| `ENTRA_CLIENT_ID` | Application (client) ID | Step 1 |
| `ENTRA_CLIENT_SECRET` | Client secret **value** | Step 5 |

Already set, no change needed unless you deviated from this guide:

| Key | Default | Must match |
|---|---|---|
| `ENTRA_REQUIRED_SCOPE` | `Datasheets.Read` | Scope name, step 2 |
| `ENTRA_REQUIRED_ROLE` | `Datasheet.Reader` | App role value, step 4 |
| `MCP_BASE_URL` | `http://localhost:8000` | Redirect URI base, step 1 |

**Verification by the admin**, before handing over:
- [ ] App registration is single tenant; redirect URI is `http://localhost:8000/auth/callback` (platform **Web**)
- [ ] Application ID URI `api://<client-id>`; scope `Datasheets.Read` enabled
- [ ] `requestedAccessTokenVersion` = 2
- [ ] App role `Datasheet.Reader` enabled, member types Users/Groups
- [ ] Admin consent granted (green ticks)
- [ ] Enterprise app: **Assignment required = Yes**; group or users assigned to role **Datasheet Reader**

## 10. Maintenance

| Task | How |
|---|---|
| Add or remove a user | Add or remove them in the **Datasheet MCP Users** group |
| Rotate the secret | Step 5 again → update `ENTRA_CLIENT_SECRET` in `.env` → `docker compose up -d datasheet-mcp` → delete the old secret |
| Emergency shut-off | Enterprise application → Properties → **Enabled for users to sign-in? = No** (blocks new sign-ins immediately). Also stop the container. |
| See who signed in | Entra → **Monitoring** → **Sign-in logs**, filter Application = Datasheet MCP. Tool-level activity per user is also in Phoenix (project `datasheet-mcp`, attributes `user.upn` / `user.oid`). |
| Change the port or host | Update the redirect URI (step 1) **and** `MCP_BASE_URL` |

## 11. Troubleshooting sign-in errors

| Error (on the Microsoft page or in logs) | Meaning → fix |
|---|---|
| `AADSTS50105` "not assigned to a role for the application" | User isn't in the group or assigned. Fix in step 7c. Working as designed for non-members. |
| `AADSTS500113` "No reply address is registered" | No redirect URI on the app at all. **Authentication** → **Add Redirect URI** (new "Preview" page) or **Add a platform** (old page) → platform **Web** → `http://localhost:8000/auth/callback` → save |
| `AADSTS50011` "redirect URI … does not match" | Redirect URI in step 1 ≠ `MCP_BASE_URL` + `/auth/callback` (check port, `http` vs `https`, trailing slash) |
| `AADSTS65001` "user or administrator has not consented" | Step 6 admin consent missing |
| `AADSTS7000215` "invalid client secret" | Wrong value (Secret **ID** copied instead of **Value**) or expired. Redo step 5. |
| `AADSTS700016` "application not found in the directory" | Wrong `ENTRA_CLIENT_ID` or `ENTRA_TENANT_ID` |
| Sign-in succeeds, then tool calls fail with "authorization" errors | Token lacks the `Datasheet.Reader` role: role not assigned (7c), or role **value** ≠ `ENTRA_REQUIRED_ROLE` |
| Token rejected: issuer or audience mismatch | `requestedAccessTokenVersion` not set to 2 (step 3) |

---

## Appendix A: Azure CLI alternative

For admins who prefer scripts. Run in PowerShell with `az login --tenant <tenant-id>` as an account with the roles listed in the prerequisites. Review each command before running it.

```powershell
$tenant = "<tenant-id>"
$name   = "Datasheet MCP"
$roleId = [guid]::NewGuid().ToString()
$scopeId = [guid]::NewGuid().ToString()

# 1. App registration (single tenant, Web redirect URI)
$app = az ad app create --display-name $name --sign-in-audience AzureADMyOrg `
        --web-redirect-uris "http://localhost:8000/auth/callback" | ConvertFrom-Json
$appId = $app.appId; $objId = $app.id

# 2-4. Identifier URI, v2 tokens, Datasheets.Read scope, Datasheet.Reader role (Microsoft Graph PATCH)
$body = @{
  identifierUris = @("api://$appId")
  api = @{
    requestedAccessTokenVersion = 2
    oauth2PermissionScopes = @(@{
      id = $scopeId; value = "Datasheets.Read"; type = "Admin"; isEnabled = $true
      adminConsentDisplayName = "Read datasheets"
      adminConsentDescription = "Allows the app to search and read ingested datasheets on behalf of the signed-in user."
    })
  }
  appRoles = @(@{
    id = $roleId; value = "Datasheet.Reader"; displayName = "Datasheet Reader"
    description = "Can use the Datasheet MCP server"; allowedMemberTypes = @("User"); isEnabled = $true
  })
} | ConvertTo-Json -Depth 6 -Compress
$body | Out-File -Encoding utf8 app-patch.json
az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$objId" `
        --headers "Content-Type=application/json" --body "@app-patch.json"

# 5. Client secret (12 months) - copy the "password" value from the output
az ad app credential reset --id $appId --display-name "datasheet-mcp-server" --years 1 --append

# Enterprise application (service principal) + assignment required
$sp = az ad sp create --id $appId | ConvertFrom-Json
az ad sp update --id $sp.id --set appRoleAssignmentRequired=true

# 6. Delegated permissions: User.Read (Graph) + own Datasheets.Read, then admin consent
az ad app permission add --id $appId --api 00000003-0000-0000-c000-000000000000 --api-permissions e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope
az ad app permission add --id $appId --api $appId --api-permissions "$scopeId=Scope"
az ad app permission admin-consent --id $appId

# 7. Assign the security group to the Datasheet.Reader role
$groupId = az ad group show --group "Datasheet MCP Users" --query id -o tsv
$assign = @{ principalId = $groupId; resourceId = $sp.id; appRoleId = $roleId } | ConvertTo-Json -Compress
$assign | Out-File -Encoding utf8 assign.json
az rest --method POST --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$($sp.id)/appRoleAssignedTo" `
        --headers "Content-Type=application/json" --body "@assign.json"

"ENTRA_TENANT_ID=$tenant"; "ENTRA_CLIENT_ID=$appId"
Remove-Item app-patch.json, assign.json
```

Then run the verification checklist in §9 in the portal.
