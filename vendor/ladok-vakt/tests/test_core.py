"""Unit tests: parsing and the decision rules. No network, no Chrome.

    python3 -m pytest tests/ -q
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ladok_vakt import core  # noqa: E402

REGS = """<?xml version="1.0" encoding="UTF-8"?>
<sd:TillfallesdeltagandeLista xmlns:base="http://schemas.ladok.se"
  xmlns:sd="http://schemas.ladok.se/studiedeltagande">
 <sd:Tillfallesdeltaganden>
  <sd:Tillfallesdeltagande>
   <sd:Utbildningsinformation>
    <sd:Benamningar>
     <base:Benamning><base:Sprakkod>en</base:Sprakkod><base:Text>Basic Pharmacology</base:Text></base:Benamning>
     <base:Benamning><base:Sprakkod>sv</base:Sprakkod><base:Text>Grundläggande farmakologi och omvårdnad</base:Text></base:Benamning>
    </sd:Benamningar>
    <sd:Utbildningskod>1SFOM2</sd:Utbildningskod>
   </sd:Utbildningsinformation>
   <sd:Registreringsperiod><base:Startdatum>2026-11-23</base:Startdatum><base:Slutdatum>2026-11-30</base:Slutdatum></sd:Registreringsperiod>
   <sd:Tillstand>studiedeltagande.domain.tillstandsdefinition.ej_registrerad</sd:Tillstand>
   <sd:Perioddeltagande><sd:UtbildningsPeriodFran>2026-11-30</sd:UtbildningsPeriodFran><sd:UtbildningsPeriodTill>2027-01-24</sd:UtbildningsPeriodTill></sd:Perioddeltagande>
  </sd:Tillfallesdeltagande>
  <sd:Tillfallesdeltagande>
   <sd:Utbildningsinformation>
    <sd:Benamningar>
     <base:Benamning><base:Sprakkod>sv</base:Sprakkod><base:Text>Människan i hälsa och sjukdom del 1</base:Text></base:Benamning>
    </sd:Benamningar>
    <sd:Utbildningskod>2SHS11</sd:Utbildningskod>
   </sd:Utbildningsinformation>
   <sd:Registreringsperiod><base:Startdatum>2027-01-18</base:Startdatum><base:Slutdatum>2027-01-25</base:Slutdatum></sd:Registreringsperiod>
   <sd:Tillstand>studiedeltagande.domain.tillstandsdefinition.registrerad</sd:Tillstand>
  </sd:Tillfallesdeltagande>
 </sd:Tillfallesdeltaganden>
</sd:TillfallesdeltagandeLista>
"""

EXAMS_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rr:RelevantAktivitetstillfalleForStudentLista xmlns:rr="http://schemas.ladok.se/resultat"/>
"""


def test_parse_registrations_uses_swedish_course_name():
    rows = core.parse_registrations(REGS)
    assert rows[0]['course'] == 'Grundläggande farmakologi och omvårdnad'
    assert rows[0]['code'] == '1SFOM2'
    assert rows[0]['reg_opens'] == '2026-11-23'
    assert rows[0]['reg_closes'] == '2026-11-30'
    assert rows[0]['course_start'] == '2026-11-30'
    assert rows[0]['course_end'] == '2027-01-24'


def test_registered_course_is_not_pending():
    rows = core.parse_registrations(REGS)
    assert core.is_registered(rows[1])
    pending = core.pending_actions(rows)
    assert [r['code'] for r in pending] == ['1SFOM2']


def test_empty_exam_list_is_normal():
    assert core.parse_exams(EXAMS_EMPTY) == []


def test_broken_registration_payload_is_a_fault_not_an_empty_list():
    try:
        core.parse_registrations('<html>403</html>')
    except core.Fault:
        return
    raise AssertionError('a non-Ladok body must raise Fault')


def test_urgent_uses_open_and_close_rules():
    today = dt.date.today()
    fmt = lambda d: (today + dt.timedelta(days=d)).isoformat()  # noqa: E731
    rows = [
        {'kind': 'kursregistrering', 'course': 'open now', 'code': 'A',
         'reg_opens': fmt(-1), 'reg_closes': fmt(10)},
        {'kind': 'kursregistrering', 'course': 'closing soon', 'code': 'B',
         'reg_opens': fmt(-5), 'reg_closes': fmt(2)},
        {'kind': 'kursregistrering', 'course': 'later', 'code': 'C',
         'reg_opens': fmt(30), 'reg_closes': fmt(37)},
    ]
    hot = core.urgent(rows, close_days=3)
    assert [r['code'] for r in hot] == ['A', 'B']


def test_describe_is_one_line_per_window():
    rows = core.parse_registrations(REGS)
    lines = core.describe(core.pending_actions(rows))
    assert len(lines) == 1 and '1SFOM2' in lines[0] and '2026-11-23' in lines[0]


def test_stale_note():
    cfg = core.Config({'LADOK_STALE_HOURS': '36'})
    old = (dt.datetime.now() - dt.timedelta(hours=40)).isoformat(timespec='seconds')
    fresh = (dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec='seconds')
    assert core.stale_note(cfg, {'last_success': old})
    assert core.stale_note(cfg, {'last_success': fresh}) is None
    assert core.stale_note(cfg, {}) is None


def test_mail_throttle():
    cfg = core.Config({'LADOK_MAIL_MIN_HOURS': '20'})
    now = dt.datetime.now().isoformat(timespec='seconds')
    assert core.mail_allowed(cfg, {}, 'fault')
    assert not core.mail_allowed(cfg, {'mail': {'fault': now}}, 'fault')
