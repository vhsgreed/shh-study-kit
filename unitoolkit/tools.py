"""One tool registry for every surface: CLI, MCP server, tools.json manifest.

Each tool is a plain function with a JSON-schema'd signature. Add a tool here and it
appears in `python -m unitoolkit <name>`, in the MCP server, and in `tools.json`.
All tools are read-only toward the school's systems; the only writes are local files.
"""
import json
import pathlib
import urllib.parse
import urllib.request

TOOLS = {}


def tool(name, description, properties=None, required=()):
    def wrap(fn):
        TOOLS[name] = {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": list(required), "additionalProperties": False},
            "fn": fn,
        }
        return fn
    return wrap


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}


# ------------------------------------------------------------- Agent surface
# Question-shaped tools. The MCP server lists these by default (unitoolkit/agent.py: CORE);
# UNITOOLKIT_TOOLS=all lists everything. All tools stay callable by name.

@tool("today",
      "START HERE for 'what do I need to do?'. Everything due in the next N days (default 14) "
      "from every system at once: Canvas deadlines not yet submitted, Ladok registration "
      "windows open now or opening soon, this week's timetable, and (library=true) library "
      "loans due back. "
      "Each section reports its own error, so one dead session never hides the rest.",
      {"days": I, "library": B})
def today(days=14, library=False):
    from . import agent
    return agent.today(days or 14, library=bool(library))


@tool("doctor",
      "Health check of every session the toolkit depends on (Canvas token and its expiry date, "
      "agent browser, Ladok, database proxy, library, timetable link). Each row has the fix, "
      "which is always a human action. Run it when another tool fails. only='canvas,ladok' "
      "limits the checks.",
      {"only": S})
def doctor(only=None):
    from . import agent
    return agent.doctor(only)


@tool("schedule",
      "Timetable for any course code (1SPRY2), student group (H26SK) or room, for any period, "
      "from TimeEdit without a login. kind=code|group|room|subject. start/end as YYYY-MM-DD or "
      "TimeEdit relative form (0.d = today, 4.w = four weeks). Course NAMES are not indexed: "
      "search the code or the group.",
      {"query": S, "kind": S, "start": S, "end": S}, ["query"])
def schedule(query, kind="code", start="0.d", end="14.d"):
    from . import timeedit
    return timeedit.schedule(query, kind or "code", start or "0.d", end or "14.d")


@tool("free_rooms",
      "Which student-bookable rooms are free on a date (default today), as gaps between "
      "bookings within the booking rules (Mon-Fri 08-20, up to 3 days ahead, max 4 h). "
      "Read-only: booking itself is done by the student in TimeEdit.",
      {"date": S, "min_minutes": I})
def free_rooms(date=None, min_minutes=60):
    from . import timeedit
    return timeedit.free_rooms(date or None, min_minutes or 60)


@tool("library",
      "The school library (Koha). With query: catalogue search with availability per copy. "
      "Without: the student's own loans with due dates and days left, holds and fines.",
      {"query": S, "limit": I})
def library(query=None, limit=10):
    from . import agent
    return agent.library(query, limit or 10)


# ------------------------------------------------------------------- Ladok

@tool("ladok_windows",
      "Ladok registration windows (kursregistrering + tentamensanmälan): what opens when, "
      "what closes within N days, with course period. Needs the agent Chrome logged in to "
      "Ladok (CDP :9233); read-only, never registers anything. Subrepo: ladok_vakt/.",
      {"close_days": I})
