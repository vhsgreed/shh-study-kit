"""Canvas LMS REST client (stdlib only). Token from $CANVAS_TOKEN or ~/.secrets/CANVAS_TOKEN."""
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("CANVAS_BASE", "https://shh.instructure.com")


def token():
    t = os.environ.get("CANVAS_TOKEN")
    if t:
        return t.strip()
    p = pathlib.Path(os.environ.get("CANVAS_TOKEN_FILE", "~/.secrets/CANVAS_TOKEN")).expanduser()
    return p.read_text().strip()


def _req(url, raw=False):
    if not url.startswith("http"):
        url = f"{BASE}/api/v1/{url.lstrip('/')}"
    r = urllib.request.Request(url, headers={"Authorization": f"Bearer {token()}"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                body = resp.read()
                link = resp.headers.get("Link", "")
                return (body if raw else json.loads(body or b"null")), link
        except urllib.error.HTTPError as e:
            if e.code in (429, 403) and "Rate Limit" in e.read().decode(errors="ignore") and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise


def get(path, **params):
    """GET one resource or follow pagination and return the full list."""
    q = urllib.parse.urlencode({"per_page": 100, **params}, doseq=True)
    base = path if path.startswith("http") else f"{BASE}/api/v1/{path.lstrip('/')}"
    url = base + ("&" if "?" in path else "?") + q
    data, link = _req(url)
    if not isinstance(data, list):
        return data
    out = list(data)
    while True:
        m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        if not m:
            return out
        data, link = _req(m.group(1))
        out.extend(data)


def try_get(path, **params):
    try:
        return get(path, **params)
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_detail": e.read().decode(errors="ignore")[:300]}


def download(url, dest):
    body, _ = _req(url, raw=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return len(body)


def safe(name):
    return re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", name).strip()[:150] or "unnamed"


def courses():
    return get("courses", **{"include[]": ["term", "syllabus_body"], "state[]": ["available", "completed"]})


def mirror(course_id, out, files=True):
    """Dump a course to `out`: course.json, modules.json, pages/*.html, assignments.json,
    announcements.json, files/. Pages are reached via module items, since SHH disables
    the page index for students. Returns a summary dict."""
    out = pathlib.Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    w = lambda n, d: (out / n).write_text(json.dumps(d, ensure_ascii=False, indent=1))
    course = get(f"courses/{course_id}", **{"include[]": ["syllabus_body", "term"]})
    w("course.json", course)
    modules = get(f"courses/{course_id}/modules", **{"include[]": ["items", "content_details"]})
    w("modules.json", modules)
    pages_dir = out / "pages"
    pages_dir.mkdir(exist_ok=True)
    n_pages = 0
    for m in modules:
        for it in m.get("items", []):
            if it.get("type") == "Page" and it.get("url"):
                pg = try_get(it["url"])
                if isinstance(pg, dict) and "body" in pg:
                    name = f"{m['position']:02d}_{it['position']:02d}_{safe(pg.get('url', 'page'))}.html"
                    (pages_dir / name).write_text(
                        f"<!-- module: {m['name']} | title: {pg.get('title')} | updated: {pg.get('updated_at')} -->\n"
                        + (pg.get("body") or ""))
                    n_pages += 1
    assignments = try_get(f"courses/{course_id}/assignments", **{"include[]": ["rubric_assessment", "submission"]})
    w("assignments.json", assignments)
    ann = try_get(f"courses/{course_id}/discussion_topics", only_announcements="true")
    w("announcements.json", ann)
    disc = try_get(f"courses/{course_id}/discussion_topics")
    w("discussions.json", disc)
    flist = try_get(f"courses/{course_id}/files")
    w("files.json", flist)
    n_files, n_bytes = 0, 0
    if files and isinstance(flist, list):
        for f in flist:
            dest = out / "files" / safe(f.get("display_name") or f.get("filename"))
            if dest.exists() and dest.stat().st_size == f.get("size"):
                continue
            try:
                n_bytes += download(f["url"], dest)
                n_files += 1
            except Exception as e:  # locked/unpublished files
                (out / "files_errors.log").open("a").write(f"{f.get('id')} {f.get('display_name')}: {e}\n")
    return {"course": course.get("name"), "modules": len(modules), "pages": n_pages,
            "assignments": len(assignments) if isinstance(assignments, list) else assignments,
            "announcements": len(ann) if isinstance(ann, list) else ann,
            "files_downloaded": n_files, "bytes": n_bytes, "out": str(out)}


def upcoming():
    """Planner items (assignments, quizzes, events) from today, across all courses."""
    return get("planner/items", start_date=time.strftime("%Y-%m-%d"))
