"""Optional re-login for Ladok through the browser's own saved password.

Off by default. Enable with `LADOK_AUTOLOGIN=1` (or one-off `--login`).

What it does and does not do, because this is the part worth auditing:

* It never reads, stores, prints or types a password. It does not touch the
  password manager API either. It checks whether Chrome has filled the IdP form
  (the `:-webkit-autofill` pseudo-class) and then clicks the submit button with a
  real `Input.dispatchMouseEvent`. Chrome itself releases the saved value into
  the page, because a user gesture is what makes autofill usable.
* No field value is ever evaluated: the checks read booleans, element names and
  geometry only.
* It logs in as the human whose browser profile it is. There is no credential of
  its own, and it cannot log in anywhere the human is not already saved.

The flow: Ladok -> Shibboleth -> the institution's IdP. Only the institution's
IdP can be automated this way, since that is where the browser's saved login
lives, so `LADOK_IDP_ENTITY` names it (SWAMID entityID). With a single sign-on
session still alive, the IdP also lets it back in without showing the form at
all, which is why the re-login usually takes a second.
"""
import asyncio
import json

from .core import LADOK_ORIGIN, Fault

SHIB_LOGIN = ('https://student.ladok.se/Shibboleth.sso/Login'
              '?entityID=%s&target=https%%3A%%2F%%2Fstudent.ladok.se%%2Fstudent%%2Fauth%%2Fswamid%%2Flogin')

# Read-only description of the login page: names, types, autofill state. No values.
STATE_JS = r"""JSON.stringify({host: location.host, path: location.pathname,
 form: !!document.querySelector('#username') && !!document.querySelector('#password'),
 filled: ['#username','#password'].every(function (s) { var e = document.querySelector(s);
   return !!e && e.matches(':-webkit-autofill'); }),
 error: (document.querySelector('.output--failure, .form-error, .alert-danger') || {}).innerText || ''})"""
BTN_JS = r"""(function () { var b = document.querySelector('button[name=_eventId_proceed]');
 if (!b) return null; b.scrollIntoView({block: 'center'}); var r = b.getBoundingClientRect();
 return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2}); })()"""
AUTHED_JS = ("fetch('%s',{credentials:'include',headers:{accept:"
             "'application/vnd.ladok-studiedeltagande+json'}}).then(function (r) { return r.status; })")


def login_url(cfg):
    """Direct SWAMID login for the configured institution, skipping the
    'Logga in via lärosäte' picker (which is a JS app and not automatable)."""
    if not cfg.idp_entity:
        raise Fault('LADOK_IDP_ENTITY är inte satt: ange ditt lärosätes SWAMID entityID, '
                    't.ex. https://swamid2.shh.se/idp/shibboleth')
    from urllib.parse import quote
    return SHIB_LOGIN % quote(cfg.idp_entity, safe='')


async def _login(cfg, timeout=45.0):
    try:
        import websockets
    except ImportError:
        raise Fault('websockets saknas: python3 -m pip install websockets')
    import urllib.request
    import datetime as dt

    url = login_url(cfg)
    try:
        tab = json.load(urllib.request.urlopen(urllib.request.Request(
            cfg.cdp + '/json/new?about:blank', method='PUT'), timeout=10))
    except Exception as exc:
        raise Fault('kan inte öppna en flik i Chrome (%s)' % exc)

    deadline = dt.datetime.now() + dt.timedelta(seconds=timeout)
    try:
        async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=None) as ws:
            n = 0

            async def call(method, params=None):
                nonlocal n
                n += 1
                mine = n
                await ws.send(json.dumps({'id': mine, 'method': method, 'params': params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get('id') == mine:
                        return msg.get('result', msg)

            async def ev(expr):
                r = await call('Runtime.evaluate', {'expression': expr, 'awaitPromise': True,
                                                    'returnByValue': True})
                return (r.get('result') or {}).get('value')

            await call('Page.enable')
            await call('Page.navigate', {'url': url})
            clicked = False
            state = {}
            while dt.datetime.now() < deadline:
                await asyncio.sleep(1.5)
                try:
                    state = json.loads(await ev(STATE_JS) or '{}')
                except Exception:
                    continue                      # page is navigating
                host, path = state.get('host', ''), state.get('path', '')
                if host in ('student.ladok.se', 'www.student.ladok.se') and '/app/' in path:
                    code = await ev(AUTHED_JS % ('/student/proxy/%s/studiedeltagande/internal/'
                                                 'student/inloggad/som' % cfg.larosate))
                    if code == 200:
                        return 'ok'
                    raise Fault('tillbaka i Ladok men API svarar %s' % code)
                if 'swamid' in host or (state.get('form') and not clicked):
                    if state.get('error'):
                        raise Fault('IdP visar ett fel: sparat lösenord stämmer inte?')
                    if clicked or not state.get('filled'):
                        continue                  # let autofill do its work
                    xy = json.loads(await ev(BTN_JS) or 'null')
                    if not xy:
                        raise Fault('hittade ingen Login-knapp på IdP-sidan')
                    await asyncio.sleep(0.4)
                    for kind in ('mousePressed', 'mouseReleased'):
                        await call('Input.dispatchMouseEvent', {'type': kind, 'x': xy['x'],
                                   'y': xy['y'], 'button': 'left', 'clickCount': 1})
                    clicked = True
            if not clicked:
                raise Fault('Chrome fyllde aldrig i IdP-formuläret: inget sparat lösenord för '
                            'den här sidan, eller så kräver inloggningen något mer (2FA)')
            raise Fault('klickade Login men kom inte tillbaka till Ladok inom %ds' % timeout)
    finally:
        try:
            urllib.request.urlopen(urllib.request.Request(
                cfg.cdp + '/json/close/' + tab['id'], method='PUT'), timeout=10)
        except Exception:
            pass


def login(cfg):
    """Try to restore the Ladok session. Returns a short status line, raises Fault."""
    return asyncio.run(_login(cfg))
