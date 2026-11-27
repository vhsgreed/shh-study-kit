"""Canvas access token: check, create and rotate it from the signed-in browser, never showing it.

Canvas has no token API for a machine: a user creates one in Profile → Settings, and the value is
displayed once. The browser session can do exactly what a person does there, POSTing to
`/api/v1/users/self/tokens` with the page's CSRF token, so this module recreates the token from the
agent Chrome and writes it straight to `~/.secrets/CANVAS_TOKEN` (0600) plus a small metadata file.

The value never appears in output, logs or chat: `create()` returns ids and dates only. Rotation is
safe to run at any time, because Canvas allows several tokens at once and the new one is verified
before it replaces the file.

Actions (`unitoolkit token action=...`):
    status   which token file is in use, whether Canvas accepts it, days left before the reminder
    create   mint a new token (days=90 default), verify it, install it, keep the old value aside
    verify   re-check the installed token against /api/v1/users/self
"""
import datetime as dt
import json
import os
import pathlib
import re
import urllib.request

SECRETS = pathlib.Path(os.environ.get("CANVAS_TOKEN_FILE", "~/.secrets/CANVAS_TOKEN")).expanduser()
META = SECRETS.with_suffix(".meta.json")          # created_at / expires_at, never the value
OLD = SECRETS.with_suffix(".old")                 # the previous value, 0600, for one rotation

def _csrf(js_eval):
    """The CSRF token every Canvas mutating request needs, taken from a meta tag or the cookie."""
    m = js_eval("document.querySelector('meta[name=csrf-token]')?.content || ''")
    if m:
        return m
    return ""


def status():
    """Is the installed token valid, and how long has it been in service?"""
    if not SECRETS.exists():
        return {"installed": False, "fix": "unitoolkit token action=create"}
    meta = {}
    if META.exists():
        try:
            meta = json.loads(META.read_text())
        except Exception:
            meta = {}
    try:
        me = _api("/api/v1/users/self")
        ok = bool(me.get("id"))
    except Exception as e:  # noqa: BLE001
        return {"installed": True, "valid": False, "error": str(e)[:200],
                "fix": "unitoolkit token action=create"}
    out = {"installed": True, "valid": ok, "user": me.get("name"), "id": me.get("id")}
    if meta.get("created_at"):
        out["created_at"] = meta["created_at"]
    if meta.get("expires_at"):
        exp = dt.date.fromisoformat(meta["expires_at"])
        out["expires_at"] = exp.isoformat()
        out["days_left"] = (exp - dt.date.today()).days
    return out


