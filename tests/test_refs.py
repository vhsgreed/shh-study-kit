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


def test_allows_the_bare_lakemedelsboken_host():
    # the site serves pages from lakemedelsboken.se, not only www.
    assert refs._check("https://lakemedelsboken.se/terapiomrader/psykiatri/") == "lakemedelsboken.se"


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


def test_lakemedelsboken_needs_a_deliberate_opt_in(monkeypatch):
    # it 404s non-browser agents, so reading it means wearing a browser UA; that is a choice
    monkeypatch.delenv("UNITOOLKIT_REFS_BROWSER_UA", raising=False)
    with pytest.raises(ValueError) as exc:
        refs._get("https://lakemedelsboken.se/terapiomrader/psykiatri/")
    assert "UNITOOLKIT_REFS_BROWSER_UA=1" in str(exc.value)


def test_opt_in_picks_the_browser_ua_without_a_request(monkeypatch):
    monkeypatch.setenv("UNITOOLKIT_REFS_BROWSER_UA", "1")
    sent = {}

    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html><title>t</title><p>ok</p></html>"

    def fake_urlopen(req, timeout=0):
        sent["ua"] = req.get_header("User-agent")
        return Fake()

    monkeypatch.setattr(refs.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(refs, "_last", {})
    refs._get("https://lakemedelsboken.se/terapiomrader/psykiatri/")
    assert sent["ua"].startswith("Mozilla/5.0")
    sent.clear()
    refs._get("https://www.vardhandboken.se/vard-och-behandling/lakemedel/")
    assert sent["ua"].startswith("unitoolkit/")


def test_tools_are_registered():
    from unitoolkit.tools import TOOLS
    assert set(TOOLS["ref_search"]["inputSchema"]["properties"]) == {"query", "limit"}
    assert set(TOOLS["ref_page"]["inputSchema"]["properties"]) == {"url", "refresh"}


def test_search_folds_diacritics(monkeypatch):
    pages = [{"url": "https://www.vardhandboken.se/a/hjartsvikt/", "section": "a",
              "slug": "hjartsvikt", "depth": 2},
             {"url": "https://www.vardhandboken.se/a/hjärtsvikt-hos-aldre/", "section": "a",
              "slug": "hjärtsvikt hos äldre", "depth": 3}]
    monkeypatch.setattr(refs, "vardhandboken_index", lambda refresh=False: pages)
    monkeypatch.setattr(refs, "page", lambda url, refresh=False: {
        "url": url, "title": "t", "fetched": "2026-11-19", "text": "x"})
    # written without diacritics, it must still find the real Swedish spelling
    assert len(refs.vardhandboken_search("hjartsvikt", limit=5)) == 2
