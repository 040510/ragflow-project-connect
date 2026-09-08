# Client Adapters

## Default Self-Service

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

## Legacy HTTP Connections

project_connect.py retains the control-token workflow, producing direct HTTPS entries with headers.Authorization. These Agents still require their own CA trust: --ca-file only configures the helper.

```powershell
python scripts/project_connect.py projects --control-url "https://172.16.3.173:8892" --ca-file "path-to-ca.crt"
python scripts/project_connect.py verify --server-name ragflow-librechat --ca-file "path-to-ca.crt"
python scripts/project_connect.py disconnect --server-name ragflow-librechat --ca-file "path-to-ca.crt"
```

Administrative credentials come from RAGFLOW_CONTROL_TOKEN or hidden input. Do not prompt self-service users for them. Legacy state bindings.json is separate from self-service.json. Preserve existing entries; for deliberate migration, create and verify a separately named stdio connection before disconnecting the old one.
