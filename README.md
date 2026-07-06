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
- **Favorites** (star chats to pin them), **desktop notifications** on turn completion, per-chat **context-usage ring**.
- **Project logos:** give a project's chats a custom avatar by adding one line to its `AGENTS.md` — `<!-- logo: assets/logo.png -->` (image path, URL, or an emoji fallback). cc-bridge serves the image; falls back to a title monogram.
- Zero dependencies (Python standard library only). One command to run.

## Run

Global command via [uv](https://docs.astral.sh/uv/) — one line, any machine, still zero runtime deps:

```bash
uv tool install git+https://github.com/qxZap/cc-bridge
cc-bridge
```

Or run it without installing:

```bash
uvx --from git+https://github.com/qxZap/cc-bridge cc-bridge   # ephemeral
```

Or from a clone (no install needed):

```bash
uv run cc-bridge      # or: python bridge.py      (Windows: double-click start.bat)
```

It prints a URL like `http://192.168.1.20:8787/`. Open that on your phone over Wi-Fi/VPN.

### Autostart on login (Windows, optional)

Start it **windowless** one minute after you log in (`pythonw` = no console window; requires `uv tool install` first):

```powershell
$pw = "$env:APPDATA\uv\tools\cc-bridge\Scripts\pythonw.exe"
$a  = New-ScheduledTaskAction -Execute $pw -Argument "-m cc_bridge"
$t  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $t.Delay = "PT1M"
Register-ScheduledTask -TaskName cc-bridge -Action $a -Trigger $t -Force
```

Remove with `Unregister-ScheduledTask -TaskName cc-bridge`.

Options:

```bash
cc-bridge --port 9000
cc-bridge --permission-mode default   # default is bypassPermissions
```

Requirements: Python 3.8+ and the `claude` CLI on PATH, logged in. `uv` optional (only for the global command).

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

## Support

cc-bridge is free and MIT-licensed. If it saves you a few walks back to your desk:

- ☕ [Ko-fi](https://ko-fi.com/qxzap)
- ☕ [Buy Me a Coffee](https://buymeacoffee.com/qxzap)

## Files

- `cc_bridge/server.py` — stdlib HTTP server + transcript reader + resume runner + SSE.
- `cc_bridge/index.html` — the entire front-end (bundled as package data).
- `bridge.py` — thin shim so `python bridge.py` works from a clone.
- `pyproject.toml` — packaging for the `cc-bridge` command (build-time hatchling only).
- `start.bat` / `start.sh` — launchers.
