"""Offline tests for the agent surface: listing modes, date maths, partial failure."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unitoolkit import agent, mcp  # noqa: E402
from unitoolkit.tools import TOOLS  # noqa: E402


def test_core_tools_all_exist():
    missing = [n for n in agent.CORE if n not in TOOLS]
    assert not missing, missing


def test_listing_modes(monkeypatch):
    monkeypatch.delenv('UNITOOLKIT_TOOLS', raising=False)
    assert agent.listed(list(TOOLS)) == list(agent.CORE)
    monkeypatch.setenv('UNITOOLKIT_TOOLS', 'all')
    assert len(agent.listed(list(TOOLS))) == len(TOOLS)
    monkeypatch.setenv('UNITOOLKIT_TOOLS', 'pubmed, apa, nope')
    assert agent.listed(list(TOOLS)) == ['pubmed', 'apa']


def test_mcp_lists_core_but_calls_anything(monkeypatch):
    monkeypatch.delenv('UNITOOLKIT_TOOLS', raising=False)
    names = [t['name'] for t in mcp.handle({'method': 'tools/list', 'id': 1})['tools']]
    assert names == list(agent.CORE)
    # an unlisted tool is still callable by name (offline: apa with a bad DOI errors cleanly)
    r = mcp.handle({'method': 'tools/call', 'id': 2, 'params': {'name': 'apa', 'arguments': {'doi': 'x'}}})
    assert r['isError'] and 'not a DOI' in r['content'][0]['text']


def test_days():
    import datetime as dt
    base = dt.date(2026, 11, 17)
    assert agent._days('2026-11-26T15:00:00Z', base) == 9
    assert agent._days('', base) is None and agent._days('garbage', base) is None


def test_guard_keeps_going_after_a_failure():
    ok = agent._guard(lambda: [1])
    bad = agent._guard(lambda: (_ for _ in ()).throw(SystemExit(2)))
    assert ok['ok'] and ok['data'] == [1]
    assert bad['ok'] is False and 'SystemExit' in bad['error']


def test_doctor_reports_every_check_even_when_they_crash(monkeypatch):
    def boom():
        raise RuntimeError('down')
    monkeypatch.setattr(agent, 'CHECKS', [('a', boom), ('b', lambda: ('ok', 'fine', ''))])
    r = agent.doctor()
    assert r['overall'] == 'fail'
    assert [c['check'] for c in r['checks']] == ['a', 'b']
    assert r['checks'][1]['status'] == 'ok'


def _fake_ladok(monkeypatch, result):
    import unitoolkit.tools as tools
    monkeypatch.setattr(tools, 'call', lambda name, args: result)


def test_ladok_check_points_to_the_opt_in_when_auto_login_is_off(monkeypatch):
    monkeypatch.delenv('LADOK_AUTOLOGIN', raising=False)
    monkeypatch.delenv('LADOK_IDP_ENTITY', raising=False)
    _fake_ladok(monkeypatch, {'error': 'ingen Ladok-session'})
    status, detail, fix = agent._check_ladok()
    assert status == 'fail' and 'ingen Ladok-session' in detail
    assert 'LADOK_AUTOLOGIN=1' in fix


def test_ladok_check_asks_for_a_human_only_after_auto_login_failed(monkeypatch):
    monkeypatch.setenv('LADOK_AUTOLOGIN', '1')
    monkeypatch.setenv('LADOK_IDP_ENTITY', 'https://swamid.example.se/idp/shibboleth')
    _fake_ladok(monkeypatch, {'error': 'inloggning misslyckades: picker: ...'})
    status, detail, fix = agent._check_ladok()
    assert status == 'fail' and detail.startswith('auto-login tried and failed')
    assert 'sign in to Ladok once' in fix and 'LADOK_AUTOLOGIN' not in fix


def test_ladok_auto_login_needs_both_settings(monkeypatch):
    monkeypatch.setenv('LADOK_AUTOLOGIN', '1')
    monkeypatch.delenv('LADOK_IDP_ENTITY', raising=False)
    assert agent._ladok_autologin_on() is False
    monkeypatch.setenv('LADOK_IDP_ENTITY', 'x')
    monkeypatch.setenv('LADOK_AUTOLOGIN', 'no')
    assert agent._ladok_autologin_on() is False


def test_ladok_check_ok_counts_rows(monkeypatch):
    _fake_ladok(monkeypatch, {'windows': [{'course': 'A'}, {'course': 'B'}, 'stray text line']})
    assert agent._check_ladok() == ('ok', '2 registration windows readable', '')
