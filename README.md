# unitoolkit

**Give your agent the student's whole university, read-only.** Canvas, SharePoint, Ladok,
TimeEdit, PubMed and Libris, as 17 tools behind one MCP server. Built for nursing students
at Sophiahemmet Högskola. The Canvas half works at any Canvas school.

```bash
claude mcp add unitoolkit -- python3 -m unitoolkit.mcp      # Claude Code
codex mcp add unitoolkit -- python3 -m unitoolkit.mcp       # Codex
# Hermes, Cursor, anything MCP: command `python3 -m unitoolkit.mcp`, cwd = this repo
```

Then ask your agent "what is due this week?" and let it work.

## For agents
- **Start with [`skill/SKILL.md`](skill/SKILL.md).** It is written for you: tool table,
  rules, and the pitfalls that cost us an afternoon.
- **Schemas:** [`tools.json`](tools.json) (MCP `inputSchema` format, regenerate with
  `python -m unitoolkit manifest > tools.json`).
- **CLI fallback:** `python -m unitoolkit <tool> key=value ...`, JSON on stdout, errors on stderr.
- **Extend:** add one decorated function in [`unitoolkit/tools.py`](unitoolkit/tools.py). It shows up
  in the CLI, the MCP server and the manifest at once. See [`AGENTS.md`](AGENTS.md).

## Tools
| Tool | Source | Auth |
|---|---|---|
| `upcoming`, `courses`, `assignment`, `mirror`, `canvas_get` | Canvas REST | personal access token |
| `login_check`, `page`, `fetch` | any SSO site | logged-in agent browser (CDP) |
| `sharepoint_download`, `programme` | SharePoint, shh.se | logged-in agent browser |
| `ladok_windows` | Ladok studentwebb (subrepo [`vendor/ladok-vakt`](vendor/ladok-vakt)) | logged-in agent browser |
| `timeedit` | TimeEdit | none |
| `pubmed`, `libris` | NCBI E-utilities, Libris | none |

All tested live against SHH on 2026-11-15: 3 Canvas courses mirrored, 21 of 21 programme
courses mapped with kursplan and litteraturlista, MCP handshake and tool calls verified.

## Subrepo: ladok-vakt

[`vendor/ladok-vakt`](vendor/ladok-vakt) is a git subtree of
[ladok-vakt](https://github.com/vhsgreed/ladok-vakt): mejl när Ladok-anmälan öppnar
(email alerts for registration windows). It is here because a Canvas mirror answers
"what is due" and Ladok answers "what can I still register for", and a student needs
both. Sync it with:

```bash
git subtree pull --prefix=vendor/ladok-vakt https://github.com/vhsgreed/ladok-vakt main --squash
```

The `ladok_windows` tool is a thin read-only wrapper around it. Run the watcher
itself (with mail and cron) from the standalone repo.

## Setup
```bash
git clone https://github.com/vhsgreed/unitoolkit && cd unitoolkit
pip install websockets          # only for the browser tools; the rest is stdlib
echo "<canvas token>" > ~/.secrets/CANVAS_TOKEN && chmod 600 ~/.secrets/CANVAS_TOKEN
# browser tools: a separate Chrome profile with CDP, log in once by hand
google-chrome --user-data-dir=$HOME/.config/unitoolkit-chrome --remote-debugging-port=9233
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
