---
name: unitoolkit
description: "Use when an agent must read a student's Canvas, Ladok, library or databases."
version: 0.3.0
author: vhsgreed
license: MIT
metadata:
  hermes:
    tags: [canvas, instructure, ladok, sharepoint, timeedit, pubmed, libris, cinahl, cochrane, web-of-science, apa, citations, ezproxy, koha, student, sso, mcp]
---

# unitoolkit

Read access to a university student's own course systems, plus open research APIs.
Built at Sophiahemmet Högskola (SHH, Stockholm, nursing). Canvas parts work at any
Canvas school (`CANVAS_BASE`).

## When to Use
- "What is due?", "what does assignment X ask?", "mirror my course", "get the kursplan".
- A page sits behind school SSO (SharePoint, Ladok, Inspera) and the student runs the
  agent browser.
- "Find me sources on X", "give me the APA reference", "get me the full text": see
  **Sources and citations** below.
- "Is the book on the shelf", "what have I borrowed, when is it due": the `koha_*` tools.
- Verifying a reference: PubMed for articles, Libris for Swedish books and ISBNs.
- Skip for public pages you can fetch directly.

## Fastest path
MCP: `python -m unitoolkit.mcp` (stdio). It **lists 8 question-shaped tools** by default and
keeps the rest callable by name; `UNITOOLKIT_TOOLS=all` lists everything. CLI:
`python -m unitoolkit <tool> k=v ...`, JSON on stdout. `tools.json` holds every schema.

| Student asks | Call |
|---|---|
| "What do I need to do?" | `today` (deadlines not yet submitted, new announcements, unread discussions, Ladok windows, timetable; `library=true` adds loans) |
| "Did a teacher post something?" / "Anything waiting in a group discussion?" | `announcements` / `discussions unread_only=true` |
| "Something is broken" / any tool failed | `doctor`: every session with its fix |
| "Find me sources on X" | `find_sources` |
| "Get me the article" | `db_fulltext doi=` |
| "What does the assignment ask?" | `assignment` / `search course= query=` |
| "Is the book in?" / "When are my books due?" | `library query=` / `library` |
| "When is the seminar?" / "What's on this week?" | `schedule query=1SPRY2` or `query=H26SK kind=group` |
| "Is there a room free to study in tomorrow?" | `free_rooms date=` |

The full registry, for when the core is not enough:

| Tool | Needs | Returns |
|---|---|---|
| `upcoming` | token | everything due from today, with `submitted` |
| `courses` | token | id, code, name, term |
| `assignment course= assignment=` | token | task text + rubric rows as plain text |
| `search course= query=` | token | full-text search inside a course: pages, assignments with rubrics, modules, discussions, files. Operators `"phrase" -exclude +required OR type: filetype: intitle: before: after:`, plus `regex`/`fuzzy`/`whole_word`. `source=auto` uses the local mirror when one exists, `api` goes live |
| `docs course=` | local binaries | download a course's files once and cache their text (pdf via pdftotext, docx/pptx/xlsx via zipfile, html, images). `ocr=true` adds tesseract for image-only files; the text cache is keyed by file id + updated_at, so a reindex is free |
| `search ... include_docs=true` | token/local | also search the cached document text; hits come back as `type=fildok` with `page` numbers |
| `serve` | none | `python -m unitoolkit serve` starts the local deep-index HTTP endpoint (127.0.0.1:8791): `/health`, `/search?course=&q=&docs=1`, `/docs`, `/index`. Reachable from another machine through `tailscale serve --bg 8791` |
| `mirror course=<id or all>` | token | modules, pages, assignments, announcements, files on disk |
| `surface course=` | token | probe which endpoints a student session can reach: status, item count, blocked ones (pages index, quizzes, rubrics) |
| `serve` | none | CLI subcommand, not a tool: `python3 -m unitoolkit serve` (127.0.0.1:8791) exposes /health, /search, /docs, /index to the browser plugin and to other machines via `tailscale serve --bg 8791` |
| `canvas_get path= params=` | token | any Canvas GET, paginated |
| `login_check` | browser | logged-in state per SSO site |
| `page url=` | browser | rendered text of any walled page |
| `fetch url= origin=` | browser | JSON from internal APIs with the site's cookies |
| `sharepoint_download share_url= out=` | browser | file from a SharePoint share link |
| `programme` | browser | all 21 SHH nursing courses, kursplan + litteraturlista on disk |
| `timeedit url=` | none | bookings from a TimeEdit link |
| `pubmed query= max=` | none | PMID, title, journal, year, DOI, authors |
| `libris query= max=` | none | title, author, year, publisher, ISBN |

## Sources and citations (the usual student request)
One sign-in covers all of it: the school's EZproxy (`shh.idm.oclc.org`) hands over to the
Microsoft Entra session in the agent browser, and then every subscribed database admits the
agent silently. Run `db_status` first; if it says `authenticated: false`, ask the human to
sign in once, do not try to log in.

1. `find_sources query="..."` searches CINAHL, PubMed and Cochrane at once (add `psycinfo`,
   `wos` with `databases=[...]`), dedupes by DOI, and returns each hit with an **APA 7
   reference built from the publisher's DOI metadata** plus a `relevance` score.
2. Read before citing: `db_detail url=<record url>` gives the abstract; `db_fulltext doi=...`
   saves the PDF and returns its text when the school subscribes.
3. `apa doi=...` for a single reference; `lang=sv` for svensk APA wording.

