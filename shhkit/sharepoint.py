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
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:2] == b"PK":
        return "docx"
    if b.lstrip()[:1] == b"<":
        return "html"
    return "bin"


def fetch_share(share):
    """Return (bytes, ext) or (None, None)."""
    url, origin = share_to_download(share)
    if not url:
        return None, None
    r = browser.fetch_binary(url, origin) or {}
    b = r.get("bytes") or b""
    if r.get("status") != 200 or not b:
        return None, None
    return b, sniff(b)


def save_share(share, out):
    """Download a share link to `out` (a file path, or a dir to auto-name in)."""
    b, ext = fetch_share(share)
    if not b:
        return {"ok": False, "share": share, "error": "download failed (logged in? link valid?)"}
    out = pathlib.Path(out).expanduser()
    if out.is_dir() or not out.suffix:
        out.mkdir(parents=True, exist_ok=True)
        out = out / f"{SHARE_RE.match(share).group(4)[:24]}.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b)
    return {"ok": True, "path": str(out), "bytes": len(b), "type": ext}
