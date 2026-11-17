"""Offline tests for TimeEdit helpers: period strings and the free-slot maths."""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unitoolkit import timeedit as te  # noqa: E402


def test_period_accepts_iso_dates_and_relative():
    assert te._period('2026-11-17', '2026-11-27') == '20261117.x,20261127.x'
    assert te._period('0.d', '4.w') == '0.d,4.w'
    assert te._period(dt.date(2026, 11, 18), dt.date(2026, 11, 18)) == '20261118.x,20261118.x'


def test_free_rooms_gaps_and_rules(monkeypatch):
    rows = {'1': [{'date': '2026-11-18', 'start': '09:00', 'end': '12:00'},
                  {'date': '2026-11-18', 'start': '13:00', 'end': '16:00'}],
            '2': []}
    monkeypatch.setattr(te, 'bookings', lambda ids, a, b: rows[ids[0]])
    rooms = [{'id': '1', 'name': 'A'}, {'id': '2', 'name': 'B'}]
    r = te.free_rooms('2026-11-18', rooms=rooms, today=dt.date(2026, 11, 17))
    assert r['bookable'] is True
    b = next(x for x in r['rooms'] if x['room'] == 'B')
    assert b['free'] == [{'from': '08:00', 'to': '20:00', 'minutes': 720, 'bookable_until': '12:00'}]
    a = next(x for x in r['rooms'] if x['room'] == 'A')
    assert [(s['from'], s['to']) for s in a['free']] == [('08:00', '09:00'), ('12:00', '13:00'), ('16:00', '20:00')]
    assert r['rooms'][0]['room'] == 'B'          # most free time first


def test_weekend_and_far_dates_are_not_bookable(monkeypatch):
    monkeypatch.setattr(te, 'bookings', lambda ids, a, b: [])
    rooms = [{'id': '1', 'name': 'A'}]
    assert te.free_rooms('2026-11-21', rooms=rooms, today=dt.date(2026, 11, 17))['bookable'] is False  # Saturday
    assert te.free_rooms('2026-11-24', rooms=rooms, today=dt.date(2026, 11, 17))['bookable'] is False  # 7 days ahead


def test_bookable_rooms_fallback_without_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(te, 'CACHE', tmp_path / 'none.json')
    assert len(te.bookable_rooms()) == 9
