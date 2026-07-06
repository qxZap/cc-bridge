# cc-bridge

View and continue your local **Claude Code** chats from your phone or any device
on your LAN/VPN. No cloud relay, no accounts, no build step.

It reads Claude Code's own session transcripts (`~/.claude/projects/`) and, when
you reply, continues that exact conversation with full context by running
`claude --resume <id> -p "…"` in the chat's own directory. Same conversation,
same history — just reachable from the couch.

![one Python file + one HTML file](.)

## Features

- **Live view** of every Claude Code chat (VS Code *and* terminal), updated ~1s.
- **Reply** into any conversation — full context preserved, appended to the same session.
- **Stop** a running turn remotely.
- Clean mobile UI: searchable chat list → tap → conversation, light/dark theme.
- Markdown rendering, collapsible tool calls, infinite scroll.
- Zero dependencies (Python standard library only). One command to run.

## Run

```bash
python bridge.py
```

It prints a URL like `http://192.168.1.20:8787/`. Open that on your phone over
Wi-Fi/VPN. On Windows just double-click `start.bat`.

> Coming soon: a global `cc-bridge` command via `uv tool install` — see [PLAN.md](PLAN.md).

Options:

```bash
python bridge.py --port 9000
python bridge.py --permission-mode default   # default is bypassPermissions
```

Requirements: Python 3.8+ and the `claude` CLI on PATH, logged in.

## The one limitation (by design of Claude Code)

A message you send from cc-bridge lands in the conversation **on disk**, but an
**open VS Code Claude Code panel won't visually refresh** with it — that panel
keeps its copy in memory and Claude Code exposes no way for anything outside to
push a turn into it. So:

- **VS Code → cc-bridge is live** (it tails the file).
- **cc-bridge → VS Code needs a manual `/resume`** in that window to pull in phone turns.

Think of cc-bridge as a **second live head on the same conversation**, not a
mirror of the VS Code window. When you're remote, drive from cc-bridge; back at
the desk, `/resume` catches VS Code up.

## Security

There is **no authentication** — bind it only on a LAN/VPN you trust. Exposing
it to the public internet is on you. Permission mode defaults to
`bypassPermissions`, so turns you send run tools without prompting.

## Files

- `bridge.py` — stdlib HTTP server + transcript reader + resume runner.
- `index.html` — the entire front-end.
- `start.bat` / `start.sh` — launchers.
