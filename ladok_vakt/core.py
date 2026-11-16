"""Core of ladok-vakt: fetch Ladok registration windows, decide, notify.

Design rules, in order of importance:

1. Read only. Only GETs against Ladok's student API. Nothing is ever registered,
   unregistered or changed. The human still does the anmälan.
2. Silence means success. A run that found data and has nothing urgent to say
   prints nothing, so it can run from cron every morning without noise.
3. Silence may never hide a fault. A run that could not fetch, that got no
   courses, or that has not succeeded in ``stale_hours`` always says so, even
   from cron. Faults are also emailed, because a chat message is exactly what a
   sleeping laptop misses.
4. No credentials in the repo. The session comes from a browser the human
   logged in to; mail credentials come from files or env vars.

Ladok has no student API with a token, so the data is read out of a
LibreOffice-free, already-authenticated Chrome over the DevTools protocol: the
student pages call ``https://student.ladok.se/student/proxy/<larosate>/...``
with the session cookie, and so do we.
"""
import asyncio
import datetime as dt
import json
import os
import re
import smtplib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from pathlib import Path

S = '{http://schemas.ladok.se/studiedeltagande}'
B = '{http://schemas.ladok.se}'
R = '{http://schemas.ladok.se/resultat}'

LADOK_ORIGIN = 'https://www.student.ladok.se'
START_PATH = '/student/app/studentwebb/start'


