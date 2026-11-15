---
name: shh-study-kit
description: "Use when an agent must read a student's Canvas, SharePoint or Ladok."
version: 0.1.0
author: vhsgreed
metadata:
  hermes:
    tags: [canvas, instructure, ladok, sharepoint, student, study, sso]
---

# shh-study-kit

Gives an agent read access to a university student's own course systems, built at
Sophiahemmet Högskola (SHH) but generic for any Canvas school.

## When to Use
- The student asks what is due, what a course page says, or wants a course mirrored for study.
- A page sits behind SSO (SharePoint, Ladok, Inspera, library) and the student has a
  logged-in agent browser running.
- Skip for public pages (kursplaner, utbildningsplaner): plain HTTP is enough.

## Setup (once, by the student)
1. Canvas: Account > Settings > New access token. Save it to `~/.secrets/CANVAS_TOKEN`
   (mode 0600). Tokens expire; note the date. Set `CANVAS_BASE` for non-SHH schools.
2. SSO systems: start a dedicated Chrome profile with remote debugging and log in by hand:
   `google-chrome --user-data-dir=$HOME/.config/shhkit-chrome --remote-debugging-port=9233`
   (flatpak: `flatpak run com.google.Chrome ...`). Never the default profile.

## Commands (JSON on stdout)
| Command | Does |
|---|---|
| `python -m shhkit courses` | Enrolled courses (id, code, term) |
| `python -m shhkit upcoming` | Planner items from today: title, due (UTC), submitted |
| `python -m shhkit mirror <id\|all> [--out DIR] [--no-files]` | Modules, pages, assignments, announcements, discussions, files to disk |
| `python -m shhkit get <api/path> k=v` | Any Canvas REST GET, paginated |
| `python -m shhkit login-check` | Which SSO sites the agent browser is logged in to |
| `python -m shhkit page <url>` | Rendered text of a page via the logged-in browser |
| `python -m shhkit fetch <url> <origin>` | JSON fetch with the site's own cookies |

## Rules for the agent
- Read only. Never submit, post, or change settings on the student's behalf unless asked
  for that specific action.
- Due dates from Canvas are UTC upload cutoffs, not session times. Convert to local time;
  get lecture/seminar times from the timetable (TimeEdit), not Canvas.
- Course content is copyrighted and personal. Keep mirrors out of any repo.
- `submitted: true` means Canvas has a submission, not that it passed.

## Pitfalls
- Some schools disable the page index (`/pages` 404 "avaktiverats"). Pages are still
  readable through module items; `mirror` uses that route.
- Quiz contents are not exposed to students over the API; use the saved attempt page.
- A login check that lands on `login.microsoftonline.com` means the session expired;
  the student logs in again in the agent browser. Do not try to automate the login.
