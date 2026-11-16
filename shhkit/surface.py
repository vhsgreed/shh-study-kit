"""What a student session can actually extract from Canvas, endpoint by endpoint.

This exists because assumptions about Canvas are wrong often enough to be expensive: pages
may be disabled per course, rubrics are empty unless asked for with include[]=rubric, the
quizzes endpoint can be off, and students cannot read the rubric collection. Probing first,
then building, keeps the search index honest about what it can cover.

    from shhkit import surface
    surface.probe_course(2416)     # -> list of rows with status, count, bytes
"""
import json
import urllib.error
import urllib.request

from . import canvas

# (label, path template, note) -- {c} is the course id
PROBES = [
    ("courses", "courses", "enrolments, term, codes"),
    ("course meta", "courses/{c}", "name, settings"),
    ("course tabs", "courses/{c}/tabs", "which tools the course shows"),
    ("course settings", "courses/{c}/settings", "feature switches"),
    ("features", "courses/{c}/features", "account flags, e.g. smart_search"),
    ("modules", "courses/{c}/modules?include[]=items&include[]=content_details", "the only route to pages"),
    ("pages index", "courses/{c}/pages", "often disabled: 'Sidan har avaktiverats'"),
    ("pages search", "courses/{c}/pages?search_term=kurs", "disabled together with the index"),
    ("assignments", "courses/{c}/assignments?per_page=100", "task text"),
    ("assignments + rubric", "courses/{c}/assignments?include[]=rubric&per_page=100", "rubric rows, needed for criteria"),
    ("rubrics", "courses/{c}/rubrics", "students are refused (403)"),
    ("assignment groups", "courses/{c}/assignment_groups", "weights"),
    ("quizzes", "courses/{c}/quizzes", "may be disabled"),
    ("discussions", "courses/{c}/discussion_topics?per_page=100", "threads"),
    ("announcements", "courses/{c}/discussion_topics?only_announcements=true&per_page=100", "the 'anvisningar' feed"),
    ("files", "courses/{c}/files?per_page=100", "names, sizes, download urls"),
    ("folders", "courses/{c}/folders?per_page=100", "file tree"),
    ("enrollments self", "courses/{c}/enrollments?user_id=self", "own role, section"),
    ("submissions self", "courses/{c}/students/submissions?student_ids[]=self&per_page=100",
     "own grades, submission state, bodies"),
    ("external tools", "courses/{c}/external_tools", "LTI tools in the course"),
    ("course nav LTI", "courses/{c}/external_tools/visible_course_nav_tools", "Kaltura etc."),
    ("planner items", "planner/items?per_page=100", "unified feed: assignments, files, announcements"),
    ("todo", "users/self/todo", "what Canvas thinks is pending"),
    ("upcoming events", "users/self/upcoming_events", "calendar feed"),
    ("profile", "users/self/profile", "self"),
    ("groups", "users/self/groups", "group work and group submissions"),
    ("calendar events", "calendar_events?context_codes[]=course_{c}&type=event", "course events"),
]


def _probe(path):
    url = f"{canvas.BASE}/api/v1/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {canvas.token()}"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        return {"status": e.code, "count": None, "bytes": 0,
                "note": e.read()[:80].decode("utf-8", "replace").replace("\n", " ")}
    except Exception as e:  # noqa: BLE001 - a probe reports, it does not raise
        return {"status": "ERR", "count": None, "bytes": 0, "note": str(e)[:80]}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"status": status, "count": None, "bytes": len(raw), "note": "non-JSON"}
    count = len(data) if isinstance(data, list) else None
    return {"status": status, "count": count, "bytes": len(raw), "note": ""}


def probe_course(course_id):
    """Probe every endpoint a student session might use. Returns one row per endpoint."""
    rows = []
    for label, tpl, note in PROBES:
        path = tpl.format(c=course_id)
        res = _probe(path)
        rows.append({"label": label, "path": path, **res, "why": note})
    return rows


def summary(rows):
    ok = [r for r in rows if r["status"] == 200]
    return {
        "reachable": len(ok),
        "blocked": [f"{r['label']}: {r['status']}" for r in rows if r["status"] != 200],
        "documents": {r["label"]: r["count"] for r in ok if r["count"] is not None},
    }
