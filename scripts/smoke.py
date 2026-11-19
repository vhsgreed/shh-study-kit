#!/usr/bin/env python3
"""Weekly smoke test: does every reader still understand its site?

Sites change their markup without notice (Cochrane's search form and Koha's loans table both
did during development). This runs one real, cheap query per reader and checks the shape of
the answer. Silent and exit 0 when everything passes; one line per failure and exit 1
otherwise, so cron mail or a wrapper only speaks when something broke.

It deliberately does not log in to the library: failed logins count against the student's
account. doctor's library row checks the plumbing without a login.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CHECKS = [
    ('doctor', 'doctor', {'only': 'canvas,browser,ladok,databases'},
     lambda r: r['overall'] != 'fail' or [c['check'] + ': ' + c['detail'] for c in r['checks'] if c['status'] == 'fail']),
    ('canvas upcoming', 'upcoming', {}, lambda r: isinstance(r, list)),
    ('ladok windows', 'ladok_windows', {}, lambda r: 'windows' in r or r.get('error')),
    ('cinahl search', 'db_search', {'database': 'cinahl', 'query': 'delirium', 'limit': 3},
     lambda r: len(r['results']) == 3 and all(x['title'] and x['journal'] for x in r['results'])),
    ('psycinfo search', 'db_search', {'database': 'psycinfo', 'query': 'burnout nurses', 'limit': 2},
     lambda r: len(r['results']) == 2),
    ('cochrane search', 'db_search', {'database': 'cochrane', 'query': 'delirium', 'limit': 3},
     lambda r: len(r['results']) == 3 and all(x['doi'] for x in r['results'])),
    ('wos search', 'db_search', {'database': 'wos', 'query': 'delirium prevention', 'limit': 3},
     lambda r: len(r['results']) == 3 and all(x['title'] and x['authors'] for x in r['results'])),
    ('apa from doi', 'apa', {'doi': '10.1111/inr.13079'},
     lambda r: r['apa'].startswith('Cho, H.') and '72(3)' in r['apa']),
    ('pubmed', 'pubmed', {'query': 'delirium prevention', 'max': 2}, lambda r: len(r) == 2),
    ('timeedit course', 'schedule', {'query': '1SPRY2', 'kind': 'code', 'start': '0.d', 'end': '8.w'},
     lambda r: r['objects'] and isinstance(r['bookings'], list)),
    ('timeedit rooms', 'free_rooms', {}, lambda r: len(r['rooms']) >= 5),
    ('library catalogue', 'koha_search', {'q': 'omvårdnad', 'limit': 2}, lambda r: len(r) >= 1),
    ('sharepoint portal', 'page',
     {'url': 'https://shh365.sharepoint.com/sites/Minasidor-Student'},
     lambda r: 'Studentportalen' in (r.get('title') or '') or 'Studentportalen' in (r.get('text') or '')[:600]),
    ('vardhandboken search', 'ref_search', {'query': 'hjart', 'limit': 2},
     lambda r: len(r) == 2 and all(h.get('url') and h.get('title') for h in r)),
    ('viss page', 'ref_page', {'url': 'https://viss.nu/kunskapsstod'},
     lambda r: 'Viss' in (r.get('title') or '') and len(r.get('text') or '') > 200),
]


def main():
    from unitoolkit.tools import call
    failures = []
    for label, tool, args, ok in CHECKS:
        try:
            res = call(tool, args)
            verdict = ok(res)
        except BaseException as e:
            verdict = '%s: %s' % (type(e).__name__, str(e)[:160])
        if verdict is True:
            continue
        if isinstance(verdict, (list, str)):
            failures.append('%s: %s' % (label, verdict if isinstance(verdict, str) else '; '.join(verdict)))
        else:
            failures.append('%s: unexpected answer shape' % label)
    if '--verbose' in sys.argv:
        print(json.dumps({'checked': len(CHECKS), 'failed': len(failures)}))
    for f in failures:
        print('- unitoolkit smoke FAIL ' + f)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
