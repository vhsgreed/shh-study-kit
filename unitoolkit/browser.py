"""Logged-in browser access over Chrome DevTools Protocol.

For SSO-walled systems with no student API (SharePoint, Ladok, Inspera, library), run a
dedicated Chrome profile with remote debugging, log in once by hand, and let the agent
read pages through it. Never point this at the user's default profile (Chrome refuses,
and it should not be the agent's).

    flatpak run com.google.Chrome --user-data-dir=~/.var/app/com.google.Chrome/config/unitoolkit \
        --remote-debugging-port=9233
"""
import asyncio
import json
import os
import urllib.request

PORT = int(os.environ.get("SHHKIT_CDP_PORT", "9233"))


def targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5) as r:
        return [t for t in json.load(r) if t.get("type") == "page"]


def new_tab(url="about:blank"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?{url}", method="PUT")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def close_tab(tid):
    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{tid}", timeout=5).read()


async def _session(ws_url, steps):
    import websockets  # only dependency outside stdlib

    async with websockets.connect(ws_url, max_size=64 * 2**20) as ws:
        n = 0

        async def call(method, **params):
            nonlocal n
            n += 1
            mid = n
            await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg.get("result", {})

        return await steps(call)


def _eval(call, expr):
    async def run():
        r = await call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")
    return run()


def page(url, wait=4.0, text=True):
    """Open url in a fresh tab of the logged-in profile, wait, return {url,title,text|html}."""
    t = new_tab(url)

    async def steps(call):
        await call("Page.enable")
        await call("Page.navigate", url=url)
        for _ in range(int(wait * 4)):
            await asyncio.sleep(0.25)
            if await _eval(call, "document.readyState") == "complete" and (await _eval(call, "location.href")) != "about:blank":
                break
        await asyncio.sleep(1.0)  # SPAs (SharePoint, Ladok) render after load
        body = "document.body.innerText" if text else "document.documentElement.outerHTML"
        return {"url": await _eval(call, "location.href"),
                "title": await _eval(call, "document.title"),
                ("text" if text else "html"): await _eval(call, body)}

    try:
        return asyncio.run(_session(t["webSocketDebuggerUrl"], steps))
    finally:
        close_tab(t["id"])


def fetch(url, origin, as_json=True):
    """fetch() from inside a tab on `origin`, so the site's own cookies/SSO apply.
    Use for internal JSON APIs (e.g. Ladok student, SharePoint REST)."""
    t = new_tab(origin)

    async def steps(call):
        await call("Page.enable")
        await asyncio.sleep(3)
        js = (f"fetch({json.dumps(url)},{{credentials:'include',headers:{{Accept:'application/json'}}}})"
              f".then(async r=>({{status:r.status,body:await r.text()}}))")
        return await _eval(call, js)

    try:
        r = asyncio.run(_session(t["webSocketDebuggerUrl"], steps))
    finally:
        close_tab(t["id"])
    if as_json and r and r.get("status") == 200:
        try:
            r["body"] = json.loads(r["body"])
        except ValueError:
            pass
    return r


LOGIN_MARKERS = ("login.microsoftonline.com", "/login", "wayf", "discovery", "seamlessaccess")


def fetch_binary(url, origin):
    """Fetch bytes with the site's cookies. SharePoint serves binary only to a fetch inside
    its own origin, so this goes through arrayBuffer -> base64 -> Python."""
    t = new_tab(origin)

    async def steps(call):
        await call("Page.enable")
        await asyncio.sleep(3)
        js = (f"fetch({json.dumps(url)},{{credentials:'include'}}).then(async r=>{{"
              "const b=new Uint8Array(await r.arrayBuffer());let s='';"
              "for(let i=0;i<b.length;i+=8192)s+=String.fromCharCode.apply(null,b.subarray(i,i+8192));"
              "return {status:r.status,type:r.headers.get('content-type'),"
              "cd:r.headers.get('content-disposition'),b64:btoa(s)};})")
        return await _eval(call, js)

    try:
        r = asyncio.run(_session(t["webSocketDebuggerUrl"], steps))
    finally:
        close_tab(t["id"])
    if r and r.get("b64"):
        import base64
        r["bytes"] = base64.b64decode(r["b64"])
        del r["b64"]
    return r


def logged_in(url):
    """True when url renders without bouncing to an SSO login page."""
    p = page(url, wait=6)
    u = (p.get("url") or "").lower()
    head = (p.get("text") or "")[:1500].lower()
    # Some SPAs (Ladok) show the login screen without changing the URL.
    walled = any(m in u for m in LOGIN_MARKERS) or any(
        s in head for s in ("log in through your university", "logga in via ditt lärosäte",
                            "sign in to your account", "logga in"))
    return not walled, p.get("url")
