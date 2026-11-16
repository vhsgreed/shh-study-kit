"""CLI: `python3 -m ladok_vakt` (silent unless something is worth saying).

    python3 -m ladok_vakt              # cron mode: prints only alerts/faults/heartbeat
    python3 -m ladok_vakt --all        # every window, always
    python3 -m ladok_vakt --json       # machine-readable
    python3 -m ladok_vakt --test-mail  # prove the notification path works

Exit codes: 0 ok (including "nothing to report"), 2 configuration problem.
"""
import json
import sys

from . import core


def main(argv):
    cfg = core.Config()
    want_alert = not ('--all' in argv or '--json' in argv)
    email = '--no-email' not in argv

    if '--test-mail' in argv:
        subject = 'ladok-vakt: test av notifieringen'
        body = ('Det här är ett test. Inget har hänt i Ladok.\n\n'
                'Kanaler som svarade: %s\n')
        done = core.notify(cfg, subject, body % 'test')
        print('kanaler: %s' % (', '.join(done) if done else 'inga'))
        return 0 if done else 2

    lines = core.run(cfg, alert=want_alert, email=email)

    if '--json' in argv:
        print(json.dumps({'lines': lines, 'state': str(cfg.state_file)}, ensure_ascii=False, indent=1))
        return 0

    if lines:
        print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
