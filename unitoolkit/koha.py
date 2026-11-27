"""Koha (biblioteket.shh.se) through the proxied agent browser.

Why a browser and not HTTP: the library sits behind Cloudflare, which answers plain
HTTP clients (including Cloudflare Workers and Apify's HTTP proxy) with 429. A real
Chrome through a residential proxy gets through, and the catalogue is rendered by
JavaScript anyway, so the browser is the only reliable reader.

Transport: Chrome with --proxy-server=http://127.0.0.1:8118 (a local chaining proxy
that adds Apify residential credentials; see scripts/apify_chain_proxy.py in the
school-helper profile). Read-only: search, item availability, your own loans.
"""
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CDP = os.environ.get('KOHA_CDP', 'http://127.0.0.1:9334')
CHAIN_PORT = int(os.environ.get('KOHA_CHAIN_PORT', '8118'))
PROFILE = os.environ.get('KOHA_CHROME_PROFILE',
                         str(Path.home() / '.cache/unitoolkit/koha-chrome'))
PROXY_PASSWORD_FILE = os.path.expanduser(os.environ.get('CHAIN_UPSTREAM_PASSWORD_FILE',
                                                        '~/.secrets/APIFY_PROXY_PASSWORD'))
# Chrome flags for the library profile. SameSite enforcement is off because the SSO hand-back
# is a cross-site POST and Koha's CGISESSID is SameSite=Lax (dropped after a 2-minute grace
# that two-factor outlasts). This profile holds no other sessions.
CHROME_FLAGS = ['--no-first-run', '--no-default-browser-check',
                '--disable-features=SameSiteByDefaultCookies,CookiesWithoutSameSiteMustBeSecure']


def _chrome_cmd():
    """How to start Chrome here: KOHA_CHROME_SCRIPT, else a Chrome on PATH, else flatpak."""
    import shutil
    script = os.environ.get('KOHA_CHROME_SCRIPT', '')
    if script and Path(script).exists():
        return [script]
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
        exe = shutil.which(name)
        if exe:
            return [exe]
    if shutil.which('flatpak'):
        return ['flatpak', 'run', 'com.google.Chrome']
    raise KohaError('no Chrome found: install Chrome or set KOHA_CHROME_SCRIPT')
BASE = 'https://biblioteket.shh.se'
SEARCH = BASE + '/cgi-bin/koha/opac-search.pl?idx=&q=%s&weight_search=1'
DETAIL = BASE + '/cgi-bin/koha/opac-detail.pl?biblionumber=%s'
USER = BASE + '/cgi-bin/koha/opac-user.pl'


class KohaError(RuntimeError):
    pass


def _port_open(port, host='127.0.0.1', timeout=1.0):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _cdp_up():
    try:
        urllib.request.urlopen(CDP + '/json/version', timeout=3)
        return True
    except Exception:
        return False


