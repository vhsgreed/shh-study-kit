"""Offline tests for the database layer: query normalisation and citation parsing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unitoolkit import databases as db  # noqa: E402


def test_bare_multiword_query_gets_and():
    assert db._normalise_query('delirium older adults') == 'delirium AND older AND adults'


def test_explicit_operators_are_left_alone():
    for q in ('delirium AND older', '"person-centred care" delirium', 'delirium OR confusion',
              'delirium NOT child', 'delirium N3 older'):
        assert db._normalise_query(q) == q


def test_single_word_untouched():
    assert db._normalise_query('delirium') == 'delirium'


def test_parse_source_reads_volume_issue_pages():
    src = db.parse_source('In:Perfusion, Nov2026, volume 41, issue 8, pages 1492-1501 (10p)CINAHL')
    assert src['journal'] == 'Perfusion'
    assert src['year'] == '2026'
    assert src['volume'] == '41'
    assert src['issue'] == '8'
    assert src['pages'] == '1492-1501'


def test_parse_source_handles_singular_page():
    src = db.parse_source('In:Psychiatry International, Oct2026, volume 7, issue 5, page 235CINAHL')
    assert (src['journal'], src['pages']) == ('Psychiatry International', '235')


def test_parse_source_drops_an_issn_so_it_is_not_read_as_a_year():
    src = db.parse_source('Dementia (14713012), 2026, volume 25, issue 8, pages 2000-2019')
    assert (src['journal'], src['year'], src['volume'], src['pages']) == (
        'Dementia', '2026', '25', '2000-2019')


def test_split_authors_strips_by_and_count():
    got = db._split_authors('By:Güdel, Marion⁠;Grunt, Sebastian⁠; +1 more')
    assert got == ['Güdel, Marion', 'Grunt, Sebastian']


def test_unknown_database_is_refused():
    try:
        db.search('upToDate', 'delirium')
    except db.DatabaseError as exc:
        assert 'unknown database' in str(exc)
    else:
        raise AssertionError('should have raised')


def test_catalogue_lists_the_nursing_databases():
    keys = {d['key'] for d in db.catalogue()}
    assert {'cinahl', 'psycinfo', 'cochrane', 'wos'} <= keys
    assert db.catalogue()[0]['behind_ezproxy'] is True
