"""Database access for SHH, through the library's OCLC EZproxy.

Every subscription database Sophiahemmet offers sits behind `shh.idm.oclc.org`, which hands
the visitor to the school's Microsoft Entra tenant. Once one sign-in has happened with
"stay signed in", the browser profile keeps an Entra session and EZproxy accepts it silently
afterwards, so a search needs no credentials at all. When the session has expired EZproxy
shows the Entra sign-in page, and every function here raises instead of pretending the
result set was empty.

What is read-only: searches and record pages. Nothing signs in, renews, or writes.
"""

import asyncio
import json
import os
import re
import urllib.request

CDP = os.environ.get('UNITOOLKIT_CDP_SCHOOL', 'http://127.0.0.1:9233')
EZ = 'https://shh.idm.oclc.org/login?url='
EBSCO = 'https://research-ebsco-com.shh.idm.oclc.org'
EBSCO_COLLECTION = 'm7tdf3'


class DatabaseError(RuntimeError):
    pass


# Databases the school subscribes to, with the EZproxy target each one needs.
# `ebsco` entries are searched through the EBSCOhost collection; the rest open their own site.
DATABASES = {
    'cinahl': {'name': 'CINAHL', 'ebsco': 'cin20',
               'url': EBSCO + '/c/%s/search/results?db=cin20&q=' % EBSCO_COLLECTION},
    'psycinfo': {'name': 'PsycInfo', 'ebsco': 'psyh',
                 'url': EBSCO + '/c/%s/search/results?db=psyh&q=' % EBSCO_COLLECTION},
    'cochrane': {'name': 'Cochrane Library', 'reader': 'cochrane',
                 'url': 'https://www.cochranelibrary.com/cdsr/reviews'},
    'wos': {'name': 'Web of Science', 'reader': 'wos', 'url': 'https://webofknowledge.com/wos'},
    'jcr': {'name': 'Journal Citation Reports', 'url': 'https://jcr.clarivate.com/jcr/home'},
    'sage': {'name': 'Sage Research Methods', 'url': 'https://methods.sagepub.com/'},
    'nese': {'name': 'Nationalencyklopedin', 'url': 'https://www.ne.se/'},
    'ffhb': {'name': 'Författningshandbok (Liber)',
             'url': 'https://online.liber.se/Home/LinkAccess/?acid=qQdnnEJcDpnaiECJYMw0BQ%3d%3d&'},
    'pubmed': {'name': 'PubMed (EZproxy, for full-text links)',
               'url': 'http://www.ncbi.nlm.nih.gov/pubmed/?otool=isesushlib'},
}

FREE = {
    'pubmed_api': 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils',
    'libris': 'https://libris.kb.se',
    'diva': 'http://shh.diva-portal.org/smash/search.jsf',
    'doaj': 'https://www.doaj.org/',
    'eric': 'https://eric.ed.gov/',
    'swepub': 'http://swepub.kb.se/form_extended.jsp',
}

# A result card on the EBSCOhost results page.
CARDS_JS = r"""(() => {
  const cards = [...document.querySelectorAll('div[class*="search-result-item_search-result-item__"]')]
    .filter(c => c.querySelector('a[class*="result-item-title_result-item-title"]'));
  const total = (document.body.innerText.match(/([\d\s,.]+)\s+total results/i) || [])[1] || '';
  return JSON.stringify({
    total: total.trim(),
    authenticated: /SOPHIAHEMMET/i.test(document.body.innerText),
    cards: cards.map(c => {
      const a = c.querySelector('a[class*="result-item-title_result-item-title"]');
      const lines = (c.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
      return {
        title: (a ? a.innerText : '').trim(),
        href: a ? a.getAttribute('href') : '',
        lines: lines,
        peer_reviewed: lines.includes('Peer reviewed') || /Peer reviewed/i.test(c.innerText),
        full_text: /Full Text Finder|PDF Full Text|HTML Full Text/i.test(c.innerText),
      };
    })
  });
})()"""


