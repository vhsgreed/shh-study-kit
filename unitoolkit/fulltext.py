"""Full text of an article the school subscribes to, by DOI.

The DOI is resolved through the school's EZproxy (`shh.idm.oclc.org`), so the publisher
sees Sophiahemmet's subscription. The PDF is captured at the network layer (CDP Fetch
interception) rather than by a page-side fetch(): Wiley, Elsevier and others answer a
script fetch with an HTML viewer or a bot check, but hand the real PDF to a top-level
navigation.

Tested 2026-11-17: Wiley (two paywalled articles, Unpaywall is_oa=false) and Elsevier
(ScienceDirect, via pdf.sciencedirectassets.com) return the PDF. De Gruyter is not
rewritten by the school's EZproxy, so there is no subscription route; the function says so
instead of returning a paywall page.

Licensed content: the school's licences allow a student to read and download for their own
study. They do not allow systematic or bulk downloading, and publishers react to it by
blocking the institution's whole EZproxy. Hence the hourly cap below, which is a rule, not
a tuning knob.
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .databases import DatabaseError, _Tab, EZ

OUT = Path(os.environ.get('UNITOOLKIT_FULLTEXT_DIR',
                          str(Path.home() / '.local/share/unitoolkit/fulltext')))
LEDGER = OUT / '.downloads.json'
MAX_PER_HOUR = 20            # licence hygiene: never let a loop turn into a bulk download

# Publisher hosts that put the PDF behind a URL we can build from the DOI.
PDF_PATTERNS = [
    (re.compile(r'onlinelibrary-wiley-com|wiley-com\.shh'), '/doi/pdfdirect/{doi}?download=true'),
    (re.compile(r'journals-sagepub-com'), '/doi/pdf/{doi}?download=true'),
    (re.compile(r'tandfonline-com'), '/doi/pdf/{doi}?download=true'),
    (re.compile(r'cochranelibrary-com'), '/cdsr/doi/{doi}/pdf/full'),
]

PAGE_JS = r"""(() => {
  const t = document.body ? document.body.innerText : '';
  const meta = (document.querySelector('meta[name=citation_pdf_url]') || {}).content || '';
  const links = [...new Set([...document.querySelectorAll('a')]
      .map(a => a.href).filter(h => h && /pdf/i.test(h) && !/supplement|suppl|figure/i.test(h)))];
  const body = document.querySelector('.article__body, section.article-section__full, #body, '
      + 'article .Body, div.article-body, #article-body, main article, .cdsr-article, '
      + 'section.abstract ~ section, #content article') || (t.length > 20000 ? document.body : null);
  return JSON.stringify({
    url: location.href, host: location.host, title: document.title,
    proxied: /\.shh\.idm\.oclc\.org$/.test(location.host),
    signin: /login\.microsoftonline\.com/.test(location.href),
    meta_pdf: meta, links: links.slice(0, 10),
    access: (t.match(/(Open Access|Free Access|Full Access|Access provided by[^\n]{0,60}|institutional access|Get access|Purchase PDF|Buy article)/i) || [''])[0],
    body_text: body ? body.innerText : '',
  });
})()"""


def _slug(doi):
    return re.sub(r'[^\w.-]+', '_', doi.strip())


def _ledger():
    try:
        return json.loads(LEDGER.read_text())
    except Exception:
        return []


def _note_download(doi):
    now = time.time()
    rows = [r for r in _ledger() if now - r['t'] < 3600]
    rows.append({'t': now, 'doi': doi})
    OUT.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(rows))


def _under_cap():
    now = time.time()
    return sum(1 for r in _ledger() if now - r['t'] < 3600) < MAX_PER_HOUR


def unpaywall(doi):
    """Open-access status, so a free copy is preferred and the paid route is not used for it."""
    try:
        j = json.load(urllib.request.urlopen(
            'https://api.unpaywall.org/v2/%s?email=unitoolkit@users.noreply.github.com'
            % urllib.parse.quote(doi), timeout=15))
    except Exception:
        return {'is_oa': None}
    loc = j.get('best_oa_location') or {}
    return {'is_oa': j.get('is_oa'), 'oa_pdf': loc.get('url_for_pdf') or '',
            'publisher': j.get('publisher') or '', 'title': j.get('title') or ''}


async def _capture_pdf(page, url, timeout=45):
    """Navigate and return the first PDF response body seen on the wire."""
    await page.call('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Response'}]},
                    page.sid)
    page.n += 1
    await page.ws.send(json.dumps({'id': page.n, 'method': 'Page.navigate',
                                   'params': {'url': url}, 'sessionId': page.sid}))
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    try:
        while loop.time() < end:
            try:
                msg = json.loads(await asyncio.wait_for(page.ws.recv(), timeout=end - loop.time()))
            except asyncio.TimeoutError:
                break
            if msg.get('method') != 'Fetch.requestPaused':
                continue
            ev = msg['params']
            rid = ev['requestId']
            ctype = next((h['value'] for h in ev.get('responseHeaders') or []
                          if h['name'].lower() == 'content-type'), '')
            status = ev.get('responseStatusCode') or 0
            if 'pdf' in ctype.lower() and status < 300:
                page.n += 1
                k = page.n
                await page.ws.send(json.dumps({'id': k, 'method': 'Fetch.getResponseBody',
                                               'params': {'requestId': rid}, 'sessionId': page.sid}))
                while True:
                    r = json.loads(await page.ws.recv())
                    if r.get('id') == k:
                        break
                res = r.get('result') or {}
                body = res.get('body', '')
                raw = base64.b64decode(body) if res.get('base64Encoded') else body.encode('latin1')
                page.n += 1
                await page.ws.send(json.dumps({'id': page.n, 'method': 'Fetch.continueRequest',
                                               'params': {'requestId': rid}, 'sessionId': page.sid}))
                return raw, ev['request']['url']
            page.n += 1
            await page.ws.send(json.dumps({'id': page.n, 'method': 'Fetch.continueRequest',
                                           'params': {'requestId': rid}, 'sessionId': page.sid}))
    finally:
        try:
            await page.call('Fetch.disable', {}, page.sid)
        except Exception:
            pass
    return None, None


def resolve(doi):
    """Publisher landing URL for a DOI, resolved outside the proxy.

    Some DOI redirects break when they run through EZproxy (Cochrane lands on a Chrome
    error page), while the publisher URL itself proxies fine. Resolving first and proxying
    the result works for both kinds.
    """
    if doi.lower().startswith('10.1002/14651858.'):      # Cochrane reviews: Wiley's resolver
        return 'https://www.cochranelibrary.com/cdsr/doi/%s/full' % doi   # stops at doi.wiley.com
    req = urllib.request.Request('https://doi.org/' + doi,
                                 headers={'User-Agent': 'Mozilla/5.0 unitoolkit'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:      # many publishers refuse HEAD but still redirected
        return e.geturl() or ''
    except Exception:
        return ''


def _pdf_text(path):
    try:
        return subprocess.run(['pdftotext', '-layout', str(path), '-'], capture_output=True,
                              text=True, timeout=60).stdout
    except Exception:
        return ''


def fetch(doi, text=True):
    """Download the full text of one article through the school's subscription.

    Returns the saved PDF path (or the HTML full text when the publisher only offers that),
    page count, and optionally the extracted text. Raises with a reason when the school has
    no route to it.
    """
    doi = re.sub(r'^(https?://(dx\.)?doi\.org/|doi:\s*)', '', (doi or '').strip(), flags=re.I)
    if not doi.startswith('10.'):
        raise DatabaseError('not a DOI: %r' % doi)
    OUT.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT / (_slug(doi) + '.pdf')
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        return _result(doi, pdf_path, 'cache', text)
    if not _under_cap():
        raise DatabaseError('hourly cap of %d full-text downloads reached; the licence forbids '
                            'bulk downloading, wait or narrow the list' % MAX_PER_HOUR)

    oa = unpaywall(doi)
    landing = resolve(doi)
    with _Tab(EZ + 'https://doi.org/' + doi) as page:
        page._run(asyncio.sleep(12))
        info = json.loads(page.eval(PAGE_JS) or '{}')
        if (not info.get('proxied')) and landing and not info.get('signin'):
            page.goto(EZ + landing, settle=12)
            info = json.loads(page.eval(PAGE_JS) or '{}')
        if info.get('signin'):
            raise DatabaseError('school sign-in expired; run db_status / sign in once in the '
                                'school browser')
        candidates = []
        for rx, tmpl in PDF_PATTERNS:
            if rx.search(info.get('host', '')):
                candidates.append('https://%s%s' % (info['host'], tmpl.format(doi=doi)))
        if info.get('meta_pdf'):
            candidates.append(info['meta_pdf'])
        candidates += [l for l in info.get('links', []) if doi.split('/')[-1].lower() in l.lower()
                       or 'pdfft' in l or '/pdf' in l]
        seen = set()
        for url in candidates:
            if url in seen:
                continue
            seen.add(url)
            raw, src = page._run(_capture_pdf(page, url))
            if raw and raw[:5] == b'%PDF-':
                pdf_path.write_bytes(raw)
                _note_download(doi)
                return _result(doi, pdf_path, (src or url).split('/')[2], text, oa=oa,
                               access=info.get('access'))
        # no PDF: keep the HTML full text if the page has it
        body = info.get('body_text') or ''
        if len(body) > 4000 and info.get('proxied'):
            html_path = OUT / (_slug(doi) + '.txt')
            html_path.write_text(body)
            _note_download(doi)
            return {'doi': doi, 'format': 'html-text', 'path': str(html_path),
                    'chars': len(body), 'via': info.get('host'), 'open_access': oa.get('is_oa'),
                    'text': body if text else ''}
    if oa.get('oa_pdf'):
        reason = 'no subscribed copy found, but an open-access PDF exists: %s' % oa['oa_pdf']
    elif not info.get('proxied'):
        host = (landing or '').split('/')[2] if landing.count('/') >= 2 else info.get('host')
        reason = ('%s is not rewritten by the school EZproxy, so Sophiahemmet has no '
                  'subscription route to it; order it via the library (fjärrlån)' % host)
    else:
        reason = 'the publisher page was reached through EZproxy but offered no PDF (%s)' % (
            info.get('access') or 'no access marker')
    raise DatabaseError(reason)


def _result(doi, path, via, text, oa=None, access=''):
    pages = 0
    try:
        out = subprocess.run(['pdfinfo', str(path)], capture_output=True, text=True, timeout=20).stdout
        m = re.search(r'Pages:\s+(\d+)', out)
        pages = int(m.group(1)) if m else 0
    except Exception:
        pass
    body = _pdf_text(path) if text else ''
    return {'doi': doi, 'format': 'pdf', 'path': str(path), 'bytes': path.stat().st_size,
            'pages': pages, 'via': via, 'access': access,
            'open_access': (oa or {}).get('is_oa'), 'chars': len(body), 'text': body}
