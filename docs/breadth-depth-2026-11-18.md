# unitoolkit: breadth and depth

2026-11-18. Measured on this machine against live systems. Every number below was produced by
running the tool, not read off a design document. Where something was not verified, it says so.

Code state: 38 registered tools, ~4 640 lines in `unitoolkit/*.py`, 89 offline tests in 11 files
(green), `unitoolkit` 0.7.0. Both Canvas gaps below were closed the same day this report was written. The announcements reader below was added while writing this report,
after the gap turned up in testing.

## Breadth: eight systems, one server

| System | Reach | Depth today | Last verified |
|---|---|---|---|
| **Canvas** | any Canvas school (token) | courses, modules, assignments with rubric, submissions, files, pages (via module items, the index is off at SHH), disk mirror, text extraction with OCR, full-text search over course + documents, endpoint surface probe | 2026-11-18 09:38, token expires 2026-12-14 |
| **Databases (EZproxy)** | 9 subscription databases + 6 free sources | 4 searchable: CINAHL, PsycInfo, Cochrane, Web of Science. 5 open-only: JCR, Sage Research Methods, Nationalencyklopedin, Författningshandbok, PubMed-via-EZproxy. Full text by DOI where the publisher is proxied | 2026-11-18 09:41 (EZproxy admits as Sophiahemmet) |
| **Library (Koha)** | catalogue + own account | search, per-copy availability, item detail, loans, holds, fines (Koha 26 table ids) | 2026-11-18 10:12, 6 loans read after a full form login (80 s) |
| **Ladok** | registration windows | windows with days to open/close, 4 live. No results, no ISP, no tillgodoräknande status | 2026-11-18 09:39 (heal 7 s, windows=4) |
| **TimeEdit** | any course code, group, room | schedule for any period, bookable rooms, free gaps with the booking rules applied | 2026-11-18 09:40 (H26SK, 09:00–12:00 seminar) |
| **SharePoint** | SHH share links | real file bytes through the logged-in browser | 2026-11-17 |
| **Citations** | DOI registry | APA 7 from doi.org content negotiation (Crossref, DataCite), English and Swedish | 2026-11-17 |
| **Agent surface** | MCP, CLI, JSON manifest | 11 core tools by default, 38 with `UNITOOLKIT_TOOLS=all` | 2026-11-18 |

Depth means: how far past "it opens" a reader goes. Four of the nine databases are searchable from
inside the agent; the other five open correctly but have to be searched by hand, which is the
honest line between breadth and depth here.

## Depth: what is actually deep

**Canvas is the deepest.** It mirrors a course to disk, extracts text from the PDFs once (with
OCR for scans), and searches the whole thing locally, so a question about a rubric or a syllabus
paragraph is answered without another API call. The submission status is live, which is what makes
`today` trustworthy.

**Full text works where the library is proxied.** Verified on four paywalled articles: Wiley
(9 pages, 9 pages), Elsevier (11 pages), Cochrane review (188 pages). Captured at the network
layer on a navigation, not by a script download, because publishers answer the latter with a
viewer or a bot check. De Gruyter is not proxied and says so. Capped at 20 downloads an hour, a
licence rule: systematic downloading gets the institution's proxy blocked, which would take the
databases from every student at the school.

**Searching the databases took the longer work.** EBSCO reads a space-separated URL query as one
phrase, so bare words are AND-joined. Cochrane ignores Enter and renders its box late, and keeps
the previous search in the session. Web of Science takes JSON in the URL, which skips the Angular
form.

**TimeEdit is deeper than the timetable.** A saved guide link is frozen to the week it was made
for; the reader asks for any period, and `free_rooms` applies the school's own booking rules
(Mon–Fri 08–20, three days ahead, max four hours) rather than guessing.

**`doctor` is the depth that pays off in maintenance.** Six checks, each failing row carrying the
human fix, and the Canvas token expiry read from Canvas rather than from a note on disk.

## Depth: where it stops

Ordered by what it costs Karl, not by how hard it is.

1. ~~**Koha login is unreliable.**~~ Fixed 2026-11-18 10:12, and the diagnosis was the opposite of
   a lockout: `LOGIN_JS` carried a Python comment (`# Koha ignores the POST without it`) inside
   the JavaScript. The script threw, Chrome DevTools answered with a JS exception, `eval()`
   returned `None`, and `None` had been coded as "the form submitted and the page navigated" -
   so no request was ever sent and the login reported "no answer" for a day and a half. One
   character (`#` to `//`) and it logs in: 6 loans, 80 s round trip. `eval()` now keeps the JS
   exception and `_form_login` returns it as `js error: ...`; a test forbids Python comments in
   the JS constants. Remaining depth limit: the round trip is slow because it goes through the
   residential proxy, so `today` keeps the library opt-in.
2. **Ladok is windows only.** No results, no certificates, no ISP, no tillgodoräknande. The ISP
   question (worth 120 hp of credit transfer) cannot be answered by the tool yet.
3. **Ladok's session does not persist.** Every Ladok and SWAMID cookie is a session cookie
   (`JSESSIONID`, `_shibsession_*`, `__Host-shib_idp_session`), plus a server-side idle timeout:
   the session was live at 08:24 and gone at 09:39 with Chrome never restarting. So every
   unattended run must heal first. That now works (7 s, twice today), but it is a login per run,
   not a stored session.
4. **Five databases open but cannot be searched** from the agent. JCR, Sage Research Methods,
   Nationalencyklopedin and Författningshandbok are the realistic candidates (they have simple
   search boxes); PubMed-EZproxy is covered by the free E-utilities API instead.
5. ~~**Canvas announcements have no reader.**~~ Fixed 2026-11-18 while testing for this report:
   `discussion_topics` without `only_announcements=True` returns an empty list, which is why the
   first check said "no announcements" when there were 12, newest 2026-11-15. There is now an
   `announcements` tool (title, body, author, date) and `today` carries the last 14 days.
   **Discussions proper** got their reader the same afternoon: `discussions` returns topics with
   reply count, `read_state` and last reply, and `today` now lists unread threads inside the
   window (live: empty for PRY, which has none, and one read thread in 2415).
6. **Free sources are wired as constants, not as tools.** DiVA, DOAJ, ERIC and SwePub exist in
   `databases.FREE` but only PubMed and Libris have callable tools.
7. **Not on PyPI.** Wheel built and installed into a clean 3.12 venv, but publishing needs a token
   that does not exist yet.
8. **The smoke test does not cover the two things that break silently**: the Koha login (deliberate,
   lockout risk) and the Ladok heal (covered by the daily watcher instead). 10 of 10 readers pass
   as of 2026-11-18 08:00.

## What I would do next, in order

1. ~~Koha login.~~ Done: it was a comment in JavaScript, not the library refusing us. Next: keep
   the attempt cap (one login per call, no retry loop) and see whether the chain proxy can be
   dropped in favour of the school's EZproxy-style session.
2. ~~Canvas discussions reader.~~ Done 2026-11-18.
3. Ladok: results and ISP. Results are the same proxy pattern as windows; ISP may not exist as a
   student endpoint at all, which is itself the answer Karl needs for the course admin.
4. Readers for Författningshandbok and Nationalencyklopedin: both have plain search boxes and are
   the two the nursing programme actually cites.
5. Ladok results, then the ISP question for the course admin: whether a student endpoint exists
   at all is itself the answer Karl needs for credit transfer.
5. Publish to PyPI once the token exists.