def ladok_windows(close_days=None):
    """Structured rows, not the watcher's text lines.

    core.run() is the cron path: it returns human lines and turns a missing session into a
    "VARNING" line, which an agent would read as data. Here a fault is an error field, and
    LADOK_AUTOLOGIN=1 (with LADOK_IDP_ENTITY) lets it restore the session once first.
    """
    import sys
    try:
        from ladok_vakt import core              # installed wheel ships it as a package
    except ImportError:                          # source checkout: the vendored subtree
        _vendor = pathlib.Path(__file__).resolve().parents[1] / 'vendor' / 'ladok-vakt'
        sys.path.insert(0, str(_vendor))
        from ladok_vakt import core
    cfg = core.Config()
    if close_days is not None:
        cfg.close_days = int(close_days)
    try:
        try:
            uid, reg_xml, exam_xml = core.fetch(cfg)
        except core.Fault:
            if not cfg.autologin:
                raise
            from ladok_vakt.login import login
            login(cfg)
            uid, reg_xml, exam_xml = core.fetch(cfg)
    except core.Fault as exc:
        return {"error": str(exc),
                "hint": "sign in to Ladok once in the agent browser (CDP :9233), or set "
                        "LADOK_AUTOLOGIN=1 and LADOK_IDP_ENTITY for the opt-in re-login"}
    rows = core.pending_actions(core.parse_registrations(reg_xml) + core.parse_exams(exam_xml))
    for r in rows:
        r["days_to_open"] = core.days_until(r.get("reg_opens"))
        r["days_to_close"] = core.days_until(r.get("reg_closes"))
    return {"windows": rows, "urgent": core.urgent(rows, cfg.close_days)}


# ---------------------------------------------------------------- Canvas (token)

@tool("courses", "List the student's Canvas courses: id, code, name, term.")
def courses():
    from . import canvas
    return [{"id": c["id"], "code": c.get("course_code"), "name": c.get("name"),
             "term": (c.get("term") or {}).get("name")} for c in canvas.courses()]


@tool("discussions",
      "Discussion topics across courses with read state and reply count, so a group discussion "
      "that is waiting for an answer does not go unnoticed. Announcements are separate.",
      {"course": {**I, "description": "Canvas course id; omitted: every active course"},
       "days": {**I, "description": "Only topics replied to in the last N days; omitted: all"},
       "unread_only": {**B, "description": "Skip topics already read"}})
def discussions(course=None, days=None, unread_only=False):
    from . import canvas
    return canvas.discussions(course=course, days=days, unread_only=unread_only)


@tool("announcements",
      "Course announcements, newest first, with body text. Canvas hides these from the plain "
      "discussion list, so this is the only way to see what a teacher posted.",
      {"course": {**I, "description": "Canvas course id; omitted: every active course"},
       "days": {**I, "description": "How far back, default 30"}})
def announcements(course=None, days=30):
    from . import canvas
    return canvas.announcements(course=course, days=days)


@tool("canvas_file",
      "Download one Canvas file by its id (lecture slides, an article attached to an assignment, a "
      "rubric) to a local folder. Use the API route, not the /download web path: without a Canvas "
      "browser session the web path redirects to the school homepage and yields an empty file. "
      "Ids scraped from an assignment description may not all be course files; those 404.",
      {"course": I, "file_id": I, "out": S}, ["course", "file_id"])
def canvas_file(course, file_id, out=None):
    from . import canvas
    return canvas.save_file(course, file_id, out or "~/courses/downloads")


@tool("upcoming", "Everything due from today across all Canvas courses: title, course, "
      "due (UTC), submitted. Start here for 'what is due?'.")
def upcoming():
    from . import canvas
    out = []
    for i in canvas.upcoming():
        sub = i.get("submissions")
        out.append({"course": i.get("context_name"), "type": i.get("plannable_type"),
                    "title": ((i.get("plannable") or {}).get("title") or "").strip(),
                    "due_utc": i.get("plannable_date"),
                    "submitted": sub.get("submitted") if isinstance(sub, dict) else None})
    return out


@tool("mirror", "Download a whole Canvas course (modules, pages, assignments with rubrics, "
      "announcements, discussions, files) to disk. course='all' mirrors every course.",
      {"course": S, "out": S, "files": B}, ["course"])
