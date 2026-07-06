# cc-bridge — roadmap

Status: **working, in daily use.** Repo is **private**. Landing page + Pages workflow are **prepared but not deployed**.

The idea: keep it dead simple and dependency-free, but make it *installable* so it's one global command on any machine — then, when it's polished, go public with a landing page and funding.

---

## Done

- `bridge.py` — stdlib-only HTTP server (no third-party libraries).
- `index.html` — mobile web UI: searchable chat list, conversation view, markdown (incl. **tables**), collapsible tool calls, infinite scroll, dark/light, iOS no-zoom inputs.
- Reply into any chat via `claude --resume` (full context, same session file), **stop** button, double-run guard.
- **Live updates via SSE** (server pushes on file change; client re-reads on demand).
- Deep-link: selected chat lives in the URL hash, survives refresh.
- Landing page (`site/index.html`) — prepared.
- Pages workflow (`.github/workflows/pages.yml`) — prepared, **manual-trigger only** (won't deploy on push).

---

## Next (deferred — pick up on the go)

### 1. Packaging with `uv` → global `cc-bridge` command  ⭐ main ask
Goal: `uv tool install git+https://github.com/qxZap/cc-bridge` gives a `cc-bridge` command on PATH, any machine, **still zero runtime deps**. Also `uvx cc-bridge` for ephemeral runs.

Work required:
- Restructure into a package: `cc_bridge/` with `server.py` (current `bridge.py`) + `__main__.py`; keep `python bridge.py` working during transition via a thin shim.
- Bundle `index.html` as **package data** and load it with `importlib.resources` instead of `__file__`-relative path (so it works when installed).
- `pyproject.toml`: `[project]` (name, version, `requires-python`), `[project.scripts] cc-bridge = "cc_bridge:main"`, build backend `hatchling`. No `dependencies` — stays stdlib.
- Optional: PEP 723 inline metadata in a single-file `bridge.py` so `uv run bridge.py` also works with no install.
- Update README run instructions once shipped.

### 2. Funding
- `FUNDING.yml` added (`github: qxZap`). Needs GitHub Sponsors enabled on the account to go live.
- Optional ko-fi / Buy Me a Coffee links.
- Add a "Sponsor" button to the landing page + README once live.

### 3. Ship the landing page
- Add real phone screenshots to `site/shots/` (swap out the CSS mockups).
- Flip `pages.yml` trigger to `push` and enable Pages (source: GitHub Actions).
- URL will be `https://qxzap.github.io/cc-bridge/`.

### 4. Go public
- Flip repo visibility to public when it's ready to show. (Pages on private repos needs a paid plan, so public + Pages is the clean combo.)

### 5. Nice-to-haves
- Optional token auth flag for less-trusted networks.
- Favicon / app icon, PWA manifest (add-to-home-screen).
- Screenshots gallery on the landing page.
- Autostart snippets (Windows Task Scheduler / systemd user unit).

---

## Constraints / decisions (settled — don't relitigate)

- **No third-party libraries.** Standard library only, both runtime and packaging-wise (hatchling is build-time only).
- **Can't inject into a live VS Code panel** — Claude Code exposes no API for it (checked thoroughly). cc-bridge is a *second live head* on the same on-disk conversation. Rule: **one head at a time** — don't drive the same chat from VS Code and phone simultaneously.
- **Don't restart the server while it's in remote use.**
