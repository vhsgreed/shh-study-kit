"""Build the programme map: every course, its kursplan and kurslitteraturlista, downloaded.

Source: the public course-overview page on shh.se. Course names are plain text, and the
document links appear as unordered "Kursplan"/"Kurslitteraturlista" anchor pairs, so they
are NOT paired by position. Instead every document is downloaded and named by the course
code found inside it (the page's own index misaligns whenever a course omits a link).

Documents live in SharePoint as share links; `download.aspx?share=<docid>` returns the
real bytes to a logged-in browser (`unistudy.browser`). SHH publishes kursplaner as HTML
pages and kurslitteraturlistor as PDFs.

    python -m unistudy.programme
"""
import asyncio
import json
import pathlib
import re
import subprocess

from . import browser
from .sharepoint import fetch_share

LANDING = "https://www.shh.se/sv/utbildningar/grundniva/sjukskoterskeprogrammet-kurser/"
OUT = pathlib.Path("~/courses/programme").expanduser()
CODE_RE = re.compile(r"\b([0-9][A-ZÅÄÖ]{2,4}[0-9]+)\b")
COURSE_RE = re.compile(r"^(.+?),?\s*\(?([0-9][A-ZÅÄÖ]{2,4}[0-9]+)\)?,?\s*([\d.,]+)\s*hp\s*$")


async def _scrape(t):
    from .browser import _eval, _session

    async def steps(call):
        await call("Page.enable")
        await call("Page.navigate", url=LANDING)
        await asyncio.sleep(4)
        anchors = await _eval(call, (
            "JSON.stringify([...document.querySelectorAll('a')]"
            ".filter(a=>/^Kurs(plan|litteraturlista)$/.test(a.innerText.trim()))"
            ".map(a=>[a.innerText.trim(),a.href]))"))
        text = await _eval(call, "document.body.innerText")
        return json.loads(anchors), text

    return await _session(t["webSocketDebuggerUrl"], steps)


def _courses_from_text(text):
    out = []
    for line in text.split("\n"):
        line = line.strip()
        m = COURSE_RE.match(line)
        if m:
            out.append({"name": m.group(1).strip(), "code": m.group(2),
                        "hp": float(m.group(3).replace(",", "."))})
        elif re.search(r"[0-9][A-ZÅÄÖ]{2,4}[0-9]", line):
            code = CODE_RE.search(line)
            out.append({"name": re.sub(r"\(?[0-9][A-ZÅÄÖ]{2,4}[0-9]+\)?,?.*$", "", line).strip(),
                        "code": code.group(1), "hp": None})
    return out


def doc_text(path):
    p = pathlib.Path(path)
    if p.suffix in (".html", ".htm"):
        raw = p.read_text(errors="ignore")
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
    if p.suffix == ".pdf":
        try:
            return subprocess.run(["pdftotext", "-q", str(p), "-"], capture_output=True,
                                  text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    if p.suffix == ".docx":
        try:
            import html
            import zipfile
            x = zipfile.ZipFile(p).read("word/document.xml").decode("utf8", "ignore")
            return html.unescape(re.sub(r"<[^>]+>", " ", x.replace("</w:p>", "\n")))
        except Exception:
            return ""
    return ""


KNOWN_CODES = set()


def build(out=None):
    global KNOWN_CODES, OUT
    if out:
        OUT = pathlib.Path(out).expanduser()
    t = browser.new_tab(LANDING)
    try:
        anchors, text = asyncio.run(_scrape(t))
    finally:
        browser.close_tab(t["id"])
    courses = _courses_from_text(text)
    KNOWN_CODES = {c["code"] for c in courses}
    OUT.mkdir(parents=True, exist_ok=True)
    raw = OUT / "raw"
    raw.mkdir(exist_ok=True)
    got = []
    for i, (kind, share) in enumerate(anchors):
        body, ext = fetch_share(share)
        if not body:
            got.append({"kind": kind, "share": share, "error": "download failed"})
            continue
        kind_slug = "kursplan" if kind == "Kursplan" else "litteratur"
        tmp = raw / f"_tmp_{i}.{ext}"
        tmp.write_bytes(body)
        text_ = doc_text(tmp)
        codes = CODE_RE.findall(text_)
        known = [c for c in codes if c in KNOWN_CODES]
        code = (known or codes or [f"unknown{i}"])[0]
        final = raw / f"{code}_{kind_slug}.{ext}"
        if final.exists() and final.stat().st_size == tmp.stat().st_size:
            tmp.unlink()
        else:
            tmp.rename(final)
        got.append({"kind": kind, "code": code, "file": str(final), "bytes": len(body)})
    for c in courses:
        c["kursplan_file"] = next((g["file"] for g in got
                                   if g.get("code") == c["code"] and g["kind"] == "Kursplan"), None)
        c["litteratur_file"] = next((g["file"] for g in got
                                     if g.get("code") == c["code"] and g["kind"] == "Kurslitteraturlista"), None)
    (OUT / "programme.json").write_text(json.dumps({"courses": courses, "documents": got},
                                                   ensure_ascii=False, indent=1))
    return courses, got


def report(courses):
    lines = ["# Sjuksköterskeprogrammet 180 hp, kurskarta", "",
             f"{len(courses)} kurser enligt shh.se. Kursplan och kurslitteraturlista hämtade från "
             "programmets kursöversikt.", ""]
    for i, c in enumerate(courses, 1):
        lines.append(f"{i}. **{c['name']}** ({c['code']}): "
                     f"kursplan {'ja' if c.get('kursplan_file') else 'NEJ'}, "
                     f"litteratur {'ja' if c.get('litteratur_file') else 'NEJ'}")
    return "\n".join(lines)


if __name__ == "__main__":
    cs, docs = build()
    md = report(cs)
    (OUT / "KURSKARTA.md").write_text(md)
    print(md)
    print(f"\n{len(cs)} kurser, {sum(1 for c in cs if c.get('kursplan_file'))} kursplaner, "
          f"{sum(1 for c in cs if c.get('litteratur_file'))} litteraturlistor, "
          f"{sum(1 for d in docs if d.get('error'))} fel -> {OUT}")
