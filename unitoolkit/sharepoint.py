"""SharePoint share links -> real file bytes, through the logged-in agent browser.

SHH publishes kursplaner, litteraturlistor and the utbildningsplan as tenant-restricted
share links (`https://<tenant>.sharepoint.com/:b:/s/<site>/<docid>?e=...`). A plain GET
returns the viewer or a login page. `download.aspx?share=<docid>`, fetched from inside
the site's own origin with the user's cookies, returns the file itself.

Walking document libraries over REST is not offered: SHH answers AccessDenied for
students on folder endpoints, so share links are the supported route.
"""
import pathlib
import re

from . import browser

SHARE_RE = re.compile(r"https://([^/]+)/:([a-z]):/[sr]/([^/]+)/([^?]+)")


def share_to_download(share):
    """Return (download_url, origin) for a share link, or (None, None)."""
    m = SHARE_RE.match(share)
    if not m:
        return None, None
    host, _kind, site, docid = m.groups()
    origin = f"https://{host}/sites/{site}/"
    return f"{origin}_layouts/15/download.aspx?share={docid}", origin


def sniff(b):
    """Classify bytes. A UTF-8 BOM precedes the '<' of an HTML page, so check past it."""
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:2] == b"PK":
        return "docx"  # docx/pptx/xlsx; real extension comes from Content-Disposition
    head = b.lstrip(b"\xef\xbb\xbf").lstrip()
    if head[:1] == b"<":
        return "html"
    return "bin"


def name_from_cd(cd):
    """Filename out of Content-Disposition, preferring filename* (RFC 5987)."""
    if not cd:
        return None
    m = re.search(r"filename\*=utf-8''([^;]+)", cd, re.I)
    if m:
        import urllib.parse
        return urllib.parse.unquote(m.group(1))
    m = re.search(r'filename="?([^";]+)"?', cd, re.I)
    return m.group(1) if m else None


def fetch_share(share):
    """Return (bytes, kind, suggested_name) or (None, None, None).

    The real extension comes from Content-Disposition: a share can point at a PDF, a docx,
    or something like a saved .aspx page, and magic bytes alone cannot tell docx from pptx.
    """
    url, origin = share_to_download(share)
    if not url:
        return None, None, None
    r = browser.fetch_binary(url, origin) or {}
    b = r.get("bytes") or b""
    if r.get("status") != 200 or not b:
        return None, None, None
    kind = sniff(b)
    name = name_from_cd(r.get("cd"))
    if name and "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        if ext in ("pdf", "docx", "pptx", "xlsx", "txt", "html", "htm", "aspx", "csv"):
            kind = ext
    return b, kind, name


def save_share(share, out):
    """Download a share link to `out` (a file path, or a dir to auto-name in)."""
    b, ext, name = fetch_share(share)
    if not b:
        return {"ok": False, "share": share, "error": "download failed (logged in? link valid?)"}
    out = pathlib.Path(out).expanduser()
    if out.is_dir() or not out.suffix:
        out.mkdir(parents=True, exist_ok=True)
        out = out / (name or f"{SHARE_RE.match(share).group(4)[:24]}.{ext}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b)
    return {"ok": True, "path": str(out), "bytes": len(b), "type": ext}
