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
import os

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
# Logged out, the proxy answers 200 with the app's HTML shell, so the status alone proves
# nothing: count it as signed in only when the body is the student record (JSON).
AUTHED_JS = ("fetch('%s',{credentials:'include',headers:{accept:"
             "'application/vnd.ladok-studiedeltagande+json'}}).then(function (r) {"
             " return r.text().then(function (b) {"
             " return (r.status === 200 && /^\\s*[{\\[]/.test(b)) ? 200 : (r.status === 200 ? 'html' : r.status);"
             " }); })")


STUDENTWEBB = 'https://student.ladok.se/student/app/studentwebb/'

# The human route: "Logga in via ditt lärosäte" -> SeamlessAccess -> your institution.
# Link text in both languages; the institution is matched on the domain the picker shows
# under its name ("Sophiahemmet University / shh.se"), which survives renames and language.
PICKER_LINK_JS = r"""(function () {
 var a = [].slice.call(document.querySelectorAll('a,button')).filter(function (e) {
   return /lärosäte|through your university/i.test(e.innerText || ''); })[0];
 if (!a) return 'none'; a.click(); return 'clicked'; })()"""
PICK_IDP_JS = r"""(function (dom) {
 var hit = [].slice.call(document.querySelectorAll('a.institution, a.identityprovider, li a'))
   .filter(function (e) { return (e.innerText || '').toLowerCase().indexOf(dom) >= 0; })[0];
 if (hit) { hit.click(); return 'picked'; }
 var box = document.querySelector('#searchinput');
 if (!box) return 'no picker';
 Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(box, dom);
 box.dispatchEvent(new Event('input', {bubbles: true}));
 return 'searched'; })(%s)"""


def idp_domain(cfg):
    """'https://swamid2.shh.se/idp/shibboleth' -> 'shh.se'; LADOK_IDP_DOMAIN overrides."""
    import os
    from urllib.parse import urlparse
    dom = os.environ.get('LADOK_IDP_DOMAIN', '').strip().lower()
    if dom:
        return dom
    host = urlparse(cfg.idp_entity or '').hostname or ''
    return '.'.join(host.split('.')[-2:])


def login_url(cfg):
    """Direct SWAMID login for the configured institution (the fallback route)."""
    if not cfg.idp_entity:
        raise Fault('LADOK_IDP_ENTITY är inte satt: ange ditt lärosätes SWAMID entityID, '
                    't.ex. https://swamid2.shh.se/idp/shibboleth')
    from urllib.parse import quote
    return SHIB_LOGIN % quote(cfg.idp_entity, safe='')


