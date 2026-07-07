#!/usr/bin/env python3
"""
cc-bridge — view and continue your local Claude Code chats from any device on
your LAN/VPN. No cloud relay, no MCP, no auth (LAN-only by design).

  - View  : tails ~/.claude/projects/**/<sessionId>.jsonl (every chat, live).
  - Reply : runs `claude --resume <sessionId> -p "<text>"` in that chat's cwd,
            loading the FULL context and appending to the SAME session file.
  - Stop  : kills the running turn (remote interrupt).

Known ceiling: a VS Code panel holds its chat in memory, so a turn you send from
the phone lands on disk but the open VS Code window won't show it until you
/resume there. VS Code -> bridge is live; bridge -> VS Code is not. Claude Code
exposes no inbound door into a running panel, so this can't be fixed here.

Run:  python bridge.py            # 0.0.0.0:8787, permission bypass
      python bridge.py --port 9000 --permission-mode default
"""
import argparse
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
CLAUDE = shutil.which("claude") or "claude"
CONFIG = {"permission_mode": "bypassPermissions"}
# When cc-bridge runs windowless (pythonw / autostart), a child console app would
# otherwise pop its own terminal window on every turn. Suppress it on Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# mtime-keyed caches so a poll only re-reads transcripts that actually changed.
_META = {}     # path -> (mtime, (sid, cwd, title))
_MSGS = {}     # path -> (mtime, [msg,...])
_RUNNING = {}  # sid -> {"proc": Popen, "stopped": bool}
_LOCK = threading.Lock()


def session_files():
    return glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))


def read_lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def _plain_text(rec):
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for it in content:
            if isinstance(it, dict) and it.get("type") == "text":
                out.append(it.get("text", ""))
    return "\n".join(out).strip()


def session_meta(path, mtime):
    with _LOCK:
        c = _META.get(path)
    if c and c[0] == mtime:
        return c[1]
    sid = os.path.splitext(os.path.basename(path))[0]
    cwd, title, first_user, last_usage = None, None, None, None
    for rec in read_lines(path):
        if cwd is None and isinstance(rec.get("cwd"), str):
            cwd = rec["cwd"]
        if rec.get("type") == "ai-title" and rec.get("aiTitle"):
            title = rec["aiTitle"]
        if first_user is None and rec.get("type") == "user":
            first_user = _plain_text(rec)
        u = (rec.get("message") or {}).get("usage")
        if isinstance(u, dict):
            last_usage = u
    # context in use = last turn's prompt size (fresh input + cached). Window is
    # 200K normally, 1M for [1m] sessions — inferred from the token count.
    ctx = 0
    if last_usage:
        ctx = (last_usage.get("input_tokens", 0) + last_usage.get("cache_read_input_tokens", 0)
               + last_usage.get("cache_creation_input_tokens", 0))
    win = 1000000 if ctx > 200000 else 200000
    meta = (sid, cwd, (title or (first_user or "")[:60] or sid[:8]), ctx, win)
    with _LOCK:
        _META[path] = (mtime, meta)
    return meta


# ---- project logo ----------------------------------------------------------
# A project can give its chats an avatar via a marker in its AGENTS.md:
#   <!-- logo: assets/logo.png -->   (path relative to AGENTS.md, an http(s)
#   URL, or an emoji as a fallback). Local image files are served at /api/logo.
# AGENTS.md is looked up cheaply: cwd, then up the parent chain, then one level
# down (nested sub-projects). Cached per cwd; re-reads on mtime change.
_LOGO = {}   # cwd -> (agents_path|None, mtime, resolved)   resolved: (kind, value)|None
LOGO_RE = re.compile(r"<!--\s*logo:\s*(.+?)\s*-->", re.I)
_LOGO_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "env",
              "dist", "build", ".next", ".cache", "target", "vendor"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}
IMG_CTYPE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".gif": "image/gif", ".webp": "image/webp",
             ".svg": "image/svg+xml", ".ico": "image/x-icon"}


