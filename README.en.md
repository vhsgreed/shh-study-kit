# ladok-vakt

**Get email when Ladok registration opens, before the window closes.**

`ladok-vakt` watches course registration and exam registration in Ladok.

Swedish: [README.md](README.md).

---

## Why ladok-vakt?

Ladok does not offer email notifications in its student service; this script works around that.

`ladok-vakt` fetches the registration dates for courses and exams directly from [Ladok](https://student.ladok.se/) and reminds the user by email or another channel.

## Quick start

### Requirements

- Python 3.8+
- Chrome or Chromium
- The Python package `websockets`

### Install and try it

```bash
git clone https://github.com/vhsgreed/ladok-vakt && cd ladok-vakt
python3 -m pip install websockets

# Start Chrome with its own profile and log in to Ladok there once
flatpak run com.google.Chrome \
  --user-data-dir=$HOME/.config/ladok-vakt-chrome \
  --remote-debugging-port=9233
# Non-Flatpak: google-chrome with the same flags

# Try reading from Ladok
python3 -m ladok_vakt --all
```

Run `python3 -m ladok_vakt` with no arguments for cron mode: it writes only when
there is something to say. Set up a notification channel below to get alerts.

## Features

- Fetches your course occasions, registration periods and course periods.
- Writes one line per window that is open now or closes within the chosen number of days.
- Alerts on faults, for example if no fetch has succeeded for 36 hours, the course list is empty, the session has died or the answer is malformed.
- Sends a weekly health check on Mondays.
- Does not register, unregister or change anything.
- Optional login feature for keeping a session alive over time

## Schedule the run

### cron

Example: run at 07:10 every day.

```cron
10 7 * * *  cd $HOME/src/ladok-vakt && LADOK_MAIL_TO=you@example.com /usr/bin/python3 -m ladok_vakt
```

### systemd

Example files are in [`examples/`](examples/).

```bash
cp examples/ladok-vakt.service examples/ladok-vakt.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now ladok-vakt.timer
```

### Agent

Put the run in the agent's cron, or register
[`skill/SKILL.md`](skill/SKILL.md) as a skill. The script is built to be run
without having to be interpreted as free text.

## Choose a notification channel

| Channel | Variables | Information |
|---|---|---|
| **Resend** (email) | `LADOK_MAIL_TO`, `LADOK_MAIL_FROM`, `~/<PATH TO RESEND_KEY>` | The mail is sent via `https://api.resend.com/emails`. Works on the free tier. |
| **Gmail** (email) | `LADOK_MAIL_TO`, `LADOK_MAIL_FROM`, `LADOK_SMTP_USER`, `LADOK_SMTP_PASSWORD_FILE` | Requires an [app password](https://myaccount.google.com/apppasswords) from a Gmail account with 2FA. Set `LADOK_SMTP_PASSWORD_FILE=$<PATH TO GMAIL_APP_PASSWORD>`. When the variable is set, SMTP is used instead of Resend. |
| **Telegram** | `LADOK_TELEGRAM_TOKEN`, `LADOK_TELEGRAM_CHAT` | Bot and chat id; simple push to the phone. |
| **Slack** | `LADOK_WEBHOOK_URL`, `LADOK_WEBHOOK_FORMAT=slack` | Incoming webhook; sends `{"text": ...}`. |
| **Discord** | `LADOK_WEBHOOK_URL`, `LADOK_WEBHOOK_FORMAT=discord` | Sends `{"content": ...}`. |
| **ntfy** | `LADOK_WEBHOOK_URL=https://ntfy.sh/<topic>`, `LADOK_WEBHOOK_FORMAT=ntfy` | No registration; pick a topic name. |
| **Your own webhook** | `LADOK_WEBHOOK_URL=...`, `LADOK_WEBHOOK_FORMAT=generic` | Sends `{"text": ..., "source": "ladok-vakt"}`. |
| **Your own command** | `LADOK_NOTIFY_COMMAND='curl -d @- https://...'` | Passed on via stdin |

## Optional automatic login

The Ladok session can end when the browser restarts or after a period of inactivity.
With `LADOK_AUTOLOGIN=1` the script tries to log in again before it raises a fault. It
assumes you have the login saved in the session the script uses.

```bash
export LADOK_IDP_ENTITY='https://swamid2.shh.se/idp/shibboleth'   # your institution's SWAMID entityID
export LADOK_AUTOLOGIN=1
python3 -m ladok_vakt --login   # try it in advance
```

The re-login takes your route: *Log in through your university*, picks the institution, and presses Login once Chrome has filled in the saved password. If that fails it tries the direct SWAMID link. The window does not need to be visible, so it works with the screen locked.

The script opens the login via SWAMID, waits until Chrome has filled in the form
and clicks Login with a real mouse event. It then checks that Ladok answers before
the login is reported as successful.

**Security boundaries:** the script never reads the value of the password field, stores
nothing, writes nothing, sends nothing and does not use the browser's password-manager
API. It only checks whether the fields have been filled in, and clicks. See the code in
`ladok_vakt/login.py`; the test in `tests/` fails if anyone adds reading of field
values.

For the re-login to work, the password must be saved in the Chrome profile the script
uses. The login must not require 2FA, a one-time code or approval in an app. If it
does, the re-login fails and you get a fault alert: the script does not guess.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `LADOK_CDP` | `http://127.0.0.1:9233` | Chrome DevTools endpoint for your logged-in session |
| `LADOK_LAROSATE` | `38` | The institution's id in Ladok (38 = Sophiahemmet) |
| `LADOK_MAIL_TO` | empty | Recipient; an empty value turns email off |
| `LADOK_MAIL_FROM` | `ladok-vakt <vakt@example.org>` | Sender; must be verified with Resend |
| `LADOK_CLOSE_DAYS` | `3` | Alert when the window closes within that many days |
| `LADOK_AUTOLOGIN` | `0` | Set to `1` to try logging in again with the saved password |
| `LADOK_IDP_ENTITY` | empty | The institution's SWAMID entityID; required for re-login |
| `LADOK_IDP_DOMAIN` | from entityID | Domain the institution picker searches for, e.g. `shh.se` |
| `LADOK_STALE_HOURS` | `36` | Alert when the last successful fetch is older than this |
| `LADOK_MAIL_MIN_HOURS` | `20` | Minimum time between fault alerts per channel |
| `LADOK_HEARTBEAT` | `1` | Weekly health check on Mondays; `0` turns it off |
| `LADOK_STATE_DIR` | `~/.local/state/ladok-vakt` | Directory for state and log |

State is stored in `$LADOK_STATE_DIR/state.json`, and history in `ladok-vakt.log`.

## How it works

Ladok has no student API with tokens. Studentwebben fetches data via
`https://student.ladok.se/student/proxy/<institution>/...` with the session cookie.
`ladok-vakt` makes the equivalent calls from a tab on the same origin, via the Chrome
DevTools protocol.

| Data | Endpoint |
|---|---|
| Logged-in student | `/studiedeltagande/internal/student/inloggad/som` |
| Course occasions and registration period | `/studiedeltagande/internal/tillfallesdeltagande/kurstillfallesdeltagande/student/<uid>` |
| Exam registrations | `/resultat/internal/studentweb/aktivitetstillfalle/inloggadstudent/relevanta` |

The answers are XML in Ladok's schema. The script uses, among other fields,
`sd:Registreringsperiod` (`base:Startdatum` / `base:Slutdatum`), `sd:Tillstand`
and `sd:UtbildningsPeriodFran/Till`.

## Privacy

No data leaves the computer except the alert itself. The session cookie stays in your
Chrome profile. The script only sends course names, dates and warning text to the
channel you choose. No statistics, telemetry or account of any kind.

The password never leaves the browser: the script does not read, store, send or log
it. It uses the login you saved yourself in Chrome. Automatic re-login is off by
default; if you choose to enable it, you are responsible for it following your
institution's rules.

## License

MIT

---

<sub>

*Xom is interested. Point your agent at the repo and see what happens.*

</sub>
