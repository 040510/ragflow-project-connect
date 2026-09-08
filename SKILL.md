---
name: ragflow-project-connect
description: Create a read-only MCP connection between the user's Agent and selected RAGFlow project knowledge bases on the enterprise LAN. Discover the live catalog, select knowledge bases, install a local stdio adapter with bundled CA and automatic credentials, verify retrieval, rotate or disconnect. No manual CA installation or control token is required for self-service.
---

# RAGFlow Project Connect

Create a working MCP connection, including local runtime and Agent configuration:

`Agent -> local stdio adapter -> verified HTTPS gateway -> selected RAGFlow datasets`

The user chooses the project and knowledge bases. The gateway exposes configured projects and their available knowledge bases on the configured LAN without user-level project permission filtering. Unassigned knowledge bases are not displayed or offered for new connections. Each connection fixes its own read-only dataset selection and automatically managed credential.

## Connect

1. Identify the target Agent and its actual MCP config format. WorkBuddy defaults to `~/.workbuddy/mcp.json`; `generic-json` is only for clients with the same `mcpServers`/`command`/`args` format. Do not write JSON into a TOML client.
2. Use Python 3.10+ and the whole Skill folder, including `assets/ragflow-project-mcp-ca.crt`. Self-service needs only the Python standard library. Read [references/client-adapters.md](references/client-adapters.md) for runtime/trust details.
3. Run `scripts/self_service.py projects`. Present actual project names and IDs; ask for a selection if the request did not specify one.
4. Run `scripts/self_service.py knowledge-bases --project <id>`. Present returned knowledge bases. Use repeatable `--knowledge-base` for choices, or `--all-knowledge-bases` only when the user requested all. No silent all-dataset default.
5. Run `connect` with the selection. It creates a binding without a control token, checks MCP initialization and tool schema, copies runtime and CA into a private local directory, then backs up and updates the Agent config. Add `--probe-query` for a relevant retrieval check. Do not silently replace an existing server entry.
6. Reload the Agent's MCP configuration when supported. Report Agent, project, knowledge-base count, expiry, config path and checks actually performed. Distinguish a configured entry from verification in the Agent UI.

Commands are relative to the installed Skill folder; resolve absolute paths when executing elsewhere:

```powershell
python scripts/self_service.py projects
python scripts/self_service.py knowledge-bases --project librechat
python scripts/self_service.py connect --client workbuddy --project librechat --knowledge-base "selected-id"
```

For all CURRENT knowledge bases in a project:

```powershell
python scripts/self_service.py connect --project librechat --all-knowledge-bases
```

Default gateway: `https://172.16.3.173:8892`. The helper loads the bundled CA automatically: no OS certificate import, RAGFlow password or control-credential prompt. Other deployments can override `--control-url` and `--ca-file`. Preserve full certificate verification.

## Operate

```powershell
python scripts/self_service.py status
python scripts/self_service.py verify --server-name ragflow-librechat --probe-query "a relevant test question"
python scripts/self_service.py rotate --server-name ragflow-librechat
python scripts/self_service.py disconnect --server-name ragflow-librechat
```

Metadata: `~/.ragflow-project-connect/self-service.json`; protected runtime and credentials: its `connections/` directory. The adapter reloads credentials for each request. Rotation uses the existing unexpired credential automatically. Expired/revoked connections must be disconnected and recreated. `status` reports local metadata; `verify` checks the remote binding.

Existing HTTP/control-token connections still use `scripts/project_connect.py` and separate metadata. See [references/client-adapters.md](references/client-adapters.md). Do not silently migrate existing connections.

## Directory And Boundaries

- Project listings reload the server registry, and knowledge-base listings refresh the live RAGFlow inventory. Only datasets assigned to a configured project are displayed. Unassigned datasets and the former `ragflow-dynamic` fallback are hidden; do not offer them through a client-side fallback. Newly configured projects and dataset memberships appear on the next listing without restarting the gateway. This is not a native RAGFlow business-project entity or guaranteed visibility across all RAGFlow tenants.
- New datasets do not expand existing bindings, including those initially made with `--all-knowledge-bases`. Create another binding when the selection changes.
- Self-service only admits configured LAN CIDRs. A 403 may mean the source network is not enabled; report it without asking for control credentials. A 404 may mean self-service is disabled.
- The Agent tool is `ragflow_project_retrieval`; it cannot inject project or dataset IDs.
- Only the CA public certificate is bundled. Never distribute RAGFlow API tokens, administrator credentials, CA private keys or users' runtime binding files.
- Keep the gateway on the LAN with verified TLS. No SSH forwarding, HTTP downgrade or public MCP.

Read [references/backend-contract.md](references/backend-contract.md) when maintaining the gateway.
