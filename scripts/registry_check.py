"""Weekly registry check: run each deployment's probes, write the status and date back.

This is the part that keeps the registry honest instead of hopeful. For every deployment in
registry/registry.json it runs its `probes` (names from scripts/smoke.py) and sets

    status = ok | partial | fail        (partial is never written by this script, only by hand)
    last_verified = today

Rows without probes are left untouched: an untestable row is better listed as untested than
reported as working. Exit 0 when nothing failed; one line per failure and exit 1 otherwise, so a
cron wrapper only speaks when something broke. It never sends mail itself.

    python scripts/registry_check.py [--dry-run] [--only shh/ladok,shh/koha]
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

import smoke  # noqa: E402  (the probe table lives there, one place for site-shape checks)


def run_probe(label):
    """Run one named smoke probe. True when it passes, else a short reason."""
    for name, tool, args, ok in smoke.CHECKS:
        if name != label:
            continue
        from unitoolkit.tools import call
        try:
            res = call(tool, args)
        except BaseException as exc:
            return '%s: %s' % (type(exc).__name__, str(exc)[:140])
        try:
            verdict = ok(res)
        except BaseException as exc:
            return 'shape check raised %s: %s' % (type(exc).__name__, str(exc)[:120])
        if verdict is True:
            return True
        if isinstance(verdict, str):
            return verdict
        if isinstance(verdict, list):
            return '; '.join(str(v) for v in verdict)[:160]
        return 'unexpected answer shape'
    return 'no such probe %r' % label


def main(argv):
    from unitoolkit import registry
    reg_path = registry.path()
    data = json.loads(reg_path.read_text())
    only = None
    if '--only' in argv:
        only = {x.strip() for x in argv[argv.index('--only') + 1].split(',') if x.strip()}
    dry = '--dry-run' in argv

    today = date.today().isoformat()
    failures, checked = [], 0
    for inst in data.get('institutions') or []:
        for dep in inst.get('deployments') or []:
            dep_id = dep.get('id')
            labels = dep.get('probes') or []
            if only and dep_id not in only:
                continue
            if not labels:
                continue
            checked += 1
            results = {label: run_probe(label) for label in labels}
            bad = {k: v for k, v in results.items() if v is not True}
            if bad:
                dep['status'] = 'fail'
                failures.append('%s (%s): %s' % (dep_id, dep.get('system'),
                                                 '; '.join('%s -> %s' % (k, v) for k, v in bad.items())))
            else:
                dep['status'] = 'ok'
            dep['last_verified'] = today

    if not dry:
        data['last_run'] = today
        reg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')

    if '--verbose' in argv:
        print('checked %d deployments (%s)' % (checked, 'dry run' if dry else 'written'))
    for f in failures:
        print('- registry FAIL ' + f)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