def mirror(course, out="~/courses/canvas", files=True):
    from . import canvas
    base = pathlib.Path(out).expanduser()
    ids = [c["id"] for c in canvas.courses()] if str(course) == "all" else [int(course)]
    return [canvas.mirror(i, base / str(i), files=files) for i in ids]


@tool("canvas_get", "Raw Canvas REST GET with pagination, e.g. path='courses/123/assignments'. "
      "params is a JSON object of query parameters.", {"path": S, "params": {"type": "object"}}, ["path"])
def canvas_get(path, params=None):
    from . import canvas
    return canvas.get(path, **(params or {}))


@tool("assignment", "One Canvas assignment with description and rubric as plain text.",
      {"course": I, "assignment": I}, ["course", "assignment"])
def assignment(course, assignment):
    import html
    import re
    from . import canvas
    a = canvas.get(f"courses/{course}/assignments/{assignment}")
    desc = html.unescape(re.sub(r"<[^>]+>", " ", a.get("description") or ""))
    return {"name": a.get("name"), "due_utc": a.get("due_at"), "points": a.get("points_possible"),
            "description": re.sub(r"\s+", " ", desc).strip(),
            "rubric": [{"criterion": r.get("description"), "long": r.get("long_description"),
                        "ratings": [x.get("description") for x in r.get("ratings", [])]}
                       for r in a.get("rubric") or []]}


# ------------------------------------------------------- SSO browser (CDP)

@tool("login_check", "Which SSO sites (Canvas, SharePoint, Ladok) the agent browser is logged in to.")
def login_check():
    from . import browser
    sites = ("https://shh.instructure.com/", "https://shh365.sharepoint.com/",
             "https://www.student.ladok.se/student/app/studentwebb/")
    out = {}
    for u in sites:
        ok, landed = browser.logged_in(u)
        out[u] = {"logged_in": ok, "landed": landed[:120]}
    return out


@tool("page", "Rendered text of any page, read through the student's logged-in browser "
      "(SharePoint, Ladok, Inspera, intranet).", {"url": S}, ["url"])
def page(url):
    from . import browser
    return browser.page(url)


@tool("fetch", "JSON/text fetch executed inside `origin` so the site's own cookies apply. "
      "Use for internal APIs (Ladok student API, SharePoint REST).", {"url": S, "origin": S}, ["url", "origin"])
def fetch(url, origin):
    from . import browser
    return browser.fetch(url, origin)


@tool("sharepoint_download", "Download a SharePoint share link (/:b:/ or /:u:/ style) to a "
      "local file; returns path, size, type.", {"share_url": S, "out": S}, ["share_url"])
def sharepoint_download(share_url, out="~/courses/downloads"):
    from . import sharepoint
    return sharepoint.save_share(share_url, out)


# --------------------------------------------------- OneDrive / Office 365 (read only)

@tool("onedrive", "The student's OneDrive and files shared with them in the school's Office 365, "
      "read only, via the signed-in agent browser. action=status|ls|shared|recent|search|download. "
      "ls takes path= (own drive) or drive_id+item_id (any folder); shared takes query= (name or "
      "owner filter); search takes query=; download takes name= or drive_id+item_id, plus out= "
      "(dir, default ~/courses/onedrive; never overwrites).",
      {"action": S, "path": S, "query": S, "name": S, "drive_id": S, "item_id": S, "out": S},
      ["action"])
def onedrive(action, path="", query=None, name=None, drive_id=None, item_id=None,
             out="~/courses/onedrive"):
    from . import onedrive as od
    try:
        if action == "status":
            return od.status()
        if action == "ls":
            return {"items": od.ls(path, drive_id, item_id)}
        if action == "shared":
            return {"items": od.shared(query)}
        if action == "recent":
            return {"items": od.recent()}
        if action == "search":
            return od.search(query or "")
        if action == "download":
            return od.download(out, name, drive_id, item_id)
    except (od.SessionGone, LookupError, ValueError, RuntimeError) as e:
        return {"error": str(e)}
    return {"error": f"unknown action {action!r}: status|ls|shared|recent|search|download"}


