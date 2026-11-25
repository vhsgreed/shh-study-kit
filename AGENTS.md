# AGENTS.md

You are an agent working in or with this repo.

- **Using the kit:** read `skill/SKILL.md`, then run `python3 -m unitoolkit.mcp` or
  `python3 -m unitoolkit <tool>`. Start with `upcoming`.
- **Changing the kit:** tools live in `unitoolkit/tools.py` (one `@tool` function each). After
  any change run `python3 -m unitoolkit manifest > tools.json` and the MCP smoke test below.
- **Never commit** course content, tokens, cookies, or anything under `~/courses`.

`vendor/ladok-vakt` is a subtree of https://github.com/vhsgreed/ladok-vakt. Never edit
files under it here: change them in that repo, pull the subtree back. The
`ladok_windows` tool wraps it.

MCP smoke test:
```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python3 -m unitoolkit.mcp
```
6. **Koha reads go through the browser, never plain HTTP.** biblioteket.shh.se is behind
   Cloudflare, which returns 429 to any non-browser client, including Cloudflare Workers and
   the Apify HTTP proxy. Use `unitoolkit/koha.py` (CDP to the proxied Chrome). Credentials only
   ever come from `~/.secrets/KOHA_USER` and `~/.secrets/KOHA_PIN`: never accept them in a
   prompt, a tool argument or a log line.

7. **Koha login has two routes, and only the session one is proven.** The library offers the
   local form (username + 4-digit pinkod, `~/.secrets/KOHA_CREDENTIALS`, two lines) and
   "Log in using your SHH account", which hands off to Shibboleth and then to Microsoft Entra.
   Submitting the local form with a personnummer, the SSO username or the full e-mail was refused
   from this machine with a plain re-render and no error text, so do not keep guessing
   usernames: repeated failures risk a lockout. Preferred path: `koha_open_login`, a human signs
   in once in the proxied window, `koha_wait_login`, then `koha_loans` reads the session.
   The form must carry `login_op=cud-login` or Koha ignores the POST entirely.
   The SSO route fails at the last step for a cookie reason, not a proxy one: the SAML
   hand-back from login.microsoftonline.com is a cross-site POST, and Koha's CGISESSID is
   SameSite=Lax, so it is dropped once the 2-minute grace expires (two-factor takes longer).
   Launch that profile with `--disable-features=SameSiteByDefaultCookies,CookiesWithoutSameSiteMustBeSecure`
   (see the school-helper profile's `scripts/koha_chrome.sh`) and the login sticks.

8. **Database access is one session, not one login per database.** CINAHL, PsycInfo, Cochrane,
   Web of Science, JCR, Sage and NE all sit behind `shh.idm.oclc.org`, which delegates to the
   school's Microsoft Entra tenant. A single sign-in with "stay signed in" leaves an Entra
   cookie in the agent browser and EZproxy then admits every one of them silently, so never
   ask for credentials per database. Search the ones with a structured reader
   (`db_search`: cinahl, psycinfo); the rest open with `db_open`. Two traps cost us time:
   EBSCO's URL parameter reads a space-separated query as one phrase (hence `_normalise_query`
   joins bare words with AND), and a record's source line can sit on the authors' line, so
   parse `In:` out of the `By:` line before splitting authors.

9. **Full text is licensed, so it is rate-limited by design.** `db_fulltext` keeps a one-hour
   ledger and refuses past 20 downloads. Do not raise the cap or loop around it: publishers
   detect systematic downloading and block the institution's whole EZproxy, which would take
   the databases away from every student at the school. Resolve the DOI outside the proxy
   when the proxied DOI redirect fails (Cochrane lands on a Chrome error page through
   EZproxy, while its own URL proxies fine).
10. **Session freshness:** the EZproxy cookies die with the browser, but the Microsoft Entra
    `ESTSAUTHPERSISTENT` cookie lasts 90 days and is renewed on use, and EZproxy re-admits
    silently from it. A daily keepalive (`db_status`) is therefore enough; only a renewed MFA
    demand needs a human.

11. **When you find a new site quirk, write it into the registry, not just into a comment.**
    `registry/registry.json` is what the next agent reads before it debugs the same SSO route or
    cookie by hand. Add the row or extend its `gotchas`, and give it probes if the failure is
    detectable (`scripts/smoke.py`). Statuses and `last_verified` dates are written by
    `scripts/registry_check.py` only: never hand-edit them, and never mark a row `ok` by hand.
    A row without probes stays `untested` on purpose; an honest gap beats a false green.
12. **Untested rows are the registry's weak point, so keep them visible.** `partial` is a human
    statement about a known limit (write it by hand, with the limit in `gotchas`); `fail` and `ok`
    come from a test run. If a probe needs a login that costs the student something (Koha account
    logins count against the card), do not add a probe for it.