DETAIL_JS = r"""(() => {
  const t = (document.body.innerText || '');
  const flat = t.replace(/\s+/g, ' ');
  const blocks = [];
  const abs = t.match(/Abstract\s*\n([^\n][\s\S]{80,3000})/);
  if (abs) blocks.push(abs[1].split(/(?:Subjects|DOI|PMID|Author Affiliations)/)[0].trim());
  const pick = (re) => (flat.match(re) || [])[1] || '';
  return JSON.stringify({
    title: (document.querySelector('h1') || {}).innerText || '',
    abstract: (blocks[0] || '').slice(0, 4000),
    doi: pick(/DOI\s*:?\s*(10\.[^\s,;]+)/i),
    pmid: pick(/PMID\s*:?\s*(\d+)/),
    authors: pick(/Authors\s+(.{0,400}?)(?:Source|Abstract|Journal|Publication)/),
    source_line: pick(/Source\s+(.{0,200}?)(?:Abstract|Subjects|DOI)/),
    authenticated: /SOPHIAHEMMET/i.test(t),
    login_wall: /Sign in to your account|Logga in med/i.test(t),
  });
})()"""


class _Tab:
    """One CDP page target, kept for the length of a call."""

    def __init__(self, url=None):
        self.url = url

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        self._run(self._open())
        return self

    def __exit__(self, *exc):
        try:
            try:
                self.loop.run_until_complete(self.call('Target.closeTarget', {'targetId': self.target}))
            except Exception:
                pass
            self.loop.run_until_complete(self.ws.close())
        finally:
            self.loop.close()
        return False

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    async def _open(self):
        import websockets
        try:
            ver = json.load(urllib.request.urlopen(CDP + '/json/version', timeout=5))
        except Exception:
            raise DatabaseError(
                'the school browser is not running on %s; start it with agent_chrome.sh' % CDP)
        self.ws = await websockets.connect(ver['webSocketDebuggerUrl'], max_size=None)
        self.n = 0
        self.target = (await self.call('Target.createTarget', {'url': self.url or 'about:blank'}))['targetId']
        self.sid = (await self.call('Target.attachToTarget',
                                    {'targetId': self.target, 'flatten': True}))['sessionId']
        await self.call('Runtime.enable', {}, self.sid)

    async def call(self, method, params=None, sid=None):
        self.n += 1
        msg = {'id': self.n, 'method': method, 'params': params or {}}
        if sid:
            msg['sessionId'] = sid
        await self.ws.send(json.dumps(msg))
        while True:
            r = json.loads(await self.ws.recv())
            if r.get('id') == self.n:
                if 'error' in r:
                    raise DatabaseError('%s: %s' % (method, r['error'].get('message')))
                return r.get('result', r)

    def goto(self, url, settle=3.0):
        self._run(self.call('Page.navigate', {'url': url}, self.sid))
        self._run(asyncio.sleep(settle))

    def eval(self, expr):
        r = self._run(self.call('Runtime.evaluate',
                                {'expression': expr, 'returnByValue': True, 'awaitPromise': True},
                                self.sid))
        if r.get('exceptionDetails'):
            raise DatabaseError('page script failed: %s' % r['exceptionDetails'].get('text'))
        return (r.get('result') or {}).get('value')

    def text(self):
        return (self.eval('document.body.innerText') or '')


def _check_session(page):
    """EZproxy answers with the Entra sign-in page when the school session has gone."""
    url = page.eval('location.href') or ''
    body = page.text()
    if 'login.microsoftonline.com' in url or 'Sign in to your account' in body:
        raise DatabaseError(
            'the school sign-in has expired: open %s in the school browser and sign in once '
            '(Microsoft Entra, with MFA). After that EZproxy accepts the session silently.'
            % (EZ + '%s' % EBSCO))


def _split_authors(by):
    by = re.sub(r'^\s*By:\s*', '', by or '')
    by = re.split(r'(?:In:|Subjects:)|\s{2,}', by)[0]      # a short record puts fields on one line
    by = re.sub(r'\s*\+\s*\d+\s*more\s*$', '', by)
    out = []
    for name in re.split(r'\s*[;⁠]\s*', by):
        name = name.strip(' .,')
        if not name:
            continue
        out.append(name)
    return out


_MONTH = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'


