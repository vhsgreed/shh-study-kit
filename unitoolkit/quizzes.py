"""Canvas New Quizzes (Instructure quiz-lti) over CDP: read a question, answer it, submit.

New Quizzes is not Canvas Classic Quizzes: the assignment is an LTI link and the whole quiz runs
inside a cross-origin iframe (`*.quiz-lti-*.instructure.com`). There is no Canvas REST route for
it, so the only way in is the signed-in agent browser, attaching to that iframe's own CDP target
(an OOPIF) and driving its DOM.

Everything here is deliberately observation-first: `state()` reports what it can see (question
text, radios and checkboxes with their labels and checked state, text fields, contenteditable
blocks, buttons) without assuming class names, because Instructure's markup is generated and
changes. Answering uses label text, not indices, so it survives option shuffling.

Read the flow as: open(url) -> state() -> answer(...) -> state() -> submit() -> result().
"""
import asyncio
import json
import re
import urllib.request

from . import browser

# New Quizzes renders questions with Learnosity inside a second cross-origin iframe,
# and the LTI shell is quiz-lti; both are candidates for where the DOM lives.
QUIZ_HOST = re.compile(r'quiz-lti|quizzes\.next|learnosity', re.I)

# ---------------------------------------------------------------- DOM inspection (in page)
SCAN_JS = r"""
(() => {
  const seen = new Set();
  const vis = el => !!(el.offsetParent || (el.getClientRects && el.getClientRects().length));
  const txt = el => (el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const labelFor = (input) => {
    if (input.id) {
      const l = document.querySelector('label[for="' + CSS.escape(input.id) + '"]');
      if (l && txt(l)) return txt(l);
    }
    const wrap = input.closest('label');
    if (wrap && txt(wrap)) return txt(wrap);
    const byAria = input.getAttribute('aria-label');
    if (byAria) return byAria.trim();
    const lb = input.getAttribute('aria-labelledby');
    if (lb) {
      const parts = lb.split(/\s+/).map(id => txt(document.getElementById(id))).filter(Boolean);
      if (parts.length) return parts.join(' ');
    }
    let p = input.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      if (/option|answer|choice|row/i.test(p.className || '') && txt(p)) return txt(p);
    }
    return '';
  };
  const container = (el) => {
    let p = el;
    for (let i = 0; i < 8 && p; i++, p = p.parentElement) {
      if (/question|item|problem|prompt/i.test((p.className || '') + ' ' + (p.getAttribute('data-testid') || ''))) return p;
    }
    return el.parentElement;
  };
  const radios = [...document.querySelectorAll('input[type=radio]')].filter(vis).map(el => ({
    kind: 'radio', label: labelFor(el), checked: el.checked, name: el.name,
    question: txt(container(el)).slice(0, 600),
  }));
  const checks = [...document.querySelectorAll('input[type=checkbox]')].filter(vis).map(el => ({
    kind: 'checkbox', label: labelFor(el), checked: el.checked, name: el.name,
    question: txt(container(el)).slice(0, 600),
  }));
  const fields = [...document.querySelectorAll('input[type=text], textarea')].filter(vis).map(el => ({
    kind: el.tagName.toLowerCase(), value: el.value, placeholder: el.placeholder || '',
    aria: el.getAttribute('aria-label') || '',
    question: txt(container(el)).slice(0, 600),
  }));
  const editables = [...document.querySelectorAll('[contenteditable="true"]')].filter(vis).map(el => ({
    kind: 'contenteditable', value: txt(el), question: txt(container(el)).slice(0, 600),
  }));
  const buttons = [...document.querySelectorAll('button, [role=button], input[type=submit]')].filter(vis)
    .map(el => ({ text: txt(el) || el.value || '', disabled: !!el.disabled }))
    .filter(b => b.text);
  const bodyText = txt(document.body).slice(0, 4000);
  return {radios, checks, fields, editables, buttons,
          progress: (bodyText.match(/(?:fråga|question)\s*\d+\s*(?:av|of)\s*\d+/i) || [])[0] || null,
          bodyText};
})()
"""

# ---------------------------------------------------------------- actions (in page)
SET_CHOICE_JS = r"""
(({label, kind}) => {
  const txt = el => (el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const want = label.toLowerCase();
  const cands = [...document.querySelectorAll('input[type=' + kind + '], [role=' + kind + ']')];
  const labelFor = (input) => {
    if (input.id) { const l = document.querySelector('label[for="' + CSS.escape(input.id) + '"]'); if (l) return txt(l); }
    const wrap = input.closest('label'); if (wrap) return txt(wrap);
    if (input.getAttribute('aria-label')) return input.getAttribute('aria-label');
    let p = input.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) { if (txt(p)) return txt(p); }
    return '';
  };
  const hit = cands.find(el => labelFor(el).toLowerCase().includes(want));
  if (!hit) return {ok: false, error: 'no option label contains ' + JSON.stringify(label),
                    options: cands.map(labelFor)};
  hit.click();
  const ev = t => hit.dispatchEvent(new Event(t, {bubbles: true}));
  ev('input'); ev('change');
  return {ok: true, chosen: labelFor(hit)};
})
"""

