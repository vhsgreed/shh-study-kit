"""Local HTTP endpoint for the deep index, for the browser plugin and for other tools.

The plugin is the fast path (in-browser index of pages, assignments, rubrics, announcements,
module items). This server is the deep path: document text, OCR results, mirrors, the whole
programme. Running it lets the plugin merge both layers without bundling a PDF parser into a
Chrome extension, and without the plugin ever needing a Canvas token.

Binds loopback only. Reach it from another machine with `tailscale serve`:

    python -m shhkit serve                 # 127.0.0.1:8790
    tailscale serve --bg 8790              # https://<host>.<tailnet>.ts.net/

Endpoints (all GET, JSON):
    /health                      service state, cached document count
    /courses                     the student's courses
    /search?course=&q=&limit=    course search; add docs=1 to include document text
    /docs?course=&q=             document text only, with page numbers
    /index                       what is cached per course
"""
import json
import pathlib
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import docs as docs_mod
from .tools import TOOLS, call

PORT = 8791  # 8790 is the agentrecourse site preview on this machine
ALLOWED_PREFIXES = ("https://shh.instructure.com",
                    "https://cloud.timeedit.net",
                    "http://127.0.0.1", "http://localhost")


def _cached_index():
    root = docs_mod.CACHE
    out = {}
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir():
            files = list(d.glob("*.json"))
            chars = 0
            for f in files:
                try:
                    chars += len((json.loads(f.read_text()).get("text") or ""))
                except Exception:  # noqa: BLE001
                    pass
            out[d.name] = {"documents": len(files), "chars": chars}
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "shhkit"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - matches the base signature
        pass

    def _cors(self):
        origin = self.headers.get("Origin") or ""
        if origin.startswith(ALLOWED_PREFIXES):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            self.send_header("Access-Control-Max-Age", "600")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802 - http.server API
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802 - http.server API
        u = urllib.parse.urlparse(self.path)
        q = {k: v for k, v in urllib.parse.parse_qsl(u.query)}
        try:
            if u.path in ("/", "/health"):
                return self._json({"ok": True, "service": "shhkit", "version": _version(),
                                   "tools": len(TOOLS), "cached": _cached_index()})
            if u.path == "/courses":
                return self._json(call("courses", {}))
            if u.path == "/index":
                return self._json(_cached_index())
            if u.path == "/search":
                course = q.get("course")
                query = q.get("q") or q.get("query") or ""
                if not course:
                    return self._json({"error": "course is required"}, 400)
                args = {"course": str(course), "query": query,
                        "limit": int(q.get("limit") or 25),
                        "include_docs": (q.get("docs") or "") in ("1", "true", "yes"),
                        "fuzzy": (q.get("fuzzy") or "") in ("1", "true"),
                        "regex": (q.get("regex") or "") in ("1", "true")}
                if q.get("source"):
                    args["source"] = q["source"]
                return self._json(call("search", args))
            if u.path == "/docs":
                course = q.get("course")
                if not course:
                    return self._json({"error": "course is required"}, 400)
                args = {"course": str(course), "query": q.get("q") or "",
                        "include_docs": True, "limit": int(q.get("limit") or 25)}
                if q.get("source"):
                    args["source"] = q["source"]
                return self._json(call("search", args))
            return self._json({"error": "unknown path", "paths": ["/health", "/courses",
                                                                 "/search", "/docs", "/index"]}, 404)
        except Exception as e:  # noqa: BLE001 - a server must answer, not crash
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def _version():
    try:
        from . import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "?"


def serve(host="127.0.0.1", port=PORT):
    httpd = None
    last = None
    for candidate in range(port, port + 6):
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            port = candidate
            break
        except OSError as e:  # port taken by something else: try the next one
            last = e
    if httpd is None:
        raise SystemExit(f"no free port in {port}-{port + 5}: {last}")
    print(f"shhkit serving on http://{host}:{port}", flush=True)
    print("  tailscale serve --bg " + str(port) + "   # then https://<machine>.<tailnet>.ts.net/",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        httpd.server_close()
