"""Guards for the Koha tools that must fail loudly instead of guessing.

The happy path needs a proxied browser and the library's Cloudflare to cooperate, so it
is exercised by hand (`python -m unitoolkit koha_search q=omvårdnad`); what is pinned
here is the behaviour that must not silently degrade.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unitoolkit import koha  # noqa: E402
from unitoolkit import tools as toolreg  # noqa: E402


def test_empty_query_is_refused():
    with pytest.raises(koha.KohaError):
        koha.search('   ')


def test_biblionumber_must_be_digits():
    with pytest.raises(koha.KohaError):
        koha.availability('../../etc/passwd')


def test_credentials_file_is_two_lines(monkeypatch, tmp_path):
    f = tmp_path / 'KOHA_CREDENTIALS'
    f.write_text('username\n1234\n')
    monkeypatch.setenv('KOHA_CREDENTIALS_FILE', str(f))
    assert koha.credentials() == ('username', '1234')


def test_missing_credentials_names_the_file():
    with pytest.raises(koha.KohaError) as exc:
        koha._no_credentials()
    assert 'KOHA_CREDENTIALS' in str(exc.value)


def test_tools_are_registered_with_schemas():
    for name in ('koha_search', 'koha_item', 'koha_loans', 'koha_status'):
        assert name in toolreg.TOOLS, name
        assert toolreg.TOOLS[name]['inputSchema']['type'] == 'object'
    assert toolreg.TOOLS['koha_search']['inputSchema']['required'] == ['q']


def test_status_reports_the_transport_without_starting_anything():
    st = koha.status()
    assert set(st) == {'cdp', 'cdp_up', 'chain_proxy', 'chain_up', 'profile', 'credentials'}
    assert isinstance(st['cdp_up'], bool) and isinstance(st['chain_up'], bool)
