"""Karl's completed Ladok results, all institutions, read-only, through the signed-in agent Chrome.

Course occasions come from studiedeltagande; the grade for each comes from the attested-results link
the occasion carries (resultat/studentresultat/attesterade?kursUID=...). Writes a plain list plus JSON
to ~/courses/out/.
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
P = '/student/proxy/38'
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
    """Text values ordered Swedish-first, then the rest; '-' dropped."""
    sv, other = [], []
    for ben in node.iter(L + 'Benamning'):
        lang = ''
        txt = ''
        for e in ben.iter():
            t = e.tag.split('}')[-1]
            if t == 'Sprakkod':
                lang = (e.text or '').strip()
            elif t == 'Text':
                txt = (e.text or '').strip()
        if txt and txt != '-':
            (sv if lang == 'sv' else other).append(txt)
    return sv + other


def one(node, tag, default=''):
    for e in node.iter():
        if e.tag.split('}')[-1] == tag and (e.text or '').strip():
            return e.text.strip()
    return default


def course_rows(xml):
    root = ET.fromstring(xml)
    rows = []
    for td in root.iter('{http://schemas.ladok.se/studiedeltagande}Tillfallesdeltagande'):
        names = sv_names(td)
        res_url = ''
        inst_url = ''
        for e in td.iter():
            if e.tag.endswith('link') and 'studentresultat/attesterade' in (e.get('uri') or ''):
                res_url = P + (e.get('uri') or '').replace(BASE, '')
            if e.tag.endswith('link') and '/resultat/kursinstans' in (e.get('rel') or ''):
                inst_url = P + (e.get('uri') or '').replace(BASE, '')
        rows.append({
            'code': one(td, 'Utbildningskod') or one(td, 'Kod'),
            'name': names[0] if names else '',
            'hp': max([float(e.text) for e in td.iter()
                       if e.tag.split('}')[-1] == 'Omfattningsvarde' and (e.text or '').strip()
                       and e.text.replace('.', '', 1).isdigit()] or [0]),
            'larosate_id': one(td, 'LarosateID'),
            'start': one(td, 'Startdatum'),
            'end': one(td, 'Slutdatum'),
            'godkand_datum': one(td, 'Godkannandedatum'),
            'avklarad': one(td, 'Avklarad'),
            'tillstand': one(td, 'Sammanfattattillstand').split('.')[-1],
            'result_url': res_url,
            'inst_url': inst_url,
        })
    return [r for r in rows if r['code'] or r['name']]


def parse_grades(xml):
    """Module and course-level results for one course: Studentresultat nodes with Betygsgradskod,
    Examinationsdatum, Omfattningsvarde and the module name (Swedish text)."""
    root = ET.fromstring(xml)
    out = []
    for sr in root.iter('{http://schemas.ladok.se/resultat}Studentresultat'):
        names = [(e.text or '').strip() for e in sr.iter(L + 'Text') if (e.text or '').strip()]
        names = [n for n in names if n != '-']
        out.append({'grade': one(sr, 'Betygsgradskod') or one(sr, 'Betygsgrad'),
                    'date': one(sr, 'Examinationsdatum') or one(sr, 'Beslutdatum'),
                    'hp': one(sr, 'Omfattningsvarde'),
                    'module_sv': names[0] if names else '',
                    'module_en': names[1] if len(names) > 1 else ''})
    return out


async def main():
    key = f'{P}/studiedeltagande/internal/tillfallesdeltagande/kurstillfallesdeltagande/student/{UID}'
    res = await grab([key])
    st, body = res[key]
    (RAW / 'ladok_del.xml').write_text(body)
    rows = course_rows(body)
    print('course occasions:', len(rows), 'status', st)

    ids = sorted({r['larosate_id'] for r in rows if r['larosate_id']})
    inst = await grab([f'{P}/kataloginformation/internal/grunddata/larosatesinformation/{i}' for i in ids])
    names = {}
    for i in ids:
        stx, bx = inst[f'{P}/kataloginformation/internal/grunddata/larosatesinformation/{i}']
        nm = ''
        if stx == '200' and bx.strip().startswith('<'):
            root = ET.fromstring(bx)
            cands = [e.text.strip() for e in root.iter() if e.tag.split('}')[-1] == 'Text' and (e.text or '').strip()]
            nm = next((c for c in cands if len(c) > 3), cands[0] if cands else '')
        names[i] = nm or f'institution {i}'
        (RAW / f'ladok_larosate_{i}.xml').write_text(bx)
    print('institutions:', names)

    inst_urls = sorted({r['inst_url'] for r in rows if r['inst_url']})
    inst_data = await grab(inst_urls) if inst_urls else {}
    for r in rows:
        stx, bx = inst_data.get(r['inst_url'], ('', ''))
        if stx == '200' and bx.strip().startswith('<'):
            root = ET.fromstring(bx)
            r['hp_nominal'] = one(root, 'Omfattningsvarde')
            nm = sv_names(root)
            if nm:
                r['name'] = nm[0]

    urls = sorted({r['result_url'] for r in rows if r['result_url']})
    got = await grab(urls) if urls else {}
    for i, r in enumerate(rows):
        stx, bx = got.get(r['result_url'], ('', ''))
        (RAW / f'ladok_res_{i:02d}.xml').write_text(bx)
        r['institution'] = names.get(r['larosate_id'], '')
        r['result_status'] = stx
        r['results'] = parse_grades(bx) if stx == '200' and bx.strip().startswith('<') else []
        nm = (r['name'] or '').lower()
        graded = [x for x in r['results'] if x.get('grade')]
        course_grade = next((x for x in graded if nm and nm in (x['module_sv'] or '').lower()), None)
        if course_grade is None and graded:
            course_grade = max(graded, key=lambda x: x.get('date') or '')
        r['grade'] = (course_grade or {}).get('grade', '')
        r['grade_date'] = (course_grade or {}).get('date', '')
        if course_grade and course_grade.get('hp'):
            r['grade_hp'] = course_grade['hp']

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'ladok_resultat.json').write_text(json.dumps(
        {'fetched': dt.datetime.now().isoformat(timespec='minutes'), 'student': UID,
         'institutions': names, 'courses': rows}, ensure_ascii=False, indent=1))

    tg = ''
    tgp = f'{P}/resultat/internal/tillgodoraknande/aktiva/student/{UID}'
    tg_st, tg_xml = (await grab([tgp]))[tgp]
    if tg_st == '200' and tg_xml.strip().startswith('<'):
        (RAW / 'ladok_tillgodoraknande.xml').write_text(tg_xml)
        groot = ET.fromstring(tg_xml)
        tgs = []
        for node in groot.iter('{http://schemas.ladok.se/resultat}Tillgodoraknande'):
            def child(parent_tag, tag):
                for parent in node:
                    if parent.tag.split('}')[-1] != parent_tag:
                        continue
                    for c in parent:
                        if c.tag.split('}')[-1] == tag and (c.text or '').strip():
                            return c.text.strip()
                return ''
            # Mal = the course being credited (SHH side); Grunder = the prior study it rests on (KI side)
            tgs.append({
                'typ': one(node, 'TypAvTillgodoraknande'),
                'decided': child('Attesteringsbeslut', 'Beslutsdatum'),
                'target_code': child('Mal', 'Utbildningskod'),
                'target_name': (sv_names(next((m for m in node if m.tag.split('}')[-1] == 'Mal'), node)) or [''])[0],
                'target_hp': child('Mal', 'TillgodoOmfattning') or child('Mal', 'Omfattning'),
                'grade': child('Grunder', 'Betygskod'),
                'source_code': child('Grunder', 'Utbildningskod'),
                'source_name': (sv_names(next((g for g in node if g.tag.split('}')[-1] == 'Grunder'), node)) or [''])[0],
                'source_hp': child('Grunder', 'Omfattning'),
                'source_exam': child('Grunder', 'Examinationsdatum'),
                'source_larosate_id': (lambda n: next((c.text.strip() for c in n.iter()
                                        if c.tag.split('}')[-1] == 'ID' and (c.text or '').strip()), '')
                                       )(next((g for g in node if g.tag.split('}')[-1] == 'Grunder'), node)),
                'source_examiner': child('Grunder', 'Examinator'),
            })
        if tgs:
            for t in tgs:
                iid = t.get('source_larosate_id') or ''
                if iid and iid not in names:
                    stx, bx = (await grab([f'{P}/kataloginformation/internal/grunddata/larosatesinformation/{iid}']))[
                        f'{P}/kataloginformation/internal/grunddata/larosatesinformation/{iid}']
                    if stx == '200' and bx.strip().startswith('<'):
                        nm = sv_names(ET.fromstring(bx)) or [f'institution {iid}']
                        names[iid] = nm[0]
                t['source_institution'] = names.get(iid) or ('Karolinska Institutet'
                                                            if 'ki.se' in (t.get('source_examiner') or '') else '')
            tg = '\n'.join(
                ['## Tillgodoräknanden (beslut i Ladok)', '',
                 '| SHH-kurs | hp | Betyg | Beslut | Grund: kurs | hp | Examinationsdatum | Examinator |',
                 '|---|---|---|---|---|---|---|---|'] +
                [f"| {t['target_code']} {t['target_name']} | {t['target_hp']} | {t['grade'] or '-'} | "
                 f"{t['decided'] or '-'} | {t['source_institution']} {t['source_code']} {t['source_name']} | "
                 f"{t['source_hp']} | {t['source_exam'] or '-'} | {t['source_examiner'] or '-'} |" for t in tgs])
            for t in tgs:
                print('  TILLGODORAKNAT', t)

    lines = ['# Ladok: avklarade kurser', f"Hämtad {dt.datetime.now():%Y-%m-%d %H:%M}", '']
    for iid, nm in sorted(names.items(), key=lambda kv: kv[1]):
        sel = [r for r in rows if r['larosate_id'] == iid]
        tot = sum(float(r['hp'] or 0) for r in sel if r.get('grade'))
        lines += [f'## {nm}: {len([r for r in sel if r.get("grade")])} avklarade kurser, {tot:g} hp', '']
        lines += ['| Kurskod | Kurs | hp | Betyg | Datum |', '|---|---|---|---|---|']
        for r in sorted(sel, key=lambda x: (x.get('godkand_datum') or x.get('start') or '')):
            lines.append(f"| {r['code']} | {r['name']} | {r['hp']:g} | {r['grade'] or '-'} | "
                         f"{r['grade_date'] or r['godkand_datum'] or r['end'] or '-'} |")
        lines.append('')
    if tg:
        lines += ['', tg]
    ung = [r for r in rows if not r.get('grade')]
    lines += ['', '## Gaps', '',
              'Only Sophiahemmet is visible from this session. A Ladok student account exposes its own',
              'institution\'s record plus attesterade tillgodoräknanden; KI\'s own course results sit behind',
              'KI\'s Ladok access (alumni/eduID login) or a transcript from KI.',
              '',
              'No grade yet (in progress or future): ' + (', '.join(f"{r['code']} {r['name']}" for r in ung) or 'none')]
    (OUT / 'ladok_resultat.md').write_text('\n'.join(lines) + '\n')
    print('wrote', OUT / 'ladok_resultat.md')
    for r in rows:
        print(f"  {r['institution'][:24]:24} {r['code']:10} {r['name'][:42]:42} {r['hp']:>5} "
              f"{r['grade'] or '-':>3} {r['grade_date'] or r['godkand_datum'] or r['end']}")


asyncio.run(main())
