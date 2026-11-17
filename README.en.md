# ladok-vakt

**Email notifications when your Ladok course registration opens, before the window closes.**
A small watch script for Swedish university students: course registration and exam
registration windows from Ladok, announced once, on the right day, silent otherwise.

`ladok-vakt` is read only. It never registers anything for you.

```
- Grundläggande farmakologi och omvårdnad (1SFOM2): anmälan 2026-11-23 till 2026-11-30 [om 7 d], kursstart 2026-11-30
- Människan i hälsa och sjukdom del 1 (2SHS11): anmälan 2027-01-18 till 2027-01-25 [om 63 d], kursstart 2027-01-25
```

Swedish: [README.md](README.md).

---

## The problem

Ladok has no reminder for course registration. The window is often one week, it
lives only inside studentwebb, and missing it means no place on the course or no
seat at the exam. Calendar reminders work until a date moves or a tillfälle is
published late, which is exactly when they stop working.

`ladok-vakt` reads those windows out of Ladok and runs as often as you like. It is
built to run from cron once a day and to be quiet.

## What it does

- Reads every course tillfälle with its **registreringsperiod** (registration opens, registration closes) and the course period.
- Reads **exam registration** windows where Ladok exposes them.
- Prints one line per window that is open now or closes within N days.
- **Shouts only on faults**: no successful fetch for 36 h, empty course list, dead session, unparseable answer.
- Posts a Monday heartbeat so silence can be told apart from silence.

## What it does not do