13. **Never commit `*.egg-info/`, and never trust it for the version.** It sat in the repo with
    `0.7.0` inside while `pyproject.toml` said `0.8.0`, so the wheel built in CI carried the old
    version and PyPI rejected the upload as a duplicate. `build/`, `dist/` and the egg-info are
    gitignored now; the version lives in `pyproject.toml` and `unitoolkit/__init__.py` only.

14. **Canvas files: the API route, never the web `/download` path.** `courses/:id/files/:file_id`
    returns metadata plus a pre-signed `url`; GET that URL and you have the bytes, cookies not
    involved. `/courses/:id/files/:file_id/download` is the browser route: without a Canvas session
    it 302s to shh.se and hands back an empty body, which reads like "the file is unavailable" and
    sent us chasing the browser instead of the API. Files attached to an assignment are covered by
    the API. Ids lifted out of assignment HTML are not all course files (banners, emoji), so expect
    404 on those and skip them. `canvas_file course= file_id= out=` does all of it.

15. **A soft block is still a block.** lakemedelsboken.se answers 404 to non-browser
    User-Agents. Sending a browser UA gets the page, but that is choosing to get past a filter the
    site put up, so it sits behind `UNITOOLKIT_REFS_BROWSER_UA=1` and the refusal names the reason.
    Do not quietly dress another reader in a browser UA; either use the real browser (`page`) or
    make the choice explicit and log it in the registry.

16. **Shared checkout: read the staged diff, not the file list.** Two agents work in this clone.
    `git add <path>` stages everything in that file, including another agent's uncommitted edits;
    that is how health-kun's `refs_search`/`refs_page` landed inside an unrelated `canvas_file`
    commit and then got registered a second time under other names. Before every commit run
    `git diff --cached` and confirm each hunk is yours. Contributions arrive on a branch as a PR.
    And regenerate `tools.json` (rule at the top) in the same commit as any tool change; it had
    drifted eight tools behind.

17. **Viva Engage: list posts from the SPA's GraphQL, not the feed DOM.** A group feed renders only
    2-4 `a[href*=threads]` links whatever you scroll, and a mouse wheel at fixed coordinates does
    nothing in headless Chrome. Scroll the `.os-viewport` that actually moves (set `scrollTop`) and
    read thread ids from the `/graphql` responses (Network.getResponseBody); thread nodes there have
    `databaseId` + `threadStarter` but no `__typename`, so match on shape. 4 posts became 217 this
    way. Bodies still come from opening each thread page. `scripts/fetch_engage.py`.

19. **New Quizzes is not Classic Quizzes, and its questions are NOT in an iframe.** The
    `Självrättande test` assignments are LTI links with no Canvas REST route (`quizzes` returns
    empty). Opened in the signed-in browser they redirect to `/assignments/<id>/taking/<n>` and the
    questions render in the top page. The cross-origin frames you will find (Learnosity's
    `xdomain` XHR proxy, Canvas's `post_message_forwarding`) hold nothing: attaching to them was
    the first, wrong, attempt. One question per screen, marked "Frågan på placeringen N"; the
    "Frågenavigatör" strip lists every question, marks the unanswered ones ("…, obesvarad") and
    jumps directly. Radios/checkboxes are real inputs at opacity 0.0001 behind a label, so click
    the label. A blank is a textbox named "Fråga tom 1 av 1"; fill, then Tab. Answers save
    server-side as you go (they survive a reload). Answer by label text, never index: the order
    is shuffled per attempt. `quiz` uses Playwright `connect_over_cdp` on the agent Chrome, run on
    a private thread because the MCP server already owns an event loop.

20. **A Canvas URL with no session bounces to shh.se, not to a login form.** `canvas_session` is
    a session cookie and dies with every browser restart; the Canvas front door then sends you to
    `www.shh.se/sv/mina-sidor/canvas/`, which looks like "not signed in" but is not. The Entra
    cookie (`ESTSAUTHPERSISTENT`, months) is still there: `/login/saml/237` goes straight back in
    with nothing typed. `browser.page()` does this once, by itself (`canvas_heal`).

18. **OneDrive needs no Graph app.** The school's personal site (`shh365-my.sharepoint.com`) answers
    the OneDrive v2.0 API (`/_api/v2.0/me/drive/...`) to a fetch made inside that origin with the
    signed-in agent browser's cookies, so `onedrive` reads the student's drive and "Shared with me"
    without an app registration, token or admin consent. Shared items are `remoteItem`s whose bytes
    sit in the sharer's drive: address `drives/<driveId>/items/<id>`. The sharer is in
    `shared.sharedBy` on this tenant. Read only: do not add write routes; a group member's live
    document is theirs.
