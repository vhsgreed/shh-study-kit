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
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

CDP = os.environ.get('KOHA_CDP', 'http://127.0.0.1:9334')
CHAIN_PORT = int(os.environ.get('KOHA_CHAIN_PORT', '8118'))
PROFILE = os.environ.get(
    'KOHA_CHROME_PROFILE',
    str(Path.home() / '.hermes/profiles/school-helper/cache/scratch/koha-chrome'))
_HERE = Path(__file__).resolve().parents[1]
_DEFAULT_CHAIN = (_HERE / 'scripts/apify_chain_proxy.py')
if not _DEFAULT_CHAIN.exists():                      # older layout: the profile copy
    _DEFAULT_CHAIN = Path.home() / '.hermes/profiles/school-helper/scripts/apify_chain_proxy.py'
CHAIN_SCRIPT = os.environ.get('KOHA_CHAIN_SCRIPT', str(_DEFAULT_CHAIN))
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
    if not _port_open(CHAIN_PORT):
        if not Path(CHAIN_SCRIPT).exists() or not Path.home().joinpath('.secrets/APIFY_PROXY_PASSWORD').exists():
            raise KohaError('no chain proxy and no Apify credentials: cannot reach biblioteket.shh.se '
                            '(your home IP is dropped on :443)')
        subprocess.Popen([str(Path.home() / '.local/venvs/unitoolkit/bin/python'), CHAIN_SCRIPT],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(20):
            time.sleep(0.5)
            if _port_open(CHAIN_PORT):
                break
    if not _cdp_up():
        subprocess.Popen(['flatpak', 'run', 'com.google.Chrome', '--user-data-dir=' + PROFILE,
                          '--remote-debugging-port=' + CDP.rsplit(':', 1)[1],
                          '--no-first-run', '--no-default-browser-check',
                          '--proxy-server=http://127.0.0.1:%d' % CHAIN_PORT],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(int(timeout / 1.5)):
            time.sleep(1.5)
            if _cdp_up():
                return True
        raise KohaError('proxied Chrome did not come up on %s' % CDP)
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

    def eval(self, expr):
        res = self.loop.run_until_complete(self.call(
            'Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True}))
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


def loans():
    """Requires ~/.secrets/KOHA_USER and ~/.secrets/KOHA_PIN (you create them)."""
    user, pin = _secret('KOHA_USER'), _secret('KOHA_PIN')
    if not user or not pin:
        raise KohaError('no credentials: create ~/.secrets/KOHA_USER and ~/.secrets/KOHA_PIN '
                        '(chmod 600). They are read from disk and never printed.')
    ensure_browser()
    with _Page() as p:
        p.goto(USER, marker="document.querySelectorAll('#checkouts_table, #holdst, form[action*=\"opac-user\"]').length > 0")
        if p.eval("!!document.querySelector('#checkouts_table')"):
            return json.loads(p.eval(CHECKOUTS_JS) or '{}')
        filled = p.eval("""(function(){
          var u = document.querySelector('#login_userid, input[name=login_userid], #userid');
          var s = document.querySelector('#login_password, input[name=login_password], #password');
          var form = (u && u.closest('form')) || document.querySelector('form[action*="opac-user"]');
          if (!u || !s || !form) return 'no form';
          u.value = %s; s.value = %s;
          form.querySelectorAll('input[name="koha_login_context"]').forEach(function(i){i.value='opac';});
          form.submit();
          return 'submitted';
        })()""" % (json.dumps(user), json.dumps(pin)))
        if filled != 'submitted':
            raise KohaError('could not find the login form on the Koha page')
        for _ in range(20):
            time.sleep(1)
            if p.eval("!!document.querySelector('#checkouts_table')"):
                return json.loads(p.eval(CHECKOUTS_JS) or '{}')
            if p.eval("!!document.querySelector('.alert, .error')") and not p.eval(
                    "!!document.querySelector('#login_userid, input[name=login_userid]')"):
                break
        err = p.eval("(document.querySelector('.alert, .error') || {}).innerText || ''")
        raise KohaError('login did not reach the account page%s' % (': ' + err.strip()[:120] if err else ''))


def status():
    return {'cdp': CDP, 'cdp_up': _cdp_up(), 'chain_proxy': '127.0.0.1:%d' % CHAIN_PORT,
            'chain_up': _port_open(CHAIN_PORT), 'profile': PROFILE,
            'credentials': bool(_secret('KOHA_USER') and _secret('KOHA_PIN'))}
