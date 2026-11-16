---
name: unistudy
description: "Use when an agent must read a student's Canvas, SharePoint, Ladok or TimeEdit."
version: 0.2.0
author: vhsgreed
license: MIT
metadata:
  hermes:
    tags: [canvas, instructure, ladok, sharepoint, timeedit, pubmed, libris, student, sso, mcp]
---

# unistudy

Read access to a university student's own course systems, plus open research APIs.
Built at Sophiahemmet Högskola (SHH, Stockholm, nursing). Canvas parts work at any
Canvas school (`CANVAS_BASE`).

## When to Use
- "What is due?", "what does assignment X ask?", "mirror my course", "get the kursplan".
- A page sits behind school SSO (SharePoint, Ladok, Inspera) and the student runs the
  agent browser.
- Verifying a reference: PubMed for articles, Libris for Swedish books and ISBNs.
- Skip for public pages you can fetch directly.

## Fastest path
MCP: `python -m unistudy.mcp` (stdio). Every tool below is exposed; `tools.json` holds
the schemas. CLI: `python -m unistudy <tool> k=v ...`, JSON on stdout.

| Tool | Needs | Returns |
|---|---|---|
| `upcoming` | token | everything due from today, with `submitted` |
| `courses` | token | id, code, name, term |
| `assignment course= assignment=` | token | task text + rubric rows as plain text |
| `search course= query=` | token | full-text search inside a course: pages, assignments with rubrics, modules, discussions, files. Operators `"phrase" -exclude +required OR type: filetype: intitle: before: after:`, plus `regex`/`fuzzy`/`whole_word`. `source=auto` uses the local mirror when one exists, `api` goes live |
| `docs course=` | local binaries | download a course's files once and cache their text (pdf via pdftotext, docx/pptx/xlsx via zipfile, html, images). `ocr=true` adds tesseract for image-only files; the text cache is keyed by file id + updated_at, so a reindex is free |
| `search ... include_docs=true` | token/local | also search the cached document text; hits come back as `type=fildok` with `page` numbers |
| `serve` | none | `python -m unistudy serve` starts the local deep-index HTTP endpoint (127.0.0.1:8791): `/health`, `/search?course=&q=&docs=1`, `/docs`, `/index`. Reachable from another machine through `tailscale serve --bg 8791` |
| `mirror course=<id or all>` | token | modules, pages, assignments, announcements, files on disk |
| `canvas_get path= params=` | token | any Canvas GET, paginated |
| `login_check` | browser | logged-in state per SSO site |
| `page url=` | browser | rendered text of any walled page |
| `fetch url= origin=` | browser | JSON from internal APIs with the site's cookies |
| `sharepoint_download share_url= out=` | browser | file from a SharePoint share link |
| `programme` | browser | all 21 SHH nursing courses, kursplan + litteraturlista on disk |
| `timeedit url=` | none | bookings from a TimeEdit link |
| `pubmed query= max=` | none | PMID, title, journal, year, DOI, authors |
| `libris query= max=` | none | title, author, year, publisher, ISBN |

## Setup (the human does this once)
1. Canvas: Account > Settings > New access token -> `~/.secrets/CANVAS_TOKEN` (0600).
   Note the expiry date; tokens die silently.
2. Browser: `google-chrome --user-data-dir=$HOME/.config/unistudy-chrome --remote-debugging-port=9233`
   (flatpak: `flatpak run com.google.Chrome ...`), then log in by hand. Never the default
   profile: Chrome refuses CDP there, and it is the human's, not yours.

## Rules
- Read only. Never submit, post, enrol, or change settings unless the human asks for that
  exact action.
- Never automate the SSO login. If `login_check` says false, ask the human to log in.
- Canvas `due_utc` is an upload cutoff in UTC, not a session time. Convert to local time;
  session times come from TimeEdit.
- `submitted: true` means a submission exists, not that it passed.
- Course content is copyrighted and personal. Keep mirrors out of any repo.

## Pitfalls (found the hard way)
- SHH disables the Canvas page index (`/pages` 404 "avaktiverats"). Pages are readable via
  module items; `mirror` takes that route.
- Quiz contents are not exposed to students; use the saved attempt page.
- SHH kursplaner are **HTML** pages (Ladok export), litteraturlistor are PDFs.
- On the shh.se course overview, "Kursplan"/"Kurslitteraturlista" links cannot be paired
  to courses by position: some courses lack links and the pairing shifts. Name each file by
  the course code found **inside** it. Codes are 3 or 4 letters (`2SHS11`, `1SOMV2`).
- Ladok shows its login screen without changing the URL. Check the page text, not only the
  URL, before deciding the human is logged in.
- SharePoint folder REST endpoints return AccessDenied for students. Share links via
  `download.aspx?share=<docid>` work.
- A TimeEdit link covers only the window it was made for; date-range parameters on a
  saved link return nothing. Ask for a new link for a new period.
- The SHH library host (`biblioteket.shh.se`) may be unreachable from off-campus networks.
- Canvas files are behind viewer chrome: `?wrap=1`, `?module_item_id=`, `folder?preview=<id>`.
  Strip to `/files/<id>` and the API serves the file itself.
- Assignments need `include[]=rubric`: without it Canvas returns `rubric: []` and the grading
  criteria are silently missing from any index.
- Offsets for snippets and page numbers must be measured in the **original** text. `fold()`
  collapses whitespace and folds diacritics, so offsets taken in the folded string land on the
  wrong page. `norm_len()` keeps length; locate a phrase by its leading words with `\s+`
  tolerance, since a phrase can match only across a line break.
- A phrase query through the CLI needs `--json`: `parse()` runs `json.loads` on each value, so
  `query="\"two words\""` arrives as an AND of words, not a phrase.
- OCR is opt-in (`ocr=true`) and capped; `pages_total` vs `ocr_pages` plus `ocr_truncated`
  shows whether a book was only partly read.
- Port 8790 on geekom is the agentrecourse site preview; `serve` therefore defaults to 8791.