def parse_source(source):
    """'Psychiatry International, Oct2026, volume 7, issue 5, page 235 • CINAHL'"""
    s = re.sub(r'^(?:In:)\s*', '', (source or '').strip())
    s = re.split(r'\s*\u2022\s*', s)[0].strip()
    s = re.sub(r'\s*\(\d+p\)\s*$', '', s)                    # "(10p)" page count
    s = re.sub(r'(CINAHL|PsycINFO|MEDLINE|Academic Search\w*)\s*$', '', s).strip(' .,')
    s = re.sub(r'\s*\(\d{4,5}-?[\dxX]*\)', '', s)   # journal ISSN in brackets, not a year
    out = {'raw': s}
    m = re.search(r'(?<!\d)(19|20)\d{2}(?!\d)', s)
    if m:
        out['year'] = m.group(0)
    m = re.search(r'volume\s+([\w-]+)', s)
    if m:
        out['volume'] = m.group(1)
    m = re.search(r'issue\s+([\w-]+)', s)
    if m:
        out['issue'] = m.group(1)
    m = re.search(r'pages?\s+([\w\-–]+)', s, re.I)
    if m:
        out['pages'] = m.group(1)
    out['journal'] = re.split(
        r',\s*(?:%s\s*)?(?:19|20)\d{2}|,\s*(?:19|20)\d{2}|,\s*volume' % _MONTH, s)[0].strip(' .,')
    return {k: v for k, v in out.items() if v}


def _normalise_query(query):
    """Give a bare multi-word query AND semantics.

    EBSCO's URL parameter treats a space-separated string as one phrase, so "delirium older
    adults" comes back with a single hit while the same words joined by AND return the
    expected set. Keep quotes, braces and existing operators untouched, since those carry
    the author's intent.
    """
    q = (query or '').strip()
    if not q or re.search(r'"|\bAND\b|\bOR\b|\bNOT\b|\bN[0-9]\b|[()]', q):
        return q
    words = q.split()
    if len(words) < 2:
        return q
    return ' AND '.join(words)


def search(database, query, limit=10):
    """Run a search in one database and return citation-shaped results."""
    key = (database or '').strip().lower()
    if key not in DATABASES:
        raise DatabaseError('unknown database %r; known: %s' % (database, ', '.join(sorted(DATABASES))))
    db = DATABASES[key]
    if db.get('reader') == 'cochrane':
        return _search_cochrane(query, limit)
    if db.get('reader') == 'wos':
        return _search_wos(query, limit)
    if not db.get('ebsco'):
        raise DatabaseError('%s has no structured search here; open it with open_database()'
                            % db['name'])
    import urllib.parse
    # %20, not '+': EBSCO reads a literal plus as an operator and a two-word query then
    # matches nothing at all.
    url = db['url'] + urllib.parse.quote(_normalise_query(query), safe='') + '&limiters=None'
    with _Tab(url) as page:
        for _ in range(12):
            page._run(asyncio.sleep(3))
            try:
                raw = json.loads(page.eval(CARDS_JS) or '{}')
            except DatabaseError:
                continue
            if raw.get('cards'):
                break
            _check_session(page)
            if 'No results' in page.text():
                return {'database': db['name'], 'query': query, 'total': '0', 'results': []}
        else:
            _check_session(page)
            raise DatabaseError('no result cards appeared for %r in %s' % (query, db['name']))
    out = []
    for c in raw['cards'][:limit]:
        lines = c.get('lines') or []
        by = next((l for l in lines if l.lower().startswith('by:')), '')
        # A short record puts the source on the same line as the authors: "By:A;BIn:Journal, 2026, ..."
        inline_src = ''
        if 'In:' in by:
            by, inline_src = by.split('In:', 1)
            inline_src = 'In:' + inline_src
        src_line = next((l for l in lines if l.lower().startswith('in:')), '') or inline_src
        if not src_line:
            src_line = next((l for l in lines
                             if not l.lower().startswith('by:')
                             and re.search(r'\bvolume\s+\d', l) and len(l) < 220), '')
        subj = next((l for l in lines if l.lower().startswith('subjects:')), '')
        subj = re.split(r'\+\d+ more', subj)[0]
        src = parse_source(src_line)
        out.append({
            'title': c.get('title', ''),
            'authors': _split_authors(by),
            'journal': src.get('journal', ''),
            'year': src.get('year', ''),
            'volume': src.get('volume', ''),
            'issue': src.get('issue', ''),
            'pages': src.get('pages', ''),
            'subjects': re.sub(r'^Subjects:\s*', '', subj).strip(' ⁠;'),
            'peer_reviewed': c.get('peer_reviewed', False),
            'full_text': c.get('full_text', False),
            'url': (EBSCO + c['href']) if c.get('href', '').startswith('/') else c.get('href', ''),
            'database': db['name'],
        })
    return {'database': db['name'], 'query': query, 'total': raw.get('total', ''), 'results': out}


