"""The agent-facing surface: a few tools shaped like the questions a student asks.

The full registry has 30-odd tools, and every tool description sits in the agent's context
on every turn. Most requests are one of a handful of questions, so the MCP server lists only
CORE by default and the rest stay one environment variable away (UNITOOLKIT_TOOLS=all).
Every tool stays callable by name either way, and all of them stay importable as a library.

Design rules for this layer:
- One question, one call. `today` answers "what do I need to do" across Canvas, Ladok, the
  library and the timetable, so the agent does not have to know which system holds what.
- Partial answers beat no answer. A dead Ladok session must not hide the Canvas deadlines,
  so each source reports its own error next to the others' results.
- `doctor` names the fix, not just the fault, because the fix is always a human action.
"""

import datetime as dt
import os
import time

CORE = ('today', 'doctor', 'find_sources', 'db_fulltext', 'search', 'assignment', 'library',
        'schedule', 'free_rooms', 'announcements')


def listed(all_tools):
    """Names the MCP server should list."""
    mode = os.environ.get('UNITOOLKIT_TOOLS', 'core').strip().lower()
    if mode == 'all':
        return list(all_tools)
    if mode and mode != 'core':                       # explicit comma list
        return [n for n in (x.strip() for x in mode.split(',')) if n in all_tools]
    return [n for n in CORE if n in all_tools]


def _days(date_str, today=None):
    if not date_str:
        return None
    try:
        d = dt.date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return None
    return (d - (today or dt.date.today())).days


def _guard(fn):
    t0 = time.time()
    try:
        return {'ok': True, 'data': fn(), 'seconds': round(time.time() - t0, 1)}
    except BaseException as e:                         # SystemExit from vendored code too
        return {'ok': False, 'error': '%s: %s' % (type(e).__name__, str(e)[:240]),
                'seconds': round(time.time() - t0, 1)}


# ------------------------------------------------------------------------- today

def _canvas_due(days):
    from .tools import call
    out = []
    for item in call('upcoming'):
        left = _days(item.get('due_utc'))
        if item.get('submitted') or left is None or left > days:
            continue
        out.append({'due_utc': item['due_utc'], 'days_left': left, 'course': item.get('course'),
                    'title': item.get('title'), 'type': item.get('type')})
    return sorted(out, key=lambda r: r['due_utc'])


def _canvas_announcements(days):
    from .tools import call
    out = []
    for a in call('announcements', {'days': days}):
        out.append({'posted_utc': a.get('posted_utc'), 'days_ago': abs(_days(a.get('posted_utc')) or 0),
                    'course_id': a.get('course_id'), 'title': a.get('title')})
    return out


def _ladok_windows(days):
    from .tools import call
    res = call('ladok_windows', {})
    if 'error' in res:
        raise RuntimeError(res['error'] + ' (' + res.get('hint', '') + ')')
    out = []
    for r in res.get('windows') or []:
        opens, closes = _days(r.get('reg_opens')), _days(r.get('reg_closes'))
        open_now = opens is not None and opens <= 0 and (closes is None or closes >= 0)
        soon = opens is not None and 0 < opens <= days
        if open_now or soon:
            out.append({'course': r.get('course'), 'code': r.get('code'), 'kind': r.get('kind'),
                        'opens': r.get('reg_opens'), 'closes': r.get('reg_closes'),
                        'open_now': open_now, 'days_to_close': closes, 'days_to_open': opens})
    return out


def _loans_due(days):
    from . import koha
    acc = koha.loans()
    return [{'title': c['title'], 'due': c['due'], 'days_left': c['days_left']}
            for c in acc.get('checkouts') or []
            if c.get('days_left') is not None and c['days_left'] <= days]


def _schedule(days):
    group = os.environ.get('UNITOOLKIT_GROUP', '').strip()
    if group:                                       # the whole cohort, any period, no login
        from . import timeedit
        end = (dt.date.today() + dt.timedelta(days=days)).isoformat()
        return timeedit.schedule(group, 'group', dt.date.today().isoformat(), end)['bookings']
    url = os.environ.get('UNITOOLKIT_TIMEEDIT_URL', '').strip()
    if not url:
        raise RuntimeError('no timetable configured: set UNITOOLKIT_GROUP to your student group '
                           '(e.g. H26SK), or UNITOOLKIT_TIMEEDIT_URL to a saved TimeEdit link')
    from .tools import call
    today = dt.date.today()
    return [b for b in call('timeedit', {'url': url})
            if (_days(b.get('date'), today) or 0) >= 0 and (_days(b.get('date'), today) or 0) <= days]


def today(days=14, library=False):
    """What needs doing in the next `days` days, from every system at once.

    library=False by default: the Koha login goes through a residential proxy, takes up to
    two minutes when it fails, and repeated failed logins risk locking the student's library
    account. Ask for it explicitly.
    """
    days = int(days or 14)
    out = {'date': dt.date.today().isoformat(), 'window_days': days,
           'canvas': _guard(lambda: _canvas_due(days)),
           'announcements': _guard(lambda: _canvas_announcements(days)),
           'ladok': _guard(lambda: _ladok_windows(days)),
           'schedule': _guard(lambda: _schedule(min(days, 7)))}
    if library:
        out['library'] = _guard(lambda: _loans_due(days))
    out['problems'] = [k for k, v in out.items() if isinstance(v, dict) and v.get('ok') is False]
    if out['problems']:
        out['hint'] = 'run doctor for the fix; the other sections are still valid'
    return out