def _find_agents(cwd):
    p = os.path.join(cwd, "AGENTS.md")
    if os.path.isfile(p):
        return p
    d = cwd                                     # up the parent chain (repo root)
    for _ in range(6):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
        p = os.path.join(d, "AGENTS.md")
        if os.path.isfile(p):
            return p
    try:                                        # one level down (sub-projects)
        for name in sorted(os.listdir(cwd)):
            if name in _LOGO_SKIP or name.startswith("."):
                continue
            sub = os.path.join(cwd, name)
            p = os.path.join(sub, "AGENTS.md")
            if os.path.isdir(sub) and os.path.isfile(p):
                return p
    except OSError:
        pass
    return None


def _resolve_logo(agents_path):
    try:
        head = open(agents_path, "r", encoding="utf-8", errors="replace").read(6000)
    except Exception:
        return None
    m = LOGO_RE.search(head)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return ("url", raw)
    base = os.path.dirname(agents_path)
    p = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(base, raw))
    if os.path.splitext(p)[1].lower() in IMG_EXTS and os.path.isfile(p):
        return ("file", p)
    return ("txt", raw[:24])                     # emoji / short text fallback


def project_logo(cwd):
    if not cwd or not os.path.isdir(cwd):
        return None
    cached = _LOGO.get(cwd)
    if cached:
        path, mtime, resolved = cached
        if path is None:
            return resolved                      # negative cached — restart to rescan
        try:
            if os.path.getmtime(path) == mtime:
                return resolved
        except OSError:
            pass                                 # gone — re-resolve
    path = _find_agents(cwd)
    resolved = _resolve_logo(path) if path else None
    mtime = 0
    if path:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            path = None
    _LOGO[cwd] = (path, mtime, resolved)
    return resolved


def list_sessions():
    # cheap mtime sort first; only read (cache-miss) transcripts for the top N.
    paths = [(os.path.getmtime(p), p) for p in session_files()]
    paths.sort(reverse=True)
    with _LOCK:
        running = set(_RUNNING.keys())
        armed = set(_ARMED.keys())
    rows = []
    for mtime, p in paths[:40]:
        try:
            sid, cwd, title, ctx, win = session_meta(p, mtime)
            lg = project_logo(cwd)
            if lg and lg[0] == "file":
                logo = {"img": "/api/logo?id=" + sid}
            elif lg and lg[0] == "url":
                logo = {"img": lg[1]}
            elif lg and lg[0] == "txt":
                logo = {"txt": lg[1]}
            else:
                logo = None
            rows.append({"id": sid, "cwd": cwd or "?", "title": title,
                         "mtime": mtime, "running": sid in running,
                         "ctx": ctx, "ctxpct": round(100 * ctx / win) if win else 0,
                         "logo": logo, "armed": sid in armed})
        except Exception:
            continue
    return rows


def _find(sid):
    for p in session_files():
        if os.path.splitext(os.path.basename(p))[0] == sid:
            return p
    return None


def render_messages(sid):
    path = _find(sid)
    if not path:
        return []
    mtime = os.path.getmtime(path)
    with _LOCK:
        c = _MSGS.get(path)
    if c and c[0] == mtime:
        return c[1]
    msgs = []
    for rec in read_lines(path):
        if rec.get("isSidechain"):
            continue
        t = rec.get("type")
        if t not in ("user", "assistant"):
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            if content.strip():
                msgs.append({"role": t, "kind": "text", "text": content})
            continue
        if not isinstance(content, list):
            continue
        for it in content:
            if not isinstance(it, dict):
                continue
            k = it.get("type")
            if k == "text" and it.get("text", "").strip():
                msgs.append({"role": t, "kind": "text", "text": it["text"]})
            elif k == "thinking" and it.get("thinking", "").strip():
                msgs.append({"role": t, "kind": "thinking", "text": it["thinking"]})
            elif k == "tool_use":
                detail = json.dumps(it.get("input", {}), indent=2)[:8000]
                msgs.append({"role": t, "kind": "tool", "text": it.get("name", "?"), "detail": detail})
    with _LOCK:
        _MSGS[path] = (mtime, msgs)
    return msgs


