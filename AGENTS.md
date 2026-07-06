# AGENTS.md — working on cc-bridge

<!-- logo: assets/logo.svg -->

Guidance for AI agents (and humans) editing this repo. Read `PLAN.md` for the
roadmap and settled decisions; this file is the how-to-work.

## Project logo (the `<!-- logo: … -->` convention)

cc-bridge gives every chat an avatar. By default it's a monogram of the chat
title; if the project's `AGENTS.md` contains a **logo marker**, cc-bridge shows
that instead. The marker is a single HTML comment (invisible in rendered
Markdown) whose value is, in order of preference:

```
<!-- logo: assets/logo.png -->          image file, path relative to AGENTS.md
<!-- logo: https://…/logo.png -->       image URL
<!-- logo: 👻 -->                        emoji / short text (fallback)
```

Supported image types: png, jpg, gif, webp, svg, ico. Local image files are
served by cc-bridge at `/api/logo`. It finds `AGENTS.md` cheaply — the chat's
working directory, then up the parent chain, then one level down (for nested
sub-projects) — and caches the result (a live `AGENTS.md` re-reads on mtime
change; adding one where none existed needs a server restart). Any repo can
adopt this by adding one line near the top of its `AGENTS.md`.

## What it is

A tiny local web app to view and continue your Claude Code chats from a phone/PC
on the LAN/VPN. It reads Claude Code's own transcripts under
`~/.claude/projects/**/<sessionId>.jsonl` and, to reply, runs
`claude --resume <id> -p "<text>"` in the chat's own cwd — appending to the same
session file. No cloud relay, no MCP, no auth.

## Hard rules (do not break)

- **Standard library only.** No third-party runtime dependencies, ever. Hatchling
  is build-time only. If you're reaching for `pip install`, stop and find a stdlib
  way or don't build it.
- **Two source files carry the whole app:** `cc_bridge/server.py` (stdlib HTTP
  server) and `cc_bridge/index.html` (the entire front-end — vanilla JS/CSS, no
  build step, no framework, no CDN). Keep it that way.
- **Self-contained front-end.** No external requests (fonts, CDNs, images). Inline
  everything. The markdown renderer is hand-rolled and **escapes HTML first** —
  keep that ordering or you open an injection hole.
- **LAN-only, no auth by design.** Don't add telemetry or outbound calls.

## Architecture

- `server.py`: `list_sessions()` (mtime-sorted, cached per file), `render_messages()`
  (paginated tail/window), `send_turn()` (Popen `claude --resume`, tracked in
  `_RUNNING` with a per-session guard so two turns can't collide), `stop_session()`,
  and `/api/stream` (SSE — pushes on transcript mtime change). Caches are mtime-keyed
  (`_META`, `_MSGS`); a poll only re-reads files that changed.
- `index.html`: two screens (chat list / conversation). Incremental render
  (`renderWindow` full paint, `loadLive` appends only new/changed). Per-chat state:
  drafts + last-message snapshot in `localStorage`, `sendingSet` so chats run
  independently. Live updates via `EventSource` on `/api/stream`.
- `bridge.py`: thin shim so `python bridge.py` works from a clone. Real entry point
  is `cc_bridge:main` (the `cc-bridge` console script).

## Run & verify (there are no unit tests — drive it)

```bash
python bridge.py --port 8899        # live files; use this while developing
# then:
curl -s localhost:8899/api/sessions | head
curl -s localhost:8899/ | grep -c "<title>"
```

Server code parses cleanly: `python -c "import ast,sys; ast.parse(open('cc_bridge/server.py').read())"`.
The front-end has no build — just reload the browser (server re-reads `index.html`
per request, so HTML edits need no restart; **server.py edits need a restart**).

## Gotcha: the installed `cc-bridge` bundles a snapshot

`uv tool install` freezes `index.html`/`server.py` into its own venv. A running
`cc-bridge` won't see repo edits. During development prefer `python bridge.py`
(live files). To refresh the installed command: stop it (it locks `cc-bridge.exe`
on Windows), then `uv tool install "<repo path>" --force`.

## Known limitation (don't try to "fix" it)

A message sent from cc-bridge lands in the on-disk conversation, but an **open VS
Code Claude Code panel won't refresh** — it holds its copy in memory and Claude
Code exposes no inbound API to push a turn into a live panel (checked thoroughly:
URI handler doesn't auto-submit, no command takes a prompt, IDE websocket is
one-way). cc-bridge is a *second live head* on the same conversation. Rule: one
head at a time.

## Style

Match the existing terse, comment-where-non-obvious style. Small diffs. Don't add
abstractions for one caller. Every non-trivial behavior should be curl-verifiable.