| Tool | Returns |
|---|---|
| `find_sources query= databases= limit=` | deduped hits across databases, each with `apa`, `doi`, `relevance`, `database`, `also_in` |
| `db_search database= query= limit=` | one database: cinahl, psycinfo, cochrane, wos. Citation fields + record URL |
| `db_detail url=` | abstract, DOI, authors, source line of one record |
| `db_fulltext doi=` | PDF saved under `~/.local/share/unitoolkit/fulltext`, page count, text |
| `apa doi= lang=` | APA 7 reference string |
| `db_status database=` / `db_open database=` / `db_list` | session check / open a database in the browser / what exists |
| `koha_search q=` / `koha_item biblionumber=` / `koha_loans` | library catalogue, copies on the shelf, own loans with due dates |

Rules for this part:
- Never present a source the student has not seen as read. Give the abstract or full text and
  let the student decide; an APA line is not evidence the article says what you claim.
- APA from the registry keeps the publisher's title case; APA 7 wants sentence case for article
  titles. Point it out, do not lowercase by rule (acronyms and proper nouns break).
- `db_fulltext` is capped at 20 downloads an hour. Do not work around it: publishers block the
  whole school's proxy for bulk downloading.
- "No subscription route" (e.g. De Gruyter) means order it through the library (fjärrlån), and
  an open-access copy, when one exists, is returned in the error.

## Setup (the human does this once)
1. Canvas: Account > Settings > New access token -> `~/.secrets/CANVAS_TOKEN` (0600).
   Note the expiry date; tokens die silently.
2. Browser: `google-chrome --user-data-dir=$HOME/.config/unitoolkit-chrome --remote-debugging-port=9233`
   (flatpak: `flatpak run com.google.Chrome ...`), then log in by hand. Never the default
   profile: Chrome refuses CDP there, and it is the human's, not yours.

## Health
- `doctor` checks the Canvas token (and reads its real expiry date from Canvas), the agent
  browser, Ladok, the database proxy, the library plumbing and the timetable link. Each failing
  row says what the human has to do.
- `scripts/smoke.py` runs one real query per reader and is silent when all pass; run it weekly.
  It never logs in to the library: failed library logins count against the student's account.

## Rules
- Read only. Never submit, post, enrol, or change settings unless the human asks for that
  exact action.
- Never type a password. If `login_check` or `db_status` says false, ask the human to sign in
  once in the agent browser. (Ladok re-login in `vendor/ladok-vakt` is opt-in and uses Chrome's
  own saved-password autofill only.)
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
- A saved TimeEdit link covers only the window it was made for; date-range parameters on it
  return nothing. Don't use saved links: `schedule` searches the public guide (sid=5) by course
  code or student group and passes the period itself (`p=YYYYMMDD.x,YYYYMMDD.x`). Set
  `UNITOOLKIT_GROUP` (e.g. H26SK) and `today` carries the cohort's week.
- TimeEdit object types are 5=Kurs, 21=Kurskod, 6=Grupp, 4=Sal, 8=Ämne, 9=Undervisningsform. The
  numbers on the page's filter boxes (11, 19, 20, ...) are room attributes, not object types.
  Course names are not indexed: search the code.
- "Boka salar" (ri1Q3, sid=8) needs the school SSO, but only to learn which rooms students may
  book; their occupancy is in the public guide. Query rooms one at a time: a combined query
  returns some rows whose room column is empty and cannot be attributed. `free_rooms` applies
  the booking rules (Mon-Fri 08-20, 3 days ahead, max 4 h) and never books.
- The SHH library host (`biblioteket.shh.se`) drops port 443 from some home IPs and answers
  plain HTTP clients with Cloudflare 429. Only a real browser behind a proxy reads it, and the
  proxy exit must be **sticky**: Koha binds the session to the client IP ("You are logging from
  a different IP address"), so a rotating proxy fails every login.
- Koha's login form needs `login_op=cud-login`, a CSRF token from a non-cached page load, and the
  card number (personnummer) + 4-digit pinkod, not the SSO account. Its loans table is
  `#checkoutst` (Koha 26), not `#checkouts_table`.
- EBSCO (CINAHL, PsycInfo) reads a space-separated URL query as **one phrase**: "delirium older
  adults" returned 1 hit, the same words with AND returned 403. `db_search` joins bare words with
  AND; quotes and explicit operators pass through.
- EBSCO result cards: the source line can sit on the authors' line (`By:...In:Journal, 2025`),
  and an ISSN in brackets reads like a year. Parse `In:` out first, strip the ISSN.
- Cochrane keeps the previous search in the session, so the result page can still show the last
  query for a few seconds. Wait until the result links' `highlightAbstract` carries the new
  terms before reading.
- Cochrane ignores Enter in its search box and the URL parameters on `/search`: set the value
  with the native setter, then click `.searchByBtn`. Its default order puts off-topic reviews
  first for multi-word queries, hence the `relevance` score.
- Web of Science takes the query as JSON in the URL (`general-summary?queryJson=...`), which
  skips the Angular form. Its cards carry no DOI; `find_sources` asks Crossref by title and
  only keeps a match with >= 80% word overlap.
- Some DOI redirects break through EZproxy (Cochrane lands on a Chrome error page). Resolve the
  DOI outside the proxy, then proxy the publisher URL.
- Publishers answer a script `fetch()` of a PDF with a viewer or a bot check; capture the PDF at
  the network layer (CDP `Fetch` interception) on a top-level navigation instead.
- Crossref throttles bursts with 429. Send a `mailto` in the User-Agent (polite pool) and back
  off; one throttled lookup must not sink a multi-database search.
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