def detail(url):
    """Open one record and return its abstract and identifiers."""
    with _Tab(url) as page:
        for _ in range(6):
            page._run(asyncio.sleep(2))
            if 'Abstract' in page.text() or 'Subjects' in page.text():
                break
        _check_session(page)
        return json.loads(page.eval(DETAIL_JS) or '{}')


def status(database='cinahl'):
    """Is the school session still good for this database?"""
    key = (database or '').strip().lower()
    db = DATABASES.get(key)
    if not db:
        raise DatabaseError('unknown database %r' % database)
    with _Tab() as page:
        page.goto(EZ + db['url'], settle=4)
        page._run(asyncio.sleep(3))
        url = page.eval('location.href') or ''
        body = page.text()
        signed_out = 'login.microsoftonline.com' in url or 'Sign in to your account' in body
        return {
            'database': db['name'],
            'authenticated': not signed_out,
            'landed_on': url.split('?')[0][:120],
            'institution': 'Sophiahemmet Högskola' if 'SOPHIAHEMMET' in body.upper() else '',
        }


def open_database(database):
    """Open a database in the school browser and report where it landed."""
    key = (database or '').strip().lower()
    db = DATABASES.get(key)
    if not db:
        raise DatabaseError('unknown database %r; known: %s' % (database, ', '.join(sorted(DATABASES))))
    with _Tab(EZ + db['url']) as page:
        page._run(asyncio.sleep(4))
        url = page.eval('location.href') or ''
        body = page.text()
        return {
            'database': db['name'],
            'landed_on': url.split('?')[0][:120],
            'authenticated': 'login.microsoftonline.com' not in url,
            'hint': ('open in the school browser and sign in once' if 'login.microsoftonline.com' in url
                     else ''),
        }


def catalogue():
    """The databases the school lists, with what each is good for."""
    return [
        {'key': k, 'name': v['name'], 'searchable': bool(v.get('ebsco') or v.get('reader')),
         'behind_ezproxy': True}
        for k, v in DATABASES.items()
    ]


# ---- Cochrane Library -------------------------------------------------------------------
# The URL parameters on /search are ignored unless the session came from the form, and Enter
# in the box does nothing: set the value through the native setter, then click .searchByBtn.
COCHRANE = 'https://www-cochranelibrary-com.shh.idm.oclc.org'

COCHRANE_JS = r"""(() => {
  const b = document.body.innerText;
  const total = ((b.match(/(\d[\d,]*)\s*\n?\s*Cochrane Reviews/) || [])[1] || '').replace(/,/g, '');
  return JSON.stringify({total: total, url: location.href, items:
    [...document.querySelectorAll('.search-results-item')].map(e => {
      const a = e.querySelector('a[href*="/cdsr/doi/"]');
      const lines = e.innerText.split('\n').map(s => s.trim()).filter(Boolean);
      return {title: a ? a.innerText.trim() : '', href: a ? a.getAttribute('href') : '', lines: lines};
    })});
})()"""


def _search_cochrane(query, limit):
    with _Tab(COCHRANE + '/cdsr/reviews') as page:
        for _ in range(12):                      # the search box is rendered late
            page._run(asyncio.sleep(2))
            try:
                if page.eval("!!document.querySelector('#searchText') && !!document.querySelector('.searchByBtn')"):
                    break
            except DatabaseError:
                continue
        _check_session(page)
        done = page.eval("""(q => {
          const i = document.querySelector('#searchText');
          if (!i) return 'no box';
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(i, q);
          i.dispatchEvent(new Event('input', {bubbles: true}));
          i.dispatchEvent(new Event('change', {bubbles: true}));
          const b = document.querySelector('.searchByBtn');
          if (!b) return 'no button';
          b.click(); return 'ok';
        })(%s)""" % json.dumps(query))
        if done != 'ok':
            raise DatabaseError('Cochrane search form changed: %s' % done)
        raw = {}
        for _ in range(8):
            page._run(asyncio.sleep(3))
            try:
                raw = json.loads(page.eval(COCHRANE_JS) or '{}')
            except DatabaseError:
                continue
            if raw.get('url', '').rstrip('/').endswith('/search') and raw.get('items') is not None:
                break
    out = []
    for it in (raw.get('items') or [])[:max(limit, 25)]:
        m = re.search(r'/cdsr/doi/(10\.[^/]+/[^/]+)/', it.get('href') or '')
        doi = m.group(1) if m else ''
        lines = [l for l in it.get('lines') or [] if not re.fullmatch(r'\d+', l)]
        title = it.get('title') or (lines[0] if lines else '')
        rest = [l for l in lines if l != title]
        authors = [a.strip() for a in (rest[0] if rest else '').split(',') if a.strip()]
        date = next((l for l in rest if re.search(r'\b(19|20)\d{2}\b', l)), '')
        kind = next((l for l in rest if re.search(r'Intervention|Diagnostic|Prognosis|Overview|Methodology|Qualitative', l)), '')
        out.append({
            'title': title, 'authors': authors, 'journal': 'Cochrane Database of Systematic Reviews',
            'year': (re.search(r'(19|20)\d{2}', date) or [''])[0] if date else '',
            'review_type': kind, 'doi': doi, 'peer_reviewed': True, 'full_text': True,
            'url': COCHRANE + (it.get('href') or ''), 'database': 'Cochrane Library',
        })
    return {'database': 'Cochrane Library', 'query': query, 'total': raw.get('total', ''),
            'results': out}


