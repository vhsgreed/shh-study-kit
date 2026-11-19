"""Offline tests for the reference readers: robots guards, text extraction, slug search."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from unitoolkit import refs  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "vardhandboken_page.html"


def test_refuses_hosts_outside_the_allowlist():
    for url in ("https://www.internetmedicin.se/psykiatri/bipolar-sjukdom",
                "https://example.com/x", "http://www.vardhandboken.se.evil.test/x"):
        with pytest.raises(ValueError):
            refs._check(url)


def test_refuses_paths_the_robots_file_disallows():
    with pytest.raises(ValueError):
        refs._check("https://www.vardhandboken.se/episerver/foo")
    with pytest.raises(ValueError):
        refs._check("https://www.lakemedelsboken.se/wp-content/uploads/x.pdf")
    with pytest.raises(ValueError):
        refs._check("https://www.janusinfo.se/print/thing")


def test_allows_ordinary_pages():
    assert refs._check("https://www.vardhandboken.se/vard-och-behandling/lakemedel/") == "www.vardhandboken.se"
    assert refs._check("https://viss.nu/kunskapsstod") == "viss.nu"


def test_text_of_keeps_content_and_drops_markup():
    text = refs.text_of(FIXTURE.read_text())
    assert "Litium" in text
    assert "<" not in text and "script" not in text.lower()
    assert "\n\n\n" not in text


def test_vardhandboken_search_ranks_slug_matches(monkeypatch):
    pages = [{"url": "https://www.vardhandboken.se/a/lakemedel/litium/", "section": "a",
              "slug": "litium", "depth": 3},
             {"url": "https://www.vardhandboken.se/a/lakemedel/insulin/", "section": "a",
              "slug": "insulin", "depth": 3}]
    monkeypatch.setattr(refs, "vardhandboken_index", lambda refresh=False: pages)
    monkeypatch.setattr(refs, "page", lambda url, refresh=False: {
        "url": url, "title": "sida", "fetched": "2026-11-19", "text": "innehåll om " + url})
    hits = refs.vardhandboken_search("litium", limit=5)
    assert [h["url"] for h in hits] == ["https://www.vardhandboken.se/a/lakemedel/litium/"]
    assert hits[0]["title"] == "sida"


def test_search_rejects_sites_without_an_index():
    with pytest.raises(ValueError):
        refs.search("internetmedicin", "litium")
