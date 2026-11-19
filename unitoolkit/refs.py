"""Clinician-side reference readers: Vårdhandboken, Läkemedelsboken, Janusinfo/Viss.

Read-only, plain HTTP, robots.txt obeyed per site (checked 2026-11-19):
  www.vardhandboken.se   crawls allowed; only /episerver /util /modules /error are off-limits.
                         Page list comes from its sitemap, so no search endpoint is scraped.
  www.lakemedelsboken.se HTML pages allowed; the .pdf/.doc/.xls downloads are not, so this
                         module only ever asks for HTML.
  www.janusinfo.se, viss.nu  content allowed; session, bookmark, print and cart paths refused.
  www.internetmedicin.se NOT supported: robots.txt disallows every crawler except search
                         engines, so there is no reader for it (one page by hand in a browser).
  fass.se                not here: sections are filled in by JavaScript, so its product pages
                         have to be read in a browser (see `page`).

Every result carries its source URL and fetch date. Requests are throttled per host and
cached on disk, so a repeated lookup costs nothing.
"""

import hashlib
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request

CACHE = pathlib.Path.home() / ".cache/unitoolkit/refs"
UA = "unitoolkit/1.0 (student research; +https://github.com/vhsgreed/shh-study-kit)"
PAUSE = 1.5                     # seconds between requests to the same host

HOSTS = {
    "www.vardhandboken.se": {"sitemap": "https://www.vardhandboken.se/pagessitemap.xml", "deny": ("/episerver/", "/util/", "/modules/", "/error/")},
    "www.lakemedelsboken.se": {"deny": (".pdf", ".doc", ".xls")},
    "www.janusinfo.se": {"deny": ("/session", "/bookmark", "/print", "/cart")},
    "viss.nu": {"deny": ("/session", "/bookmark", "/print", "/cart")},
}

_last = {}


# ------------------------------------------------------------------ plumbing
def _check(url):
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    if host not in HOSTS:
        raise ValueError(f"host not allowed: {host!r}; allowed: {sorted(HOSTS)}")
    low = (p.path + ("?" + p.query if p.query else "")).lower()
    for bad in HOSTS[host].get("deny", ()):
        if bad in low:
            raise ValueError(f"robots.txt disallows this path: {bad} in {low}")
    return host


def _get(url, timeout=30):
    host = _check(url)
    wait = PAUSE - (time.monotonic() - _last.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        _last[host] = time.monotonic()
        return r.read().decode("utf-8", "replace")


def _cache_path(url):
    return CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")


def _cached(url, refresh=False):
    p = _cache_path(url)
    if p.exists() and not refresh:
        return json.loads(p.read_text())
    return None


def _store(url, data):
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(json.dumps(data, ensure_ascii=False))


def text_of(html):
    """Visible text of an HTML page, roughly as a reader would see it."""
    html = re.sub(r"(?is)<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr|section|article|table)>", "\n", html)
    html = re.sub(r"(?i)</t[dh]>", " | ", html)
    txt = re.sub(r"<[^>]+>", "", html)
    import html as _h
    txt = _h.unescape(txt)
    lines, out, blank = [re.sub(r"[ \t\u00a0]+", " ", l).strip() for l in txt.splitlines()], [], False
    for l in lines:
        if l:
            out.append(l)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def page(url, refresh=False):
    """One allowed page as text: {url, title, fetched, text}."""
    hit = _cached(url, refresh)
    if hit:
        return hit
    html = _get(url)
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    import html as _h
    data = {
        "url": url,
        "title": _h.unescape(re.sub(r"\s+", " ", title.group(1))).strip() if title else "",
        "fetched": time.strftime("%Y-%m-%d"),
        "text": text_of(html),
    }
    _store(url, data)
    return data


# ------------------------------------------------------- Vårdhandboken index
def vardhandboken_index(refresh=False):
    """Every Vårdhandboken page URL from its sitemap: {url, slug, section}."""
    url = HOSTS["www.vardhandboken.se"]["sitemap"]
    hit = _cached(url, refresh)
    if hit:
        return hit["pages"]
    xml = _get(url)
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    pages = []
    for u in urls:
        path = urllib.parse.urlsplit(u).path.strip("/").split("/")
        pages.append({"url": u, "section": path[0] if path else "",
                      "slug": "/".join(path).replace("-", " ").lower(),
                      "page": path[-1] if path else "", "depth": len(path)})
    _store(url, {"fetched": time.strftime("%Y-%m-%d"), "pages": pages})
    return pages


STOP = {"och", "i", "vid", "for", "för", "av", "till", "med", "den", "det", "en", "ett", "pa", "på", "som", "om"}


def vardhandboken_search(query, limit=10):
    """Match a query against Vårdhandboken's page paths, then read the best hits.

    No site search is scraped: the sitemap gives the page list, and only the top `limit`
    hits are fetched for their title and a snippet.
    """
    words = [w for w in re.split(r"[^a-zåäö0-9]+", query.lower()) if w and w not in STOP]
    if not words:
        return []
    scored = []
    for p in vardhandboken_index():
        score = sum(1 for w in words if w in p["slug"])
        if score:
            scored.append((score, len(p["slug"]), p))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out = []
    for score, _, p in scored[:limit]:
        try:
            d = page(p["url"])
        except Exception as e:                      # a dead page must not kill the search
            out.append({"url": p["url"], "title": "", "score": score, "error": str(e)})
            continue
        snippet = " ".join(d["text"].split())[:300]
        out.append({"url": p["url"], "title": d["title"], "score": score,
                    "fetched": d["fetched"], "snippet": snippet})
    return out


def search(site, query, limit=10):
    """Search one supported site. site: 'vardhandboken' (the only site with an index)."""
    if site in ("vardhandboken", "vardhandboken.se"):
        return vardhandboken_search(query, limit)
    raise ValueError("only 'vardhandboken' has an index; use page(url) for the others")