class Config:
    """Everything comes from env vars so the tool is publishable as-is."""

    def __init__(self, env=None):
        e = os.environ if env is None else env
        self.cdp = e.get('LADOK_CDP', 'http://127.0.0.1:9233')
        self.larosate = e.get('LADOK_LAROSATE', '38')       # 38 = Sophiahemmet
        self.state_dir = Path(e.get('LADOK_STATE_DIR',
                                    str(Path.home() / '.local' / 'state' / 'ladok-vakt')))
        self.stale_hours = float(e.get('LADOK_STALE_HOURS', '36'))
        self.close_days = int(e.get('LADOK_CLOSE_DAYS', '3'))
        self.mail_to = e.get('LADOK_MAIL_TO', '')
        self.mail_from = e.get('LADOK_MAIL_FROM', 'ladok-vakt <vakt@example.org>')
        self.mail_min_hours = float(e.get('LADOK_MAIL_MIN_HOURS', '20'))
        self.resend_key_file = Path(e.get('LADOK_RESEND_KEY_FILE',
                                          str(Path.home() / '.secrets' / 'RESEND_KEY')))
        self.smtp_password_file = Path(e.get('LADOK_SMTP_PASSWORD_FILE', '')) \
            if e.get('LADOK_SMTP_PASSWORD_FILE') else None
        self.smtp_user = e.get('LADOK_SMTP_USER', '')
        self.smtp_host = e.get('LADOK_SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(e.get('LADOK_SMTP_PORT', '587'))
        self.monday_heartbeat = e.get('LADOK_HEARTBEAT', '1') not in ('0', 'false', 'no')
        # Any of these can carry the fault notice instead of (or as well as) mail.
        self.webhook_url = e.get('LADOK_WEBHOOK_URL', '')
        self.webhook_format = e.get('LADOK_WEBHOOK_FORMAT', 'slack')  # slack|discord|ntfy|generic
        self.telegram_token = e.get('LADOK_TELEGRAM_TOKEN', '')
        self.telegram_chat = e.get('LADOK_TELEGRAM_CHAT', '')
        self.notify_command = e.get('LADOK_NOTIFY_COMMAND', '')

    @property
    def state_file(self):
        return self.state_dir / 'state.json'

    @property
    def log_file(self):
        return self.state_dir / 'ladok-vakt.log'

    def proxy(self, path):
        """Relative to the Ladok origin on purpose: an absolute URL with the wrong
        host (www. vs not) is a different origin and carries no session cookie."""
        return '/student/proxy/%s%s' % (self.larosate, path)


# --------------------------------------------------------------------------
# Chrome / DevTools
# --------------------------------------------------------------------------

def _tab(cdp):
    try:
        tabs = json.load(urllib.request.urlopen(cdp + '/json/list', timeout=5))
    except Exception as exc:
        raise Fault('hittar ingen Chrome på %s (%s)' % (cdp, exc))
    for t in tabs:
        if t.get('type') == 'page' and 'ladok' in t.get('url', ''):
            return t
    try:
        return json.load(urllib.request.urlopen(
            cdp + '/json/new?' + LADOK_ORIGIN + START_PATH, timeout=10))
    except Exception as exc:
        raise Fault('kan inte öppna en Ladok-flik (%s)' % exc)


async def _fetch_all(cdp, paths):
    import websockets
    tab = _tab(cdp)
    out = {}
    async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=80_000_000) as ws:
        i = 0
        for p in paths:
            i += 1
            expr = ("fetch(%s,{credentials:'include'})"
                    ".then(r=>r.text().then(t=>r.status+'|'+t))" % json.dumps(p))
            await ws.send(json.dumps({'id': i, 'method': 'Runtime.evaluate',
                                      'params': {'expression': expr, 'awaitPromise': True,
                                                 'returnByValue': True}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get('id') == i:
                    break
            v = (m.get('result', {}).get('result') or {}).get('value') or ''
            st, _, body = v.partition('|')
            out[p] = (int(st or 0), body)
    return out


def fetch(cfg):
    """Return (student_uid, registration_xml, exam_xml). Raises Fault."""
    who = cfg.proxy('/studiedeltagande/internal/student/inloggad/som')
    res = asyncio.run(_fetch_all(cfg.cdp, [who]))
    st, body = res[who]
    if st != 200 or 'StudentElement' not in body:
        raise Fault('ingen Ladok-session: logga in i agent-Chrome och kör igen')
    m = re.search(r'/student/([0-9a-f-]{36})', body)
    if not m:
        raise Fault('Ladok svarade utan student-id (inloggad med annan roll?)')
    uid = m.group(1)
    paths = [cfg.proxy('/studiedeltagande/internal/tillfallesdeltagande/'
                       'kurstillfallesdeltagande/student/' + uid),
             cfg.proxy('/resultat/internal/studentweb/aktivitetstillfalle/'
                       'inloggadstudent/relevanta')]
    res = asyncio.run(_fetch_all(cfg.cdp, paths))
    # One retry: the proxy occasionally answers 404 for a moment under load.
    for p in paths:
        st, body = res[p]
        if st != 200 or 'schemas.ladok.se' not in body:
            res.update(asyncio.run(_fetch_all(cfg.cdp, [p])))
    return uid, res[paths[0]][1], res[paths[1]][1]


class Fault(Exception):
    """Something is wrong with the watch itself, not with the student's Ladok."""


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _text(el, tag):
    x = el.find(tag)
    return (x.text or '').strip() if x is not None and x.text else None


def _first(el, tag):
    for x in el.iter(tag):
        if x.text and x.text.strip():
            return x.text.strip()
    return None


def _name(el):
    """Swedish course name from a Benamningar block, else the first name given.

    Scope matters: the first <base:Benamning> in a course element is the
    *tillfällestyp* ("Kurs, grundnivå"), not the course name.
    """
    if el is None:
        return None
    sv = first = None
    for b in el.iter(B + 'Benamning'):
        txt = _text(b, B + 'Text')
        if not txt:
            continue
        first = first or txt
        if _text(b, B + 'Sprakkod') == 'sv':
            sv = txt
    return sv or first


def parse_registrations(xml_text):
    if 'schemas.ladok.se' not in (xml_text or ''):
        raise Fault('Ladok svarade inte med kurstillfällen (trasig hämtning, inte tom)')
    root = ET.fromstring(xml_text)
    rows = []
    for td in root.iter(S + 'Tillfallesdeltagande'):
        info = td.find(S + 'Utbildningsinformation')
        name = code = None
        if info is not None:
            ben = info.find(S + 'Benamningar')
            name = _name(ben if ben is not None else info)
            code = _text(info, S + 'Utbildningskod')
        rp = td.find(S + 'Registreringsperiod')
        rows.append({
            'kind': 'kursregistrering',
            'course': name,
            'code': code,
            'state': _text(td, S + 'Tillstand'),
            'reg_opens': _text(rp, B + 'Startdatum') if rp is not None else None,
            'reg_closes': _text(rp, B + 'Slutdatum') if rp is not None else None,
            'course_start': _first(td, S + 'UtbildningsPeriodFran'),
            'course_end': _first(td, S + 'UtbildningsPeriodTill'),
        })
    return rows


def parse_exams(xml_text):
    """Exam registration windows. An empty list is the normal state."""
    if 'schemas.ladok.se' not in (xml_text or ''):
        return []
    root = ET.fromstring(xml_text)
    rows = []
    for at in root.iter():
        if not at.tag.endswith('Aktivitetstillfalle'):
            continue
        ben = None
        for b in at.iter(B + 'Benamning'):
            ben = _text(b, B + 'Text')
            break
        rows.append({
            'kind': 'tentamen',
            'course': ben,
            'reg_opens': _text(at, R + 'AnmalningsperiodFran') or _first(at, B + 'Startdatum'),
            'reg_closes': _text(at, R + 'AnmalningsperiodTill'),
            'exam_date': _first(at, B + 'Startdatum'),
        })
    return rows


def is_registered(row):
    return (row.get('state') or '').endswith('.registrerad')


def pending_actions(rows):
    """What is worth telling the human: courses not yet registered for, plus exams."""
    out = []
    for r in rows:
        if r['kind'] == 'kursregistrering':
            if is_registered(r):
                continue
            if r.get('reg_closes') is None:
                continue
        out.append(r)
    return out


def days_until(date_str):
    if not date_str:
        return None
    try:
        return (dt.date.fromisoformat(date_str[:10]) - dt.date.today()).days
    except ValueError:
        return None


def urgent(rows, close_days):
    out = []
    for r in rows:
        opens = days_until(r.get('reg_opens'))
        closes = days_until(r.get('reg_closes'))
        if (opens if opens is not None else 99) <= 0:
            out.append(r)
        elif (closes if closes is not None else 99) <= close_days:
            out.append(r)
    return out


def describe(rows):
    lines = []
    for r in sorted(rows, key=lambda x: x.get('reg_opens') or '9999'):
        left = days_until(r.get('reg_opens'))
        when = 'öppen nu' if left is not None and left <= 0 else ('om %d d' % left if left is not None else '?')
        line = '- %s (%s): anmälan %s till %s [%s]' % (
            r.get('course') or '?', r.get('code') or r['kind'],
            r.get('reg_opens') or '?', r.get('reg_closes') or '?', when)
        if r.get('exam_date'):
            line += ', tentamen %s' % r['exam_date']
        if r.get('course_start'):
            line += ', kursstart %s' % r['course_start']
        lines.append(line)
    return lines


# --------------------------------------------------------------------------
# State + log
# --------------------------------------------------------------------------

def load_state(cfg):
    try:
        return json.loads(cfg.state_file.read_text())
    except Exception:
        return {}


def save_state(cfg, state):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_file.write_text(json.dumps(state, indent=1))


def log(cfg, line):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with cfg.log_file.open('a') as f:
        f.write(dt.datetime.now().isoformat(timespec='seconds') + ' ' + line + '\n')


def stale_note(cfg, state):
    last = state.get('last_success')
    if not last:
        return None
    try:
        age = (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        return None
    if age >= cfg.stale_hours:
        return ('- VARNING: ingen lyckad Ladok-hämtning på %.0f h (senast %s). '
                'Kontrollera Chrome och inloggningen.' % (age, last))
    return None


# --------------------------------------------------------------------------
# Mail: Resend or SMTP, whichever the human configured
# --------------------------------------------------------------------------

def send_mail(cfg, subject, text):
    if not cfg.mail_to:
        log(cfg, 'mail skipped: LADOK_MAIL_TO is not set')
        return False
    if cfg.smtp_password_file:
        return _send_smtp(cfg, subject, text)
    return _send_resend(cfg, subject, text)


def _send_resend(cfg, subject, text):
    try:
        key = cfg.resend_key_file.read_text().strip()
    except OSError:
        log(cfg, 'mail skipped: no Resend key at %s' % cfg.resend_key_file)
        return False
    body = json.dumps({'from': cfg.mail_from, 'to': [cfg.mail_to],
                       'subject': subject, 'text': text}).encode()
    req = urllib.request.Request('https://api.resend.com/emails', body, {
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
        # Cloudflare in front of Resend rejects python-urllib's default agent
        # with a bare 403, so name ourselves.
        'User-Agent': 'ladok-vakt/0.1 (+https://github.com/vhsgreed/ladok-vakt)'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = 200 <= r.status < 300
        log(cfg, 'mail via resend ok=%s' % ok)
        return ok
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:300]
        except Exception:
            detail = ''
        log(cfg, 'mail via resend failed: %s %s' % (e, detail))
        return False
    except Exception as e:
        log(cfg, 'mail via resend failed: %s' % e)
        return False


def _send_smtp(cfg, subject, text):
    try:
        pw = cfg.smtp_password_file.read_text().strip()
    except OSError:
        log(cfg, 'mail skipped: no SMTP password at %s' % cfg.smtp_password_file)
        return False
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = cfg.mail_from
    msg['To'] = cfg.mail_to
    msg.set_content(text)
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as s:
            s.starttls()
            s.login(cfg.smtp_user or cfg.mail_from, pw)
            s.send_message(msg)
        log(cfg, 'mail via smtp ok')
        return True
    except Exception as e:
        log(cfg, 'mail via smtp failed: %s' % e)
        return False


def _post_json(url, payload, headers=None):
    body = json.dumps(payload).encode()
    h = {'Content-Type': 'application/json',
         'User-Agent': 'ladok-vakt/0.1 (+https://github.com/vhsgreed/ladok-vakt)'}
    h.update(headers or {})
    req = urllib.request.Request(url, body, h)
    with urllib.request.urlopen(req, timeout=20) as r:
        return 200 <= r.status < 300


def send_webhook(cfg, text):
    if not cfg.webhook_url:
        return None
    fmt = cfg.webhook_format.lower()
    if fmt == 'discord':
        payload = {'content': text}
    elif fmt == 'ntfy':
        payload = {'topic': cfg.webhook_url.rstrip('/').split('/')[-1], 'message': text}
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    cfg.webhook_url, text.encode(), {'User-Agent': 'ladok-vakt/0.1'},
                    method='POST'), timeout=20) as r:
                ok = 200 <= r.status < 300
            log(cfg, 'webhook ntfy ok=%s' % ok)
            return ok
        except Exception as e:
            log(cfg, 'webhook ntfy failed: %s' % e)
            return False
    elif fmt == 'generic':
        payload = {'text': text, 'source': 'ladok-vakt'}
    else:
        payload = {'text': text}
    try:
        ok = _post_json(cfg.webhook_url, payload)
        log(cfg, 'webhook %s ok=%s' % (fmt, ok))
        return ok
    except Exception as e:
        log(cfg, 'webhook %s failed: %s' % (fmt, e))
        return False


def send_telegram(cfg, text):
    if not (cfg.telegram_token and cfg.telegram_chat):
        return None
    url = 'https://api.telegram.org/bot%s/sendMessage' % cfg.telegram_token
    try:
        ok = _post_json(url, {'chat_id': cfg.telegram_chat, 'text': text})
        log(cfg, 'telegram ok=%s' % ok)
        return ok
    except Exception as e:
        log(cfg, 'telegram failed: %s' % e)
        return False


def send_command(cfg, subject, text):
    """Escape hatch: pipe the message into any command (ntfy, curl, notify-send)."""
    if not cfg.notify_command:
        return None
    import subprocess
    try:
        p = subprocess.run(cfg.notify_command, shell=True, input=subject + '\n' + text,
                           text=True, timeout=30, capture_output=True)
        log(cfg, 'command ok=%s rc=%s' % (p.returncode == 0, p.returncode))
        return p.returncode == 0
    except Exception as e:
        log(cfg, 'command failed: %s' % e)
        return False


def notify(cfg, subject, text):
    """Fault notice out over every configured channel. Returns channels that worked."""
    done = []
    if send_mail(cfg, subject, text):
        done.append('mail')
    for name, fn in (('telegram', lambda: send_telegram(cfg, subject + '\n\n' + text)),
                     ('webhook', lambda: send_webhook(cfg, subject + '\n\n' + text)),
                     ('command', lambda: send_command(cfg, subject, text))):
        try:
            if fn():
                done.append(name)
        except Exception as e:
            log(cfg, '%s failed: %s' % (name, e))
    if not done:
        log(cfg, 'no channel delivered the notice')
    return done


def mail_allowed(cfg, state, kind):
    last = (state.get('mail') or {}).get(kind)
    if not last:
        return True
    try:
        age = (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        return True
    return age >= cfg.mail_min_hours


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------

def run(cfg, alert=True, email=True, write_files=True):
    """Do one check. Returns the lines that should be sent to the human."""
    prev = load_state(cfg)
    try:
        uid, reg_xml, exam_xml = fetch(cfg)
        rows = pending_actions(parse_registrations(reg_xml) + parse_exams(exam_xml))
    except Fault as exc:
        note = '- VARNING: ladok-vakt kunde inte läsa Ladok: %s' % exc
        log(cfg, 'fault: %s' % exc)
        out = [note]
        if email and mail_allowed(cfg, prev, 'fault'):
            if notify(cfg, 'Ladok-vakt: hämtningen fungerar inte', note + '\n'):
                st = load_state(cfg)
                st.setdefault('mail', {})['fault'] = dt.datetime.now().isoformat(timespec='seconds')
                save_state(cfg, st)
        return out

    save_state(cfg, {'last_success': dt.datetime.now().isoformat(timespec='seconds'),
                     'student': uid, 'windows': len(rows), 'mail': prev.get('mail', {})})
    log(cfg, 'ok windows=%d' % len(rows))
    if write_files:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)

    hot = urgent(rows, cfg.close_days)
    stale = stale_note(cfg, prev)
    heartbeat = None
    if cfg.monday_heartbeat and dt.date.today().weekday() == 0 and not hot:
        heartbeat = ('- Veckokoll ladok-vakt %s: körningen gick igenom, %d anmälningsfönster, '
                     'inget inom %d dagar.' % (dt.date.today().isoformat(), len(rows), cfg.close_days))

    if not alert:
        return describe(rows)

    out = [x for x in (stale, heartbeat) if x]
    if hot:
        out += describe(hot)
    if stale and email and mail_allowed(cfg, prev, 'stale'):
        if notify(cfg, 'Ladok-vakt: hämtningen har stannat', stale + '\n'):
            st = load_state(cfg)
            st.setdefault('mail', {})['stale'] = dt.datetime.now().isoformat(timespec='seconds')
            save_state(cfg, st)
    return out
