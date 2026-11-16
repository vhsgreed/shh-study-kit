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