# ---------------------------------------------------------- Course search

@tool("search", "Full-text search inside one course: pages, assignments with rubrics, "
      "modules, discussions, files. Operators: \"phrase\", -exclude, +required, OR, "
      "type:sida|uppgift|fil|modul|diskussion, filetype:pdf, intitle:word, before:YYYY-MM-DD, "
      "after:YYYY-MM-DD, plus regex/fuzzy toggles. source='mirror' searches a local mirror "
      "(fast, may be stale), 'api' fetches live from Canvas, 'auto' prefers the mirror. "
      "include_docs=true also searches the document text built by the docs tool.",
      {"course": S, "query": S, "source": S, "regex": B, "fuzzy": B, "whole_word": B,
       "case_sensitive": B, "include_docs": B, "limit": I}, ["course", "query"])
def search(course, query, source="auto", regex=False, fuzzy=False, whole_word=False,
           case_sensitive=False, include_docs=False, limit=25):
    import pathlib as _p
    from . import docs as docs_mod
    from . import search as s
    mirror = _p.Path("~/courses/canvas").expanduser() / str(course)
    use_mirror = source == "mirror" or (source == "auto" and (mirror / "pages").exists())
    idx = s.index_from_mirror(mirror) if use_mirror else s.build_index(course)
    if include_docs:
        extra = docs_mod.load(course)
        # a file with extracted text should appear once, as the text hit, not also as a name
        with_text = {(d.get("title") or "").lower() for d in extra}
        idx = {**idx, "docs": [d for d in idx["docs"]
                               if not (d["type"] == "fil" and (d.get("title") or "").lower() in with_text)]
               + extra}
    res = s.run(idx, query, case_sensitive=case_sensitive, regex=regex, fuzzy=fuzzy,
                whole_word=whole_word, limit=limit)
    hits = []
    for h in res.get("hits", []):
        row = {"type": h["type"], "title": h["title"], "score": h["score"],
               "anchor": h.get("anchor", ""), "snippet": h["snippet"], "url": h["url"]}
        if h["type"] == "fildok":
            # page numbers matter for citations, and pdftotext keeps them as \f
            row["page"] = docs_mod.page_of(h.get("body") or "", h.get("at") or 0)
            row["ocr_pages"] = h.get("ocr_pages") or 0
        hits.append(row)
    out = {"source": idx["source"], "documents": len(idx["docs"]),
           "total": res.get("total", 0), "hits": hits}
    if res.get("error"):
        out["error"] = res["error"]
    if not out["total"] and not regex:
        alt = s.suggest(idx, query)
        if alt:
            out["did_you_mean"] = alt
    return out


# ---------------------------------------------------------- Document text

@tool("docs", "Download a course's files once, extract their text (pdf, docx, pptx, xlsx, html, "
      "images) and cache it keyed by file id + updated_at. Bytes are discarded, text is kept. "
      "ocr=true adds tesseract for documents with no usable text layer; it is slow, so it is "
      "not part of a normal index run.",
      {"course": S, "ocr": B, "force": B, "max_files": I, "ocr_max_pages": I,
       "only_contains": S, "links": B}, ["course"])
def docs(course, ocr=False, force=False, max_files=None, ocr_max_pages=60, only_contains=None,
         links=False):
    from . import docs as d
    out = {"files": d.build(course, ocr=ocr, force=force, max_files=max_files,
                            ocr_max_pages=ocr_max_pages, only_contains=only_contains)}
    if links:
        # shares are not in /files at all; they live only as links inside pages
        out["links"] = d.resolve_links(course, allow_ocr=ocr)
    return out


# ---------------------------------------------------------- Session surface