# ---- Web of Science ---------------------------------------------------------------------
# The summary page takes the query as JSON in the URL (`queryJson`), which skips the Angular
# search form entirely. Records are <app-record>; the card has no DOI, so ask Crossref by
# title when a DOI is needed (doi_for_title).
WOS = 'https://www-webofscience-com.shh.idm.oclc.org'

WOS_JS = r"""(() => {
  const b = document.body.innerText;
  return JSON.stringify({
    total: ((b.match(/([\d,]+)\s+results/) || [])[1] || '').replace(/,/g, ''),
    records: [...document.querySelectorAll('app-record')].map(r => {
      const a = r.querySelector('a[data-ta="summary-record-title-link"]');
      const cites = r.querySelector('a[data-ta="stat-number-citation-related-count"]');
      return {
        title: a ? a.innerText.trim() : '',
        href: a ? a.getAttribute('href') : '',
        authors: [...r.querySelectorAll('a[data-ta*="DisplayName-author"]')].map(x => x.innerText.trim()),
        lines: r.innerText.split('\n').map(s => s.trim()).filter(Boolean),
        cited_by: cites ? cites.innerText.trim() : '',
      };
    })});
})()"""


def _search_wos(query, limit, field='TS'):
    import urllib.parse
    qj = json.dumps([{'rowBoolean': None, 'rowField': field, 'rowText': query}])
    url = WOS + '/wos/woscc/general-summary?queryJson=' + urllib.parse.quote(qj)
    raw = {}
    with _Tab(url) as page:
        for _ in range(10):
            page._run(asyncio.sleep(4))
            try:
                raw = json.loads(page.eval(WOS_JS) or '{}')
            except DatabaseError:
                continue
            if raw.get('records'):
                break
            _check_session(page)
            if re.search(r'Your search found no results|0 results', page.text()):
                break
    out = []
    for r in (raw.get('records') or [])[:limit]:
        lines = r.get('lines') or []
        title = r.get('title', '')
        i = lines.index(title) if title in lines else 1
        tail = lines[i + 1:]
        date = next((l for l in tail if re.fullmatch(r'(?:[A-Z][a-z]{2}\s+)?(?:\d{1,2}\s+)?(19|20)\d{2}', l)), '')
        journal = next((re.sub(r'arrow_drop_down$', '', l).strip() for l in tail
                        if l.endswith('arrow_drop_down')), '')
        vol = next((l for l in tail if re.match(r'^\d+(\(\w+\))?(,\s*pp?\.)?', l) and len(l) < 40), '')
        m = re.match(r'^(\d+)(?:\(([^)]+)\))?(?:,\s*pp?\.\s*([\w\-–]+))?', vol)
        authors = r.get('authors') or []
        if not authors and len(tail) > 0:
            authors = [a.strip() for a in re.split(r';|\band\b', tail[0]) if a.strip()]
        out.append({
            'title': title, 'authors': authors, 'journal': journal.title() if journal.isupper() else journal,
            'year': (re.search(r'(19|20)\d{2}', date) or [''])[0] if date else '',
            'volume': m.group(1) if m else '', 'issue': (m.group(2) or '') if m else '',
            'pages': (m.group(3) or '') if m else '', 'cited_by': r.get('cited_by', ''),
            'free_full_text': any('Free Full Text' in l for l in lines),
            'url': WOS + (r.get('href') or ''), 'database': 'Web of Science',
        })
    return {'database': 'Web of Science', 'query': query, 'total': raw.get('total', ''),
            'results': out}
