"""Verified HTTPS transport shared by onboarding and the local stdio adapter."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOOL = "ragflow_project_retrieval"
PROTOCOL = "2025-03-26"
MAX_RESPONSE = 2 * 1024 * 1024


class ConnectionError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def origin(url):
    value = urllib.parse.urlsplit(url)
    if value.scheme != "https" or not value.hostname or value.username or value.password or value.query or value.fragment:
        raise ConnectionError("Use an HTTPS URL without credentials, query or fragment")
    return value.scheme, value.hostname.lower(), value.port or 443


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectionError("Gateway redirects are refused")


class Transport:
    def __init__(self, base_url, ca_file, timeout=120):
        self.base_url = base_url.rstrip("/")
        self.origin = origin(self.base_url)
        try:
            context = ssl.create_default_context(cafile=str(Path(ca_file).resolve()))
        except (OSError, ssl.SSLError):
            raise ConnectionError("Cannot load the bundled CA certificate") from None
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=context)
        )

    def request(self, method, path, body=None, token=None, protocol=None):
        url = path if path.startswith("https://") else self.base_url + path
        if origin(url) != self.origin:
            raise ConnectionError("Gateway endpoint changed origin")
        headers = {"Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if protocol:
            headers["MCP-Protocol-Version"] = protocol
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ConnectionError("Gateway response is too large")
                if not raw:
                    return None
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    for block in raw.decode("utf-8").replace("\r\n", "\n").split("\n\n"):
                        lines = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
                        if lines:
                            value = json.loads("\n".join(lines))
                            if isinstance(value, dict) and value.get("id") == (body or {}).get("id"):
                                return value
                    raise ConnectionError("Missing MCP response")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ConnectionError("Invalid gateway response")
                return value
        except urllib.error.HTTPError as exc:
            raise ConnectionError(f"Gateway HTTP {exc.code}", exc.code) from None
        except (urllib.error.URLError, OSError):
            raise ConnectionError("Cannot reach gateway with verified TLS; check LAN access and certificate") from None
        except (ValueError, UnicodeError):
            raise ConnectionError("Invalid gateway JSON") from None


def result(message):
    if not isinstance(message, dict) or "error" in message or not isinstance(message.get("result"), dict):
        raise ConnectionError("MCP request failed")
    return message["result"]


def verify(transport, binding, query=None):
    def call(i, method, params):
        return transport.request("POST", binding["url"], {"jsonrpc": "2.0", "id": i, "method": method, "params": params}, binding["token"], PROTOCOL)
    initialized = result(call(1, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "ragflow-project-connect", "version": "2.0"}}))
    transport.request("POST", binding["url"], {"jsonrpc": "2.0", "method": "notifications/initialized"}, binding["token"], initialized["protocolVersion"])
    tools = result(call(2, "tools/list", {})).get("tools", [])
    if len(tools) != 1 or tools[0].get("name") != TOOL:
        raise ConnectionError("Unexpected MCP tools for a retrieval-only binding")
    schema = tools[0].get("inputSchema", {})
    if set(schema.get("properties", {})) - {"query", "top_k"} or schema.get("additionalProperties") is not False:
        raise ConnectionError("MCP tool scope is not fixed")
    count = None
    if query:
        evidence = result(call(3, "tools/call", {"name": TOOL, "arguments": {"query": query, "top_k": 3}}))
        if evidence.get("isError") or not evidence.get("content"):
            raise ConnectionError("Knowledge retrieval probe failed")
        count = evidence.get("structuredContent", {}).get("evidence_count")
    return {"protocol": initialized["protocolVersion"], "tools": [TOOL], "evidence_count": count}