async def _login(cfg, route='picker', timeout=45.0):
    try:
        import websockets
    except ImportError:
        raise Fault('websockets saknas: python3 -m pip install websockets')
    import urllib.request
    import datetime as dt

    url = STUDENTWEBB if route == 'picker' else login_url(cfg)
    domain = idp_domain(cfg)
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

            async def ev(expr, gesture=False):
                r = await call('Runtime.evaluate', {'expression': expr, 'awaitPromise': True,
                                                    'returnByValue': True, 'userGesture': gesture})
                return (r.get('result') or {}).get('value')

            await call('Page.enable')
            # Treat the tab as focused even when the window is not (screen locked, other
            # window on top), so autofill and focus behave as for a person at the desk.
            await call('Emulation.setFocusEmulationEnabled', {'enabled': True})
            # A tab opened over CDP starts in the background, and a background tab is not
            # rendered: a mouse event by coordinates then lands nowhere and Login is never
            # pressed. The autofill click needs the tab in front.
            await call('Page.bringToFront')
            await call('Page.navigate', {'url': url})
            clicked = False
            clicks = 0
            clicked_at = None
            link_clicked = False
            picked = 0
            state = {}
            while dt.datetime.now() < deadline:
                await asyncio.sleep(1.5)
                try:
                    state = json.loads(await ev(STATE_JS) or '{}')
                except Exception:
                    continue                      # page is navigating
                host, path = state.get('host', ''), state.get('path', '')
                if os.environ.get('LADOK_LOGIN_TRACE'):
                    print('  [%s] %s%s form=%s filled=%s' % (route, host, path, state.get('form'),
                                                          state.get('filled')), flush=True)
                if host in ('student.ladok.se', 'www.student.ladok.se') and '/app/' in path:
                    code = await ev(AUTHED_JS % ('/student/proxy/%s/studiedeltagande/internal/'
                                                 'student/inloggad/som' % cfg.larosate))
                    if code == 200:
                        return 'ok'
                    # Logged out: the studentwebb start page. Take the human route once,
                    # then let the SPA finish rendering before judging again.
                    if route == 'picker' and not link_clicked:
                        if await ev(PICKER_LINK_JS) == 'clicked':
                            link_clicked = True
                        continue
                    if route == 'picker' and not clicked:
                        continue
                    raise Fault('tillbaka i Ladok men API svarar %s' % code)
                if 'seamlessaccess' in host or '/ds/' in path:
                    if picked >= 3:
                        raise Fault('lärosätesväljaren hittar inte %s' % domain)
                    if await ev(PICK_IDP_JS % json.dumps(domain)) in ('picked', 'searched'):
                        picked += 1
                    continue
                if 'swamid' in host or (state.get('form') and not clicked):
                    if state.get('error'):
                        raise Fault('IdP visar ett fel: sparat lösenord stämmer inte?')
                    stuck = clicked and clicks < 2 and (dt.datetime.now() - clicked_at).total_seconds() > 8
                    if (clicked and not stuck) or not state.get('filled'):
                        continue                  # let autofill / the redirect do their work
                    # Chrome hands an autofilled password to the page only after a user
                    # gesture; until then the field reads empty and Login posts nothing.
                    # The first try is a gesture-flagged focus and click inside the page: it
                    # needs no drawn window, so it works minimized, covered or with the screen
                    # off (the unattended 07:10 run, where a coordinate click landed nowhere).
                    # The second try is a real mouse click, for when the window is drawn.
                    if clicks == 0:
                        await ev("document.querySelector('#password').focus(); 1", gesture=True)
                        await asyncio.sleep(0.4)
                        await ev("document.querySelector('button[name=_eventId_proceed]').click(); 1",
                                 gesture=True)
                        xy = 'gesture'
                    else:
                        await call('Page.bringToFront')
                        xy = json.loads(await ev(BTN_JS) or 'null')
                        if not xy:
                            raise Fault('hittade ingen Login-knapp på IdP-sidan')
                        await asyncio.sleep(0.4)
                        for kind in ('mouseMoved', 'mousePressed', 'mouseReleased'):
                            await call('Input.dispatchMouseEvent', {'type': kind, 'x': xy['x'],
                                       'y': xy['y'], 'button': 'left', 'clickCount': 1})
                    if os.environ.get('LADOK_LOGIN_TRACE'):
                        print('  [%s] click %d at %s' % (route, clicks + 1, xy), flush=True)
                    clicked = True
                    clicks += 1
                    clicked_at = dt.datetime.now()
            where = '%s%s' % (state.get('host', '?'), state.get('path', ''))
            if not clicked:
                raise Fault('Chrome fyllde aldrig i IdP-formuläret (stannade på %s): inget sparat '
                            'lösenord för den här sidan, eller så kräver inloggningen något mer '
                            '(2FA)' % where)
            raise Fault('klickade Login men kom inte tillbaka till Ladok inom %ds (stannade på %s)'
                        % (timeout, where))
    finally:
        try:
            urllib.request.urlopen(urllib.request.Request(
                cfg.cdp + '/json/close/' + tab['id'], method='PUT'), timeout=10)
        except Exception:
            pass


def login(cfg):
    """Try to restore the Ladok session. Returns a short status line, raises Fault.

    First the route a human takes (studentwebb -> "Logga in via ditt lärosäte" ->
    SeamlessAccess -> the institution), then the direct Shibboleth URL. Two routes because
    each has failed alone: the direct URL returned exit 3 one morning while the picker worked,
    and the picker is a third-party page that can change. The error names both.
    """
    errors = []
    for route in ('picker', 'direct'):
        try:
            return '%s (%s)' % (asyncio.run(_login(cfg, route=route)), route)
        except Fault as exc:
            errors.append('%s: %s' % (route, exc))
    raise Fault(' | '.join(errors))
