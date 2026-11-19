"""Save Viva Engage posts (student admin, student union, library) to ~/courses/sharepoint/engage/.

Route, and why:
- The Engage REST/JSON API answers 406 without a token the SPA mints for itself, so it is not used.
- The feed DOM is a poor index: posts are mostly not <a href=threads> links (2-4 exist on a feed
  page) and the list is virtualized, so counting links stalls at 4 however far you scroll.
- The SPA's own GraphQL responses (FeedGroupNestedClients and friends) carry every thread as
  data.group.feed.threads.edges[].node with databaseId, createdAt and threadStarter.sender. So this
  scrolls the feed's real scroller (the .os-viewport that actually moves; a wheel event at fixed
  coordinates does nothing headless) and reads thread ids from those responses over CDP
  (Network.getResponseBody).
- Text: each thread is then opened as a page and its rendered text saved; the feed query does not
  select message bodies.

Run: python3 -u scripts/fetch_engage.py [--limit N] [--rounds N] [--since YYYY-MM-DD] [--refresh]
--limit caps how many threads are opened (0 = list only, index still rebuilt from what is on disk).
Threads already saved are not reopened unless --refresh.
"""
import asyncio
import base64
import json
import os
import sys
import urllib.request

import websockets

CDP = 'http://127.0.0.1:9233'
OUT = os.path.expanduser('~/courses/sharepoint/engage')
GROUPS = {'81604190208': 'Sophiahemmet_Hogskola', '95726215168': 'SHH_Studentkar'}
SEED: dict = {'4120546222833664': ('Biblioteket', None, None)}      # Karl's library-admin post


def arg(name, default, cast=None):
    if name not in sys.argv:
        return default
    raw = sys.argv[sys.argv.index(name) + 1]
    return cast(raw) if cast else raw


LIMIT = int(arg('--limit', 400))
ROUNDS = int(arg('--rounds', 250))
SINCE = str(arg('--since', ''))
REFRESH = '--refresh' in sys.argv


def tok(obj):
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(',', ':')).encode()).decode().rstrip('=')


def threads_in(body):
    """Every Thread node in a GraphQL response: {databaseId: (createdAt, sender)}."""
    out = {}
    try:
        data = json.loads(body)
    except ValueError:
        return out

    def walk(o):
        if isinstance(o, dict):
            # feed edges carry no __typename on the thread node, so match on shape
            if o.get('databaseId') and isinstance(o.get('threadStarter'), dict):
                ts = o.get('threadStarter') or {}
                out[str(o['databaseId'])] = (o.get('createdAt') or ts.get('createdAt'),
                                             (ts.get('sender') or {}).get('displayName'))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


def head_of(path):
    with open(path, encoding='utf-8') as f:
        return f.readline().lstrip('# ').strip()


SCROLL = """(()=>{const vs=[...document.querySelectorAll('.os-viewport')]
  .filter(v=>v.scrollHeight>v.clientHeight+50);
  vs.forEach(v=>{v.scrollTop+=Math.floor(v.clientHeight*0.9)});
  return JSON.stringify(vs.map(v=>[v.scrollTop,v.scrollHeight]))})()"""