@tool("surface", "Probe which Canvas endpoints a student session can reach, before building "
      "anything on assumptions. Returns per-endpoint status, item count and payload size, "
      "plus a summary of what is blocked (pages index, quizzes and rubrics are commonly off).",
      {"course": S}, ["course"])
def surface(course):
    from . import surface as s
    rows = s.probe_course(course)
    return {"course": course, "summary": s.summary(rows),
            "endpoints": [{"label": r["label"], "status": r["status"], "count": r["count"],
                           "bytes": r["bytes"], "note": r["note"]} for r in rows]}


# ---------------------------------------------------------- Programme map

@tool("programme", "Map the whole SHH nursing programme: every course with its kursplan and "
      "kurslitteraturlista downloaded and verified by the course code inside each file.",
      {"out": S})
def programme(out="~/courses/programme"):
    from . import programme as pr
    cs, docs = pr.build(out)
    md = pr.report(cs)
    (pathlib.Path(out).expanduser() / "KURSKARTA.md").write_text(md)
    return {"courses": [{"code": c["code"], "name": c["name"], "kursplan": c.get("kursplan_file"),
                         "litteratur": c.get("litteratur_file")} for c in cs],
            "errors": [d for d in docs if d.get("error")]}


# ---------------------------------------------------------- TimeEdit (public)

@tool("timeedit", "Bookings from a TimeEdit schedule link (the .html URL the student copies "
      "from TimeEdit). Returns date, start, end and the booking columns. Note: a link covers "
      "only the period it was created for.", {"url": S}, ["url"])
def timeedit(url):
    j = url.split("?")[0].rsplit(".", 1)[0] + ".json"
    with urllib.request.urlopen(j, timeout=30) as r:
        d = json.load(r)
    head = d.get("columnheaders", [])
    return [{"date": x["startdate"], "start": x["starttime"], "end": x["endtime"],
             **{(head[k] if k < len(head) else f"col{k}") or f"col{k}": v
                for k, v in enumerate(x.get("columns", []))}}
            for x in d.get("reservations", [])]


# -------------------------------------------------------------------- Koha

@tool("koha_search",
      "Search the SHH library catalogue (Koha, biblioteket.shh.se) for course books and "
      "literature: title, biblionumber and availability per hit. Read-only. Goes through the "
      "proxied agent browser because the library's Cloudflare refuses plain HTTP clients.",
      {"q": S, "limit": I}, ("q",))
def koha_search(q, limit=10):
    from . import koha
    rows = koha.search(q, limit=limit or 10)
    return {"query": q, "hits": len(rows), "results": rows}


@tool("koha_item",
      "One catalogue record by biblionumber: item table (placering, status, item type) and the "
      "record summary (publisher, edition, language). Read-only.",
      {"biblionumber": S}, ("biblionumber",))
def koha_item(biblionumber):
    from . import koha
    return koha.availability(biblionumber)


@tool("koha_loans",
      "Your own Koha account: loans with due dates and reservations with queue position. Needs "
      "~/.secrets/KOHA_USER and ~/.secrets/KOHA_PIN (you create them; never printed). Read-only: "
      "no renewal, no reservation changes.",
      {})
def koha_loans():
    from . import koha
    return koha.loans()


@tool("koha_open_login",
      "Open the library account page in the proxied Chrome window and report whether the "
      "session is signed in. For the one-time manual sign-in; after that koha_loans reads "
      "the account from the session cookie.",
      {})
def koha_open_login():
    from . import koha
    return koha.open_login()


@tool("koha_wait_login",
      "Wait until the library account page shows a signed-in session, for the one-time manual "
      "sign-in in the proxied Chrome window. After that koha_loans needs no credentials.",
      {"timeout": I})
def koha_wait_login(timeout=300):
    from . import koha
    return {"result": koha.wait_for_login(timeout=timeout or 300)}


