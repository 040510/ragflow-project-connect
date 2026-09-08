"""Forward MCP JSON lines through verified HTTPS; stdout contains only MCP."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from transport import ConnectionError, PROTOCOL, Transport


def run(binding_file, input_stream, output_stream):
    path = Path(binding_file).resolve()
    protocol = PROTOCOL
    while True:
        raw = input_stream.readline(1_048_577)
        if not raw:
            return
        if len(raw) > 1_048_576:
            raise ConnectionError("MCP input is too large")
        if not raw.strip():
            continue
        message = None
        try:
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("Invalid MCP message")
            # Reload per request so credential rotation takes effect without restarting the Agent.
            binding = json.loads(path.read_text(encoding="utf-8"))
            ca = path.parent / binding["ca_file"]
            transport = Transport(binding["base_url"], ca)
            response = transport.request("POST", binding["url"], message, binding["token"], protocol)
            if message.get("method") == "initialize" and response and "result" in response:
                protocol = response["result"].get("protocolVersion", PROTOCOL)
        except (ConnectionError, OSError, ValueError, KeyError):
            response = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                        "error": {"code": -32000, "message": "Knowledge gateway unavailable or connection expired; run Skill verify/reconnect."}}
        if isinstance(message, dict) and "id" not in message:
            continue
        if response is not None:
            output_stream.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            output_stream.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True)
    args = parser.parse_args()
    try:
        run(args.binding, sys.stdin.buffer, sys.stdout.buffer)
    except (ConnectionError, OSError):
        print("MCP adapter stopped; check the local connection configuration.", file=sys.stderr)
        raise SystemExit(1)