# ------------------------------------------------------------------------ doctor

def _check_canvas():
    from . import canvas
    tok = canvas.token()
    if not tok:
        return 'fail', 'no Canvas token', 'Canvas > Account > Settings > New access token, save it to ~/.secrets/CANVAS_TOKEN'
    me = canvas.get('users/self/profile')
    expiry = None
    try:
        for t in canvas.get('users/self/user_generated_tokens') or []:
            prefix = (t.get('visible_token') or '').rstrip('.')
            if prefix and tok.startswith(prefix):
                expiry = t.get('expires_at')
    except Exception:
        pass
    left = _days(expiry) if expiry else None
    detail = 'signed in as %s' % me.get('name', '?') + (
        ', token expires %s (%s days)' % (expiry[:10], left) if expiry else ', token expiry unknown')
    if left is not None and left <= 14:
        return 'warn', detail, 'make a new token before it expires: Canvas > Account > Settings'
    return 'ok', detail, ''


def _check_browser():
    from . import browser
    try:
        n = len([t for t in browser.targets() if t.get('type') == 'page'])
    except Exception:
        return 'fail', 'agent browser not running on CDP %s' % os.environ.get('UNITOOLKIT_CDP', ':9233'), \
            'start the agent Chrome with --remote-debugging-port=9233 (see skill/SKILL.md, Setup)'
    return ('warn' if n > 15 else 'ok'), '%d open tabs' % n, ('close stray tabs' if n > 15 else '')


def _check_ladok():
    from .tools import call
    res = call('ladok_windows', {})
    if 'error' in res:
        return 'fail', res['error'][:160], 'sign in to Ladok once in the agent browser'
    rows = [r for r in res.get('windows') or [] if isinstance(r, dict)]   # rows, not text lines
    return 'ok', '%d registration windows readable' % len(rows), ''


def _check_databases():
    from . import databases
    st = databases.status('cinahl')
    if not st.get('authenticated'):
        return 'fail', 'EZproxy sends us to the Microsoft sign-in', \
            'sign in once at https://shh.idm.oclc.org in the agent browser (Authenticator)'
    return 'ok', 'EZproxy admits us (%s)' % (st.get('institution') or 'institution not shown'), ''


def _check_library():
    from . import koha
    st = koha.status()
    if st.get('proxy_enabled', True) and not st.get('chain_up'):
        return 'warn', 'library proxy not running on %s (starts on demand)' % st.get('chain_proxy'), \
            'only needed where the library drops your IP; otherwise set KOHA_PROXY=0'
    if not st.get('cdp_up'):
        return 'warn', 'library browser is down (starts on demand)', ''
    try:
        user, pin = koha.credentials()
    except Exception:
        user = pin = None
    if not (user and pin):
        return 'warn', 'no library credentials on file', 'put card number and pinkod in ~/.secrets/KOHA_CREDENTIALS (two lines)'
    return 'ok', 'proxy, browser and credentials in place (login not attempted)', ''


def _check_timeedit():
    group = os.environ.get('UNITOOLKIT_GROUP', '').strip()
    if group:
        from . import timeedit
        hits = timeedit.find(group, 'group')
        if not hits:
            return 'fail', 'TimeEdit has no group %r' % group, 'set UNITOOLKIT_GROUP to the group on your timetable (e.g. H26SK)'
        return 'ok', 'group %s found (%d subgroups)' % (group, len(hits)), ''
    url = os.environ.get('UNITOOLKIT_TIMEEDIT_URL', '').strip()
    if not url:
        return 'warn', 'no timetable configured', 'set UNITOOLKIT_GROUP to your student group (e.g. H26SK)'
    from .tools import call
    n = len(call('timeedit', {'url': url}))
    return ('ok' if n else 'warn'), '%d bookings in the saved link' % n, (
        '' if n else 'the link covers an old period: make a new one in TimeEdit')


CHECKS = [('canvas', _check_canvas), ('browser', _check_browser), ('ladok', _check_ladok),
          ('databases', _check_databases), ('library', _check_library),
          ('timeedit', _check_timeedit)]


def doctor(only=None):
    """Every session and credential the toolkit depends on, each with the human fix."""
    wanted = {x.strip() for x in (only or '').split(',') if x.strip()}
    rows = []
    for name, fn in CHECKS:
        if wanted and name not in wanted:
            continue
        t0 = time.time()
        try:
            status, detail, fix = fn()
        except BaseException as e:
            status, detail, fix = 'fail', '%s: %s' % (type(e).__name__, str(e)[:200]), \
                'see the error; most often the agent browser is down or signed out'
        rows.append({'check': name, 'status': status, 'detail': detail, 'fix': fix,
                     'seconds': round(time.time() - t0, 1)})
    worst = 'fail' if any(r['status'] == 'fail' for r in rows) else (
        'warn' if any(r['status'] == 'warn' for r in rows) else 'ok')
    return {'overall': worst, 'checks': rows}


# ----------------------------------------------------------------------- library

def library(query=None, limit=10):
    """Catalogue search when given a query, otherwise the student's own loans."""
    from . import koha
    if query:
        return {'mode': 'search', 'query': query, 'results': koha.search(query, limit=limit or 10)}
    acc = koha.loans()
    return {'mode': 'loans', 'loans': [
        {'title': c['title'], 'author': c.get('author', ''), 'due': c['due'],
         'days_left': c['days_left']} for c in acc.get('checkouts') or []],
        'holds': acc.get('holds') or [], 'fines': acc.get('fines') or []}
