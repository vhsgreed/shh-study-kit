"""shhkit CLI. Every tool in shhkit.tools is a subcommand; output is JSON on stdout.

  python -m shhkit                      list tools
  python -m shhkit <tool> [k=v ...]     run a tool (values parsed as JSON when possible)
  python -m shhkit <tool> --json '{..}' run a tool with a JSON argument object
  python -m shhkit manifest             tool schemas (same as tools.json)
  python -m shhkit.mcp                  MCP server over stdio

Examples:
  python -m shhkit upcoming
  python -m shhkit mirror course=all
  python -m shhkit pubmed query="person-centred care psychiatry" max=5
"""
import json
import sys

from .tools import TOOLS, call, manifest


def emit(x):
    print(json.dumps(x, ensure_ascii=False, indent=1, default=str))


def parse(rest):
    if rest[:1] == ["--json"]:
        return json.loads(rest[1])
    args = {}
    for kv in rest:
        k, _, v = kv.partition("=")
        try:
            args[k] = json.loads(v)
        except ValueError:
            args[k] = v
    return args


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        for t in TOOLS.values():
            print(f"  {t['name']:<20} {t['description'][:80]}")
        return
    if argv[0] == "manifest":
        emit(manifest())
        return
    try:
        emit(call(argv[0], parse(argv[1:])))
    except KeyError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
