"""TimeEdit, the whole timetable rather than one saved link.

Two guides matter at SHH (cloud.timeedit.net/shh/web/student/):
- ri1Q9 "Schema" is public (sid=5). Its object search answers course codes, groups and rooms,
  and `ri.json` with `objects=<id>` returns the bookings of any of them for any period. That is
  every course's schedule without a login.
- ri1Q3 "Boka salar" (sid=8) sits behind the school SSO. It only tells us WHICH rooms students
  may book; their occupancy is in the public guide like any other room. So the login is needed
  once, to learn the room list, which is then cached.

Read-only: nothing here books a room. The booking rules the guide shows (Mon-Fri 08-20, three
days ahead, one room per student per day, max 4 hours) are applied to `free_rooms`, so the
answer is "what you could book", not just "what is empty".

Gotchas found while building this:
- The type codes are not the ones the filter boxes on the page carry (11, 19, 20, ...): those are
  room attributes. Objects are 5=Kurs, 21=Kurskod, 6=Grupp, 4=Sal, 8=Ämne, 9=Undervisningsform.
- A course name search ("Sjuksköterskan...") finds nothing; search the course code (type 21),
  and the student group (type 6, e.g. "H26SK") to see the whole cohort's timetable.
- The period parameter is `p=<from>,<to>` with dates as YYYYMMDD.x, or relative 0.d / 4.w.
"""

import datetime as dt
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

BASE = 'https://cloud.timeedit.net/shh/web/student/'
PUBLIC_SID = 5
BOOKING_SID = 8
TYPES = {'course': 5, 'code': 21, 'group': 6, 'room': 4, 'subject': 8, 'form': 9}
CACHE = Path.home() / '.cache/unitoolkit/timeedit_bookable_rooms.json'

# Bookable rooms as read from the logged-in "Boka salar" guide on 2026-11-17. Used when the
# login is unavailable; refresh with bookable_rooms(refresh=True).
BOOKABLE_FALLBACK = [
    {'id': '1777', 'name': '100 Lill Jan', 'kind': 'Seminarierum'},
    {'id': '1779', 'name': '101A Lill Jan', 'kind': 'Seminarierum'},
    {'id': '1780', 'name': '101B Lill Jan', 'kind': 'Seminarierum'},
    {'id': '1781', 'name': '102A Lill Jan', 'kind': 'Seminarierum'},
    {'id': '1782', 'name': '102B Lill Jan', 'kind': 'Seminarierum'},
    {'id': '1783', 'name': '103 Lill Jan', 'kind': 'Seminarierum'},
    {'id': '427', 'name': '201 Lill Jan', 'kind': 'Föreläsningssal'},
    {'id': '440', 'name': 'Henderson', 'kind': 'Seminarierum'},
    {'id': '439', 'name': 'Nightingale', 'kind': 'Seminarierum'},
]
BOOKING_RULES = {'weekdays': (0, 1, 2, 3, 4), 'open': '08:00', 'close': '20:00',
                 'days_ahead': 3, 'max_hours': 4}

UA = {'User-Agent': 'unitoolkit (https://github.com/vhsgreed/shh-study-kit)'}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def _period(start, end):
    def one(d):
        if isinstance(d, (dt.date, dt.datetime)):
            return d.strftime('%Y%m%d') + '.x'
        s = str(d).strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
            return s.replace('-', '') + '.x'
        return s                                   # already TimeEdit form (0.d, 4.w, YYYYMMDD.x)
    return '%s,%s' % (one(start), one(end))


def find(query, kind='code', limit=20):
    """Object ids for a course code, group, room, subject or teaching form."""
    t = TYPES.get(kind, kind)
    q = urllib.parse.urlencode({'max': 100, 'fr': 't', 'partajax': 't', 'im': 'f',
                                'sid': PUBLIC_SID, 'l': 'sv_SE', 'search_text': query,
                                'objects': '', 'types': t})
    h = _get(BASE + 'objects.html?' + q)
    import html as _html
    rows = re.findall(r'data-idonly="([^"]+)"[^>]*data-name="([^"]*)"', h) or \
        re.findall(r'data-id="([^"]+)"[^>]*data-name="([^"]*)"', h)
    out, seen = [], set()
    for oid, name in rows:
        if oid.startswith('-') or oid in seen:
            continue
        seen.add(oid)
        out.append({'id': oid.split('.')[0], 'name': _html.unescape(name).strip(), 'kind': kind})
    return out[:limit]


def bookings(object_ids, start: object = '0.d', end: object = '14.d'):
    """Every booking of the given objects between start and end, as plain rows."""
    ids = ','.join(str(i).split('.')[0] for i in (object_ids if isinstance(object_ids, (list, tuple)) else [object_ids]))
    if not ids:
        return []
    q = 'h=t&sid=%d&p=%s&objects=%s&ox=0&types=0&fe=0' % (
        PUBLIC_SID, urllib.parse.quote(_period(start, end), safe=',.'), ids)
    d = json.loads(_get(BASE + 'ri.json?' + q))
    head = [h or 'col%d' % i for i, h in enumerate(d.get('columnheaders') or [])]
    names = {'Kurs/Grupp': 'group', 'Undervisningsform': 'form', 'Ämne': 'subject',
             'Sal': 'room', 'Lärare, Personal, Person': 'teacher', 'Information': 'info'}
    out = []
    for r in d.get('reservations') or []:
        row = {'date': r['startdate'], 'start': r['starttime'], 'end': r['endtime']}
        for i, v in enumerate(r.get('columns') or []):
            key = names.get(head[i], head[i]) if i < len(head) else 'col%d' % i
            row[key] = v
        out.append(row)
    return sorted(out, key=lambda x: (x['date'], x['start']))


