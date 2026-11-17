"""Minimal MCP server over stdio (JSON-RPC 2.0, newline-delimited). Stdlib only.

    python -m unitoolkit.mcp

Register it in any MCP client, e.g. Claude Code:
    claude mcp add unitoolkit -- python -m unitoolkit.mcp
Hermes (config.yaml):
    mcp_servers: {unitoolkit: {command: python3, args: [-m, unitoolkit.mcp]}}

Lists the question-shaped core tools by default (agent.CORE); UNITOOLKIT_TOOLS=all lists all,
or give a comma list. Every tool is callable by name regardless of what is listed.
"""
import json
import sys
import traceback

from . import __version__
from .tools import TOOLS, call, manifest

PROTOCOL = "2025-06-18"


def _reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req):
    method, mid, params = req.get("method"), req.get("id"), req.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "unitoolkit", "version": __version__},
                "instructions": ("Read-only access to a student's own school systems: Canvas, "
                                 "Ladok, the library, the timetable, and the subscribed research "
                                 "databases. Start with `today` for 'what do I need to do', "
                                 "`find_sources` for 'find me sources'. When anything fails, run "
                                 "`doctor`: it names the fix, which is always a human action "
                                 "(sign in once, renew a token). Never type a password. "
                                 "Set UNITOOLKIT_TOOLS=all to list every tool.")}
    if method == "tools/list":
        from .agent import listed
        return {"tools": manifest(listed(list(TOOLS)))}
    if method == "tools/call":
        name = params.get("name")
        try:
            out = call(name, params.get("arguments") or {})
            return {"content": [{"type": "text",
                                 "text": json.dumps(out, ensure_ascii=False, indent=1, default=str)}],
                    "isError": False}
        except Exception as e:  # tool errors are results, not protocol errors
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    if method == "ping":
        return {}
    raise LookupError(method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "parse error"})
            continue
        if "id" not in req:  # notification (e.g. notifications/initialized)
            continue
        try:
            _reply(req["id"], handle(req))
        except LookupError as e:
            _reply(req["id"], error={"code": -32601, "message": f"method not found: {e}"})
        except Exception as e:
            _reply(req["id"], error={"code": -32603, "message": str(e),
                                     "data": traceback.format_exc()[-800:]})


if __name__ == "__main__":
    main()
