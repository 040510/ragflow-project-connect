# Client Adapters

## Select The Client

Use the requested Agent, or reliably identify the current Agent. Ask if ambiguous. Installing the Skill and registering an MCP server are separate operations. The helper requires an explicit `--client`; it does not guess from installed applications.

## Codex

Use Python 3.11+ for standard-library TOML parsing. The default is `$CODEX_HOME/config.toml`, or `~/.codex/config.toml` when unset. `--config` can select another file, but Codex must actually load that file; writing an arbitrary TOML does not register it in the current app. For an isolated CLI test set `CODEX_HOME` to a test directory.

```powershell
python scripts/self_service.py connect --client codex --project librechat --knowledge-base "selected-id"
codex mcp get ragflow-librechat --json
```

The helper backs up existing configuration and appends a marked `[mcp_servers."server-name"]` table containing `command`, `args`, `enabled = true`, `startup_timeout_sec = 30`, and `tool_timeout_sec = 180`. TOML is parsed before/after changes; unrelated settings and comments are retained. Existing names are refused. Disconnect removes only the unchanged managed block; a modified or reformatted block requires manual review. Never substitute JSON for TOML, and never overwrite the whole user config with an example.

Start a new Codex session or reload MCP using the client's supported mechanism. `codex mcp get/list` verifies configuration, not actual retrieval. Test that Codex sees `ragflow_project_retrieval` and calls it successfully with a relevant query. Report a CLI test separately from desktop UI verification.

Codex may ask for tool approval. Approve the intended read-only retrieval in the client when prompted; the Skill does not change approval policy or sandbox settings. Non-interactive runs can cancel a tool call when no reviewer is available. Do not misreport this as a gateway failure or silently disable approvals.

Validated on Windows with Python 3.13 and codex-cli 0.142.5: isolated config discovery, one actual Codex retrieval returning three evidence results, credential rotation and disconnect. The test used the existing auto-review approval mechanism for its authorized read-only call; it did not disable the sandbox. This is CLI validation, not a desktop UI or every-client compatibility claim.

## WorkBuddy And Compatible JSON

WorkBuddy uses ~/.workbuddy/mcp.json. Generated entries contain executable and file paths only:

```json
{
  "mcpServers": {
    "ragflow-librechat": {
      "command": "absolute-path-to-python",
      "args": ["absolute-path-to-stdio_adapter.py", "--binding", "absolute-path-to-binding.json"],
      "disabled": false
    }
  }
}
```

Runtime scripts and the CA are copied per connection, so moving the Skill does not break adapters. Python must stay at the configured executable path. The private binding JSON holds endpoint and token. Windows directory ACLs grant the current user access; Unix directory/file modes are 0700/0600. Do not share users' runtime directories.

The adapter reads newline-delimited MCP JSON on stdin and forwards to the stateless Streamable HTTP gateway over verified HTTPS. stdout only contains MCP. It loads the bundled CA, disables environment proxy routing for LAN requests and refuses redirects/origin changes. No pip packages or OS CA import. Long-lived subscriptions and session-based remote MCP servers are outside this adapter's scope.

For another client with the same JSON schema:

```powershell
python scripts/self_service.py connect --client generic-json --config "absolute-client-config.json" --project librechat --knowledge-base "selected-id"
```

For other schemas, inspect official product documentation first. Do not write JSON into TOML or invent supported fields. Clients must support local stdio for this automatic-CA flow. Reload only the intended Agent; distinguish CLI checks from actual UI connectivity.

Clients that support only remote HTTP, or cannot run a local adapter, are not covered by this automatic-CA setup. Explain that limitation before creating a binding. Do not claim universal Agent compatibility or switch clients without the user's request.

## Legacy HTTP Connections

project_connect.py retains the control-token workflow, producing direct HTTPS entries with headers.Authorization. These Agents still require their own CA trust: --ca-file only configures the helper.

```powershell
python scripts/project_connect.py projects --control-url "https://172.16.3.173:8892" --ca-file "path-to-ca.crt"
python scripts/project_connect.py verify --server-name ragflow-librechat --ca-file "path-to-ca.crt"
python scripts/project_connect.py disconnect --server-name ragflow-librechat --ca-file "path-to-ca.crt"
```

Administrative credentials come from RAGFLOW_CONTROL_TOKEN or hidden input. Do not prompt self-service users for them. Legacy state bindings.json is separate from self-service.json. Preserve existing entries; for deliberate migration, create and verify a separately named stdio connection before disconnecting the old one.