def ensure_browser(timeout=40):
    """Start the chain proxy and the proxied Chrome if they are not up. Idempotent."""
    use_proxy = os.environ.get('KOHA_PROXY', '1').lower() not in ('0', 'false', 'no', '')
    if use_proxy and not _port_open(CHAIN_PORT):
        if not Path(PROXY_PASSWORD_FILE).exists():
            raise KohaError('the library proxy is not running and %s is missing. If your network '
                            'reaches biblioteket.shh.se directly, set KOHA_PROXY=0.' % PROXY_PASSWORD_FILE)
        subprocess.Popen([sys.executable, '-m', 'unitoolkit.chainproxy'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(20):
            time.sleep(0.5)
            if _port_open(CHAIN_PORT):
                break
    if not _cdp_up():
        cmd = _chrome_cmd()
        if len(cmd) == 1 and os.environ.get('KOHA_CHROME_SCRIPT') == cmd[0]:
            args = cmd                                  # the script carries its own flags
        else:
            Path(PROFILE).mkdir(parents=True, exist_ok=True)
            args = cmd + ['--user-data-dir=' + PROFILE,
                          '--remote-debugging-port=' + CDP.rsplit(':', 1)[1]] + CHROME_FLAGS
            if use_proxy:
                args.append('--proxy-server=http://127.0.0.1:%d' % CHAIN_PORT)
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        for _ in range(int(timeout / 1.5)):
            time.sleep(1.5)
            if _cdp_up():
                return True
        # a cold start on a loaded machine can take longer than one wait: give it a second window
        # before declaring a fault, so a slow launch is not reported as a dead reader
        for _ in range(int(timeout / 1.5)):
            time.sleep(1.5)
            if _cdp_up():
                return True
        raise KohaError('proxied Chrome did not come up on %s (twice %ds)' % (CDP, timeout))
    return True


class _Page:
    """One CDP session against a page tab, synchronous on top of asyncio."""

    def __init__(self):
        import websockets  # noqa: F401  (checked here so the error names the dependency)
        self.loop = asyncio.new_event_loop()

    def __enter__(self):
        self.loop.run_until_complete(self._open())
        return self

    def __exit__(self, *exc):
        try:
            self.loop.run_until_complete(self._close())
        finally:
            self.loop.close()

    async def _open(self):
        import websockets
        ver = json.load(urllib.request.urlopen(CDP + '/json/version', timeout=5))
        self.ws = await websockets.connect(ver['webSocketDebuggerUrl'], max_size=None)
        tabs = json.load(urllib.request.urlopen(CDP + '/json/list', timeout=5))
        pages = [t for t in tabs if t.get('type') == 'page']
        target = pages[0]['id'] if pages else None
        if target is None:
            target = json.load(urllib.request.urlopen(urllib.request.Request(
                CDP + '/json/new?about:blank', method='PUT'), timeout=10))['id']
        res = await self.call('Target.attachToTarget', {'targetId': target, 'flatten': True})
        self.sid = res['sessionId']
        await self.call('Page.enable')
        await self.call('Runtime.enable')

    async def _close(self):
        try:
            await self.ws.close()
        except Exception:
            pass

    async def call(self, method, params=None):
        self.n = getattr(self, 'n', 0) + 1
        mine = self.n
        msg = {'id': mine, 'method': method, 'params': params or {}}
        if getattr(self, 'sid', None):
            msg['sessionId'] = self.sid
        await self.ws.send(json.dumps(msg))
        while True:
            raw = json.loads(await self.ws.recv())
            if raw.get('id') == mine:
                return raw.get('result', raw)
            # Events arrive between replies. Keep the recent ones: a login that fails with no
            # on-page message is diagnosed from the POST's status and the page it landed on.
            if raw.get('method') and hasattr(self, 'events'):
                self.events.append(raw)
                del self.events[:-40]

    def eval(self, expr):
        res = self.loop.run_until_complete(self.call(
            'Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True}))
        self.last_error = ((res.get('exceptionDetails') or {}).get('exception') or {}).get(
            'description') or ''
        return (res.get('result') or {}).get('value')

    def goto(self, url, marker=None, timeout=25):
        """Navigate and wait until marker (a JS expression returning truthy) or readyState."""
        self.loop.run_until_complete(self.call('Page.navigate', {'url': url}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.6)
            try:
                ok = self.eval('document.readyState === "complete"')
            except Exception:
                continue
            if not ok:
                continue
            if marker is None:
                return True
            try:
                if self.eval(marker):
                    return True
            except Exception:
                continue
        return False


SEARCH_JS = r"""(function(){
  var out = [];
  document.querySelectorAll('a[href*="opac-detail"]').forEach(function(a){
    var row = a.closest('tr') || a.closest('div.search-result') || a.parentElement.parentElement;
    var m = /biblionumber=(\d+)/.exec(a.getAttribute('href') || '');
    var txt = row ? row.innerText.replace(/\s+/g, ' ').trim() : '';
    var av = /Availability:\s*(.*?)(?: Save to lists| Add to cart|$)/.exec(txt);
    out.push({biblio: m ? m[1] : null,
              title: a.innerText.trim(),
              availability: av ? av[1].trim() : '',
              row: txt});
  });
  return JSON.stringify(out);
})()"""

ITEMS_JS = r"""(function(){
  var t = document.querySelector('#holdingst') || document.querySelector('table.table');
  var rows = [];
  if (t) { t.querySelectorAll('tr').forEach(function(r){
      var cells = [...r.querySelectorAll('td,th')].map(function(c){return c.innerText.replace(/\s+/g,' ').trim();});
      if (cells.join('').trim()) rows.push(cells); }); }
  var summ = document.querySelectorAll('.results_summary');
  return JSON.stringify({rows: rows,
    summary: [...summ].map(function(e){return e.innerText.replace(/\s+/g,' ').trim();}).slice(0, 8),
    title: document.title});
})()"""

CHECKOUTS_JS = r"""(function(){
  function grab(sel) {
    var t = document.querySelector(sel);
    if (!t) return null;
    return [...t.querySelectorAll('tr')].map(function(r){
      return [...r.querySelectorAll('td,th')].map(function(c){
        return c.innerText.replace(/\s+/g,' ').trim();}).join(' | ');
    }).filter(function(x){return x.replace(/\|/g,'').trim();});
  }
  return JSON.stringify({checkouts: grab('#checkouts_table'),
    holds: grab('#holdst') || grab('#reservest'),
    user: (document.querySelector('#logged-in-info, .loggedinusername') || {}).innerText || ''});
})()"""


SUBMIT_SEARCH_JS = r'''(function(q){
  var form = document.querySelector('form[action*="opac-search"]') || document.querySelector('#searchform');
  var input = document.querySelector('#transl1') || document.querySelector('input[name="q"]');
  if (!form || !input) return 'no form';
  input.value = q;
  form.submit();
  return 'submitted';
})(%s)'''


_tries = [0]


def search(q, limit=10):
    """Search through the catalogue form, like a person would.

    Loading opac-search.pl directly gets the home page back (Koha hands out a session
    on the front page first), so: open the front page, fill the search box, submit.
    """
    if not q or not str(q).strip():
        raise KohaError('empty query')
    ensure_browser()
    with _Page() as p:
        if not p.goto(BASE, marker="!!document.querySelector('#transl1, input[name=\"q\"]')"):
            raise KohaError('the catalogue front page did not load (proxy or Cloudflare problem?)')
        state = p.eval(SUBMIT_SEARCH_JS % json.dumps(str(q)))
        if state != 'submitted':
            raise KohaError('could not find the search form on the front page')
        ok = False
        for _ in range(25):
            time.sleep(1)
            try:
                if p.eval("document.querySelectorAll('a[href*=\"opac-detail\"]').length > 0"):
                    ok = True
                    break
            except Exception:
                pass
        rows = json.loads(p.eval(SEARCH_JS) or '[]')
        if not ok and not rows:
            raise KohaError('search returned no result page (proxy, Cloudflare or zero hits)')
    # Sometimes the catalogue answers a search with the account page's login message instead
    # of results. That row is not a hit: dropping it, and retrying once, turns a bogus
    # "Log in using your SHH account" into either results or an honest error.
    rows = [r for r in rows if not (r.get('biblio') is None and
            (r.get('title') or '').lower().startswith('log in'))]
    if not rows:
        if _tries[0] >= 1:
            raise KohaError('the catalogue served its login page instead of results for %r '
                            '(session or Cloudflare, not an empty result)' % str(q)[:60])
        _tries[0] += 1
        try:
            return search(q, limit)
        finally:
            _tries[0] = 0
    return rows[:limit]


def availability(biblionumber):
    if not str(biblionumber).isdigit():
        raise KohaError('biblionumber must be digits')
    ensure_browser()
    with _Page() as p:
        p.goto(DETAIL % biblionumber, marker="document.querySelectorAll('.results_summary').length > 0")
        data = json.loads(p.eval(ITEMS_JS) or '{}')
    return data


def _secret(name):
    path = Path.home() / '.secrets' / name
    if not path.exists():
        return None
    value = path.read_text().strip()
    return value or None


def credentials():
    """(userid, pin) from ~/.secrets/KOHA_CREDENTIALS (two lines: personnummer, pin).

    Falls back to the separate KOHA_USER / KOHA_PIN files. Never printed, never logged,
    and only ever posted to the library's own login form.
    """
    path = Path(os.environ.get('KOHA_CREDENTIALS_FILE',
                               str(Path.home() / '.secrets' / 'KOHA_CREDENTIALS')))
    if path.exists():
        lines = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1]
    user, pin = _secret('KOHA_USER'), _secret('KOHA_PIN')
    return (user, pin) if user and pin else (None, None)


LOGIN_JS = r"""(function(user, pin){
  var f = document.querySelector('#modalAuth') || document.querySelector('form[action*="opac-user"]');
  if (!f) return 'no form';
  var u = f.querySelector('input[name=login_userid], #userid, input[name=userid]');
  var s = f.querySelector('input[name=login_password], #password, input[name=password]');
  if (!u || !s) return 'no fields';
  u.value = user; s.value = pin;
  var ctx = f.querySelector('input[name=koha_login_context]');
  if (ctx) ctx.value = 'opac';
  var op = f.querySelector('input[name=login_op]');
  if (op && !op.value) op.value = 'cud-login';   // Koha ignores the POST without it
  if (f.requestSubmit) f.requestSubmit(); else f.submit();
  return 'submitted';
})(%s, %s)"""

# Tolerant scrape: keep the raw cells, then derive the fields people ask about.
ACCOUNT_JS = r"""(function(){
  function table(sel){
    var t = document.querySelector(sel);
    if (!t) return null;
    var rows = [];
    t.querySelectorAll('tr').forEach(function(r){
      var cells = [...r.querySelectorAll('td,th')].map(function(c){
        return c.innerText.replace(/\s+/g,' ').trim(); });
      if (cells.join('').trim()) rows.push(cells);
    });
    return rows;
  }
  function firstLink(sel){
    var t = document.querySelector(sel);
    if (!t) return null;
    var a = t.querySelector('a[href*="opac-detail"]');
    return a ? a.innerText.replace(/\s+/g,' ').trim() : null;
  }
  var body = document.body.innerText;
  return JSON.stringify({
    logged_in: !!document.querySelector('#checkoutst, #checkouts_table, #user_summary, #opac-user-views, .loggedinusername'),
    user: ((document.querySelector('.loggedinusername, #user_summary, .user-info') || {}).innerText || '')
        .replace(/\s+/g, ' ').trim().slice(0, 120),
    checkouts: table('#checkoutst') || table('#checkouts_table'),
    holds: table('#holdst') || table('#reservest') || table('#holdst_table'),
    fines: table('#finest') || table('#fines_table') || table('#chargest'),
    overdues: table('#overdues_table'),
    message: ((document.querySelector('.alert, .dialog.alert, #login_error') || {}).innerText || '').trim().slice(0, 300),
    login_form: !!document.querySelector('#modalAuth input[name=login_userid]'),
    titles: {checkout_first: firstLink('#checkouts_table'), hold_first: firstLink('#holdst')}
  });
})()"""

_DATE = re.compile(r'(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})')


def _secret(name):
    path = Path.home() / '.secrets' / name
    if not path.exists():
        return None
    value = path.read_text().strip()
    return value or None


def _days_left(date_str):
    m = _DATE.search(date_str or '')
    if not m:
        return None
    raw = m.group(1)
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            d = __import__('datetime').datetime.strptime(raw, fmt).date()
            return (d - __import__('datetime').date.today()).days
        except ValueError:
            continue
    return None


def _normalise_account(raw):
    """Rows come back as table cells; name the columns people actually ask for."""
    def rows_of(key):
        return raw.get(key) or []

    checkouts = []
    for cells in rows_of('checkouts'):
        joined = ' | '.join(cells)
        if re.fullmatch(r'(?i)(titel|title|author|due|förfaller|checked out|\W)+', joined):
            continue                                   # header row
        if cells and cells[0].strip().lower() in ('title', 'titel'):
            continue
        dates = _DATE.findall(joined)
        due = cells[3] if len(cells) > 3 and _DATE.search(cells[3] or '') else (dates[-1] if dates else '')
        checkouts.append({
            'cells': cells,
            'title': cells[0] if cells else '',
            'author': cells[1] if len(cells) > 1 else '',
            'due': due,
            'days_left': _days_left(due),
        })
    holds = []
    for cells in rows_of('holds'):
        joined = ' | '.join(cells)
        if re.fullmatch(r'(?i)(titel|title|\W)+', joined):
            continue
        nums = [c for c in cells if c.strip().isdigit()]
        holds.append({
            'cells': cells,
            'title': cells[0] if cells else '',
            'queue_position': int(nums[-1]) if nums else None,
        })
    return {
        'logged_in': raw.get('logged_in'),
        'checkouts': checkouts,
        'checkouts_count': len(checkouts),
        'holds': holds,
        'holds_count': len(holds),
        'fines': rows_of('fines'),
        'overdues': rows_of('overdues'),
        'message': raw.get('message', ''),
    }


SHIB_LOGIN = BASE + ('/Shibboleth.sso/Login?target=https%3A%2F%2Fbiblioteket.shh.se'
                        '%2Fcgi-bin%2Fkoha%2Fopac-main.pl')

SSO_STATE_JS = r"""JSON.stringify({
  host: location.host, path: location.pathname,
  form: !!document.querySelector('#username') && !!document.querySelector('#password'),
  filled: ['#username','#password'].every(function(s){ var e = document.querySelector(s);
     return !!e && e.matches(':-webkit-autofill'); }),
  submit: !!document.querySelector('button[name=_eventId_proceed], input[name=_eventId_proceed]'),
  logged_in: !!document.querySelector('#checkouts_table, #user_summary'),
  error: ((document.querySelector('.form-error, .output--failure, .alert-danger') || {}).innerText || '').slice(0,200)
})"""

CLICK_JS = r"""(function(sel){
  var b = document.querySelector(sel);
  if (!b) return null;
  b.scrollIntoView({block:'center'});
  var r = b.getBoundingClientRect();
  return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
})(%s)"""


def _click(p, selector):
    """Real mouse click: Chrome only releases autofilled values after a user gesture."""
    xy = p.eval(CLICK_JS % json.dumps(selector))
    if not xy:
        return False
    xy = json.loads(xy)
    p.loop.run_until_complete(p.call('Input.dispatchMouseEvent',
        {'type': 'mousePressed', 'x': xy['x'], 'y': xy['y'], 'button': 'left', 'clickCount': 1}))
    p.loop.run_until_complete(p.call('Input.dispatchMouseEvent',
        {'type': 'mouseReleased', 'x': xy['x'], 'y': xy['y'], 'button': 'left', 'clickCount': 1}))
    return True


def login_sso(timeout=60):
    """Sign in to Koha through the SHH account (SWAMID), using Chrome's saved password.

    The library has no separate password: "Log in using your SHH account" sends you to
    Shibboleth, which forwards to the SHH IdP where Chrome already has the login saved.
    Nothing is read, typed or stored here: the script waits for the autofill and clicks.
    """
    ensure_browser()
    with _Page() as p:
        p.goto(USER, marker="!!document.querySelector('#checkouts_table, a[href*=Shibboleth]')")
        if p.eval("!!document.querySelector('#checkoutst, #checkouts_table, #user_summary')"):
            return 'already in'
        p.loop.run_until_complete(p.call('Page.navigate', {'url': SHIB_LOGIN}))
        clicked = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.5)
            try:
                st = json.loads(p.eval(SSO_STATE_JS) or '{}')
            except Exception:
                continue
            if st.get('logged_in'):
                return 'ok'
            if st.get('error'):
                raise KohaError('the SHH IdP refused the login: %s' % st['error'])
            if st.get('form') and st.get('filled') and not clicked:
                clicked = _click(p, 'button[name=_eventId_proceed], input[name=_eventId_proceed]')
            if clicked and st.get('host') == 'biblioteket.shh.se':
                time.sleep(1.5)
                if p.eval("!!document.querySelector('#checkoutst, #checkouts_table, #user_summary')"):
                    return 'ok'
        raise KohaError('SSO login did not finish in %ds (autofill empty, 2FA, or slow IdP)' % timeout)


# The library's "Log in using your SHH account" goes to Shibboleth, which hands off to
# Microsoft Entra (login.microsoftonline.com), not to a Shibboleth IdP form. Its login is
# a two-step wizard: e-mail/username, then password, then "stay signed in".
ENTRA_JS = r"""JSON.stringify({
  host: location.host, path: location.pathname,
  step: (function(){
    if (document.querySelector('#i0118')) return 'password';
    if (document.querySelector('#i0116, input[name=loginfmt]')) return 'user';
    if (document.querySelector('#idBtn_Back, #idSIButton9')) return 'staysignedin';
    return 'other';
  })(),
  filled: ['#i0116','input[name=loginfmt]','#i0118'].some(function(s){
     var e = document.querySelector(s); return !!e && e.matches(':-webkit-autofill'); }),
  error: ((document.querySelector('#usernameError, #passwordError, .alert-error') || {}).innerText || '').slice(0,200),
  logged_in: !!document.querySelector('#checkouts_table, #user_summary')
})"""


def _no_credentials():
    raise KohaError('no credentials: put your library username and 4-digit pinkod on two lines '
                    'in ~/.secrets/KOHA_CREDENTIALS (chmod 600). Read from disk, never printed.')


def _advance_entra(p, timeout=90):
    """Walk the Entra wizard, clicking only when Chrome has autofilled the field."""
    clicked = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            st = json.loads(p.eval(ENTRA_JS) or '{}')
        except Exception:
            continue
        if st.get('host') == 'biblioteket.shh.se':
            if p.eval("!!document.querySelector('#checkoutst, #checkouts_table, #user_summary')"):
                return 'ok'
            return 'back'
        if st.get('error'):
            raise KohaError('Microsoft login error: %s' % st['error'])
        step = st.get('step')
        if step in ('user', 'password') and st.get('filled') and step not in clicked:
            sel = '#i0116' if step == 'user' else '#i0118'
            if _click(p, sel) and _click(p, '#idSIButton9'):
                clicked.add(step)
        elif step == 'staysignedin' and 'stay' not in clicked:
            if _click(p, '#idBtn_Back') or _click(p, '#idSIButton9'):
                clicked.add('stay')
    return 'timeout'


def _post_trace(p):
    """What the last login POST got back, for a failure with nothing on the page."""
    bits = []
    for ev in getattr(p, 'events', []):
        if ev.get('method') != 'Network.responseReceived':
            continue
        r = ev['params']['response']
        if 'biblioteket.shh.se' in r.get('url', ''):
            bits.append('%s %s' % (r.get('status'), (r.get('url') or '')[:70]))
    page = p.eval("location.pathname + ' | ' + (document.title||'') + ' | ' + "
                  "((document.querySelector('#modalAuth, #loginform, form[action*=login]') || "
                  "{innerText: ''}).innerText || '').replace(/\\s+/g, ' ').slice(0, 120)")
    return '; '.join((['page: %s' % page] if page else []) + (bits[-3:] or ['no response seen']))


def _form_login(p, user, password):
    """Library form login, from a clean session.

    Koha binds a session to the client IP and rejects the POST at the second request if the
    address changed ("You are logging from a different IP address"), which is what a rotating
    proxy does. Start from a fresh session on the sticky exit, and load the page with cache
    bypassed so the CSRF token belongs to that session.
    """
    p.events = []
    p.loop.run_until_complete(p.call('Network.enable'))
    p.loop.run_until_complete(p.call('Storage.clearCookies', {}))
    p.goto(USER + '?n=%d' % int(time.time()),
           marker="!!document.querySelector('#modalAuth, a[href*=opac-user]')")
    p.loop.run_until_complete(p.call('Page.reload', {'ignoreCache': True}))
    time.sleep(4)
    if not p.eval("!!document.querySelector('#modalAuth input[name=login_userid]')"):
        _click(p, 'a[href*="opac-user"]')
        time.sleep(2)
    state = p.eval(LOGIN_JS % (json.dumps(user), json.dumps(password)))
    if getattr(p, 'last_error', ''):
        # A script error is not a submit. It was read as one until 2026-11-18, which turned
        # every broken login script into a silent "no answer" with no request sent.
        return 'js error: %s' % p.last_error.splitlines()[0][:140]
    # A successful submit navigates immediately, which destroys the JS context and makes the
    # evaluate return None. That is success, not a missing form.
    if state not in ('submitted', None):
        return 'no form (%s)' % state
    for _ in range(14):
        time.sleep(1.5)
        if p.eval("!!document.querySelector('#checkoutst, #checkouts_table, #user_summary')"):
            return 'ok'
        msg = p.eval("((document.querySelector('.alert, .dialog, #login_error, .form-error')||{}).innerText||'').replace(/\\s+/g,' ').slice(0,160)")
        if msg:
            return msg
    return 'no answer: %s' % _post_trace(p)


def _relax_samesite(p):
    """Rewrite the library's Lax cookies to SameSite=None before a SAML login.

    The Shibboleth hand-back from login.microsoftonline.com is a cross-site POST, and a
    SameSite=Lax cookie is not sent on those (Chrome only allows it in a 2-minute grace
    window, which two-factor authentication usually outlasts). The result is a login that
    succeeds at Microsoft and lands in Koha signed out. The SP's own state cookies are
    already None; CGISESSID is the one that needs relaxing.
    """
    ck = p.loop.run_until_complete(p.call('Storage.getCookies', {})) or {}
    changed = 0
    for c in ck.get('cookies', []):
        if 'biblioteket.shh.se' not in c.get('domain', ''):
            continue
        if (c.get('sameSite') or 'Lax') == 'None':
            continue
        p.loop.run_until_complete(p.call('Storage.setCookies', {'cookies': [{
            'name': c['name'], 'value': c['value'], 'domain': c['domain'], 'path': c['path'],
            'secure': True, 'httpOnly': c.get('httpOnly', False), 'sameSite': 'None',
            'expires': c.get('expires', -1)}]}))
        changed += 1
    return changed


def open_login():
    """Bring the library up and get it signed in, using ~/.secrets/KOHA_CREDENTIALS.

    The first version of this only opened the window and told the caller to sign in by hand, even
    when usable credentials were sitting on disk: on 2026-11-27 that sent a whole session chasing a
    human sign-in that the tooling could do itself. It now tries the real login first and falls back
    to the open-window instruction only when the credentials are missing or the library refuses.
    """
    try:
        acct = loans(timeout=25)
        if acct.get('logged_in'):
            return {'url': USER, 'signed_in': True, 'user': acct.get('user'),
                    'checkouts': acct.get('checkouts_count'), 'via': 'credentials'}
    except KohaError as e:
        reason = str(e)[:200]
    except Exception as e:  # noqa: BLE001
        reason = f'{type(e).__name__}: {e}'[:200]
    else:
        reason = 'login did not reach the account page'
    return _open_window(reason)


def _open_window(reason=''):
    """Bring the proxied Chrome to the library account page and leave it there.

    The one-time case: a human signs in ("Log in using your SHH account" or the library
    form), Chrome keeps the session cookie, and afterwards loans() needs no credentials.
    """
    ensure_browser()
    with _Page() as p:
        p.loop.run_until_complete(p.call('Network.enable'))
        p.goto(USER, marker="!!document.querySelector('#modalAuth, a[href*=Shibboleth], #checkouts_table')")
        relaxed = _relax_samesite(p)
        state = p.eval("!!document.querySelector('#checkouts_table, #user_summary')")
        return {'url': USER, 'signed_in': bool(state), 'cookies_relaxed': relaxed,
                'credentials_login_failed': reason or None,
                'next': None if state else ('sign in in the open window, then run koha_wait_login'
                                            ' (credentials in ~/.secrets/KOHA_CREDENTIALS did not work)')}


def wait_for_login(timeout=300):
    """Poll until a human finishes a sign-in in the open window.

    Superseded by open_login()/loans() for the normal case: those log in from the credentials file.
    Keep this only for the one-off (new library account, changed pinkod). A dropped CDP socket used
    to surface as a raw ConnectionClosedError, so it is reported in plain words instead.
    """
    """Wait until the library account page shows a signed-in session.

    For the one-time case: the human signs in by hand in the proxied Chrome window
    ("Log in using your SHH account"), and the profile keeps the session cookie. After
    that, `loans()` reads the account with no credentials at all.
    """
    ensure_browser()
    deadline = time.time() + timeout
    with _Page() as p:
        while time.time() < deadline:
            p.goto(USER, marker="!!document.querySelector('#checkouts_table, a[href*=Shibboleth], #modalAuth')")
            if p.eval("!!document.querySelector('#checkoutst, #checkouts_table, #user_summary')"):
                return 'signed in'
            time.sleep(5)
    raise KohaError('no signed-in library session after %ds' % timeout)


def loans(user=None, pin=None, timeout=60):
    """Your own Koha account: loans, holds, fines. Read-only.

    Signs in through the SHH account (SWAMID) with Chrome's saved password. If that is not
    available and ~/.secrets/KOHA_USER + KOHA_PIN exist, the library form is used instead.
    """
    ensure_browser()
    with _Page() as p:
        p.goto(USER, marker="!!document.querySelector('#modalAuth, #checkouts_table, a[href*=Shibboleth]')")
        raw = json.loads(p.eval(ACCOUNT_JS) or '{}')
        if not raw.get('logged_in'):
            _relax_samesite(p)
            p.loop.run_until_complete(p.call('Page.navigate', {'url': SHIB_LOGIN}))
            clicked = False
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(1.5)
                try:
                    st = json.loads(p.eval(SSO_STATE_JS) or '{}')
                except Exception:
                    continue
                if st.get('form') and st.get('filled') and not clicked:
                    clicked = _click(p, 'button[name=_eventId_proceed], input[name=_eventId_proceed]')
                if st.get('error'):
                    raise KohaError('the SHH IdP refused the login: %s' % st['error'])
                if st.get('host') == 'biblioteket.shh.se' or st.get('logged_in'):
                    raw = json.loads(p.eval(ACCOUNT_JS) or '{}')
                    if raw.get('logged_in'):
                        break
            else:
                raw = json.loads(p.eval(ACCOUNT_JS) or '{}')
            if not raw.get('logged_in'):
                if not (user and pin):
                    user, pin = credentials()
                # The SSO attempt may have left us on another host: come back to the
                # account page before using the library's own form.
                p.goto(USER, marker="!!document.querySelector('#modalAuth, #checkouts_table')")
                if not user or not pin:
                    _no_credentials()
                result = _form_login(p, user, pin)
                raw = json.loads(p.eval(ACCOUNT_JS) or '{}')
                if not raw.get('logged_in'):
                    raise KohaError('library form login failed: %s' % result)
        out = _normalise_account(raw)
        out['user'] = (raw.get('user') or '').strip()[:200]
        return out


def status():
    return {'cdp': CDP, 'cdp_up': _cdp_up(), 'chain_proxy': '127.0.0.1:%d' % CHAIN_PORT,
            'proxy_enabled': os.environ.get('KOHA_PROXY', '1').lower() not in ('0', 'false', 'no', ''),
            'chain_up': _port_open(CHAIN_PORT), 'profile': PROFILE,
            'credentials': all(credentials())}
