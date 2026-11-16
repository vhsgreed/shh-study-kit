---
name: ladok-vakt
description: "Use when a student needs alerts for Ladok course or exam registration windows."
version: 0.1.0
author: vhsgreed
license: MIT
metadata:
  hermes:
    tags: [ladok, sweden, university, registration, notifications, cron, email]
---

# ladok-vakt

Report registration windows from **Ladok** (Sweden) to a human, over a channel
they already read. Read only: it never registers anything.

## When to Use
- "När öppnar anmälan?", "when does course registration open", "I missed the
  registration window", "can I get a reminder for Ladok", "did I register for X".
- A student is between courses and the next registration window is the thing that
  can silently pass.
- Skip when the question is about results or grades inside a running course: that
  is a different Ladok read, not a window watch.

## Run it

```bash
python3 -m ladok_vakt --all          # what the windows are right now
python3 -m ladok_vakt                # cron mode, silent unless urgent or broken
python3 -m ladok_vakt --json         # machine-readable
python3 -m ladok_vakt --test-mail    # prove the notification path
```

Needs a Chrome the human logged in to Ladok once, started with
`--remote-debugging-port=9233`. `LADOK_LAROSATE` selects the university.

## Rules
- **Never register, unregister or "help with" the anmälan.** The human clicks it.
- **Never automate the login.** No session: say so, ask for a login, wait.
- **Do not add progress output to cron mode.** Absence of a message is the product.
- **Faults always speak**, in stdout and on the configured channel. If you touch
  the fetch path, keep that property and prove it by faking a stale state file.

## Pitfalls
- An absolute URL with the wrong host (`www.` vs without) is a different origin and
  carries no cookie; use relative paths from the Ladok tab.
- The first `<base:Benamning>` in a course element is the tillfällestyp
  ("Kurs, grundnivå"), not the course name. Take the one with `Sprakkod=sv`.
- `sd:Registreringsperiod` sits inside the tillfälle; a missing period means no
  window to show, not an error.
- An empty exam list is normal (no bookable tentamen), an empty *course* list is a
  fault.
- Resend answers bare python-urllib requests with 403 (Cloudflare). Send a
  User-Agent.
- A cron entry stores a command line, not flags for a wrapper: make silence the
  script's default rather than passing `--alert` from cron.
