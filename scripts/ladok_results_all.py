"""Karl's completed Ladok results from BOTH institutions, read-only, through the signed-in agent Chrome.

Ladok's studentwebb is lärosäte-scoped: the SPA talks to /student/proxy/<id>/ where 38 = Sophiahemmet
and 28 = Karolinska Institutet, and switching is `/student/app/studentwebb/byt-larosate/<id>`. This
walks every institution whose record the session can open and collects, per institution:

  * course occasions (studiedeltagande) and the grade of each (resultat/studentresultat/attesterade)
  * tillgodoräknanden (credit-transfer decisions)

Writes ~/courses/out/ladok_resultat.md (one table per institution plus totals) and .json.
"""
import asyncio
import datetime as dt
import json
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import websockets

CDP = 'http://127.0.0.1:9233'
UID = '568d49c7-b43f-11e9-8bbd-25378c5a4e4c'
INSTITUTIONS = [('38', 'Sophiahemmet University'), ('28', 'Karolinska Institutet')]
OUT = Path.home() / 'courses' / 'out'
RAW = Path.home() / '.hermes/profiles/school-helper/cache/scratch'
BASE = 'https://api.ladok.se:443'
L = '{http://schemas.ladok.se}'


def tab():
    tabs = json.load(urllib.request.urlopen(CDP + '/json/list', timeout=5))
    return next(t for t in tabs if t['type'] == 'page' and 'ladok' in t['url'])


async def grab(paths):
    t = tab()
    out = {}
    async with websockets.connect(t['webSocketDebuggerUrl'], max_size=200_000_000) as ws:
        i = 0
        for p in paths:
            i += 1
            expr = ("fetch(%s,{credentials:'include'}).then(r=>r.text().then(t=>r.status+'|'+t))" % json.dumps(p))
            await ws.send(json.dumps({'id': i, 'method': 'Runtime.evaluate',
                                      'params': {'expression': expr, 'awaitPromise': True, 'returnByValue': True}}))
            while True:
                m = json.loads(await ws.recv())
                if m.get('id') == i:
                    break
            v = (m.get('result', {}).get('result') or {}).get('value') or ''
            st, _, body = str(v).partition('|')
            out[p] = (st, body)
    return out


def sv_names(node):
    sv, other = [], []
    for ben in node.iter(L + 'Benamning'):
        lang = txt = ''
        for e in ben.iter():
            t = e.tag.split('}')[-1]
            if t == 'Sprakkod':
                lang = (e.text or '').strip()
            elif t == 'Text':
                txt = (e.text or '').strip()
        if txt and txt != '-':
            (sv if lang == 'sv' else other).append(txt)
    return sv + other


def one(node, tag):
    for e in node.iter():
        if e.tag.split('}')[-1] == tag and (e.text or '').strip():
            return e.text.strip()
    return ''


def course_rows(xml, proxy):
    root = ET.fromstring(xml)
    rows = []
    for td in root.iter('{http://schemas.ladok.se/studiedeltagande}Tillfallesdeltagande'):
        res_url = ''
        for e in td.iter():
            if e.tag.endswith('link') and 'studentresultat/attesterade' in (e.get('uri') or ''):
                res_url = proxy + (e.get('uri') or '').replace(BASE, '')
        hp = max([float(e.text) for e in td.iter()
                  if e.tag.split('}')[-1] == 'Omfattningsvarde' and (e.text or '').strip()
                  and e.text.replace('.', '', 1).isdigit()] or [0])
        rows.append({'code': one(td, 'Utbildningskod'), 'name': (sv_names(td) or [''])[0], 'hp': hp,
                     'start': one(td, 'Startdatum'), 'end': one(td, 'Slutdatum'),
                     'godkand_datum': one(td, 'Godkannandedatum'),
                     'result_url': res_url})
    return [r for r in rows if r['code'] or r['name']]


def parse_results(xml):
    root = ET.fromstring(xml)
    out = []
    for sr in root.iter('{http://schemas.ladok.se/resultat}Studentresultat'):
        nm = sv_names(sr)
        out.append({'grade': one(sr, 'Betygsgradskod'), 'date': one(sr, 'Examinationsdatum'),
                    'hp': one(sr, 'Omfattningsvarde'), 'module': nm[0] if nm else ''})
    return out


def parse_tg(xml):
    root = ET.fromstring(xml)
    out = []
    for node in root.iter('{http://schemas.ladok.se/resultat}Tillgodoraknande'):
        def child(parent_tag, tag):
            for parent in node:
                if parent.tag.split('}')[-1] != parent_tag:
                    continue
                for c in parent:
                    if c.tag.split('}')[-1] == tag and (c.text or '').strip():
                        return c.text.strip()
            return ''
        mal = next((m for m in node if m.tag.split('}')[-1] == 'Mal'), node)
        gru = next((g for g in node if g.tag.split('}')[-1] == 'Grunder'), node)
        out.append({'decided': child('Attesteringsbeslut', 'Beslutsdatum'),
                    'target': f"{child('Mal', 'Utbildningskod')} {(sv_names(mal) or [''])[0]}",
                    'target_hp': child('Mal', 'TillgodoOmfattning') or child('Mal', 'Omfattning'),
                    'grade': child('Grunder', 'Betygskod'),
                    'source': f"{child('Grunder', 'Utbildningskod')} {(sv_names(gru) or [''])[0]}",
                    'source_hp': child('Grunder', 'Omfattning'),
                    'source_exam': child('Grunder', 'Examinationsdatum'),
                    'source_examiner': child('Grunder', 'Examinator')})
    return out


