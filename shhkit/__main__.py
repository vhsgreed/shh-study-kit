"""shhkit CLI. Output is JSON on stdout so an agent can parse it.

  python -m shhkit courses
  python -m shhkit mirror <course_id|all> [--out DIR] [--no-files]
  python -m shhkit upcoming
  python -m shhkit get <api/path> [key=value ...]
  python -m shhkit page <url>              (needs the logged-in CDP browser)
  python -m shhkit fetch <url> <origin>    (JSON fetch with the site's own SSO cookies)
  python -m shhkit login-check
"""
import json
import pathlib
import sys

from . import canvas


def emit(x):
    print(json.dumps(x, ensure_ascii=False, indent=1))


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "courses":
        emit([{"id": c["id"], "code": c.get("course_code"), "name": c.get("name"),
               "term": (c.get("term") or {}).get("name"), "end": c.get("end_at")} for c in canvas.courses()])
    elif cmd == "mirror":
        out = pathlib.Path(rest[rest.index("--out") + 1] if "--out" in rest else "~/courses/canvas").expanduser()
        ids = [c["id"] for c in canvas.courses()] if rest[0] == "all" else [int(rest[0])]
        emit([canvas.mirror(i, out / str(i), files="--no-files" not in rest) for i in ids])
    elif cmd == "upcoming":
        emit([{"course": i.get("context_name"), "type": i.get("plannable_type"),
               "title": (i.get("plannable") or {}).get("title"), "due": i.get("plannable_date"),
               "submitted": (i.get("submissions") or {}).get("submitted") if isinstance(i.get("submissions"), dict) else None}
              for i in canvas.upcoming()])
    elif cmd == "get":
        emit(canvas.get(rest[0], **dict(kv.split("=", 1) for kv in rest[1:])))
    elif cmd in ("page", "fetch", "login-check"):
        from . import browser
        if cmd == "page":
            emit(browser.page(rest[0]))
        elif cmd == "fetch":
            emit(browser.fetch(rest[0], rest[1]))
        else:
            emit({u: browser.logged_in(u) for u in (
                "https://shh.instructure.com/", "https://shh365.sharepoint.com/",
                "https://www.student.ladok.se/student/app/studentwebb/")})
    else:
        sys.exit(f"unknown command {cmd!r}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
