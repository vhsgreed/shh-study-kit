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


# ------------------------------------------------------------------- Ladok

@tool("ladok_windows",
      "Ladok registration windows (kursregistrering + tentamensanmälan): what opens when, "
      "what closes within N days, with course period. Needs the agent Chrome logged in to "
      "Ladok (CDP :9233); read-only, never registers anything. Subrepo: ladok_vakt/.",
      {"close_days": I})
def ladok_windows(close_days=None):
    """Silent-by-default watcher logic, exposed as a one-shot read for agents."""
    import sys
    _vendor = pathlib.Path(__file__).resolve().parents[1] / 'vendor' / 'ladok-vakt'
    if str(_vendor) not in sys.path:
        sys.path.insert(0, str(_vendor))
    from ladok_vakt import core
    cfg = core.Config()
    if close_days is not None:
        cfg.close_days = int(close_days)
    try:
        rows = core.run(cfg, alert=False, email=False)
    except core.Fault as exc:
        return {"error": str(exc), "hint": "log in to Ladok in the agent Chrome on :9233"}
    return {"windows": rows, "state": str(cfg.state_file)}


# ---------------------------------------------------------------- Canvas (token)

@tool("courses", "List the student's Canvas courses: id, code, name, term.")
def courses():
    from . import canvas
    return [{"id": c["id"], "code": c.get("course_code"), "name": c.get("name"),
             "term": (c.get("term") or {}).get("name")} for c in canvas.courses()]


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


# ---------------------------------------------------------- Open research APIs

@tool("pubmed", "Search PubMed. Returns PMID, title, journal, year, authors, DOI.",
      {"query": S, "max": I}, ["query"])
def pubmed(query, max=10):
    import urllib.parse
    E = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    q = urllib.parse.urlencode({"db": "pubmed", "term": query, "retmax": max, "retmode": "json"})
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


# ---------------------------------------------------------- dispatch

def call(name, args=None):
    if name not in TOOLS:
        raise KeyError(f"unknown tool {name!r}; have {sorted(TOOLS)}")
    return TOOLS[name]["fn"](**(args or {}))


def manifest():
    return [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS.values()]
