"""Create a local stdio MCP connection without asking users for CA or tokens."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

from transport import ConnectionError, Transport, origin, verify

PREFIX = "/v1/self-service"
SKILL = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://172.16.3.173:8892"


def load(path, default=None):
    if not path.exists() and default is not None:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ConnectionError(f"Cannot read configuration file: {path}") from None


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        info = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], check=True, capture_output=True, text=True)
        sid = next(csv.reader(io.StringIO(info.stdout)))[1]
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"], check=True, capture_output=True)
    else:
        path.chmod(0o700)


def items(response, key):
    values = (response or {}).get(key)
    if not isinstance(values, list) or any(not isinstance(v, dict) or not v.get("id") for v in values):
        raise ConnectionError("Invalid project catalog response")
    return values


def pick(values, value, kind):
    matches = [item for item in values if value.casefold() in {str(item.get(k, "")).casefold() for k in ("id", "name", "slug")}]
    if len(matches) != 1:
        raise ConnectionError(f"Select an unambiguous {kind} ID from the catalog")
    return matches[0]


def choose_project(values, requested):
    if requested:
        return pick(values, requested, "project")
    if not sys.stdin.isatty():
        raise ConnectionError("Choose a project from 'projects', then pass --project")
    print(json.dumps(values, ensure_ascii=False, indent=2))
    return pick(values, input("Project ID: ").strip(), "project")


def agent_config(path):
    config = load(path, {})
    if not isinstance(config, dict) or not isinstance(config.setdefault("mcpServers", {}), dict):
        raise ConnectionError("Expected a JSON object with mcpServers")
    return config


def descriptor(response, base_url, project_id, kb_ids):
    binding, mcp = response.get("binding", {}), response.get("mcp", {})
    if binding.get("project_id") != project_id or set(binding.get("knowledge_base_ids", [])) != set(kb_ids) or binding.get("scopes") != ["knowledge:retrieve"]:
        raise ConnectionError("Returned binding differs from selected scope")
    if not re.fullmatch(r"bind_[a-f0-9]{16}", str(binding.get("id", ""))):
        raise ConnectionError("Missing binding ID")
    if origin(mcp.get("url", "")) != origin(base_url) or not mcp.get("access_token") or not mcp.get("expires_at"):
        raise ConnectionError("Invalid MCP connection descriptor")
    return {"binding_id": binding["id"], "project_id": project_id, "knowledge_base_ids": kb_ids,
            "base_url": base_url, "url": mcp["url"], "token": mcp["access_token"],
            "expires_at": mcp["expires_at"], "ca_file": "ca.crt"}


def connect(args):
    client = Transport(args.control_url, args.ca_file, args.timeout)
    project = choose_project(items(client.request("GET", PREFIX + "/projects"), "projects"), args.project)
    available = items(client.request("GET", PREFIX + "/projects/" + quote(project["id"], safe="") + "/knowledge-bases"), "knowledge_bases")
    if not available:
        raise ConnectionError("Selected project has no available knowledge bases")
    if args.all_knowledge_bases:
        selected = available
    elif args.knowledge_base:
        selected = [pick(available, name, "knowledge base") for name in args.knowledge_base]
    elif sys.stdin.isatty():
        print(json.dumps(available, ensure_ascii=False, indent=2))
        raw = input("Knowledge base IDs (comma separated), or all: ").strip()
        selected = available if raw == "all" else [pick(available, name.strip(), "knowledge base") for name in raw.split(",")]
    else:
        raise ConnectionError("Choose --knowledge-base (repeatable) or --all-knowledge-bases explicitly")
    ids = list(dict.fromkeys(item["id"] for item in selected))
    name = args.server_name or "ragflow-" + project["id"]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
        raise ConnectionError("Invalid MCP server name")
    config_path = Path(args.config).expanduser().resolve() if args.config else Path.home() / ".workbuddy" / "mcp.json"
    if args.client == "generic-json" and not args.config:
        raise ConnectionError("generic-json requires --config")
    config = agent_config(config_path)
    registry_path = Path(args.home).expanduser().resolve() / "self-service.json"
    registry = load(registry_path, {})
    if name in config["mcpServers"] or name in registry:
        raise ConnectionError("Connection name already exists; select another --server-name or disconnect it first")
    root = registry_path.parent / "connections" / uuid.uuid4().hex
    private_directory(root)
    for filename in ("stdio_adapter.py", "transport.py"):
        shutil.copy2(SKILL / "scripts" / filename, root / filename)
    shutil.copy2(args.ca_file, root / "ca.crt")
    response = client.request("POST", PREFIX + "/bindings", {
        "name": (args.agent_name or name)[:55] + "-" + uuid.uuid4().hex[:12],
        "project_id": project["id"], "knowledge_base_ids": ids,
        "agent": {"name": args.agent_name or name, "client_type": args.client},
        "scopes": ["knowledge:retrieve"], "follow_project": False,
        "expires_in_seconds": args.expires_in_seconds,
    })
    binding_file = root / "binding.json"
    installed = False
    config_backup = None
    try:
        binding = descriptor(response, args.control_url, project["id"], ids)
        write(binding_file, binding)
        verification = verify(client, binding, args.probe_query)
        # Re-read immediately before writing to preserve unrelated client changes.
        config = agent_config(config_path)
        if name in config["mcpServers"]:
            raise ConnectionError("Client configuration changed during connection")
        if config_path.exists():
            config_backup = root / "mcp-before.json"
            shutil.copy2(config_path, config_backup)
        entry = {"command": sys.executable, "args": [str(root / "stdio_adapter.py"), "--binding", str(binding_file)], "disabled": False}
        config["mcpServers"][name] = entry
        write(config_path, config)
        installed = True
        registry[name] = {"binding_file": str(binding_file), "config_path": str(config_path), "config_entry": entry, "project": project["name"]}
        write(registry_path, registry)
    except Exception:
        if installed:
            current = agent_config(config_path)
            current["mcpServers"].pop(name, None)
            write(config_path, current)
        try:
            binding_id = response["binding"]["id"]
            client.request("DELETE", PREFIX + "/bindings/" + quote(binding_id, safe=""), token=response["mcp"]["access_token"])
            if binding_file.exists():
                binding_file.unlink()
        except Exception:
            print(f"Connection cleanup needs attention; recovery directory: {root}", file=sys.stderr)
        raise
    print(json.dumps({"connected": name, "project": project["name"], "knowledge_base_count": len(ids),
                      "transport": "stdio", "expires_at": binding["expires_at"], "verification": verification,
                      "config": str(config_path)}, ensure_ascii=False))


def operate(args):
    registry_path = Path(args.home).expanduser().resolve() / "self-service.json"
    registry = load(registry_path, {})
    if args.command == "status":
        values = {}
        for name, item in registry.items():
            binding = load(Path(item["binding_file"]))
            values[name] = {"project": item["project"], "expires_at": binding["expires_at"], "knowledge_base_count": len(binding["knowledge_base_ids"])}
        print(json.dumps(values, ensure_ascii=False))
        return
    if args.server_name not in registry:
        raise ConnectionError("No self-service connection with that name; legacy connections use project_connect.py")
    item = registry[args.server_name]
    file = Path(item["binding_file"])
    binding = load(file)
    client = Transport(binding["base_url"], file.parent / binding["ca_file"], args.timeout)
    path = PREFIX + "/bindings/" + quote(binding["binding_id"], safe="")
    if args.command == "verify":
        print(json.dumps(verify(client, binding, args.probe_query)))
    elif args.command == "rotate":
        response = client.request("POST", path + "/rotate", {}, binding["token"])
        updated = descriptor(response, binding["base_url"], binding["project_id"], binding["knowledge_base_ids"])
        try:
            write(file, updated)
        except OSError:
            # The old token is already invalid. Retain the new one for recovery.
            recovery = file.with_name("rotation-recovery.json")
            write(recovery, updated)
            raise ConnectionError(f"New credential retained at {recovery}; retry local installation") from None
        print(json.dumps({"rotated": args.server_name, "expires_at": updated["expires_at"], "verification": verify(client, updated)}))
    elif args.command == "disconnect":
        config_path = Path(item["config_path"])
        config = agent_config(config_path)
        actual = config["mcpServers"].get(args.server_name)
        if actual is not None and actual != item["config_entry"]:
            raise ConnectionError("Client entry was changed externally; refusing to remove it")
        try:
            client.request("DELETE", path, token=binding["token"])
        except ConnectionError as exc:
            if exc.status not in (401, 404):
                raise
        config["mcpServers"].pop(args.server_name, None)
        write(config_path, config)
        file.unlink(missing_ok=True)
        del registry[args.server_name]
        write(registry_path, registry)
        print("Connection revoked or already inactive; local MCP entry removed.")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default=str(Path.home() / ".ragflow-project-connect"))
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("projects", "knowledge-bases", "connect"):
        p = sub.add_parser(command)
        p.add_argument("--control-url", default=DEFAULT_URL)
        p.add_argument("--ca-file", type=Path, default=SKILL / "assets" / "ragflow-project-mcp-ca.crt")
        p.add_argument("--timeout", type=float, default=120)
        if command != "projects":
            p.add_argument("--project", required=command == "knowledge-bases")
        if command == "connect":
            p.add_argument("--client", choices=["workbuddy", "generic-json"], default="workbuddy")
            p.add_argument("--config")
            p.add_argument("--server-name")
            p.add_argument("--agent-name")
            group = p.add_mutually_exclusive_group()
            group.add_argument("--knowledge-base", action="append", default=[])
            group.add_argument("--all-knowledge-bases", action="store_true")
            p.add_argument("--expires-in-seconds", type=int, default=2592000)
            p.add_argument("--probe-query")
    sub.add_parser("status")
    for command in ("verify", "rotate", "disconnect"):
        p = sub.add_parser(command)
        p.add_argument("--server-name", required=True)
        p.add_argument("--timeout", type=float, default=120)
        if command == "verify":
            p.add_argument("--probe-query")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        if args.command in ("projects", "knowledge-bases"):
            client = Transport(args.control_url, args.ca_file, args.timeout)
            path = PREFIX + "/projects"
            if args.command == "knowledge-bases":
                project = pick(items(client.request("GET", path), "projects"), args.project, "project")
                path += "/" + quote(project["id"], safe="") + "/knowledge-bases"
            print(json.dumps(client.request("GET", path), ensure_ascii=False, indent=2))
        elif args.command == "connect":
            connect(args)
        else:
            operate(args)
        return 0
    except (ConnectionError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, ConnectionError) else "Local configuration operation failed", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
