"""Download files through a logged-in Chrome over CDP (SharePoint share links, PDFs, docx).

SharePoint /:b:/ and /:u:/ share links are tenant-restricted; a plain HTTP GET gets the
login page. Driving the logged-in browser makes SharePoint deliver the real file.
"""
import asyncio
import json
import os
import pathlib
import time
import urllib.request

PORT = int(os.environ.get("SHHKIT_CDP_PORT", "9233"))


async def _run(url, outdir, timeout=90):
    import websockets

    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=5) as r:
        ws_url = json.load(r)["webSocketDebuggerUrl"]
    outdir = pathlib.Path(outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    async with websockets.connect(ws_url, max_size=2**28) as ws:
        n = 0

        async def call(method, sid=None, **params):
            nonlocal n
            n += 1
            mid = n
            msg = {"id": mid, "method": method, "params": params}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == mid:
                    if "error" in m:
                        raise RuntimeError(f"{method}: {m['error']}")
                    return m.get("result", {})

        await call("Browser.setDownloadBehavior", behavior="allowAndName",
                   downloadPath=str(outdir), eventsEnabled=True)
        tid = (await call("Target.createTarget", url="about:blank"))["targetId"]
        sid = (await call("Target.attachToTarget", targetId=tid, flatten=True))["sessionId"]
        try:
            await call("Page.navigate", sid, url=url)
            deadline = time.time() + timeout
            last = None
            while time.time() < deadline:
                await asyncio.sleep(0.5)
                cands = [p for p in outdir.iterdir()
                         if p.is_file() and p.stat().st_mtime >= start - 1]
                if cands:
                    p = max(cands, key=lambda x: x.stat().st_mtime)
                    size = p.stat().st_size
                    if size and size == last:
                        await asyncio.sleep(1.0)
                        return str(p)
                    last = size
            return None
        finally:
            await call("Target.closeTarget", targetId=tid)


def download(url, outdir="~/courses/programme/raw", timeout=90):
    """Return the downloaded path (CDP GUID name) or None. Rename with move()."""
    return asyncio.run(_run(url, outdir, timeout))


def move(src, dest):
    dest = pathlib.Path(dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(src).rename(dest)
    return dest
