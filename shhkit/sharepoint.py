"""Enumerate and download files from an SHH SharePoint document library.

Uses the SharePoint REST API through the logged-in browser (`shhkit.browser`), paging
via odata.nextLink. Library items are the reliable route: the folder endpoints answer
empty at the root even when the library holds >1000 files.

    from shhkit import sharepoint as sp
    files = sp.list_files("742ee806-0cff-4a92-aad8-afc2b8ed565b")
    sp.save(files[0], "~/courses/programme/x.pdf")
"""
import json
import pathlib
import urllib.parse

from . import browser

SITE = "https://shh365.sharepoint.com/sites/Information_SHH_hemsida2"
ORIGIN = "https://shh365.sharepoint.com/sites/Minasidor-Student"
MAIN_LIBRARY = "742ee806-0cff-4a92-aad8-afc2b8ed565b"  # "Delade dokument", ~1100 files


def api(path):
    """GET an API path (absolute or starting with '/_api'). Returns a dict."""
    url = path if path.startswith("http") else SITE + path
    r = browser.fetch(url, ORIGIN)
    b = r.get("body")
    if isinstance(b, dict):
        return b
    try:
        return json.loads(b)
    except (TypeError, ValueError):
        return {"_raw": str(b)[:300], "_status": r.get("status")}


def list_files(list_id=MAIN_LIBRARY, page=1000, max_pages=20):
    """All files and folders in a library: [{name, path, type, size}]."""
    url = (f"/_api/web/lists(guid'{list_id}')/items"
           f"?$select=FileRef,FileLeafRef,FSObjType,File/Length&$expand=File&$top={page}")
    out = []
    for _ in range(max_pages):
        d = api(url)
        for it in d.get("value", []):
            out.append({"name": it.get("FileLeafRef"),
                        "path": urllib.parse.unquote(it.get("FileRef") or ""),
                        "type": "folder" if int(it.get("FSObjType") or 0) else "file",
                        "size": (it.get("File") or {}).get("Length")})
        nxt = d.get("odata.nextLink")
        if not nxt:
            break
        url = nxt
    return out


def file_url(server_relative):
    q = urllib.parse.quote(server_relative)
    return f"{SITE}/_api/web/GetFileByServerRelativeUrl('{q}')/$value"


def save(entry, dest, timeout=120):
    """Download one entry (as returned by list_files) to dest. Returns path or None."""
    from . import download
    raw = download.download(file_url(entry["path"]), "~/courses/programme/raw", timeout=timeout)
    return download.move(raw, dest) if raw else None