def schedule(query, kind='code', start='0.d', end='14.d'):
    """'When is PRY?' in one call: resolve the code (or group, room) and list its bookings."""
    hits = find(query, kind)
    if not hits:
        hint = (' Search the course code (e.g. 1SPRY2) or the group (e.g. H26SK); course names '
                'are not indexed.' if kind in ('code', 'course') else '')
        raise LookupError('TimeEdit has no %s matching %r.%s' % (kind, query, hint))
    exact = [h for h in hits if h['name'].lower() == query.lower()]
    picked = exact or hits[:1] if kind != 'group' else (exact or hits)
    rows = bookings([h['id'] for h in picked], start, end)
    return {'query': query, 'kind': kind, 'objects': picked, 'period': _period(start, end),
            'bookings': rows, 'count': len(rows)}


def bookable_rooms(refresh=False):
    """Rooms students may book. Needs the school SSO once (agent browser), then cached."""
    if not refresh and CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            pass
    if not refresh:
        return BOOKABLE_FALLBACK
    import asyncio
    from .databases import _Tab
    with _Tab(BASE + 'ri1Q3.html') as page:
        for _ in range(8):
            page._run(asyncio.sleep(3))
            url = page.eval('location.href') or ''
            if 'ri1Q3' in url:
                break
            page.eval("""(() => { const b = [...document.querySelectorAll('a,button')]
                .find(e => /SHH-konto/i.test(e.innerText)); if (b) b.click(); })()""")
        if 'ri1Q3' not in (page.eval('location.href') or ''):
            raise RuntimeError('TimeEdit room booking needs the school sign-in: open %sri1Q3.html '
                               'in the agent browser and choose "Sign in with SHH-konto"' % BASE)
        raw = page.eval("""(async () => JSON.stringify(await (await fetch('objects.json?max=50&fr=f'
            + '&part=t&partajax=t&im=f&step=1&sid=%d&l=sv_SE&ohg=0&types=4&subtypes=4',
            {credentials: 'include'})).json()))()""" % BOOKING_SID)
    rooms = []
    for o in json.loads(raw or '{}').get('objects') or []:
        f = o.get('fields') or {}
        rooms.append({'id': str(o['id']), 'name': (f.get('Namn') or '').strip(),
                      'kind': (f.get('Salstyp') or '').strip()})
    if rooms:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(rooms, ensure_ascii=False, indent=1))
    return rooms


def _mins(hhmm):
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def _hhmm(mins):
    return '%02d:%02d' % (mins // 60, mins % 60)


def free_rooms(date=None, min_minutes=60, rooms=None, today=None):
    """Free slots in the bookable rooms on one day, within the booking rules.

    Returns, per room, the gaps between bookings inside opening hours that are at least
    `min_minutes` long, each capped to the 4-hour booking limit. A date outside the bookable
    window still gets an answer, flagged `bookable: false`, because "is it free?" and "can I
    book it yet?" are different questions.
    """
    today = today or dt.date.today()
    day = dt.date.fromisoformat(date) if date else today
    rooms = rooms or bookable_rooms()
    rules = BOOKING_RULES
    lo, hi = _mins(rules['open']), _mins(rules['close'])
    bookable = day.weekday() in rules['weekdays'] and 0 <= (day - today).days <= rules['days_ahead']
    out = []
    for room in rooms:
        # One request per room: a combined query returns some bookings with an empty room
        # column, and those cannot be attributed to a room (they matched every room before).
        busy = sorted((_mins(r['start']), _mins(r['end']))
                      for r in bookings([room['id']], day.isoformat(), day.isoformat())
                      if r['date'] == day.isoformat())
        gaps, cursor = [], lo
        for st, en in busy:
            if st > cursor:
                gaps.append((cursor, min(st, hi)))
            cursor = max(cursor, en)
        if cursor < hi:
            gaps.append((cursor, hi))
        slots = [{'from': _hhmm(a), 'to': _hhmm(b), 'minutes': b - a,
                  'bookable_until': _hhmm(min(b, a + rules['max_hours'] * 60))}
                 for a, b in gaps if b - a >= min_minutes]
        out.append({'room': room['name'], 'kind': room.get('kind', ''), 'free': slots,
                    'busy': [{'from': _hhmm(a), 'to': _hhmm(b)} for a, b in busy]})
    out.sort(key=lambda r: -sum(s['minutes'] for s in r['free']))
    return {'date': day.isoformat(), 'bookable': bookable,
            'rules': 'Mon-Fri 08-20, up to %d days ahead, one room per student per day, max %d h; '
                     'book in TimeEdit (Boka salar), this tool only reads.' % (
                         rules['days_ahead'], rules['max_hours']),
            'rooms': out}
