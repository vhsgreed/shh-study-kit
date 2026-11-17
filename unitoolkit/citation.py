"""Citations: APA 7 references from a DOI, a DOI from a title, and one search across databases.

APA comes from the DOI registry itself (content negotiation on doi.org, which Crossref and
DataCite both answer), not from scraped search cards, so authors, volume and pages are the
publisher's own metadata. Two gotchas fixed here: Crossref lowercases Cochrane DOIs in the
URL it returns, and it adds "Portico." (an archive, not part of the reference) for some
Wiley titles. Swedish APA (svensk APA 7) differs only in "&" vs "och" and "red." vs "Ed.";
pass lang='sv' for those.
"""

import json
import re
import urllib.parse
import urllib.request

UA = {'User-Agent': 'unitoolkit (https://github.com/vhsgreed/shh-study-kit; mailto:unitoolkit@users.noreply.github.com)'}   # mailto = Crossref's polite pool


def _get(url, accept, timeout=20, tries=4):
    """GET with backoff on 429/503: Crossref throttles bursts, and one throttled lookup
    must not take down a whole multi-database search."""
    import time
    import urllib.error
    delay = 1.5
    for attempt in range(tries):
        req = urllib.request.Request(url, headers=dict(UA, Accept=accept))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == tries - 1:
                raise
            wait = e.headers.get('Retry-After')
            time.sleep(float(wait) if wait and wait.isdigit() else delay)
            delay *= 2


def clean_doi(doi):
    return re.sub(r'^(https?://(dx\.)?doi\.org/|doi:\s*)', '', (doi or '').strip(), flags=re.I)


def apa(doi, lang='en'):
    """APA 7 reference for a DOI, from the registry's own metadata."""
    doi = clean_doi(doi)
    if not doi.startswith('10.'):
        raise ValueError('not a DOI: %r' % doi)
    ref = _get('https://doi.org/' + urllib.parse.quote(doi, safe='/'),
               'text/x-bibliography; style=apa; locale=en-US').strip()
    ref = re.sub(r'\s+Portico\.(?=\s+https://doi)', '', ref)          # archive, not a source
    ref = re.sub(r'https://doi\.org/\S+$', 'https://doi.org/' + doi, ref)  # keep the DOI's case
    if lang == 'sv':
        ref = re.sub(r',? & ', ' & ', ref)       # svensk APA keeps "&" inside the reference
        ref = ref.replace('(Ed.)', '(Red.)').replace('(Eds.)', '(Red.)')
    return ref


def doi_for_title(title, author=''):
    """Best Crossref match for a title, with a score so a weak match is visible."""
    q = urllib.parse.urlencode({'query.bibliographic': title, 'query.author': author, 'rows': 1})
    j = json.loads(_get('https://api.crossref.org/works?' + q, 'application/json'))
    items = (j.get('message') or {}).get('items') or []
    if not items:
        return {'doi': '', 'score': 0}
    it = items[0]
    found = (it.get('title') or [''])[0]
    a = re.sub(r'\W+', ' ', title.lower()).split()
    b = re.sub(r'\W+', ' ', found.lower()).split()
    overlap = len(set(a) & set(b)) / max(1, len(set(a)))
    return {'doi': it.get('DOI', ''), 'title': found, 'match': round(overlap, 2),
            'confident': overlap >= 0.8}


_STOP = set('a an and the of in on for to with by at from or as is are was were be vs och i av för på med som en ett att'.split())


def _terms(text):
    return {w for w in re.findall(r'[a-zåäö0-9]+', (text or '').lower()) if w not in _STOP and len(w) > 2}


def relevance(query, row):
    """Share of the query's words found in the title (plus subjects when the card has them).

    Database ranking is not comparable across databases, and Cochrane's own order puts
    off-topic reviews first for multi-word queries. A crude, visible score is better than
    trusting three different black boxes.
    """
    q = _terms(query)
    if not q:
        return 0.0
    hay = _terms((row.get('title') or '') + ' ' + (row.get('subjects') or ''))
    stems = {w[:6] for w in hay}
    hit = sum(1 for w in q if w in hay or w[:6] in stems)
    return round(hit / len(q), 2)


def _total(v):
    digits = re.sub(r'\D', '', str(v or ''))
    return int(digits) if digits else None


def _key(r):
    if r.get('doi'):
        return 'doi:' + r['doi'].lower()
    return 't:' + re.sub(r'\W+', '', (r.get('title') or '').lower())[:80]


def find_sources(query, databases=('cinahl', 'pubmed', 'cochrane'), limit=5, with_apa=True):
    """One query across several databases, deduplicated, each hit with an APA reference.

    This is the call a student's "find me three sources on X" should become. PubMed goes
    through the free E-utilities; the rest through the school's EZproxy.
    """
    from . import databases as dbs
    seen, out, totals, errors = set(), [], {}, {}
    for name in databases:
        try:
            if name == 'pubmed':
                from .tools import call
                res = call('pubmed', {'query': query, 'max': limit})
                hits = res if isinstance(res, list) else (res or {}).get('results', [])
                totals['PubMed'] = None
                rows = [{'title': (h.get('title') or '').rstrip('.'), 'authors': h.get('authors') or [],
                         'journal': h.get('journal') or '', 'year': str(h.get('year') or ''),
                         'doi': h.get('doi') or '', 'pmid': h.get('pmid') or '',
                         'database': 'PubMed'} for h in (hits or [])]
            else:
                # ask for more than we keep: the relevance re-rank needs candidates, since
                # Cochrane's own order puts off-topic reviews first
                res = dbs.search(name, query, limit=max(limit * 4, 20) if name == 'cochrane' else limit)
                totals[res['database']] = _total(res.get('total'))
                rows = res['results']
        except Exception as e:                    # one database failing must not hide the rest
            errors[name] = str(e)[:200]
            continue
        for r in rows:
            r['relevance'] = relevance(query, r)
        # keep the database's order among equals, but never let an off-topic hit outrank
        rows = sorted(rows, key=lambda r: -r['relevance'])
        for r in rows[:limit]:
            if not r.get('doi') and r.get('title') and with_apa:
                try:
                    m = doi_for_title(r['title'], (r.get('authors') or [''])[0].split(',')[0])
                except Exception as e:
                    m = {}
                    r['doi_lookup_error'] = str(e)[:100]
                if m.get('confident'):
                    r['doi'] = m['doi']
            k = _key(r)
            if k in seen:
                for o in out:
                    if _key(o) == k:
                        o.setdefault('also_in', []).append(r.get('database'))
                continue
            seen.add(k)
            if with_apa and r.get('doi'):
                try:
                    r['apa'] = apa(r['doi'])
                except Exception as e:
                    r['apa_error'] = str(e)[:120]
            out.append(r)
    out.sort(key=lambda r: -r.get('relevance', 0))
    return {'query': query, 'totals': totals, 'results': out, 'errors': errors,
            'note': ('APA from the DOI registry keeps the publisher title case; APA 7 wants '
                     'sentence case for article titles. Fix by hand, since proper nouns and '
                     'acronyms cannot be lowercased safely by rule.')}