SET_TEXT_JS = r"""
(({value, index}) => {
  const vis = el => !!(el.offsetParent || (el.getClientRects && el.getClientRects().length));
  const fields = [...document.querySelectorAll('input[type=text], textarea')].filter(vis);
  const el = fields[index || 0];
  if (!el) return {ok: false, error: 'no text field at index ' + (index || 0)};
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  el.focus();
  setter.call(el, value);
  ['input', 'change', 'blur'].forEach(t => el.dispatchEvent(new Event(t, {bubbles: true})));
  return {ok: true, value: el.value};
})
"""

SET_EDITABLE_JS = r"""
(({value, index}) => {
  const vis = el => !!(el.offsetParent || (el.getClientRects && el.getClientRects().length));
  const els = [...document.querySelectorAll('[contenteditable="true"]')].filter(vis);
  const el = els[index || 0];
  if (!el) return {ok: false, error: 'no contenteditable at index ' + (index || 0)};
  el.focus();
  const range = document.createRange(); range.selectNodeContents(el);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  document.execCommand('insertText', false, value);
  return {ok: true, value: (el.innerText || '').trim()};
})
"""

CLICK_BUTTON_JS = r"""
(({text}) => {
  const txt = el => (el.innerText || el.value || '').replace(/\s+/g, ' ').trim();
  const btns = [...document.querySelectorAll('button, [role=button], input[type=submit]')]
    .filter(el => !el.disabled && (el.offsetParent || el.getClientRects().length));
  const want = text.toLowerCase();
  const hit = btns.find(b => txt(b).toLowerCase().includes(want));
  if (!hit) return {ok: false, error: 'no enabled button contains ' + JSON.stringify(text),
                    buttons: btns.map(txt).filter(Boolean)};
  hit.click();
  return {ok: true, clicked: txt(hit)};
})
"""


# ---------------------------------------------------------------- CDP plumbing
def _targets():
    with urllib.request.urlopen(f'{browser.base()}/json', timeout=5) as r:
        return json.load(r)


def quiz_tab():
    """The page target whose URL is an assignment/quiz page we opened, else None."""
    for t in _targets():
        if t.get('type') == 'page' and re.search(r'/assignments/\d+|/courses/\d+/quizzes', t.get('url', '')):
            return t
    return None


async def _run_in_quiz(worker, open_url=None):
    """Attach to the quiz page plus its LTI iframe(s), then run `worker(ctx)`.

    ctx.eval(expr, frame=True) returns the value of a JS expression, evaluated in the LTI
    iframe when one is present (that is where the quiz DOM lives), else in the top page.
    """
    tab = quiz_tab()
    if tab is None:
        if not open_url:
            raise RuntimeError('no quiz page open: pass open_url or call open() first')
        tab = browser.new_tab(open_url)
        await asyncio.sleep(6)

    import websockets
    url = tab['webSocketDebuggerUrl']
    async with websockets.connect(url, max_size=64 * 2 ** 20) as ws:
        n = 0

        async def call(method, session_id=None, **params):
            nonlocal n
            n += 1
            msg = {'id': n, 'method': method, 'params': params}
            if session_id:
                msg['sessionId'] = session_id
            await ws.send(json.dumps(msg))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get('id') == n:
                    if 'error' in reply:
                        raise RuntimeError(f'{method}: {reply["error"].get("message")}')
                    return reply.get('result', {})

        class Ctx:
            def __init__(self):
                self.session_id = None
                self.frames = []

            async def discover(self):
                await call('Page.enable')
                await call('Runtime.enable')
                targets = await call('Target.getTargets')
                self.frames = [t for t in targets.get('targetInfos', [])
                               if t.get('type') in ('iframe', 'other') and QUIZ_HOST.search(t.get('url', ''))]
                if self.frames:
                    att = await call('Target.attachToTarget', targetId=self.frames[0]['targetId'], flatten=True)
                    self.session_id = att.get('sessionId')
                    await asyncio.sleep(1.5)
                return self

            async def eval(self, expr, frame=True):
                r = await call('Runtime.evaluate', session_id=self.session_id if frame else None,
                               expression=expr, awaitPromise=True, returnByValue=True)
                if r.get('exceptionDetails'):
                    raise RuntimeError('JS: ' + str(r['exceptionDetails'].get('text'))[:300])
                return r.get('result', {}).get('value')

        ctx = await Ctx().discover()
        return await worker(ctx)