@tool("koha_status",
      "Diagnostics for the Koha path: whether the proxied browser and the local chain proxy are "
      "up, and whether library credentials are on file.",
      {})
def koha_status():
    from . import koha
    return koha.status()


# ---------------------------------------------------------- Open research APIs

@tool("db_list",
      "List the subscription databases Sophiahemmet gives access to (CINAHL, PsycInfo, "
      "Cochrane, Web of Science, JCR, Sage Research Methods, NE, PubMed) and whether each "
      "one can be searched from here.",
      {})
def db_list():
    from . import databases
    return {"databases": databases.catalogue()}


@tool("db_status",
      "Check whether the school sign-in still works for a database (default cinahl). "
      "authenticated=false means the Entra session expired and someone has to sign in once "
      "in the school browser.",
      {"database": S})
def db_status(database="cinahl"):
    from . import databases
    return databases.status(database or "cinahl")


@tool("db_search",
      "Search a subscription database (cinahl, psycinfo) and return citations: title, "
      "authors, journal, year, volume, issue, pages, subjects, peer-reviewed, full-text, "
      "record URL. A bare multi-word query is joined with AND; quotes keep a phrase.",
      {"database": S, "query": S, "limit": I})
def db_search(database, query, limit=10):
    from . import databases
    if not query:
        raise ValueError("query is required")
    return databases.search(database or "cinahl", query, limit=limit or 10)


@tool("db_detail",
      "Open one database record and return its abstract, DOI, authors and source line. "
      "Pass the record URL from db_search.",
      {"url": S})
def db_detail(url):
    from . import databases
    if not url:
        raise ValueError("url is required")
    return databases.detail(url)


@tool("db_open",
      "Open one database in the school browser and report where it landed. For the "
      "databases that have no structured search here (Cochrane, Web of Science, JCR, Sage, "
      "NE) and for signing in when the session has expired.",
      {"database": S})
def db_open(database):
    from . import databases
    return databases.open_database(database)


@tool("db_fulltext",
      "Full text of one article, by DOI, through the school's subscription (EZproxy). Saves "
      "the PDF under ~/.local/share/unitoolkit/fulltext and returns path, pages and the "
      "extracted text. Says why when there is no route (publisher outside the proxy: order "
      "it via fjärrlån; or an open-access copy exists). Capped at 20 downloads an hour: the "
      "licences allow reading for study, not bulk downloading.",
      {"doi": S, "text": B})
def db_fulltext(doi, text=True):
    from . import fulltext
    if not doi:
        raise ValueError("doi is required")
    return fulltext.fetch(doi, text=True if text is None else bool(text))


@tool("find_sources",
      "THE citation tool: one query across several databases (default cinahl, pubmed, "
      "cochrane; also psycinfo, wos), deduplicated by DOI, each hit with an APA 7 reference "
      "built from the publisher's own DOI metadata. Use this for 'find me sources on X'.",
      {"query": S, "databases": {"type": "array", "items": S}, "limit": I}, ["query"])
def find_sources(query, databases=None, limit=5):
    from . import citation
    return citation.find_sources(query, tuple(databases or ("cinahl", "pubmed", "cochrane")),
                                 limit=limit or 5)


@tool("apa",
      "APA 7 reference for a DOI, from the DOI registry (not from scraped pages). lang='sv' "
      "for svensk APA wording.",
      {"doi": S, "lang": S}, ["doi"])
def apa(doi, lang="en"):
    from . import citation
    return {"doi": citation.clean_doi(doi), "apa": citation.apa(doi, lang or "en")}


@tool("registry",
      "Which university systems an agent can read, per institution: the adapter, status, last "
      "verified date and the gotchas that break it. Ask before writing an adapter or debugging an "
      "SSO/cookie failure. Filters: system='ladok', institution='shh', status='ok'|'partial'|"
      "'fail'|'untested', text='cloudflare'. No filters returns every row.",
      {"system": S, "institution": S, "status": S, "text": S})
