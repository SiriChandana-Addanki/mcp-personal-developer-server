"""Minimal MCP stdio server. Protocol output is written only to stdout."""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from .core import DeveloperTools

TOOLS = [
    {"name": "search_project", "description": "Search text in non-sensitive files under the configured project root.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "maxLength": 256}, "path": {"type": "string", "default": "."}, "max_results": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["query"]}},
    {"name": "git_status", "description": "Read the configured repository's branch and working-tree status.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "git_diff", "description": "Read a bounded diff from the configured repository.", "inputSchema": {"type": "object", "properties": {"staged": {"type": "boolean", "default": False}}}},
    {"name": "run_tests", "description": "Run an approved test suite without user-supplied commands or arguments.", "inputSchema": {"type": "object", "properties": {"suite": {"type": "string", "enum": ["unittest", "pytest"]}}, "required": ["suite"]}},
    {"name": "read_logs", "description": "Read a bounded approved log file within the project root.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 64000}}, "required": ["path"]}},
    {"name": "github_repo_info", "description": "Fetch a small public-facing metadata subset for a GitHub repository.", "inputSchema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]}},
]


def _response(request_id: Any, result: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, separators=(",", ":")), flush=True)


def serve() -> None:
    root = os.getenv("PROJECT_ROOT", os.getcwd())
    try:
        tools = DeveloperTools(root)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr, flush=True)
        raise SystemExit(2)
    for raw in sys.stdin.buffer:
        if len(raw) > 1_000_000:
            continue
        try:
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                continue
            method, request_id = message.get("method"), message.get("id")
            if method == "notifications/initialized" or request_id is None:
                continue
            if method == "initialize":
                _response(request_id, {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                                       "serverInfo": {"name": "mcp-personal-developer-server", "version": "0.1.0"}})
            elif method == "ping":
                _response(request_id, {})
            elif method == "tools/list":
                _response(request_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = message.get("params")
                if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                    _response(request_id, {"content": [{"type": "text", "text": "invalid tool request"}], "isError": True})
                    continue
                outcome = tools.call(params["name"], params.get("arguments", {}), request_id)
                result = outcome["result"]
                _response(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                                       "structuredContent": result, "isError": not outcome["ok"]})
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                  "error": {"code": -32601, "message": "Method not found"}}), flush=True)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        except Exception:
            # Never include request contents, environment values, or exception details on the protocol stream.
            if isinstance(locals().get("request_id"), (str, int, float)):
                print(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                  "error": {"code": -32603, "message": "Internal error"}}), flush=True)


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