async def main():
    os.makedirs(OUT, exist_ok=True)
    tab = json.load(urllib.request.urlopen(
        urllib.request.Request(CDP + '/json/new?about:blank', method='PUT'), timeout=10))
    try:
        async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=None) as ws:
            n = 0
            gql = []          # requestIds of GraphQL responses not read yet

            async def call(method, params=None):
                nonlocal n
                n += 1
                me = n
                await ws.send(json.dumps({'id': me, 'method': method, 'params': params or {}}))
                while True:
                    r = json.loads(await ws.recv())
                    if r.get('id') == me:
                        return r.get('result', r)
                    if r.get('method') == 'Network.loadingFinished' and r['params']['requestId'] in pending:
                        gql.append(pending.pop(r['params']['requestId']))
                    elif r.get('method') == 'Network.responseReceived':
                        if '/graphql' in r['params']['response']['url']:
                            pending[r['params']['requestId']] = r['params']['requestId']

            pending = {}

            async def ev(expr):
                r = await call('Runtime.evaluate', {'expression': expr, 'returnByValue': True,
                                                    'awaitPromise': True})
                return (r.get('result') or {}).get('value')

            async def drain(found, label):
                while gql:
                    rid = gql.pop(0)
                    b = await call('Network.getResponseBody', {'requestId': rid})
                    for tid, (created, sender) in threads_in(b.get('body') or '').items():
                        found.setdefault(tid, (label, created, sender))

            await call('Page.enable')
            await call('Network.enable', {'maxResourceBufferSize': 50_000_000,
                                          'maxTotalBufferSize': 300_000_000})
            await call('Emulation.setDeviceMetricsOverride', {'width': 1400, 'height': 2000,
                                                              'deviceScaleFactor': 1, 'mobile': False})
            found = dict(SEED)
            for gid, label in GROUPS.items():
                gql.clear()
                pending.clear()
                url = 'https://engage.cloud.microsoft/main/groups/%s/all' % tok({'_type': 'Group', 'id': gid})
                await call('Page.navigate', {'url': url})
                await asyncio.sleep(15)
                await ev('1')
                await drain(found, label)
                stale, last_pos, rnd = 0, None, 0
                for rnd in range(ROUNDS):
                    before = len(found)
                    pos = await ev(SCROLL)
                    await asyncio.sleep(2.5)
                    await ev('1')               # pump queued network events
                    await drain(found, label)
                    moved = pos != last_pos
                    last_pos = pos
                    stale = 0 if (len(found) > before or moved) else stale + 1
                    if stale >= 5:
                        break
                mine = sum(1 for v in found.values() if v[0] == label)
                print('%s: %d threads after %d rounds' % (label, mine, rnd + 1), flush=True)

            # posts saved by earlier runs stay in the index even if this run's feed missed them
            for fn in os.listdir(OUT):
                if fn.endswith('.md') and not fn.startswith('_') and '_' in fn:
                    label, tid = fn[:-3].rsplit('_', 1)
                    if tid.isdigit() and tid not in found:
                        created = sender = None
                        with open(os.path.join(OUT, fn), encoding='utf-8') as f:
                            for line in f.read(2000).splitlines():
                                if line.startswith('Postad: ') and line[8:] != '?':
                                    created = line[8:]
                                elif line.startswith('Av: ') and line[4:] != '?':
                                    sender = line[4:]
                        found[tid] = (label, created, sender)
            rows = sorted(found.items(), key=lambda kv: kv[1][1] or '', reverse=True)
            if SINCE:
                rows = [r for r in rows if (r[1][1] or '9999') >= SINCE]
            index = ['# Viva Engage-poster (%d)' % len(rows), '',
                     'Källa: engage.cloud.microsoft (SHH-communities). Nyast först.', '']
            saved = had = opened = 0
            for tid, (label, created, sender) in rows:
                fn = '%s_%s.md' % (label, tid)
                path = os.path.join(OUT, fn)
                day = (created or '')[:10] or '?'
                if os.path.exists(path) and not REFRESH:
                    index.append('- %s [%s](%s) %s, %s' % (day, head_of(path)[:90], fn, label, sender or ''))
                    had += 1
                    continue
                if opened >= LIMIT:
                    index.append('- %s (inte sparad) tråd %s %s, %s' % (day, tid, label, sender or ''))
                    continue
                opened += 1
                url = 'https://engage.cloud.microsoft/main/threads/%s' % tok({'_type': 'Thread', 'id': tid})
                await call('Page.navigate', {'url': url})
                await asyncio.sleep(10)
                txt = await ev("""(()=>{const m=document.querySelector('[role=main]')||document.body;
                    return (m.innerText||'').replace(/\\n{3,}/g,'\\n\\n').slice(0,12000)})()""")
                gql.clear()
                pending.clear()
                if not txt or len(txt) < 150:
                    print('  skip', tid, len(txt or ''), flush=True)
                    index.append('- %s (tom sida) tråd %s %s' % (day, tid, label))
                    continue
                lines = [l.strip() for l in txt.split('\n') if l.strip()]
                head = next((l for l in lines if len(l) > 30 and 'Viva Engage' not in l), 'post %s' % tid)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('# %s\n\nCommunity: %s\nPostad: %s\nAv: %s\nURL: %s\n\n%s\n'
                            % (head[:160], label, created or '?', sender or '?', url, txt))
                index.append('- %s [%s](%s) %s, %s' % (day, head[:90], fn, label, sender or ''))
                saved += 1
                print('  saved', fn, day, len(txt), flush=True)

            with open(os.path.join(OUT, '_index.md'), 'w', encoding='utf-8') as f:
                f.write('\n'.join(index) + '\n')
            print('threads found %d | newly saved %d | already on disk %d'
                  % (len(rows), saved, had), flush=True)
    finally:
        urllib.request.urlopen(urllib.request.Request(CDP + '/json/close/' + tab['id'], method='PUT'),
                               timeout=10)


if __name__ == '__main__':
    asyncio.run(main())