def _sync(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- public surface
START_TEXTS = ('ta testet igen', 'gör om testet', 'starta testet', 'ta testet', 'start', 'begin')


def open_quiz(url, auto_start=True):
    """Open the assignment page, wait for the LTI frame, and enter the attempt if a start
    button is showing (New Quizzes shows one until an attempt is in progress)."""
    t = browser.new_tab(url)
    _sync(asyncio.sleep(7))
    st = state()
    started = None
    if auto_start and isinstance(st, dict) and not st.get('questions'):
        for text in START_TEXTS:
            r = click_button(text)
            if isinstance(r, dict) and r.get('ok'):
                started = r.get('clicked')
                _sync(asyncio.sleep(6))
                st = state()
                break
    return {'opened': url, 'tab': t.get('id'), 'started': started, 'state': st}


def state(url=None):
    """Everything currently observable: question text, options with checked flags, fields, buttons."""

    async def worker(ctx):
        raw = await ctx.eval(SCAN_JS)
        if not raw:
            return {'error': 'the quiz frame produced no DOM; is the LTI frame loaded and signed in?',
                    'frames': [f.get('url') for f in ctx.frames]}
        return {'quiz_frame': (ctx.frames[0]['url'] if ctx.frames else 'top page'), **shape(raw)}

    try:
        return _sync(_run_in_quiz(worker, open_url=url))
    except Exception as e:  # noqa: BLE001
        return {'error': str(e)}


def shape(raw):
    """Turn the raw scan into something an agent reads without guessing.

    One entry per question-like group: the question text, the options with `checked`, any text
    fields, and the buttons that are enabled. Grouping is by the question container text, which is
    what Instructure renders per item, not by a fixed class name.
    """
    out = {}
    for r in raw.get('radios', []):
        q = out.setdefault(r['question'][:400], {'type': 'single choice', 'options': [], 'checked': None})
        q['options'].append(r['label'])
        if r['checked']:
            q['checked'] = r['label']
    for c in raw.get('checks', []):
        q = out.setdefault(c['question'][:400], {'type': 'multiple choice', 'options': [], 'checked': []})
        q['options'].append(c['label'])
        if c['checked']:
            q['checked'].append(c['label'])
    for f in raw.get('fields', []) + raw.get('editables', []):
        q = out.setdefault(f['question'][:400], {'type': 'text', 'value': f.get('value') or '',
                                                 'placeholder': f.get('placeholder', '')})
    return {'progress': raw.get('progress'), 'questions': out,
            'buttons': [b['text'] for b in raw.get('buttons', []) if not b['disabled']],
            'body_excerpt': (raw.get('bodyText') or '')[:1200]}


def answer(text=None, choice=None, index=0, kind=None, url=None):
    """Answer the open question: `choice` picks an option by label text; `text` fills a field.

    Label matching is case-insensitive substring, so it survives a reshuffled quiz.
    """
    if choice:
        async def worker_choice(ctx):
            k = kind or 'radio'
            expr = f'({SET_CHOICE_JS})({json.dumps({"label": choice, "kind": k})})'
            return await ctx.eval(expr)
        return _safe(_run_in_quiz(worker_choice, open_url=url))

    async def worker_text(ctx):
        editable = await ctx.eval(f'({SET_EDITABLE_JS})({json.dumps({"value": text or "", "index": index})})')
        if editable and editable.get('ok'):
            return editable
        expr = f'({SET_TEXT_JS})({json.dumps({"value": text or "", "index": index})})'
        return await ctx.eval(expr)

    return _safe(_run_in_quiz(worker_text, open_url=url))


def click_button(text, url=None):
    async def worker(ctx):
        return await ctx.eval(f'({CLICK_BUTTON_JS})({json.dumps({"text": text})})')

    return _safe(_run_in_quiz(worker, open_url=url))


def submit_quiz(url=None, confirm=('skicka in', 'submit', 'lämna in')):
    """Click the submit control and report what the page says afterwards. Deliberately explicit:
    callers should have read `state()` first, because a submit cannot be undone."""
    for text in confirm:
        r = click_button(text, url=url)
        if isinstance(r, dict) and r.get('ok'):
            _sync(asyncio.sleep(4))
            after = state(url)
            return {'clicked': r.get('clicked'), 'after': after}
    return {'error': 'no submit button found', 'tried': list(confirm)}


def close_quiz():
    t = quiz_tab()
    if not t:
        return {'closed': False, 'reason': 'no quiz tab open'}
    browser.close_tab(t['id'])
    return {'closed': True, 'tab': t['id']}


def _safe(coro):
    try:
        return _sync(coro)
    except Exception as e:  # noqa: BLE001
        return {'error': str(e)}