def _api(path, token=None):
    from . import canvas
    t = token or canvas.token()
    req = urllib.request.Request("https://shh.instructure.com" + path,
                                 headers={"Authorization": f"Bearer {t}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


MAX_DAYS = 30   # SHH's Canvas rejects anything longer: "Expiration date cannot be more than 30 days"


def create(days=MAX_DAYS, purpose="unitoolkit (auto-rotated)"):
    """Mint a token through the signed-in browser, verify it, then install it.

    Returns metadata only. The value goes to disk with 0600 and nowhere else.
    """
    import asyncio
    from . import browser

    days = max(1, min(int(days), MAX_DAYS))
    expires = (dt.date.today() + dt.timedelta(days=days)).isoformat()

    async def _mint(call):
        await call("Page.enable")
        for _ in range(40):
            await asyncio.sleep(0.25)
            if (await browser._eval(call, "location.href")) and \
               "shh.se/sv/mina-sidor" in (await browser._eval(call, "location.href") or ""):
                break
        if "shh.se/sv/mina-sidor/canvas" in (await browser._eval(call, "location.href") or ""):
            await call("Page.navigate", url=browser.CANVAS_SSO)
            await asyncio.sleep(6)
            await call("Page.navigate", url="https://shh.instructure.com/profile/settings")
            await asyncio.sleep(4)
        # Canvas' SPA carries the CSRF token only in the `_csrf_token` cookie (readable from JS);
        # there is no <meta name=csrf-token> on the settings page, which is why the first version of
        # this module reported "no CSRF token" on a perfectly signed-in session.
        csrf = await browser._eval(call, """(() => {
            const fromMeta = document.querySelector('meta[name="csrf-token"]')?.content || '';
            if (fromMeta) return fromMeta;
            const raw = (document.cookie.split('; ').find(s => s.startsWith('_csrf_token=')) || '').split('=')[1] || '';
            try { return decodeURIComponent(raw); } catch (e) { return raw; }
        })()""")
        if not csrf:
            return {"error": "no CSRF token (meta or _csrf_token cookie); is the session signed in?"}
        js = """(async () => {
            const body = new URLSearchParams({'token[purpose]': %s, 'token[expires_at]': %s});
            const r = await fetch('/api/v1/users/self/tokens', {
                method: 'POST', credentials: 'include',
                headers: {'X-CSRF-Token': %s, 'Content-Type': 'application/x-www-form-urlencoded'},
                body});
            return JSON.stringify({status: r.status, body: await r.text()});
        })()""" % (json.dumps(purpose), json.dumps(expires), json.dumps(csrf))
        raw = await browser._eval(call, js)
        try:
            res = json.loads(raw)
        except Exception:
            return {"error": f"unexpected answer: {str(raw)[:160]}"}
        if res.get("status") not in (200, 201):
            return {"error": f"HTTP {res.get('status')}: {res.get('body', '')[:200]}"}
        data = json.loads(res["body"])
        # Canvas returns the value under "token" and, on some versions, only inside "token_data";
        # take whichever is a non-empty string and remember the shape when neither is.
        val = data.get("visible_token") or data.get("token") or (data.get("token_data") or {}).get("token") or ""
        return {"id": data.get("id"), "value": val or None,
                "expires_at": data.get("expires_at"),
                "shape": sorted(data) if not val else None}

    def steps():
        t = browser.new_tab("https://shh.instructure.com/profile/settings")
        try:
            return asyncio.run(browser._session(t["webSocketDebuggerUrl"], _mint))
        finally:
            browser.close_tab(t["id"])

    got = steps()
    if got.get("error") or not got.get("value"):
        return {"created": False, **got}

    # verify before installing: a token that Canvas rejects must not replace a working one
    try:
        me = _api("/api/v1/users/self", token=got["value"])
    except Exception as e:  # noqa: BLE001
        return {"created": False, "error": f"new token failed verification: {e}"}

    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    if SECRETS.exists():
        OLD.write_text(SECRETS.read_text())
        os.chmod(OLD, 0o600)
    SECRETS.write_text(got["value"] + "\n")
    os.chmod(SECRETS, 0o600)
    META.write_text(json.dumps({"created_at": dt.datetime.now().isoformat(timespec="seconds"),
                                "expires_at": expires, "token_id": got.get("id"),
                                "user": me.get("name"), "purpose": purpose}, indent=1))
    os.chmod(META, 0o600)
    # Leaving orphan tokens behind is how a student ends up with a dozen of them in Settings.
    # Delete any token minted under our purpose that is not the one just installed, best effort.
    dropped = []
    for tid in got.get("orphans") or []:
        try:
            req = urllib.request.Request(
                f"https://shh.instructure.com/api/v1/users/self/tokens/{tid}", method="DELETE",
                headers={"Authorization": f"Bearer {got['value']}"})
            with urllib.request.urlopen(req, timeout=20):
                dropped.append(tid)
        except Exception:  # noqa: BLE001
            pass
    return {"created": True, "token_id": got.get("id"), "expires_at": expires,
            "user": me.get("name"), "file": str(SECRETS), "previous_kept_in": str(OLD),
            "deleted_orphans": dropped}


def verify():
    return status()


def delete(token_id):
    """Remove a token by id with the installed token's authority (used to clean up orphans)."""
    from . import canvas
    req = urllib.request.Request(
        f"https://shh.instructure.com/api/v1/users/self/tokens/{token_id}", method="DELETE",
        headers={"Authorization": f"Bearer {canvas.token()}"})
    with urllib.request.urlopen(req, timeout=20):
        return {"deleted": token_id}


# Canvas caps a student token at 30 days, so this has to run often: rotate when 14 days or fewer
# remain, i.e. every other week. A cron entry does that (`crontab -l | grep canvastoken`).
STALE_DAYS = 14


def needs_rotation(st=None):
    """True when the token is missing, rejected, or inside STALE_DAYS of its expiry."""
    st = st or status()
    if not st.get("valid"):
        return True
    return st.get("days_left") is not None and st["days_left"] <= STALE_DAYS
