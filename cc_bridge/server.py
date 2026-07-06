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


def list_sessions():
    # cheap mtime sort first; only read (cache-miss) transcripts for the top N.
    paths = [(os.path.getmtime(p), p) for p in session_files()]
    paths.sort(reverse=True)
    with _LOCK:
        running = set(_RUNNING.keys())
    rows = []
    for mtime, p in paths[:40]:
        try:
            sid, cwd, title, ctx, win = session_meta(p, mtime)
            rows.append({"id": sid, "cwd": cwd or "?", "title": title,
                         "mtime": mtime, "running": sid in running,
                         "ctx": ctx, "ctxpct": round(100 * ctx / win) if win else 0})
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
        else:
            self._json({"error": "not found"}, 404)

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