def registry_lookup(system=None, institution=None, status=None, text=None):
    from . import registry
    rows = registry.query(system=system, institution=institution, status=status, text=text,
                          include_elsewhere=True)
    return {"count": len(rows), "last_run": registry.load().get("last_run"), "rows": rows}


@tool("pubmed", "Search PubMed. Returns PMID, title, journal, year, authors, DOI.",
      {"query": S, "max": I}, ["query"])
def pubmed(query, max=10):
    import urllib.parse
    E = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    q = urllib.parse.urlencode({"db": "pubmed", "term": query, "retmax": max, "retmode": "json",
                                "sort": "relevance"})   # E-utilities default is newest first
    with urllib.request.urlopen(E + "esearch.fcgi?" + q, timeout=30) as r:
        ids = json.load(r)["esearchresult"]["idlist"]
    if not ids:
        return []
    q = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "json"})
    with urllib.request.urlopen(E + "esummary.fcgi?" + q, timeout=30) as r:
        res = json.load(r)["result"]
    out = []
    for i in ids:
        x = res.get(i, {})
        doi = next((a["value"] for a in x.get("articleids", []) if a.get("idtype") == "doi"), None)
        out.append({"pmid": i, "title": x.get("title"), "journal": x.get("fulljournalname"),
                    "year": (x.get("pubdate") or "")[:4], "doi": doi,
                    "authors": [a.get("name") for a in x.get("authors", [])]})
    return out


@tool("libris", "Search Libris (Swedish union catalogue) for books: title, author, year, "
      "publisher, ISBN. Use to verify a course-book reference.", {"query": S, "max": I}, ["query"])
def libris(query, max=10):
    import urllib.parse
    q = urllib.parse.urlencode({"query": query, "format": "json", "n": max})
    with urllib.request.urlopen("https://libris.kb.se/xsearch?" + q, timeout=30) as r:
        d = json.load(r)
    out = []
    for x in d.get("xsearch", {}).get("list", []):
        out.append({"title": x.get("title"), "creator": x.get("creator"), "date": x.get("date"),
                    "publisher": x.get("publisher"), "isbn": x.get("isbn"),
                    "type": x.get("type"), "url": x.get("identifier")})
    return out


@tool("refs_search",
      "Search clinician-side Swedish reference sites. site='vardhandboken'. Matches the "
      "sitemap page slugs, then returns the top hits with title, source URL, fetch date and a "
      "snippet. Read a full page with refs_page. Internetmedicin is not supported (its "
      "robots.txt excludes crawlers); FASS product pages need a browser (use `page`).",
      {"site": S, "query": S, "limit": I}, ["site", "query"])
def refs_search(site, query, limit=10):
    from . import refs
    return refs.search(site, query, limit)


@tool("refs_page",
      "One clinician-side reference page as text (Vårdhandboken, Läkemedelsboken, Janusinfo, "
      "Viss). Returns url, title, fetched date and text. Cached on disk; refuses hosts and "
      "paths their robots.txt disallows. Läkemedelsboken blocks non-browser agents on purpose, so "
      "it is refused unless UNITOOLKIT_REFS_BROWSER_UA=1; otherwise read it with `page`.",
      {"url": S, "refresh": B}, ["url"])
def refs_page(url, refresh=False):
    from . import refs
    return refs.page(url, refresh)


# ---------------------------------------------------------- dispatch

def call(name, args=None):
    if name not in TOOLS:
        raise KeyError(f"unknown tool {name!r}; have {sorted(TOOLS)}")
    return TOOLS[name]["fn"](**(args or {}))


def manifest(names=None):
    """Tool schemas. names=None: every tool (tools.json); a list: only those, in that order."""
    pick = TOOLS.values() if names is None else [TOOLS[n] for n in names if n in TOOLS]
    return [{k: v for k, v in t.items() if k != "fn"} for t in pick]