def session_cwd(sid):
    path = _find(sid)
    if not path:
        return None
    for rec in read_lines(path):
        if isinstance(rec.get("cwd"), str):
            return rec["cwd"]
    return None


def _kill(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            proc.terminate()
    except Exception:
        pass


def send_turn(sid, text):
    # reserve atomically so a double-tap / racing poll can't spawn two agents
    # on the same conversation (the two-writer corruption case).
    with _LOCK:
        if sid in _RUNNING:
            return False, "a turn is already running for this chat - wait for it or press stop"
        _RUNNING[sid] = {"proc": None, "stopped": False}
    cwd = session_cwd(sid)
    if not cwd or not os.path.isdir(cwd):
        with _LOCK:
            _RUNNING.pop(sid, None)
        return False, f"cwd not found for session ({cwd!r})"
    try:
        proc = subprocess.Popen(
            [CLAUDE, "--resume", sid, "-p", text, "--permission-mode", CONFIG["permission_mode"]],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=NO_WINDOW,
        )
    except Exception as e:
        with _LOCK:
            _RUNNING.pop(sid, None)
        return False, str(e)
    with _LOCK:
        _RUNNING[sid]["proc"] = proc
    try:
        out, err = proc.communicate(timeout=1800)
    except subprocess.TimeoutExpired:
        _kill(proc)
        out, err = "", "timed out after 1800s"
    with _LOCK:
        ent = _RUNNING.pop(sid, None)
    if ent and ent["stopped"]:
        return True, "stopped"
    if proc.returncode != 0:
        return False, (err or out or f"exit {proc.returncode}").strip()[:500]
    return True, (out or "").strip()


def stop_session(sid):
    with _LOCK:
        ent = _RUNNING.get(sid)
        if ent:
            ent["stopped"] = True
    if not ent:
        return False, "nothing running"
    if ent["proc"]:
        _kill(ent["proc"])
    return True, "stopped"


# ---- auto-continue on usage-limit reset -------------------------------------
# Arm a chat and cc-bridge keeps trying to send a "continue" message every few
# minutes until it goes through — which is the moment your usage limit resets,
# so a blocked agent picks back up on its own. No clock-time parsing. Persisted
# across restarts (the autostart server may restart before the reset).
_ARMED = {}   # sid -> {"text": str, "tries": int}
ARMED_FILE = os.path.join(HOME, ".claude", "cc-bridge-armed.json")
ARMED_DEFAULT = "Session limit refreshed. Continue."
RETRY_EVERY = 300   # seconds between attempts
RETRY_MAX = 96      # give up after ~8h


def _save_armed():
    try:
        with _LOCK:
            data = dict(_ARMED)
        tmp = ARMED_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, ARMED_FILE)
    except Exception:
        pass


