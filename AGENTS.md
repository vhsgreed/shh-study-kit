# AGENTS.md

You are an agent working in or with `ladok-vakt`. Read this before touching
anything; it is short on purpose.

## What this is

A read-only watcher for **Ladok** (Sweden's national study documentation system).
It reports course registration and exam registration windows to a human over a
channel they chose. It never registers, unregisters or edits anything.

## Rules

1. **Read only toward Ladok.** Only GETs on `student.ladok.se/student/proxy/...`.
   An anmälan is the human's action, always.
2. **Never log in for the human.** There is no login automation here and none
   should be added: the session comes from a browser the human authenticated by
   hand. Do not add a credential store, a password prompt or an autofill driver.
   If a human automates their own login outside this repo, that is their
   credential handling and their responsibility, not this project's.
3. **Never commit** session cookies, `~/.secrets` contents, student ids or course
   data. `LADOK_*` env vars are configuration, not source.
4. **Silence means success.** Cron mode prints nothing on a good day. Do not "fix"
   that by adding progress output; the value is the absence of noise.
5. **A fault may never be silent.** If you change the fetch or parse path, keep the
   fault branches (`Fault`, empty course list, stale state) printing to stdout and
   emailed. That is the whole safety story.

## Layout

```
ladok_vakt/core.py     fetch (Chrome/DevTools), parse (Ladok XML), decide, notify
ladok_vakt/__main__.py CLI: cron mode, --all, --json, --test-mail
tests/test_core.py     parsing + decision rules, no network
examples/              systemd user units, crontab line
skill/SKILL.md         the same knowledge, shaped for agent skill loaders
```

## Before you change behaviour

```bash
python3 -m pytest tests -q            # must stay green
python3 -m ladok_vakt --all           # must still list real windows (needs a session)
python3 -m ladok_vakt                 # must stay silent when nothing is urgent
```

Verify a new endpoint by calling it in the browser tab first and comparing with
what studentwebb renders. The XML field names differ per module; do not guess them.

## Adding a channel

`notify()` in `core.py` fans out to mail, telegram, webhook and a command hook.
Add a channel as one function that returns True/False and appends nothing else, and
register it in `notify()`. Keep the rule that a channel failure is logged, never
raised: a notification problem must not break the watch.

## Publishing

Small, readable commits. Bump `__version__` in `ladok_vakt/__init__.py` and note the
change at the top of the README if behaviour changes.
