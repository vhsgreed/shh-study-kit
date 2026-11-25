"""OneDrive for Business + "Shared with me", read only, through the signed-in agent browser.

The school tenant's personal site (`<tenant>-my.sharepoint.com`) serves the OneDrive v2.0 API
(`/_api/v2.0/me/drive/...`, Graph-shaped JSON) to a fetch made inside that origin with the
student's own cookies. No app registration, no Graph token, no admin consent: the same Entra
session that opens SharePoint opens the drive. Everything here is GET only.

Items shared by someone else appear in `sharedWithMe` as a `remoteItem`; their bytes live in the
owner's drive, so downloads address `drives/<driveId>/items/<id>` rather than `me/drive`.
"""
import json
import os
import pathlib
import re
import urllib.parse

from . import browser

TENANT = os.environ.get("UNITOOLKIT_M365_TENANT", "shh365")
MY = f"https://{TENANT}-my.sharepoint.com/"
API = MY + "_api/v2.0/"
FIELDS = "id,name,size,lastModifiedDateTime,lastModifiedBy,webUrl,folder,file,remoteItem,parentReference"


class SessionGone(RuntimeError):
    pass


def _get(path, params=None):
    url = API + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe="$,")
    r = browser.fetch(url, MY) or {}
    status, body = r.get("status"), r.get("body")
    if status in (401, 403) or (isinstance(body, str) and "login.microsoftonline.com" in body):
        raise SessionGone(f"OneDrive answered {status}: the agent browser's school sign-in has lapsed; "
                          f"sign in once at {MY} in that browser")
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"OneDrive {status} for {path}: {str(body)[:200]}")
    return body


def _pages(path, params=None, limit=500):
    """Follow @odata.nextLink; stop on a repeated link so an ignored cursor cannot loop."""
    body = _get(path, params)
    items, seen = list(body.get("value", [])), set()
    nxt = body.get("@odata.nextLink")
    while nxt and nxt not in seen and len(items) < limit:
        seen.add(nxt)
        r = browser.fetch(nxt, MY) or {}
        b = r.get("body") if r.get("status") == 200 else None
        if not isinstance(b, dict):
            break
        items += b.get("value", [])
        nxt = b.get("@odata.nextLink")
    return items[:limit]


def _row(it):
    rem = it.get("remoteItem") or {}
    src = rem or it
    who = ((src.get("lastModifiedBy") or it.get("lastModifiedBy") or {}).get("user") or {})
    ref = src.get("parentReference") or {}
    sh = rem.get("shared") or it.get("shared") or {}
    # the tenant fills `sharedBy`; Graph docs show `owner`. Read both.
    shared = (sh.get("sharedBy") or sh.get("owner") or {}).get("user", {})
    return {
        "name": it.get("name") or src.get("name"),
        "kind": "folder" if (src.get("folder") or it.get("folder")) else "file",
        "size": src.get("size") or it.get("size"),
        "modified": src.get("lastModifiedDateTime") or it.get("lastModifiedDateTime"),
        "modified_by": who.get("displayName"),
        "owner": shared.get("displayName"),
        "drive_id": ref.get("driveId"),
        "item_id": src.get("id") or it.get("id"),
        "web_url": src.get("webUrl") or it.get("webUrl"),
    }


def _item_path(drive_id=None, item_id=None, path=None):
    base = f"drives/{drive_id}/" if drive_id else "me/drive/"
    if item_id:
        return base + f"items/{item_id}"
    if path and path.strip("/"):
        return base + "root:/" + urllib.parse.quote(path.strip("/")) + ":"
    return base + "root"


def ls(path="", drive_id=None, item_id=None):
    """Children of a folder in your own drive (by path) or any drive (drive_id + item_id)."""
    rows = [_row(i) for i in _pages(_item_path(drive_id, item_id, path) + "/children", {"$top": 200})]
    return sorted(rows, key=lambda r: (r["kind"] != "folder", (r["name"] or "").lower()))


def shared(query=None):
    rows = [_row(i) for i in _pages("me/drive/sharedWithMe", {"$top": 200})]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in (r["name"] or "").lower() or q in (r["owner"] or "").lower()]
    return sorted(rows, key=lambda r: r["modified"] or "", reverse=True)


def recent():
    return [_row(i) for i in _pages("me/drive/recent", {"$top": 100})]


def search(query):
    """Full-text search over your own drive. Shared items are matched by name via shared()."""
    q = query.replace("'", "''")
    own = [_row(i) for i in _pages(f"me/drive/root/search(q='{q}')", {"$top": 100})]
    return {"own": own, "shared": shared(query)}


def resolve(name=None, drive_id=None, item_id=None):
    """One item, by ids or by an unambiguous name across own drive + shared."""
    if item_id:
        return _row(_get(_item_path(drive_id, item_id)))
    hits = [r for r in shared() + ls() if (r["name"] or "").lower() == (name or "").lower()]
    if not hits:
        hits = [r for r in search(name)["own"] + shared(name) if r["kind"] == "file"]
    uniq = {(h["drive_id"], h["item_id"]): h for h in hits}
    if len(uniq) != 1:
        raise LookupError(f"{len(uniq)} items match {name!r}: pass drive_id + item_id "
                          + json.dumps([{k: h[k] for k in ('name', 'owner', 'modified', 'drive_id', 'item_id')}
                                        for h in uniq.values()], ensure_ascii=False)[:1500])
    return next(iter(uniq.values()))


def download(out="~/courses/onedrive", name=None, drive_id=None, item_id=None):
    """Save one file's current bytes. Never overwrites: an existing path gets a timestamp suffix."""
    it = resolve(name, drive_id, item_id)
    if it["kind"] != "file":
        raise ValueError(f"{it['name']} is a folder")
    url = API + _item_path(it["drive_id"], it["item_id"]) + "/content"
    r = browser.fetch_binary(url, MY) or {}
    b = r.get("bytes") or b""
    if r.get("status") != 200 or not b:
        raise RuntimeError(f"download {r.get('status')} for {it['name']}")
    out = pathlib.Path(out).expanduser()
    target = out / it["name"] if (out.is_dir() or not out.suffix) else out
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stamp = re.sub(r"[^0-9]", "", it["modified"] or "")[:12]
        target = target.with_name(f"{target.stem}_{stamp}{target.suffix}")
    target.write_bytes(b)
    return {**it, "path": str(target), "bytes": len(b)}


def status():
    try:
        d = _get("me/drive")
    except SessionGone as e:
        return {"ok": False, "error": str(e)}
    who = (d.get("owner") or d.get("lastModifiedBy") or {}).get("user", {})
    return {"ok": True, "drive_id": d.get("id"), "user": who.get("email") or who.get("displayName"),
            "quota_used": (d.get("quota") or {}).get("used")}
