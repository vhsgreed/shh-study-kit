"""Offline tests for the full-text layer: DOI handling, the licence cap, Cochrane routing."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unitoolkit import fulltext as ft  # noqa: E402
from unitoolkit.databases import DatabaseError  # noqa: E402


def test_rejects_non_doi():
    try:
        ft.fetch('not a doi')
    except DatabaseError as exc:
        assert 'not a DOI' in str(exc)
    else:
        raise AssertionError('should have raised')


def test_slug_is_filesystem_safe():
    assert ft._slug('10.1111/inr.13079') == '10.1111_inr.13079'


def test_cochrane_resolves_without_network():
    url = ft.resolve('10.1002/14651858.CD005563.pub3')
    assert url == 'https://www.cochranelibrary.com/cdsr/doi/10.1002/14651858.CD005563.pub3/full'


def test_hourly_cap_blocks_bulk(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, 'OUT', tmp_path)
    monkeypatch.setattr(ft, 'LEDGER', tmp_path / '.downloads.json')
    now = time.time()
    (tmp_path / '.downloads.json').write_text(json.dumps(
        [{'t': now - 60, 'doi': '10.1/x%d' % i} for i in range(ft.MAX_PER_HOUR)]))
    assert ft._under_cap() is False
    try:
        ft.fetch('10.1111/never.downloaded')
    except DatabaseError as exc:
        assert 'hourly cap' in str(exc)
    else:
        raise AssertionError('cap should refuse')


def test_cap_forgets_after_an_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, 'LEDGER', tmp_path / '.downloads.json')
    old = time.time() - 4000
    (tmp_path / '.downloads.json').write_text(json.dumps(
        [{'t': old, 'doi': '10.1/x%d' % i} for i in range(ft.MAX_PER_HOUR)]))
    assert ft._under_cap() is True


def test_cached_pdf_is_returned_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, 'OUT', tmp_path)
    (tmp_path / '10.1_cached.pdf').write_bytes(b'%PDF-1.7\n' + b'x' * 2000)
    r = ft.fetch('10.1/cached', text=False)
    assert r['via'] == 'cache' and r['format'] == 'pdf'
