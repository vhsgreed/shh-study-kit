"""Offline tests for citation helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unitoolkit import citation as c  # noqa: E402


def test_clean_doi_strips_prefixes():
    for raw in ('https://doi.org/10.1111/inr.13079', 'doi: 10.1111/inr.13079', '10.1111/inr.13079'):
        assert c.clean_doi(raw) == '10.1111/inr.13079'


def test_relevance_prefers_on_topic_titles():
    q = 'delirium prevention nursing'
    on = {'title': 'Interventions for preventing delirium in older people'}
    off = {'title': 'Neuraminidase inhibitors for preventing and treating influenza'}
    assert c.relevance(q, on) > c.relevance(q, off)


def test_relevance_ignores_stopwords():
    assert c.relevance('the and of', {'title': 'anything'}) == 0.0


def test_total_parses_non_breaking_space():
    assert c._total('1\xa0012') == 1012
    assert c._total('') is None


def test_dedupe_key_is_doi_case_insensitive():
    assert c._key({'doi': '10.1002/14651858.CD005563'}) == c._key({'doi': '10.1002/14651858.cd005563'})
