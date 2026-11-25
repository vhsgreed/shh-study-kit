"""Canvas New Quizzes (the LTI "Självrättande test"): read the current question, answer it, move.

Measured on SHH's Canvas, 2026-11-26, not assumed:
  * The assignment URL redirects to `/assignments/<id>/taking/<n>` and the questions render in the
    TOP page DOM (Instructure UI, React). The cross-origin frames present are Learnosity's xdomain
    XHR proxy and Canvas's post_message forwarder: neither holds the questions. (The first version
    of this module attached to those frames and read nothing.)
  * One question per screen. "Frågan på placeringen N" marks which one; "Nästa fråga" /
    "Föregående fråga" move. An attempt in progress shows "Fortsätt" first; a fresh one
    "Sätt igång".
  * Radios and checkboxes are real <input>s at opacity 0.0001 behind a styled label, so clicks go
    to the <label>, which is what a person clicks. Fill-in-the-blank is a textbox whose accessible
    name is "Fråga tom 1 av 1"; `fill` + Tab makes it save.
  * Answers are saved server-side as you go: they survive a full reload.

Driven with Playwright attached over CDP to the signed-in agent Chrome (connect_over_cdp), so it
rides the same session as every other tool and adds frame-aware waiting on top. Each call runs the
async API on a private thread, because the MCP server already owns an event loop.

Submitting is never implicit: `submit(confirm=True)` is the only path, and it reports the page
afterwards. Read `state()` before you call it, because a submission cannot be undone.
"""
import asyncio
import re
import threading

from . import browser

TYPES = ("Sant eller falskt", "Flera svar", "Flera val", "Fyll i den tomma rutan")
KIND = {"Sant eller falskt": "true/false", "Flera svar": "multiple answers (checkboxes)",
        "Flera val": "single choice (radio)", "Fyll i den tomma rutan": "fill in the blank"}
BLANK = "Fråga tom"          # accessible name of a blank: "Fråga tom 1 av 1"
START = ("Fortsätt", "Sätt igång")

READ_JS = r"""() => {
  const t = e => (e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '');
  const lab = i => { if (i.id) { const l = document.querySelector('label[for="' + CSS.escape(i.id) + '"]'); if (l) return t(l); }
                     const w = i.closest('label'); return w ? t(w) : (i.getAttribute('aria-label') || ''); };
  const inputs = [...document.querySelectorAll('input[type=radio], input[type=checkbox], input[type=text], textarea')]
    .map(i => ({type: i.tagName === 'TEXTAREA' ? 'text' : i.type, label: lab(i), checked: !!i.checked,
                value: (i.type === 'text' || i.tagName === 'TEXTAREA') ? i.value : ''}));
  const nav = document.querySelector('[aria-label="Frågenavigatör"]');
  const navs = nav ? [...nav.querySelectorAll('button,a')].map(b => (b.getAttribute('aria-label') || b.innerText || '').replace(/\s+/g, ' ').trim()) : [];
  return {body: t(document.body), inputs, nav: navs};
}"""


def _thread(coro_fn, *a, **kw):
    out = {}

    def run():
        try:
            out["v"] = asyncio.run(coro_fn(*a, **kw))
        except Exception as e:  # noqa: BLE001
            out["v"] = {"error": f"{type(e).__name__}: {e}"[:600]}

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(240)
    return out.get("v", {"error": "timed out after 240 s"})


def parse(raw):
    """Pure: turn the page text + inputs into the current question. Tested offline."""
    body = raw.get("body") or ""
    m = list(re.finditer(r"Frågan på placeringen (\d+)", body))
    if not m:
        return {"position": None, "type": None, "stem": None, "options": [], "blanks": [],
                "note": "no question on screen (start page, result page, or not signed in)"}
    n = int(m[-1].group(1))
    tail = body[m[-1].end():]
    end = re.search(r"Föregående fråga|Nästa fråga|Lämna in|Skicka in", tail)
    seg = tail[:end.start()] if end else tail
    # the type label sits in the header just before the stem: between the first and the last
    # "Frågan på placeringen N" (the page repeats it), else in the 120 chars before the only one
    head = body[m[0].end():m[-1].start()] if len(m) > 1 else body[max(0, m[0].start() - 120):m[0].start()]
    qtype = next((t for t in TYPES if t in head), None)
    opts = [i for i in raw.get("inputs", []) if i["type"] in ("radio", "checkbox")]
    blanks = [i for i in raw.get("inputs", []) if i["type"] == "text"]
    stem = seg
    for o in opts:                      # the option labels follow the stem; cut them off
        k = stem.find(o["label"])
        if o["label"] and k > 0:
            stem = stem[:k]
            break
    stem = re.sub(BLANK + r" \d+ av \d+", "___", stem).strip(' "')
    # the "Frågenavigatör" strip lists every question: "Navigera till frågan på placeringen 3, obesvarad"
    nav = [re.search(r"placeringen (\d+)(, obesvarad)?", s) for s in raw.get("nav", [])]
    nav = [x for x in nav if x]
    total = max([int(x.group(1)) for x in nav] or [n])
    unanswered = [int(x.group(1)) for x in nav if x.group(2)]
    return {"position": n, "of": total, "unanswered": unanswered, "type": KIND[qtype] if qtype else None, "stem": stem,
            "options": [{"label": o["label"], "checked": o["checked"]} for o in opts],
            "blanks": [b["value"] for b in blanks]}


