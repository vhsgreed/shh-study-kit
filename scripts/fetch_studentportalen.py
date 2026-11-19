"""Save the SHH Studentportalen (and APA-guide) SharePoint pages to ~/courses/sharepoint."""
import asyncio, json, urllib.request, websockets, re, html, os

CDP = 'http://127.0.0.1:9233'
OUT = '/home/karl/courses/sharepoint'
SITES = ('Minasidor-Student', 'APA-guide')

with open('/tmp/sp_dump.json') as f:
    dump = json.load(f)


def strip(h):
    h = re.sub(r'(?is)<(script|style).*?</\1>', ' ', h or '')
    h = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>', '\n', h)
    t = html.unescape(re.sub(r'(?s)<[^>]+>', ' ', h))
    t = re.sub(r'[ \t\xa0]+', ' ', t)
    t = re.sub(r'\n\s*\n+', '\n\n', t)
    return t.strip()


async def main():
    tab = json.load(urllib.request.urlopen(
        urllib.request.Request(CDP + '/json/new?about:blank', method='PUT'), timeout=10))
    async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=None) as ws:
        n = 0

        async def call(method, params=None):
            nonlocal n
            n += 1
            me = n
            await ws.send(json.dumps({'id': me, 'method': method, 'params': params or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get('id') == me:
                    return r.get('result', r)

        async def ev(expr):
            r = await call('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
            return (r.get('result') or {}).get('value')

        await call('Page.enable')
        await call('Page.navigate', {'url': 'https://shh365.sharepoint.com/sites/Minasidor-Student'})
        await asyncio.sleep(11)

        for site in SITES:
            listing = {l['Title']: l['Id'] for l in dump[site]['lists']}
            if 'Site Pages' not in listing:
                print(site, 'no page library')
                continue
            base = 'https://shh365.sharepoint.com/sites/' + site
            gid = listing['Site Pages']
            pages, last, seen = [], 0, set()
            while len(pages) < 60:
                chunk = await ev(
                    "fetch(\"%s/_api/web/lists(guid'%s')/items?$select=Id,Title,CanvasContent1,Modified"
                    "&$orderby=Id&$top=6&$filter=Id gt %d\",{headers:{accept:'application/json;odata=nometadata'}})"
                    ".then(r=>r.json()).then(j=>JSON.stringify(j.value))" % (base, gid, last))
                try:
                    v = json.loads(chunk or '[]')
                except Exception as exc:
                    print('parse fail', site, last, str(exc)[:70], flush=True)
                    break
                new = [x for x in v if (x.get('Title'), x.get('Modified')) not in seen]
                for x in new:
                    seen.add((x.get('Title'), x.get('Modified')))
                pages += new
                if not v:
                    break
                last = max(x.get('Id') or last for x in v)
                if len(v) < 6:
                    break
            print(site, 'pages fetched', len(pages), flush=True)

            sd = os.path.join(OUT, site)
            os.makedirs(sd, exist_ok=True)
            index = ['# %s: Studentportalen-sidor (%d)' % (site, len(pages)), '']
            written = 0
            for p in pages:
                title = (p.get('Title') or '').strip() or 'namnlös'
                body = strip(p.get('CanvasContent1') or '')
                if not body:
                    continue
                fn = re.sub(r'[^A-Za-z0-9ÅÄÖåäö _-]', '', title).strip().replace(' ', '_')[:60] + '.md'
                with open(os.path.join(sd, fn), 'w') as f:
                    f.write('# %s\n\nKälla: %s (sparat %s).\n\n%s\n'
                            % (title, base, (p.get('Modified') or '')[:10], body))
                index.append('- [%s](%s)  %s' % (title, fn, (p.get('Modified') or '')[:10]))
                written += 1
            with open(os.path.join(sd, '_index.md'), 'w') as f:
                f.write('\n'.join(index) + '\n')
            print(site, 'pages written', written)

        # FAQ and calendar from the dump (small enough to be intact)
        sd = os.path.join(OUT, 'Minasidor-Student')
        faq = json.loads(dump['Minasidor-Student']['items']['FAQQuestions'])['value']
        lines = ['# FAQ: Studentportalen (%d frågor)' % len(faq), '']
        for q in faq:
            lines += ['## %s' % (q.get('Title') or ''), '', strip(q.get('Answers') or '') or '(inget svar sparat)', '']
        with open(os.path.join(sd, '_faq.md'), 'w') as f:
            f.write('\n'.join(lines))
        print('faq', len(faq))

        evs = json.loads(dump['Minasidor-Student']['items']['Events'])['value']
        lines = ['# Kalender: Studentportalen (%d poster)' % len(evs), '']
        for e in evs:
            lines.append('- %s | %s | %s' % ((e.get('Title') or '')[:90],
                                             (e.get('EventDate') or '')[:10],
                                             strip(e.get('Description') or '')[:200]))
        with open(os.path.join(sd, '_events.md'), 'w') as f:
            f.write('\n'.join(lines) + '\n')
        print('events', len(evs))

    urllib.request.urlopen(urllib.request.Request(CDP + '/json/close/' + tab['id'], method='PUT'), timeout=10)


asyncio.run(main())
