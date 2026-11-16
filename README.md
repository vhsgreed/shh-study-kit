# unistudy

**Give your agent the student's whole university, read-only.** Canvas, SharePoint, Ladok,
TimeEdit, PubMed and Libris, as 13 tools behind one MCP server. Built for nursing students
at Sophiahemmet Högskola. The Canvas half works at any Canvas school.

```bash
claude mcp add unistudy -- python3 -m unistudy.mcp      # Claude Code
codex mcp add unistudy -- python3 -m unistudy.mcp       # Codex
# Hermes, Cursor, anything MCP: command `python3 -m unistudy.mcp`, cwd = this repo
```

Then ask your agent "what is due this week?" and let it work.

## For agents
- **Start with [`skill/SKILL.md`](skill/SKILL.md).** It is written for you: tool table,
  rules, and the pitfalls that cost us an afternoon.
- **Schemas:** [`tools.json`](tools.json) (MCP `inputSchema` format, regenerate with
  `python -m unistudy manifest > tools.json`).
- **CLI fallback:** `python -m unistudy <tool> key=value ...`, JSON on stdout, errors on stderr.
- **Extend:** add one decorated function in [`unistudy/tools.py`](unistudy/tools.py). It shows up
  in the CLI, the MCP server and the manifest at once. See [`AGENTS.md`](AGENTS.md).

## Tools
| Tool | Source | Auth |
|---|---|---|
| `upcoming`, `courses`, `assignment`, `mirror`, `canvas_get` | Canvas REST | personal access token |
| `login_check`, `page`, `fetch` | any SSO site | logged-in agent browser (CDP) |
| `sharepoint_download`, `programme` | SharePoint, shh.se | logged-in agent browser |
| `timeedit` | TimeEdit | none |
| `pubmed`, `libris` | NCBI E-utilities, Libris | none |

All tested live against SHH on 2026-11-15: 3 Canvas courses mirrored, 21 of 21 programme
courses mapped with kursplan and litteraturlista, MCP handshake and tool calls verified.

## Setup
```bash
git clone https://github.com/vhsgreed/unistudy && cd unistudy
pip install websockets          # only for the browser tools; the rest is stdlib
echo "<canvas token>" > ~/.secrets/CANVAS_TOKEN && chmod 600 ~/.secrets/CANVAS_TOKEN
# browser tools: a separate Chrome profile with CDP, log in once by hand
google-chrome --user-data-dir=$HOME/.config/unistudy-chrome --remote-debugging-port=9233
```
Other schools: `export CANVAS_BASE=https://<school>.instructure.com`.

## Ground rules
Read-only toward the school. No login automation: the human logs in, the agent reads. Course
material is copyrighted; mirrors stay on your disk and out of git (`.gitignore` covers it).

## License
MIT. Do what you like; no warranty.

---

<sub>

**In honour of Xom**, god of chaos, who answers prayers with teleport traps and grants
wishes nobody made. This kit was written in that spirit: a student asked for a deadline
list and an agent mapped six terms of a nursing programme. Xom finds the whole arrangement
very funny. Xom is not done with you.

*Xom is interested. Point your agent at the repo and see what happens.*

</sub>