# ------------------------------------------------------------------------- browser side
async def _page(pw, url=None):
    b = await pw.chromium.connect_over_cdp(browser.base())
    ctx = b.contexts[0]
    page = next((p for p in ctx.pages if "/taking/" in p.url), None)
    if page is None:
        if not url:
            raise RuntimeError("no quiz open: pass url= (the assignment URL)")
        page = await ctx.new_page()
        await page.set_viewport_size({"width": 1280, "height": 1400})
        await page.goto(url, wait_until="domcontentloaded")
        if browser._bounced(page.url):               # Canvas session gone: SAML back in
            await page.goto(browser.CANVAS_SSO)
            await page.wait_for_url(re.compile(re.escape(browser.CANVAS_HOST)), timeout=30000)
            await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)
    if "Frågan på placeringen" not in await page.locator("body").inner_text():
        for name in START:
            btn = page.get_by_role("button", name=name, exact=True)
            if await btn.count():
                await btn.first.click()
                await page.wait_for_timeout(5000)
                break
    return page


async def _read(page):
    return parse(await page.evaluate(READ_JS))


async def _goto(page, n):
    link = page.get_by_role("button", name=re.compile(r"^Navigera till frågan på placeringen %d(,|$)" % n))
    if await link.count():                       # the navigator jumps straight there
        await link.first.click()
        await page.wait_for_timeout(1500)
        if (await _read(page))["position"] == n:
            return True
    for _ in range(40):                          # fallback: step with Nästa / Föregående
        cur = (await _read(page))["position"]
        if cur == n:
            return True
        if cur is None:
            return False
        name = "Nästa fråga" if cur < n else "Föregående fråga"
        await page.get_by_role("button", name=re.compile(name)).last.click()
        await page.wait_for_timeout(1500)
    return False


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def plan(options, choices, multi):
    """Pure: which option labels to click so the checked set becomes `choices`. Tested offline.
    Returns (clicks, error)."""
    want = [_norm(c) for c in choices]
    labels = [o["label"] for o in options]
    missing = [c for c, w in zip(choices, want) if not any(w in _norm(lab) for lab in labels)]
    if missing:
        return [], f"no option matches {missing}; options are {labels}"
    hits = {lab for lab in labels if any(w and w in _norm(lab) for w in want)}
    if not multi and len(hits) != 1:
        return [], f"single-answer question but {len(hits)} options matched: {sorted(hits)}"
    clicks = []
    for o in options:
        should = o["label"] in hits
        if should != o["checked"] and (multi or should):
            clicks.append(o["label"])
    return clicks, None


async def _answer(page, choices, text):
    q = await _read(page)
    if text is not None:
        boxes = page.get_by_role("textbox", name=re.compile(BLANK))
        if not await boxes.count():
            return {"error": "this question has no blank", "question": q}
        await boxes.first.fill(text)
        await boxes.first.press("Tab")
        await page.wait_for_timeout(1500)
        return await _read(page)
    multi = bool(q["type"] and q["type"].startswith("multiple"))
    clicks, err = plan(q["options"], choices, multi)
    if err:
        return {"error": err, "question": q}
    for label in clicks:
        await page.locator("label").filter(has_text=label).first.click()
        await page.wait_for_timeout(600)
    await page.wait_for_timeout(1200)
    return await _read(page)


async def _op(op, url=None, position=None, choices=None, text=None, confirm=False):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        page = await _page(pw, url)
        if position and not await _goto(page, int(position)):
            return {"error": f"could not reach question {position}", "question": await _read(page)}
        if op == "state":
            return await _read(page)
        if op == "answer":
            return await _answer(page, choices or [], text)
        if op == "submit":
            if not confirm:
                return {"error": "submit needs confirm=true; read state first, it cannot be undone"}
            sub = re.compile(r"^(Lämna in|Skicka in)$")
            btn = page.get_by_role("button", name=sub)
            if not await btn.count():
                await _goto(page, (await _read(page)).get("of") or 1)
                btn = page.get_by_role("button", name=sub)
            if not await btn.count():
                return {"error": "no submit button found"}
            await btn.last.click()
            await page.wait_for_timeout(2500)
            dlg = page.get_by_role("dialog").get_by_role("button", name=re.compile(r"Lämna in|Skicka in|Bekräfta"))
            if await dlg.count():
                await dlg.last.click()
            await page.wait_for_timeout(6000)
            return {"submitted": True, "url": page.url,
                    "page": (await page.locator("body").inner_text())[:1500]}
        if op == "close":
            await page.close()
            return {"closed": True}
    return {"error": f"unknown op {op}"}


# ------------------------------------------------------------------------- public surface
def state(url=None, position=None):
    """The question on screen (or question `position`): number, type, stem, options with checked."""
    return _thread(_op, "state", url=url, position=position)


def answer(choice=None, text=None, position=None, url=None):
    """Answer the current (or `position`) question.

    choice: option label text, case-insensitive substring; several joined with "|" for
            multiple-answer questions, and the boxes are set to exactly that set.
    text:   the fill-in-the-blank value.
    Returns the question as read back after the change, so the caller sees what stuck.
    """
    if choice is None and text is None:
        return {"error": "answer needs choice= or text="}
    choices = [c.strip() for c in choice.split("|")] if choice else None
    return _thread(_op, "answer", url=url, position=position, choices=choices, text=text)


def submit(confirm=False, url=None):
    return _thread(_op, "submit", url=url, confirm=confirm)


def close():
    return _thread(_op, "close")