async def collect(proxy, label):
    p = f'/student/proxy/{proxy}'
    key = f'{p}/studiedeltagande/internal/tillfallesdeltagande/kurstillfallesdeltagande/student/{UID}'
    st, body = (await grab([key]))[key]
    (RAW / f'ladok_del_{proxy}.xml').write_text(body)
    if st != '200' or not body.strip().startswith('<'):
        return {'proxy': proxy, 'label': label, 'error': f'{st}: not readable from this session', 'courses': []}
    rows = course_rows(body, p)
    urls = sorted({r['result_url'] for r in rows if r['result_url']})
    got = await grab(urls) if urls else {}
    for r in rows:
        stx, bx = got.get(r['result_url'], ('', ''))
        r['results'] = parse_results(bx) if stx == '200' and bx.strip().startswith('<') else []
        graded = [x for x in r['results'] if x['grade']]
        nm = (r['name'] or '').lower()
        cg = next((x for x in graded if nm and nm in (x['module'] or '').lower()), None)
        if cg is None and graded:
            cg = max(graded, key=lambda x: x['date'] or '')
        r['grade'] = (cg or {}).get('grade', '')
        r['grade_date'] = (cg or {}).get('date', '')
        r.pop('result_url', None)
    tg_key = f'{p}/resultat/internal/tillgodoraknande/aktiva/student/{UID}'
    tst, tbody = (await grab([tg_key]))[tg_key]
    tg = parse_tg(tbody) if tst == '200' and tbody.strip().startswith('<') else []
    return {'proxy': proxy, 'label': label, 'courses': rows, 'tillgodoraknanden': tg}


async def main():
    data = {'fetched': dt.datetime.now().isoformat(timespec='minutes'), 'student': UID, 'institutions': []}
    for proxy, label in INSTITUTIONS:
        d = await collect(proxy, label)
        data['institutions'].append(d)
        print(f"{label} ({proxy}): {len(d.get('courses', []))} course occasions"
              + (f" ERROR {d['error']}" if d.get('error') else ''))

    lines = ['# Ladok: resultat per lärosäte', f"Hämtad {dt.datetime.now():%Y-%m-%d %H:%M}", '']
    for d in data['institutions']:
        if d.get('error'):
            lines += [f"## {d['label']}", f"Not readable from this session ({d['error']})", '']
            continue
        done = [c for c in d['courses'] if c['grade']]
        tot = sum(c['hp'] for c in done)
        lines += [f"## {d['label']}: {len(done)} avklarade kurser, {tot:g} hp", '',
                  '| Kurskod | Kurs | hp | Betyg | Datum |', '|---|---|---|---|---|']
        for c in sorted(d['courses'], key=lambda x: (x['godkand_datum'] or x['start'] or '')):
            lines.append(f"| {c['code']} | {c['name']} | {c['hp']:g} | {c['grade'] or '-'} | "
                         f"{c['grade_date'] or c['godkand_datum'] or c['end'] or '-'} |")
        ung = [c for c in d['courses'] if not c['grade']]
        if ung:
            lines += ['Registrerade utan betyg (not completed, do not list as passed):',
                      '  ' + ', '.join(f"{c['code']} {c['name']} ({c['hp']:g} hp)" for c in ung), '']
        lines.append('')
        if d['tillgodoraknanden']:
            lines += ['Tillgodoräknanden:', '',
                      '| Kurs (tillgodoräknad) | hp | Betyg | Beslut | Grund | hp | Examinationsdatum |',
                      '|---|---|---|---|---|---|---|']
            for t in d['tillgodoraknanden']:
                lines.append(f"| {t['target']} | {t['target_hp']} | {t['grade'] or '-'} | {t['decided'] or '-'} | "
                             f"{t['source']} | {t['source_hp']} | {t['source_exam'] or '-'} |")
            lines.append('')

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'ladok_resultat.md').write_text('\n'.join(lines) + '\n')
    (OUT / 'ladok_resultat.json').write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print('wrote', OUT / 'ladok_resultat.md')
    for d in data['institutions']:
        for c in d.get('courses', []):
            print(f"  {d['label'][:24]:24} {c['code']:10} {c['name'][:40]:40} {c['hp']:>5g} "
                  f"{c['grade'] or '-':>3} {c['grade_date'] or c['godkand_datum'] or c['end']}")


asyncio.run(main())
