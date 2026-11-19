"""Offline tests for the registry and its weekly checker."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from unitoolkit import registry, tools  # noqa: E402
import registry_check  # noqa: E402


def test_registry_file_is_valid_and_has_rows():
    data = registry.load()
    assert data['schema'] == 'unitoolkit-registry/1'
    rows = registry.deployments()
    assert len(rows) >= 8
    for r in rows:
        assert r['status'] in registry.STATUSES, r
        assert r['institution'] and r['system']


def test_query_filters():
    assert [r['id'] for r in registry.query(system='ladok')] == ['shh/ladok']
    assert all(r['institution'] == 'shh' for r in registry.query(institution='shh'))
    assert [r['system'] for r in registry.query(system='pubmed')] == ['pubmed']


def test_query_text_reaches_the_gotchas():
    # the reason this exists: an agent asking "cloudflare" should find the row that explains it
    assert 'shh/koha' in [r['id'] for r in registry.query(text='cloudflare')]
    assert 'shh/ladok' in [r['id'] for r in registry.query(text='JSON body')]


def test_tool_wraps_the_query():
    r = tools.call('registry', {'system': 'ezproxy'})
    assert r['count'] == 1 and r['rows'][0]['id'] == 'shh/ezproxy'
    assert 'CINAHL' in r['rows'][0]['databases']
    assert isinstance(r['rows'][0]['elsewhere'], list)


def _tmp_registry(tmp_path, monkeypatch):
    src = ROOT / 'registry' / 'registry.json'
    dst = tmp_path / 'registry.json'
    dst.write_text(src.read_text())
    monkeypatch.setenv('UNITOOLKIT_REGISTRY', str(dst))
    return dst


def test_checker_writes_status_and_date(tmp_path, monkeypatch):
    dst = _tmp_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(registry_check, 'run_probe', lambda label: True)
    rc = registry_check.main(['--only', 'shh/ladok,shh/koha', '--verbose'])
    assert rc == 0
    data = json.loads(dst.read_text())
    rows = {d['id']: d for i in data['institutions'] for d in i['deployments']}
    assert rows['shh/ladok']['status'] == 'ok' and rows['shh/ladok']['last_verified']
    # untouched rows keep their old state: no probes means untested, not ok
    assert rows['shh/sharepoint']['status'] == 'untested'
    assert data['last_run']


def test_checker_marks_a_failing_probe_and_stays_silent_on_success(tmp_path, monkeypatch):
    dst = _tmp_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(registry_check, 'run_probe', lambda label: 'TimeoutError: no answer')
    out = []
    monkeypatch.setattr('builtins.print', lambda *a, **k: out.append(' '.join(map(str, a))))
    rc = registry_check.main(['--only', 'shh/ezproxy'])
    assert rc == 1
    rows = {d['id']: d for i in json.loads(dst.read_text())['institutions'] for d in i['deployments']}
    assert rows['shh/ezproxy']['status'] == 'fail'
    assert out and 'registry FAIL shh/ezproxy' in out[0]


def test_unknown_probe_name_fails_loudly():
    assert registry_check.run_probe('no such probe here').startswith('no such probe')


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    dst = _tmp_registry(tmp_path, monkeypatch)
    before = dst.read_text()
    monkeypatch.setattr(registry_check, 'run_probe', lambda label: True)
    assert registry_check.main(['--only', 'shh/ladok', '--dry-run']) == 0
    assert dst.read_text() == before
