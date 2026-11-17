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
   Submitting the local form with a personnummer, `ht26-karsun` or the full e-mail was refused
   from this machine with a plain re-render and no error text, so do not keep guessing
   usernames: repeated failures risk a lockout. Preferred path: `koha_open_login`, a human signs
   in once in the proxied window, `koha_wait_login`, then `koha_loans` reads the session.
   The form must carry `login_op=cud-login` or Koha ignores the POST entirely.
