# Gateway Self-Service Contract

The LAN is the admission boundary for catalog discovery and binding creation. There is no per-user project selection policy. Existing administrator interfaces retain control-token authentication.

| Method | Path | Credential |
| --- | --- | --- |
| GET | /v1/self-service/projects | None; LAN only |
| GET | /v1/self-service/projects/{id}/knowledge-bases | None; LAN only |
| POST | /v1/self-service/bindings | None; LAN only |
| GET | /v1/self-service/bindings/{id} | That connection's Bearer token |
| POST | /v1/self-service/bindings/{id}/rotate | That connection's Bearer token |
| DELETE | /v1/self-service/bindings/{id} | That connection's Bearer token |
| POST | /mcp/{id} | That connection's Bearer token |

Self-service never lists existing bindings or issues control credentials. A token for A cannot inspect, rotate or revoke B. Rotation invalidates the old token; expired/revoked tokens cannot renew themselves. Administrator credentials remain available for recovery.

## Binding

```json
{
  "name": "unique-agent-connection-name",
  "project_id": "librechat",
  "knowledge_base_ids": ["selected-dataset-id"],
  "agent": {"name": "WorkBuddy", "client_type": "workbuddy"},
  "scopes": ["knowledge:retrieve"],
  "follow_project": false,
  "expires_in_seconds": 2592000
}
```

Response contains `binding.id`, `binding.project_id`, `binding.knowledge_base_ids`, `binding.scopes` and `mcp.url`, `mcp.access_token`, `mcp.expires_at`. Tokens are delivered once and stored only in private client binding files; the server stores an HMAC digest. Responses use Cache-Control: no-store.

Validate selected IDs against one live inventory/project snapshot. Reject out-of-project IDs, unknown scope, write permission and automatic project-following. Business projects remain configured groupings. Unassigned service-visible datasets are hidden, and the former ragflow-dynamic fallback is no longer listed or available for new bindings. Direct requests for that project return 404. Reload project mappings on every listing and intersect project membership with live inventory for knowledge-base listings; newly assigned datasets become visible without a restart. Existing bindings are not revoked by this catalog change. Retrieval uses fixed selections intersected with available RAGFlow datasets.

## Network And Operations

Enable with SELF_SERVICE_ENABLED=true and explicit SELF_SERVICE_CIDRS. Current admitted LANs: 172.16.0.0/22 and 192.168.20.0/22; localhost is admitted for maintenance. Other company networks must be deliberately added. Uvicorn runs with --no-proxy-headers, so spoofed X-Forwarded-For cannot bypass source checks. Reject foreign browser Origin headers. Do not add a public proxy to these routes.

Limits: 120 requests/minute/source; 30 binding creation attempts/hour/source and 500/hour globally. SQLite counters survive restarts. Bodies are bounded to 1 MiB even with chunked transfer. Audits contain metadata, not credentials or retrieved text.

Existing server certificates/private keys remain in the server volume. Only ragflow-project-mcp-ca.crt is bundled; its packaging SHA-256 is a3b822ff624a869464ba6f0dd5dd75b14ec8f763ff5754c3d2f17ed7f091b05b. Distribute a new trusted Skill bundle when rotating the CA.

Tool: ragflow_project_retrieval(query, top_k=8), query length 2-8000, top_k 1-20, no dataset/project selector. Results include evidence and its count. Zero evidence differs from transport failure.

Acceptance covers admitted/denied IPs, forged forwarding headers, anonymous catalog/create, administrator 401, dynamic discovery, fixed bindings, cross-binding rejection, rotation/revocation/expiry, rate limits, bounded bodies, trusted/wrong CA and real stdio forwarding.