- No registration, deregistration or change of anything. GET only.
- No login unless you opt in. You log in once in a Chrome profile and the script uses
  that session; optional re-login exists, off by default, see
  [Optional login](#optional-login-off-by-default).
- No daily digest. Silence is a feature.

## Quick start

```bash
git clone https://github.com/vhsgreed/ladok-vakt && cd ladok-vakt
python3 -m pip install websockets          # the only dependency outside stdlib

# 1. A Chrome with its own profile, where YOU log in to Ladok once
flatpak run com.google.Chrome \
  --user-data-dir=$HOME/.config/ladok-vakt-chrome \
  --remote-debugging-port=9233

# 2. Try a read
python3 -m ladok_vakt --all

# 3. Configure a channel (below) and drop it in cron
```

`python3 -m ladok_vakt` with no arguments is cron mode: it writes only when there
is something worth saying.

## Optional login (off by default)

The Ladok session ends: typically when the browser restarts, or after a while idle.
With `LADOK_AUTOLOGIN=1` the script tries to sign in again before it raises a fault,
using the password Chrome has already saved for your institution's login page.

```bash
export LADOK_IDP_ENTITY='https://swamid2.shh.se/idp/shibboleth'   # your SWAMID entityID
export LADOK_AUTOLOGIN=1
python3 -m ladok_vakt --login      # try it once, in advance
```

What it does: opens the SWAMID login, waits until Chrome has filled the form by
itself, clicks Login with a real mouse event, and checks that Ladok answers before
reporting success.

What it does **not** do, and this is the part worth auditing yourself: it never reads
the value of the password field, stores nothing, writes nothing, sends nothing, and
does not touch the browser's password-manager API. It asks "are the fields filled?"
and clicks. The code is `ladok_vakt/login.py`, and `tests/` contains a test that fails
if anyone adds field-value reads there.

Requirements: the password is saved in the Chrome profile the script uses, and the
login needs nothing further (2FA, one-time code, app approval). If it does, the
re-login fails and you get a fault alert, which is the point: it does not guess.

## Integrate it into your workflow

### Scheduling

**cron** (daily at 07:10):

```cron
10 7 * * *  cd $HOME/src/ladok-vakt && LADOK_MAIL_TO=you@example.com /usr/bin/python3 -m ladok_vakt
```

**systemd user timer**: see [`examples/`](examples/).

**Agents** (Hermes, Claude Code, anything MCP-adjacent): point the agent at
[`skill/SKILL.md`](skill/SKILL.md), or run the CLI from the agent's own cron. The
output is line based and parseable on purpose.

### Channel: email through Resend

Fastest route to mail without an SMTP password. Verify your domain in Resend and:

```bash
export LADOK_MAIL_TO=you@example.com
export LADOK_MAIL_FROM='ladok-vakt <vakt@your-domain.se>'
echo 're_...' > ~/.secrets/RESEND_KEY && chmod 600 ~/.secrets/RESEND_KEY
```

### Channel: email through Gmail and an app password

No third party, but Gmail needs an **app password** (Google Account > Security >
App passwords, requires 2FA):

```bash
export LADOK_MAIL_TO=you@example.com
export LADOK_MAIL_FROM=you@gmail.com
export LADOK_SMTP_USER=you@gmail.com
echo 'abcd efgh ijkl mnop' > ~/.secrets/GMAIL_APP_PASSWORD && chmod 600 ~/.secrets/GMAIL_APP_PASSWORD
export LADOK_SMTP_PASSWORD_FILE=$HOME/.secrets/GMAIL_APP_PASSWORD
```

With `LADOK_SMTP_PASSWORD_FILE` set, SMTP is used instead of Resend.

### Other channels

All of them can be set at once; the notice goes out on every configured channel.

| Channel | Variables | Note |
|---|---|---|
| Telegram | `LADOK_TELEGRAM_TOKEN`, `LADOK_TELEGRAM_CHAT` | simplest push to a phone |
| Slack | `LADOK_WEBHOOK_URL`, `LADOK_WEBHOOK_FORMAT=slack` | incoming webhook, sends `{"text": ...}` |
| Discord | `LADOK_WEBHOOK_URL`, `LADOK_WEBHOOK_FORMAT=discord` | sends `{"content": ...}` |
| ntfy | `LADOK_WEBHOOK_URL=https://ntfy.sh/<topic>`, `LADOK_WEBHOOK_FORMAT=ntfy` | no signup, topic name only |
| Any webhook | `LADOK_WEBHOOK_URL=...`, `LADOK_WEBHOOK_FORMAT=generic` | `{"text": ..., "source": "ladok-vakt"}` |
| Any command | `LADOK_NOTIFY_COMMAND='curl -d @- https://...'` | message on stdin, last resort |

```bash
python3 -m ladok_vakt --test-mail    # proves the channels answer
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `LADOK_CDP` | `http://127.0.0.1:9233` | Chrome with DevTools, your logged-in session |
| `LADOK_LAROSATE` | `38` | your university's id in Ladok (38 = Sophiahemmet) |
| `LADOK_MAIL_TO` | empty | recipient, empty disables mail |
| `LADOK_MAIL_FROM` | `ladok-vakt <vakt@example.org>` | sender, must be verified in Resend |
| `LADOK_CLOSE_DAYS` | `3` | alert when a window closes within this many days |
| `LADOK_STALE_HOURS` | `36` | alert when no successful fetch is older than this |
| `LADOK_MAIL_MIN_HOURS` | `20` | at most one fault mail per channel per day |
| `LADOK_HEARTBEAT` | `1` | Monday liveness line, `0` disables |
| `LADOK_STATE_DIR` | `~/.local/state/ladok-vakt` | state and log |

State in `$LADOK_STATE_DIR/state.json`, history in `ladok-vakt.log`.

## How it works

Ladok has no student API with a token. Studentwebb is a web app that fetches
everything through `https://student.ladok.se/student/proxy/<larosate>/...` with the
session cookie. `ladok-vakt` does the same, from a tab on the same origin, over the
DevTools protocol:

| Data | Endpoint |
|---|---|
| logged-in student | `/studiedeltagande/internal/student/inloggad/som` |
| courses + registration window | `/studiedeltagande/internal/tillfallesdeltagande/kurstillfallesdeltagande/student/<uid>` |
| exam registration | `/resultat/internal/studentweb/aktivitetstillfalle/inloggadstudent/relevanta` |

Answers are XML in Ladok's schema; the fields used are `sd:Registreringsperiod`
(`base:Startdatum` / `base:Slutdatum`), `sd:Tillstand` and `sd:UtbildningsPeriodFran/Till`.

## Where Ladok is used

Ladok is used by Swedish universities and colleges, with one id per institution:
set `LADOK_LAROSATE` and the same script works at your school. Norway, Denmark
and Finland use other systems, so this tool is Sweden specific.

## Troubleshooting

- **"ingen Ladok-session"**: the session expired, or Chrome is not running with
  `--remote-debugging-port=9233`. Check `http://127.0.0.1:9233/json/version`, log in
  to Ladok in that tab, run again.
- **Empty answer though you know you have courses**: check `LADOK_LAROSATE`. A wrong
  university id gives a valid, empty answer.
- **No mail but the line prints**: `LADOK_MAIL_TO` is empty, or the Resend key is not
  where `LADOK_RESEND_KEY_FILE` says. The log says which.
- **Resend returns 403**: Cloudflare in front of Resend rejects some user agents. The
  script sends its own; other tools sharing the key may need the same.
- **Why not Ladok's own reminder?** Ladok has no reminder for course registration.
  Exam registration may be announced by the university, but that is a local routine,
  not something to rely on.

## Privacy

No data leaves the machine except the notice itself. The session cookie stays in your
Chrome profile. The script sends course names, dates and a warning line to the channel
you chose. No analytics, no telemetry, no account.

**Login:** the password never leaves the browser. The script does not read, store,
send or log it, and it holds no account of its own: it uses the login you saved in
your own Chrome profile. Re-login is off by default, and it only clicks a form Chrome
has already filled. If you enable it, whether that is allowed where you study is your
call and your responsibility.

## Requirements

Python 3.8+, `websockets`, a Chrome or Chromium you can start with a DevTools port.
Linux, macOS and Windows.

## Development

```bash
python3 -m pytest tests -q     # 8 tests, no network
```

## License

MIT. Do what you like, no warranty.

---

<sub>

**In honour of Xom**, god of chaos, who answers prayers with teleport traps and
grants wishes nobody made. This watch was written in that spirit: a student asked
for an alert when registration opens and got a script that would rather stay silent
than lie. Xom finds the whole arrangement very funny. Xom is not done with you.

*Xom is interested. Point your agent at the repo and see what happens.*

</sub>