def _load_armed():
    try:
        with open(ARMED_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            for k, v in d.items():
                _ARMED[k] = {"text": (v or {}).get("text", ARMED_DEFAULT), "tries": 0}
    except Exception:
        pass


def arm(sid, text):
    with _LOCK:
        _ARMED[sid] = {"text": (text or "").strip() or ARMED_DEFAULT, "tries": 0}
    _save_armed()


def disarm(sid):
    with _LOCK:
        _ARMED.pop(sid, None)
    _save_armed()


def _retry_loop():
    while True:
        time.sleep(RETRY_EVERY)
        with _LOCK:
            items = list(_ARMED.items())
        for sid, info in items:
            with _LOCK:
                busy = sid in _RUNNING
            if busy:
                continue
            ok, out = send_turn(sid, info["text"])
            if ok:
                disarm(sid)                      # went through — limit is back
                continue
            low = (out or "").lower()
            limited = any(w in low for w in ("limit", "usage", "rate", "reset", "quota"))
            info["tries"] += 1
            if not limited or info["tries"] >= RETRY_MAX:
                disarm(sid)                      # real error, or gave up
            else:
                _save_armed()


class H(BaseHTTPRequestHandler):
    def _send(self, body, ctype, code=200):
        # a phone that navigates away mid-poll drops the socket; that's normal,
        # not an error worth a traceback on the console.
        try:
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _json(self, obj, code=200):
        self._send(json.dumps(obj).encode(), "application/json", code)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            try:
                self._send(open(INDEX, "rb").read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(b"index.html missing next to bridge.py", "text/plain", 500)
        elif u.path == "/api/sessions":
            self._json(list_sessions())
        elif u.path == "/api/messages":
            q = parse_qs(u.query)
            msgs = render_messages(q.get("id", [""])[0])  # full array, cached by mtime
            total = len(msgs)
            if q.get("tail"):
                try:
                    n = max(1, int(q["tail"][0]))
                except ValueError:
                    n = 30
                start = max(0, total - n)
            else:
                try:
                    start = int(q.get("start", ["0"])[0])
                except ValueError:
                    start = 0
                start = max(0, min(start, total))
            # only the requested window crosses the wire, not the whole history
            self._json({"total": total, "start": start, "messages": msgs[start:]})
        elif u.path == "/api/stream":
            self._sse(parse_qs(u.query).get("id", [""])[0])
        elif u.path == "/api/logo":
            self._logo(parse_qs(u.query).get("id", [""])[0])
        else:
            self._json({"error": "not found"}, 404)

    def _logo(self, sid):
        cwd = session_cwd(sid)
        lg = project_logo(cwd) if cwd else None
        if not lg or lg[0] != "file":
            return self._json({"error": "no logo"}, 404)
        ctype = IMG_CTYPE.get(os.path.splitext(lg[1])[1].lower())
        if not ctype:
            return self._json({"error": "bad type"}, 404)
        try:
            data = open(lg[1], "rb").read()
        except Exception:
            return self._json({"error": "read failed"}, 404)
        self._send(data, ctype)

    def _sse(self, sid):
        # Server-Sent Events: a held connection = "a viewer is reading this chat."
        # We watch just that file's mtime and push the instant it changes, so the
        # client re-reads on demand instead of polling on a timer.
        path = _find(sid)
        try:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "keep-alive")
            self.end_headers()
            last, beat = None, 0
            while True:
                m = os.path.getmtime(path) if path and os.path.exists(path) else 0
                if m != last:
                    last = m
                    self.wfile.write(f"data: {m}\n\n".encode())
                    self.wfile.flush()
                else:
                    beat += 1
                    if beat >= 40:  # ~20s keepalive comment
                        beat = 0
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                time.sleep(0.5)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            return

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/api/send":
            ok, out = send_turn(body.get("id", ""), body.get("text", ""))
            self._json({"ok": ok, "error": None if ok else out, "result": out if ok else ""})
        elif u.path == "/api/stop":
            ok, out = stop_session(body.get("id", ""))
            self._json({"ok": ok, "error": None if ok else out})
        elif u.path == "/api/arm":
            arm(body.get("id", ""), body.get("text", ""))
            self._json({"ok": True})
        elif u.path == "/api/disarm":
            disarm(body.get("id", ""))
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--permission-mode", default="bypassPermissions")
    args = ap.parse_args()
    CONFIG["permission_mode"] = args.permission_mode
    _load_armed()
    threading.Thread(target=_retry_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), H)
    _say("cc-bridge up. open on any LAN/VPN device:")
    _say(f"  http://{lan_ip()}:{args.port}/   (local: http://127.0.0.1:{args.port}/)")
    _say(f"permission-mode: {args.permission_mode}   claude: {CLAUDE}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _say("\nbye")


def _say(msg):
    # pythonw / autostart has no console — stdout is None there, and a bare print
    # would raise and kill the server before it serves. Swallow that.
    try:
        print(msg)
    except Exception:
        pass


if __name__ == "__main__":
    main()
