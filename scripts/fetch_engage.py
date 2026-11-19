"""Save Viva Engage posts (student admin, student union, library) to ~/courses/sharepoint/engage/.

Route: the Engage JSON API answers 406 without an SPA-issued token, so each post is opened as a page
and its rendered text is saved. The feed list is virtualized, so collection scrolls with real wheel
events and harvests thread links between rounds.

Run: python3 -u scripts/fetch_engage.py [--limit N] [--rounds N]
"""
import asyncio, base64, json, os, re, sys, urllib.request, websockets

CDP = 'http://127.0.0.1:9233'
OUT = '/home/karl/courses/sharepoint/engage'
GROUPS = {'81604190208': 'Sophiahemmet_Hogskola', '95726215168': 'SHH_Studentkar'}
SEED = {'4120546222833664': 'Biblioteket'}      # Karl's library-admin post
LIMIT = 30
ROUNDS = 18
if '--limit' in sys.argv:
    LIMIT = int(sys.argv[sys.argv.index('--limit') + 1])
if '--rounds' in sys.argv:
    ROUNDS = int(sys.argv[sys.argv.index('--rounds') + 1])


def tok(obj):
    raw = json.dumps(obj, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def thread_id_from(href):
    m = re.search(r'/threads/(eyJ[\w-]+)', href)
    if not m:
        return None
    part = m.group(1) + '=' * (-len(m.group(1)) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(part))['id']
    except Exception:
        return None


async def main():
    os.makedirs(OUT, exist_ok=True)
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
            res = r.get('result') or {}
            if 'value' in res:
                return res['value']
            return None

        await call('Page.enable')

        found = dict(SEED)
        for gid, label in GROUPS.items():
            url = 'https://engage.cloud.microsoft/main/groups/%s/all' % tok({'_type': 'Group', 'id': gid})
            await call('Page.navigate', {'url': url})
            await asyncio.sleep(15)
            before = 0
            for rnd in range(ROUNDS):
                hrefs = await ev("JSON.stringify([...document.querySelectorAll('a[href*=threads]')].map(a=>a.getAttribute('href')))")
                ids = [i for i in (thread_id_from(h) for h in json.loads(hrefs or '[]')) if i]
                for i in ids:
                    found.setdefault(i, label)
                if len(found) == before and rnd > 5:
                    print(label, 'no new posts after round', rnd, flush=True)
                    break
                before = len(found)
                for _ in range(3):
                    await call('Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': 900, 'y': 700,
                                                           'deltaX': 0, 'deltaY': 1200})
                    await asyncio.sleep(1.2)
            print(label, 'collected so far', len(found), flush=True)

        index = ['# Viva Engage-poster (%d)' % len(found), '',
                 'Kalla: engage.cloud.microsoft. Sparat 2026-11-18.', '']
        done = 0
        for tid, label in list(found.items())[:LIMIT]:
            url = 'https://engage.cloud.microsoft/main/threads/%s' % tok({'_type': 'Thread', 'id': tid})
            await call('Page.navigate', {'url': url})
            await asyncio.sleep(12)
            txt = await ev("""(()=>{const m=document.querySelector('[role=main]')||document.body;
                return (m.innerText||'').replace(/\\n{3,}/g,'\\n\\n').slice(0,8000)})()""")
            if not txt or len(txt) < 200:
                print('  skip', tid, len(txt or ''), flush=True)
                continue
            lines = [l.strip() for l in txt.split('\n') if l.strip()]
            head = next((l for l in lines if len(l) > 30 and 'Viva Engage' not in l), 'post %s' % tid)
            fn = '%s_%s.md' % (label, tid)
            with open(os.path.join(OUT, fn), 'w') as f:
                f.write('# %s\n\nCommunity: %s\nURL: %s\n\n%s\n' % (head[:160], label, url, txt))
            index.append('- [%s](%s) %s' % (head[:100], fn, label))
            done += 1
            print('  saved', fn, len(txt), flush=True)

        with open(os.path.join(OUT, '_index.md'), 'w') as f:
            f.write('\n'.join(index) + '\n')
        print('total saved', done, flush=True)

    urllib.request.urlopen(urllib.request.Request(CDP + '/json/close/' + tab['id'], method='PUT'), timeout=10)


asyncio.run(main())
