# shh-study-kit

Agent-first tooling that lets an AI agent read a student's own course systems:
Canvas (REST API), and SSO-walled sites (SharePoint, Ladok, Inspera, library) through a
logged-in browser over the Chrome DevTools Protocol.

Built for Sophiahemmet Högskola (nursing) and usable at any Canvas school.
The agent instructions live in [`skill/SKILL.md`](skill/SKILL.md); drop that folder into
your agent's skills directory (Hermes, Claude Code, any SKILL.md-aware agent).

## Status
| Part | State |
|---|---|
| Canvas: courses, upcoming, mirror, raw GET | working (tested against SHH, 3 courses) |
| Browser bridge: page, fetch, login-check | written; tested once logged in |
| Ladok, SharePoint, Inspera, library adapters | planned |

## Requirements
Python 3.10+. Canvas part is stdlib only. Browser part needs `websockets` and a Chrome
started with `--remote-debugging-port`.

```bash
echo "<token>" > ~/.secrets/CANVAS_TOKEN && chmod 600 ~/.secrets/CANVAS_TOKEN
python -m shhkit courses
python -m shhkit upcoming
```

No license yet: all rights reserved for now.
