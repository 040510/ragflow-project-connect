#!/usr/bin/env python3
"""Configure and operate project-scoped RAGFlow MCP bindings.

The script intentionally accepts secrets only from environment variables, hidden
input, or an existing Agent config. It never prints a bearer token.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import getpass
import json
import os
import platform
import re
import shutil
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_PROTOCOL_VERSION = "2025-03-26"
DEFAULT_TOOL = "ragflow_project_retrieval"
DEFAULT_EXPIRES_SECONDS = 30 * 24 * 60 * 60
STATE_VERSION = 1
_TLS_CONTEXT = None


class ConnectError(RuntimeError):
    pass


def configure_tls(ca_file: str | None) -> None:
    global _TLS_CONTEXT
    if not ca_file:
        _TLS_CONTEXT = None
        return
    path = Path(ca_file).expanduser().resolve()
    if not path.is_file():
        raise ConnectError(f"CA certificate file does not exist: {path}")
    try:
        _TLS_CONTEXT = ssl.create_default_context(cafile=str(path))
    except (OSError, ssl.SSLError) as exc:
        raise ConnectError(f"Cannot load CA certificate file {path}: {exc}") from exc


def open_https(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=_TLS_CONTEXT)


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def default_workbuddy_config() -> Path:
    return Path.home() / ".workbuddy" / "mcp.json"


def default_state_path() -> Path:
    return Path.home() / ".ragflow-project-connect" / "bindings.json"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectError(f"Cannot read valid JSON from {path}: {exc}") from exc


def write_json_atomic(path: Path, value: Any, backup: bool = False) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if backup and path.exists():
        backup_path = path.with_name(f"{path.name}.bak.{utc_stamp()}")
        shutil.copy2(path, backup_path)

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return backup_path


def read_secret(env_name: str, prompt: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    if not sys.stdin.isatty():
        raise ConnectError(f"Set {env_name}; hidden prompting is unavailable in this session")
    value = getpass.getpass(prompt).strip()
    if not value:
        raise ConnectError(f"No credential supplied through {env_name} or hidden input")
    return value


def api_url(base: str, path: str) -> str:
    base = base.strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https":
        raise ConnectError("The control URL must use HTTPS; insecure bypass is not supported")
    return f"{base}{path}"


def http_json(
    method: str,
    url: str,
    bearer_token: str,
    body: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": "ragflow-project-connect/1.0",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with open_https(request, timeout) as response:
            raw = response.read()
            if not raw:
                return {}
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ConnectError(f"Expected a JSON object from {url}")
            return value
    except urllib.error.HTTPError as exc:
        raise ConnectError(f"{method} {url} failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ConnectError(f"Cannot reach {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ConnectError(f"Invalid JSON returned by {url}") from exc


def require_list(response: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items = response.get(key)
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ConnectError(f"Control API response is missing a valid '{key}' list")
    return items


def item_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or item.get("slug") or item.get("id") or "unnamed")
    item_id = str(item.get("id") or "")
    return f"{name} [{item_id}]" if item_id and item_id != name else name


def match_item(items: list[dict[str, Any]], value: str, kind: str) -> dict[str, Any]:
    matches = []
    needle = value.casefold()
    for item in items:
        candidates = [item.get("id"), item.get("slug"), item.get("name")]
        if any(str(candidate).casefold() == needle for candidate in candidates if candidate is not None):
            matches.append(item)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ConnectError(f"No authorized {kind} matches '{value}'")
    raise ConnectError(f"More than one authorized {kind} matches '{value}'; use its ID")


def choose_item(items: list[dict[str, Any]], requested: str | None, kind: str) -> dict[str, Any]:
    if not items:
        raise ConnectError(f"No authorized {kind}s were returned")
    if requested:
        return match_item(items, requested, kind)
    if len(items) == 1:
        return items[0]
    if not sys.stdin.isatty():
        raise ConnectError(f"Specify --{kind.replace('_', '-')} in a non-interactive session")
    print(f"Available {kind}s:")
    for index, item in enumerate(items, start=1):
        print(f"  {index}. {item_label(item)}")
    raw = input(f"Select {kind} [1-{len(items)}]: ").strip()
    try:
        index = int(raw)
    except ValueError as exc:
        raise ConnectError(f"Invalid {kind} selection") from exc
    if index < 1 or index > len(items):
        raise ConnectError(f"Invalid {kind} selection")
    return items[index - 1]


def select_knowledge_bases(
    available: list[dict[str, Any]], requested: list[str]
) -> list[dict[str, Any]]:
    if not available:
        raise ConnectError("The selected project has no authorized knowledge bases")
    if not requested:
        return available
    selected = [match_item(available, value, "knowledge_base") for value in requested]
    unique: dict[str, dict[str, Any]] = {}
    for item in selected:
        item_id = str(item.get("id") or "")
        if not item_id:
            raise ConnectError("A selected knowledge base has no ID")
        unique[item_id] = item
    return list(unique.values())


def safe_server_name(project: dict[str, Any]) -> str:
    raw = str(project.get("slug") or project.get("name") or project.get("id") or "project")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._").lower()
    return f"ragflow-{normalized or 'project'}"


def resolve_config(client: str, configured: str | None) -> Path:
    if configured:
        return Path(configured).expanduser().resolve()
    if client == "workbuddy":
        return default_workbuddy_config()
    raise ConnectError("--config is required for generic-json clients")


def config_entry(url: str, access_token: str) -> dict[str, Any]:
    return {
        "type": "streamable-http",
        "url": url,
        "headers": {"Authorization": f"Bearer {access_token}"},
        "disabled": False,
    }


def read_agent_config(path: Path) -> dict[str, Any]:
    config = load_json(path, {"mcpServers": {}})
    if not isinstance(config, dict):
        raise ConnectError(f"Agent config root must be an object: {path}")
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ConnectError(f"Agent config 'mcpServers' must be an object: {path}")
    return config


def ensure_config_slot(path: Path, server_name: str, replace: bool) -> None:
    config = read_agent_config(path)
    if server_name in config["mcpServers"] and not replace:
        raise ConnectError(
            f"MCP server '{server_name}' already exists in {path}; use --replace only after review"
        )


def install_config(
    path: Path, server_name: str, endpoint: str, access_token: str, replace: bool
) -> Path | None:
    config = read_agent_config(path)
    if server_name in config["mcpServers"] and not replace:
        raise ConnectError(f"MCP server '{server_name}' already exists in {path}")
    config["mcpServers"][server_name] = config_entry(endpoint, access_token)
    return write_json_atomic(path, config, backup=True)


def remove_config(path: Path, server_name: str) -> tuple[bool, Path | None]:
    config = read_agent_config(path)
    if server_name not in config["mcpServers"]:
        return False, None
    del config["mcpServers"][server_name]
    backup = write_json_atomic(path, config, backup=True)
    return True, backup


def token_from_config(path: Path, server_name: str) -> tuple[str, str]:
    config = read_agent_config(path)
    entry = config["mcpServers"].get(server_name)
    if not isinstance(entry, dict):
        raise ConnectError(f"MCP server '{server_name}' was not found in {path}")
    url = entry.get("url")
    headers = entry.get("headers")
    authorization = headers.get("Authorization") if isinstance(headers, dict) else None
    if not isinstance(url, str) or not url:
        raise ConnectError(f"MCP server '{server_name}' has no URL")
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise ConnectError(f"MCP server '{server_name}' has no bearer token")
    return url, authorization[len("Bearer ") :]


def parse_sse_or_json(raw: bytes, content_type: str) -> dict[str, Any]:
    text = raw.decode("utf-8")
    if "text/event-stream" not in content_type.lower():
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ConnectError("MCP response was not a JSON object")
        return value

    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line.strip() and data_lines:
            value = json.loads("\n".join(data_lines))
            if isinstance(value, dict):
                return value
            data_lines = []
    if data_lines:
        value = json.loads("\n".join(data_lines))
        if isinstance(value, dict):
            return value
    raise ConnectError("MCP SSE response contained no JSON-RPC object")


def mcp_post(
    endpoint: str,
    token: str,
    message: dict[str, Any],
    session_id: str | None,
    protocol_version: str | None,
    timeout: float,
) -> tuple[dict[str, Any] | None, str | None]:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https":
        raise ConnectError("The MCP endpoint must use HTTPS")
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "ragflow-project-connect/1.0",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(message).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with open_https(request, timeout) as response:
            next_session = response.headers.get("Mcp-Session-Id") or session_id
            raw = response.read()
            if not raw:
                return None, next_session
            content_type = response.headers.get("Content-Type", "application/json")
            return parse_sse_or_json(raw, content_type), next_session
    except urllib.error.HTTPError as exc:
        raise ConnectError(f"MCP request failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ConnectError(f"Cannot reach MCP endpoint: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ConnectError("MCP endpoint returned invalid JSON/SSE") from exc


def rpc_result(response: dict[str, Any] | None, operation: str) -> dict[str, Any]:
    if response is None:
        raise ConnectError(f"MCP {operation} returned no response")
    if "error" in response:
        error = response.get("error")
        message = error.get("message") if isinstance(error, dict) else "unknown error"
        raise ConnectError(f"MCP {operation} failed: {message}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ConnectError(f"MCP {operation} returned no result object")
    return result


def verify_mcp(
    endpoint: str,
    token: str,
    protocol_version: str,
    expected_tool: str,
    query: str | None,
    top_k: int,
    timeout: float,
) -> dict[str, Any]:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "ragflow-project-connect", "version": "1.0"},
        },
    }
    response, session_id = mcp_post(endpoint, token, initialize, None, None, timeout)
    init_result = rpc_result(response, "initialize")
    negotiated = str(init_result.get("protocolVersion") or protocol_version)

    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    mcp_post(endpoint, token, notification, session_id, negotiated, timeout)

    tools_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    response, session_id = mcp_post(
        endpoint, token, tools_request, session_id, negotiated, timeout
    )
    tools_result = rpc_result(response, "tools/list")
    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        raise ConnectError("MCP tools/list returned no tools list")
    names = [str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")]
    if expected_tool not in names:
        raise ConnectError(f"Required read-only tool '{expected_tool}' is absent; returned: {', '.join(names)}")
    unexpected = sorted(set(names) - {expected_tool})
    if unexpected:
        raise ConnectError(
            "Retrieval-only binding exposed unexpected tools: " + ", ".join(unexpected)
        )

    expected_definition = next(
        tool for tool in tools if isinstance(tool, dict) and tool.get("name") == expected_tool
    )
    schema = expected_definition.get("inputSchema")
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    dangerous_selectors = {
        "dataset_id",
        "dataset_ids",
        "knowledge_base_id",
        "knowledge_base_ids",
        "project_id",
    }
    exposed_selectors = sorted(dangerous_selectors.intersection(properties))
    if exposed_selectors:
        raise ConnectError(
            "Project-scoped retrieval tool exposed unrestricted selectors: "
            + ", ".join(exposed_selectors)
        )

    probe_has_content = None
    if query:
        tool_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": expected_tool, "arguments": {"query": query, "top_k": top_k}},
        }
        response, _ = mcp_post(
            endpoint, token, tool_request, session_id, negotiated, timeout
        )
        call_result = rpc_result(response, "tools/call")
        if call_result.get("isError") is True:
            raise ConnectError("The retrieval probe returned isError=true")
        content = call_result.get("content")
        probe_has_content = isinstance(content, list) and len(content) > 0

    return {
        "protocol_version": negotiated,
        "tools": names,
        "retrieval_probe_has_content": probe_has_content,
    }


def load_state(path: Path) -> dict[str, Any]:
    state = load_json(path, {"version": STATE_VERSION, "bindings": {}})
    if not isinstance(state, dict) or not isinstance(state.get("bindings"), dict):
        raise ConnectError(f"Invalid state file: {path}")
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    write_json_atomic(path, state, backup=False)


def remove_state_entry(path: Path, server_name: str) -> None:
    state = load_state(path)
    state["bindings"].pop(server_name, None)
    save_state(path, state)


def get_state_entry(path: Path, server_name: str) -> dict[str, Any]:
    state = load_state(path)
    entry = state["bindings"].get(server_name)
    if not isinstance(entry, dict):
        raise ConnectError(f"No local binding state exists for '{server_name}'")
    return entry


def binding_parts(response: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    binding = response.get("binding", response)
    mcp = response.get("mcp", response)
    if not isinstance(binding, dict) or not isinstance(mcp, dict):
        raise ConnectError("Binding response has an invalid shape")
    return binding, mcp


def command_projects(args: argparse.Namespace) -> None:
    token = read_secret(args.control_token_env, "Enterprise control token: ")
    response = http_json("GET", api_url(args.control_url, "/v1/projects"), token, timeout=args.timeout)
    projects = require_list(response, "projects")
    if not projects:
        print("No authorized projects.")
        return
    for project in projects:
        print(item_label(project))


def command_connect(args: argparse.Namespace) -> None:
    control_token = read_secret(args.control_token_env, "Enterprise control token: ")
    projects_response = http_json(
        "GET", api_url(args.control_url, "/v1/projects"), control_token, timeout=args.timeout
    )
    project = choose_item(require_list(projects_response, "projects"), args.project, "project")
    project_id = str(project.get("id") or "")
    if not project_id:
        raise ConnectError("The selected project has no ID")

    kb_path = f"/v1/projects/{urllib.parse.quote(project_id, safe='')}/knowledge-bases"
    kb_response = http_json("GET", api_url(args.control_url, kb_path), control_token, timeout=args.timeout)
    available_kbs = require_list(kb_response, "knowledge_bases")
    selected_kbs = select_knowledge_bases(available_kbs, args.knowledge_base)
    kb_ids = [str(item.get("id")) for item in selected_kbs]

    server_name = args.server_name or safe_server_name(project)
    config_path = resolve_config(args.client, args.config)
    state_path = Path(args.state).expanduser().resolve()
    existing_state = load_state(state_path)
    if server_name in existing_state["bindings"]:
        raise ConnectError(
            f"Managed binding '{server_name}' already exists; use rotate or disconnect first"
        )
    ensure_config_slot(config_path, server_name, args.replace)

    agent_name = args.agent_name or f"{platform.node() or 'local'}-{args.client}"
    request_body = {
        "project_id": project_id,
        "knowledge_base_ids": kb_ids,
        "agent": {"name": agent_name, "client_type": args.client},
        "scopes": ["knowledge:retrieve"],
        "expires_in_seconds": args.expires_in_seconds,
    }
    response = http_json(
        "POST", api_url(args.control_url, "/v1/bindings"), control_token, request_body, args.timeout
    )
    binding, mcp = binding_parts(response)
    binding_id = str(binding.get("id") or "")
    endpoint = str(mcp.get("url") or "")
    access_token = str(mcp.get("access_token") or "")
    expires_at = str(mcp.get("expires_at") or "unknown")
    if not binding_id or not endpoint or not access_token:
        raise ConnectError("Binding response is missing id, mcp.url, or mcp.access_token")

    state_saved = False
    try:
        verification = None
        if not args.skip_verify:
            verification = verify_mcp(
                endpoint,
                access_token,
                args.protocol_version,
                args.expected_tool,
                args.probe_query,
                args.top_k,
                args.timeout,
            )

        state = load_state(state_path)
        state["bindings"][server_name] = {
            "binding_id": binding_id,
            "control_url": args.control_url.rstrip("/"),
            "client": args.client,
            "config_path": str(config_path),
            "project_id": project_id,
            "project_name": str(project.get("name") or project.get("slug") or project_id),
            "knowledge_base_ids": kb_ids,
            "knowledge_base_names": [str(item.get("name") or item.get("id")) for item in selected_kbs],
            "mcp_url": endpoint,
            "expires_at": expires_at,
        }
        save_state(state_path, state)
        state_saved = True
        backup = install_config(config_path, server_name, endpoint, access_token, args.replace)
    except Exception:
        if state_saved:
            try:
                remove_state_entry(state_path, server_name)
            except Exception:
                pass
        try:
            revoke_path = f"/v1/bindings/{urllib.parse.quote(binding_id, safe='')}"
            http_json("DELETE", api_url(args.control_url, revoke_path), control_token, timeout=args.timeout)
        except Exception:
            print("Warning: automatic binding cleanup failed; an administrator should revoke it.", file=sys.stderr)
        raise

    print(f"Connected MCP server: {server_name}")
    print(f"Project: {item_label(project)}")
    print("Knowledge bases: " + ", ".join(item_label(item) for item in selected_kbs))
    print(f"Endpoint: {endpoint}")
    print(f"Expires: {expires_at}")
    print(f"Config: {config_path}")
    if backup:
        print(f"Backup: {backup}")
    if args.skip_verify:
        print("Verification: skipped")
    else:
        probe = verification["retrieval_probe_has_content"]
        detail = "initialize + tools/list"
        if probe is not None:
            detail += f" + retrieval probe (content={str(probe).lower()})"
        print(f"Verification: passed ({detail})")


def command_verify(args: argparse.Namespace) -> None:
    state_path = Path(args.state).expanduser().resolve()
    entry = get_state_entry(state_path, args.server_name)
    config_path = Path(args.config or entry.get("config_path") or "").expanduser().resolve()
    endpoint, token = token_from_config(config_path, args.server_name)
    verification = verify_mcp(
        endpoint,
        token,
        args.protocol_version,
        args.expected_tool,
        args.probe_query,
        args.top_k,
        args.timeout,
    )
    print(f"Verification passed for {args.server_name}")
    print(f"Protocol: {verification['protocol_version']}")
    print("Tools: " + ", ".join(verification["tools"]))
    if verification["retrieval_probe_has_content"] is not None:
        print(
            "Retrieval probe content: "
            + str(verification["retrieval_probe_has_content"]).lower()
        )


def command_rotate(args: argparse.Namespace) -> None:
    state_path = Path(args.state).expanduser().resolve()
    entry = get_state_entry(state_path, args.server_name)
    control_url = str(entry.get("control_url") or "")
    binding_id = str(entry.get("binding_id") or "")
    config_path = Path(args.config or entry.get("config_path") or "").expanduser().resolve()
    if not control_url or not binding_id:
        raise ConnectError("Local binding state is missing control_url or binding_id")
    control_token = read_secret(args.control_token_env, "Enterprise control token: ")
    rotate_path = f"/v1/bindings/{urllib.parse.quote(binding_id, safe='')}/rotate"
    response = http_json("POST", api_url(control_url, rotate_path), control_token, {}, args.timeout)
    _, mcp = binding_parts(response)
    endpoint = str(mcp.get("url") or entry.get("mcp_url") or "")
    access_token = str(mcp.get("access_token") or "")
    expires_at = str(mcp.get("expires_at") or "unknown")
    if not endpoint or not access_token:
        raise ConnectError("Rotation response is missing mcp.url or mcp.access_token")

    backup = install_config(config_path, args.server_name, endpoint, access_token, replace=True)
    state = load_state(state_path)
    state["bindings"][args.server_name]["mcp_url"] = endpoint
    state["bindings"][args.server_name]["expires_at"] = expires_at
    save_state(state_path, state)
    try:
        verification = verify_mcp(
            endpoint,
            access_token,
            args.protocol_version,
            args.expected_tool,
            args.probe_query,
            args.top_k,
            args.timeout,
        )
    except ConnectError as exc:
        raise ConnectError(
            "The rotated token was saved, but post-rotation verification failed: " + str(exc)
        ) from exc
    print(f"Rotated binding: {args.server_name}")
    print(f"Expires: {expires_at}")
    print(f"Verification: passed ({len(verification['tools'])} tool(s))")
    if backup:
        print(f"Backup: {backup}")


def command_disconnect(args: argparse.Namespace) -> None:
    state_path = Path(args.state).expanduser().resolve()
    entry = get_state_entry(state_path, args.server_name)
    control_url = str(entry.get("control_url") or "")
    binding_id = str(entry.get("binding_id") or "")
    config_path = Path(args.config or entry.get("config_path") or "").expanduser().resolve()

    if not args.local_only:
        if not control_url or not binding_id:
            raise ConnectError("Local binding state is missing control_url or binding_id")
        control_token = read_secret(args.control_token_env, "Enterprise control token: ")
        revoke_path = f"/v1/bindings/{urllib.parse.quote(binding_id, safe='')}"
        try:
            http_json("DELETE", api_url(control_url, revoke_path), control_token, timeout=args.timeout)
        except ConnectError as exc:
            if "HTTP 404" not in str(exc):
                raise

    removed, backup = remove_config(config_path, args.server_name)
    remove_state_entry(state_path, args.server_name)
    print(f"Disconnected binding: {args.server_name}")
    print("Server-side revocation: " + ("skipped" if args.local_only else "complete"))
    print("Local config entry: " + ("removed" if removed else "already absent"))
    if backup:
        print(f"Backup: {backup}")


def command_status(args: argparse.Namespace) -> None:
    state_path = Path(args.state).expanduser().resolve()
    state = load_state(state_path)
    bindings = state["bindings"]
    names = [args.server_name] if args.server_name else sorted(bindings)
    if not names:
        print("No local RAGFlow project bindings.")
        return
    for name in names:
        entry = bindings.get(name)
        if not isinstance(entry, dict):
            print(f"{name}: no local state")
            continue
        config_path = Path(str(entry.get("config_path") or "")).expanduser()
        present = False
        try:
            config = read_agent_config(config_path)
            present = name in config["mcpServers"]
        except ConnectError:
            present = False
        print(
            f"{name}: project={entry.get('project_name') or entry.get('project_id')}, "
            f"knowledge_bases={len(entry.get('knowledge_base_ids') or [])}, "
            f"expires={entry.get('expires_at') or 'unknown'}, config_present={str(present).lower()}"
        )


def add_control_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--control-url", required=True, help="Internal HTTPS project gateway base URL")
    parser.add_argument("--ca-file", help="Enterprise CA certificate for the internal HTTPS endpoint")
    parser.add_argument(
        "--control-token-env",
        default="RAGFLOW_CONTROL_TOKEN",
        help="Environment variable containing the enterprise control token",
    )
    parser.add_argument("--timeout", type=float, default=20.0)


def add_probe_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ca-file", help="Enterprise CA certificate for the internal HTTPS endpoint")
    parser.add_argument("--protocol-version", default=DEFAULT_PROTOCOL_VERSION)
    parser.add_argument("--expected-tool", default=DEFAULT_TOOL)
    parser.add_argument("--probe-query", help="Optional non-sensitive real retrieval probe")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=20.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Connect an Agent to a project-scoped RAGFlow MCP gateway without printing secrets."
    )
    parser.add_argument("--state", default=str(default_state_path()), help="Local non-secret binding state")
    subparsers = parser.add_subparsers(dest="command", required=True)

    projects = subparsers.add_parser("projects", help="List all projects exposed by the MCP gateway")
    add_control_args(projects)
    projects.set_defaults(func=command_projects)

    connect = subparsers.add_parser("connect", help="Create, verify, and configure a project binding")
    add_control_args(connect)
    connect.add_argument("--client", choices=["workbuddy", "generic-json"], default="workbuddy")
    connect.add_argument("--config", help="Agent MCP JSON path; required for generic-json")
    connect.add_argument("--project", help="Available project ID, slug, or exact name")
    connect.add_argument(
        "--knowledge-base",
        action="append",
        default=[],
        help="Available knowledge-base ID, slug, or exact name; repeat for a subset",
    )
    connect.add_argument("--agent-name")
    connect.add_argument("--server-name")
    connect.add_argument("--expires-in-seconds", type=int, default=DEFAULT_EXPIRES_SECONDS)
    connect.add_argument("--replace", action="store_true")
    connect.add_argument("--skip-verify", action="store_true")
    connect.add_argument("--protocol-version", default=DEFAULT_PROTOCOL_VERSION)
    connect.add_argument("--expected-tool", default=DEFAULT_TOOL)
    connect.add_argument("--probe-query", help="Optional non-sensitive real retrieval probe")
    connect.add_argument("--top-k", type=int, default=3)
    connect.set_defaults(func=command_connect)

    verify = subparsers.add_parser("verify", help="Verify an existing configured binding")
    verify.add_argument("--server-name", required=True)
    verify.add_argument("--config")
    add_probe_args(verify)
    verify.set_defaults(func=command_verify)

    rotate = subparsers.add_parser("rotate", help="Rotate an existing binding token")
    rotate.add_argument("--server-name", required=True)
    rotate.add_argument("--config")
    rotate.add_argument(
        "--control-token-env", default="RAGFLOW_CONTROL_TOKEN", help="Control token environment variable"
    )
    add_probe_args(rotate)
    rotate.set_defaults(func=command_rotate)

    disconnect = subparsers.add_parser("disconnect", help="Revoke and remove an existing binding")
    disconnect.add_argument("--server-name", required=True)
    disconnect.add_argument("--config")
    disconnect.add_argument(
        "--control-token-env", default="RAGFLOW_CONTROL_TOKEN", help="Control token environment variable"
    )
    disconnect.add_argument("--local-only", action="store_true")
    disconnect.add_argument("--ca-file", help="Enterprise CA certificate for the internal HTTPS endpoint")
    disconnect.add_argument("--timeout", type=float, default=20.0)
    disconnect.set_defaults(func=command_disconnect)

    status = subparsers.add_parser("status", help="Show local non-secret binding metadata")
    status.add_argument("--server-name")
    status.set_defaults(func=command_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        configure_tls(getattr(args, "ca_file", None))
        if hasattr(args, "top_k") and not 1 <= args.top_k <= 20:
            raise ConnectError("--top-k must be between 1 and 20")
        if hasattr(args, "expires_in_seconds") and args.expires_in_seconds <= 0:
            raise ConnectError("--expires-in-seconds must be positive")
        args.func(args)
        return 0
    except ConnectError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
