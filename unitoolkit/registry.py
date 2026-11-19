"""The registry: which university systems an agent can read, per institution.

A machine-first answer to "can an agent read this system at this school, and what breaks?".
Data lives in registry/registry.json (shipped with the package and published raw on GitHub), so
anything - another agent, a search engine, a person with curl - reads the same file. Statuses are
written by scripts/registry_check.py, never by hand.

Query it with the `registry` tool, or directly::

    from unitoolkit import registry
    registry.query(system='ladok')
    registry.query(institution='shh', status='ok')
"""
import json
import os
from pathlib import Path

STATUSES = ('ok', 'partial', 'fail', 'untested')


def path():
    """Packaged copy first, then the repo checkout (editable installs, scripts)."""
    env = os.environ.get('UNITOOLKIT_REGISTRY')
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve()
    for cand in (here.parent / 'registry.json',                    # if copied into the package
                 here.parents[1] / 'registry' / 'registry.json'):  # repo layout
        if cand.exists():
            return cand
    return here.parents[1] / 'registry' / 'registry.json'


def load():
    """The whole file, dict as on disk. Empty skeleton if it is missing rather than raising."""
    p = path()
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {'schema': 'unitoolkit-registry/1', 'systems': {}, 'institutions': [],
                'about': 'registry file missing at %s' % p}


def deployments(data=None):
    """Flatten institutions -> deployments, with the institution fields merged in."""
    data = data or load()
    insts = {i['id']: i for i in data.get('institutions') or []}
    rows = []
    for i in data.get('institutions') or []:
        for d in i.get('deployments') or []:
            row = dict(d)
            row['institution'] = i.get('id')
            row['institution_name'] = i.get('name')
            row['country'] = i.get('country')
            row['maintainer'] = i.get('maintainer')
            row['system_info'] = (data.get('systems') or {}).get(d.get('system'), {})
            rows.append(row)
    for r in rows:
        r.setdefault('institution_name', insts.get(r.get('institution'), {}).get('name'))
    return rows


def _haystack(row):
    bits = [row.get('id'), row.get('system'), row.get('url'), row.get('institution'),
            row.get('institution_name')]
    bits += row.get('gotchas') or []
    bits += row.get('databases') or []
    info = row.get('system_info') or {}
    bits += [info.get('kind'), info.get('vendor'), info.get('adapter')]
    return ' '.join(str(b).lower() for b in bits if b)


def query(system=None, institution=None, status=None, text=None, include_elsewhere=False):
    """Filter deployments. Every argument is optional; no argument returns everything.

    text matches the id, system, vendor, adapter, URL, gotchas and database names, so
    "cloudflare" or "ezproxy" find the row that explains the failure.
    """
    data = load()
    rows = deployments(data)
    if system:
        rows = [r for r in rows if (r.get('system') or '').lower() == str(system).lower()]
    if institution:
        want = str(institution).lower()
        rows = [r for r in rows if want in ((r.get('institution') or '').lower(),
                                            (r.get('institution_name') or '').lower())]
    if status:
        rows = [r for r in rows if (r.get('status') or 'untested') == str(status).lower()]
    if text:
        needle = str(text).lower()
        rows = [r for r in rows if needle in _haystack(r)]
    out = []
    for r in rows:
        keep = {k: r.get(k) for k in ('id', 'system', 'institution', 'institution_name', 'country',
                                      'url', 'status', 'last_verified', 'probes', 'databases',
                                      'gotchas', 'maintainer')}
        keep['adapter'] = (r.get('system_info') or {}).get('adapter')
        if include_elsewhere:
            keep['elsewhere'] = (r.get('system_info') or {}).get('elsewhere')
        out.append(keep)
    return out


def summary():
    """One line per institution: how many deployments, how many ok/partial/fail/untested."""
    rows = deployments()
    by_inst = {}
    for r in rows:
        st = r.get('status') or 'untested'
        b = by_inst.setdefault(r.get('institution') or '?', {'name': r.get('institution_name'),
                                                             'counts': {s: 0 for s in STATUSES}})
        b['counts'][st] = b['counts'].get(st, 0) + 1
    data = load()
    return {'last_run': data.get('last_run'), 'institutions': by_inst}
