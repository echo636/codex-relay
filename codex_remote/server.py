#!/usr/bin/env python3
"""Private, single-user mobile gateway for Codex app-server. Python 3.11+."""
import argparse
from codex_remote import __version__
from codex_remote.storage import migrate
from codex_remote.i18n import locale, translate
import collections
import concurrent.futures
import fcntl
import hashlib
import hmac
import http.cookies
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

STATIC = Path(__file__).parent / "static"
# Only packaged rendering assets are public, never an arbitrary static path.
VENDOR_FILES = {
    "/vendor/markdown-it/markdown-it.min.js", "/vendor/highlight/highlight.min.js",
    "/vendor/katex/katex.min.js", "/vendor/katex/katex.min.css",
    "/vendor/mermaid/mermaid.min.js",
} | {"/vendor/katex/fonts/" + name + "." + extension
     for name in ("KaTeX_AMS-Regular", "KaTeX_Caligraphic-Bold", "KaTeX_Caligraphic-Regular",
                  "KaTeX_Fraktur-Bold", "KaTeX_Fraktur-Regular", "KaTeX_Main-Bold",
                  "KaTeX_Main-BoldItalic", "KaTeX_Main-Italic", "KaTeX_Main-Regular",
                  "KaTeX_Math-BoldItalic", "KaTeX_Math-Italic", "KaTeX_SansSerif-Bold",
                  "KaTeX_SansSerif-Italic", "KaTeX_SansSerif-Regular", "KaTeX_Script-Regular",
                  "KaTeX_Size1-Regular", "KaTeX_Size2-Regular", "KaTeX_Size3-Regular",
                  "KaTeX_Size4-Regular", "KaTeX_Typewriter-Regular")
     for extension in ("woff2", "woff", "ttf")}
APPROVALS = {
    "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
    "item/permissions/requestApproval", "item/tool/requestUserInput",
}


class APIError(Exception):
    def __init__(self, status, message, *, transport_seq=0):
        super().__init__(message)
        self.status = status
        self.transport_seq = transport_seq


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            migrate(self.db, path)
        except BaseException:
            self.db.close()
            raise
        self.event_count = 0
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY, title TEXT, cwd TEXT, created REAL,
                status TEXT DEFAULT 'idle', turn_id TEXT);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT,
                payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_thread ON events(thread_id,seq);
            CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY, thread_id TEXT, digest TEXT, result TEXT);
        """)

    def execute(self, sql, values=()):
        with self.lock, self.db:
            return self.db.execute(sql, values)

    def rows(self, sql, values=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, values).fetchall()]

    def event(self, tid, method, params, *, transport_seq=0, transport_epoch=None):
        event = {"method": method, "params": params}
        if transport_epoch and transport_seq:
            event["transportSeq"] = transport_seq
            event["transportEpoch"] = transport_epoch
        payload = json.dumps(event, ensure_ascii=False)
        self.execute("INSERT INTO events(thread_id,payload) VALUES(?,?)", (tid, payload))
        self.event_count += 1
        if self.event_count % 1000 == 0:
            self.prune_events()

    def prune_events(self, keep=100000):
        if keep < 1:
            raise ValueError("At least one replay event must be retained.")
        with self.lock, self.db:
            row = self.db.execute("SELECT seq FROM events ORDER BY seq DESC LIMIT 1 OFFSET ?", (keep,)).fetchone()
            if not row:
                return 0
            cutoff = row[0]
            self.db.execute("""INSERT INTO event_floors SELECT thread_id,MAX(seq) FROM events WHERE seq<=? GROUP BY thread_id
                ON CONFLICT(thread_id) DO UPDATE SET seq=MAX(seq,excluded.seq)""", (cutoff,))
            return self.db.execute("DELETE FROM events WHERE seq<=?", (cutoff,)).rowcount

    def replay_expired(self, tid, after):
        return bool(self.rows("SELECT 1 FROM event_floors WHERE thread_id=? AND seq>?", (tid, after)))

    def close(self):
        with self.lock:
            self.db.close()


class CodexRPC:
    """One long-lived child; browser connection lifetimes never own this process."""
    def __init__(self, command, on_message, on_exit):
        self.on_message, self.on_exit = on_message, on_exit
        self.lock = threading.Lock()
        self.pending = {}
        self.seq = 0
        self.received = 0
        self.closed = False
        self.stderr = collections.deque(maxlen=40)
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.error_reader = threading.Thread(target=self._read_errors, daemon=True)
        self.reader.start()
        self.error_reader.start()

    def _write(self, message):
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method, params=None):
        with self.lock:
            self._write({"method": method, "params": params or {}})

    def reply(self, rid, result=None, error=None):
        with self.lock:
            self._write({"id": rid, **({"error": error} if error else {"result": result})})

    def call(self, method, params=None, timeout=45):
        with self.lock:
            if self.closed:
                raise APIError(503, "Codex has stopped. Restart the gateway on the server.")
            self.seq += 1
            rid = self.seq
            future = concurrent.futures.Future()
            self.pending[rid] = future
            try:
                self._write({"id": rid, "method": method, "params": params or {}})
            except (OSError, ValueError):
                self.pending.pop(rid, None)
                raise APIError(503, "Unable to connect to Codex.")
        try:
            result = future.result(timeout)
        except concurrent.futures.TimeoutError:
            # A timed-out command may still have executed. Never retry mutations automatically.
            raise APIError(504, "Codex timed out. The operation may have been submitted. Check the conversation before sending again.")
        finally:
            with self.lock:
                self.pending.pop(rid, None)
        if "error" in result:
            raise APIError(502, str(result["error"].get("message", "Codex RPC error")),
                           transport_seq=result.get("_transportSeq", 0))
        value = result.get("result", {})
        if isinstance(value, dict):
            value["_transportSeq"] = result.get("_transportSeq", 0)
        return value

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.received += 1
                message["_transportSeq"] = self.received
                if "method" in message:
                    self.on_message(message)
                else:
                    with self.lock:
                        future = self.pending.get(message.get("id"))
                        if future and not future.done():
                            future.set_result(message)
        finally:
            with self.lock:
                self.closed = True
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(APIError(503, "The Codex process exited."))
            self.on_exit()

    def _read_errors(self):
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.reader.join(5)
        self.error_reader.join(5)
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            pipe.close()


class Gateway:
    def __init__(self, state, workspaces, command, sandbox="workspace-write", approval="on-request", rpc_factory=None, shared=False):
        self.shared = shared
        self.command = command
        self.rpc_factory = rpc_factory or CodexRPC
        self.store = Store(state / "gateway.sqlite3")
        self.workspaces = [str(Path(p).resolve(strict=True)) for p in workspaces]
        if not all(Path(p).is_dir() for p in self.workspaces):
            raise ValueError("Workspace must be a directory")
        self.sandbox, self.approval = sandbox, approval
        self.op_lock = threading.RLock()
        self.request_lock = threading.RLock()
        self.requests = {}
        self.loaded = set()
        self.online = False
        self.wake = threading.Condition()
        if not shared:
            self._interrupt_state("The gateway restarted. The previous turn was not automatically resumed; you can send a new message.")
        self.connect()

    def connect(self):
        epoch = self.event_epoch = secrets.token_hex(16)
        self.rpc = self.rpc_factory(self.command,
                                   lambda message: self.on_message({**message, "_transportEpoch": epoch}), self.on_exit)
        try:
            self.info = self.rpc.call("initialize", {
                # This UI drives the user's actual Codex CLI app-server. Native
                # originator is process-wide and fixed by its first client;
                # preserve the CLI identity used by CLI-only API providers.
                "clientInfo": {"name": "codex_cli_rs", "title": "Codex Remote (Codex CLI)", "version": __version__},
                "capabilities": {"experimentalApi": True},
            })
            self.rpc.notify("initialized")
            self.online = True
        except Exception:
            self.rpc.close()
            raise

    def _interrupt_state(self, reason):
        for row in self.store.rows("SELECT id FROM threads WHERE turn_id IS NOT NULL"):
            self.store.event(row["id"], "gateway/interrupted", {"message": reason})
        self.store.execute("UPDATE threads SET status='interrupted', turn_id=NULL WHERE turn_id IS NOT NULL")

    def on_exit(self):
        self.online = False
        with self.request_lock:
            self.requests.clear()
        self.loaded.clear()
        if not self.shared:
            self._interrupt_state("Codex exited. Restart the gateway on the server to continue.")
        with self.wake:
            self.wake.notify_all()

    def thread(self, tid):
        rows = self.store.rows("SELECT * FROM threads WHERE id=?", (tid,))
        if not rows or rows[0]["cwd"] not in self.workspaces:
            raise APIError(404, "Conversation not found, or its working directory is not enabled.")
        return rows[0]

    def list_threads(self):
        return [x for x in self.store.rows("SELECT * FROM threads ORDER BY created DESC")
                if x["cwd"] in self.workspaces]

    def options(self, cwd):
        return {"cwd": cwd, "sandbox": self.sandbox, "approvalPolicy": self.approval,
                "approvalsReviewer": "user"}

    def new_thread(self, cwd, title):
        if cwd not in self.workspaces:
            raise APIError(400, "Choose a configured working directory.")
        with self.op_lock:
            result = self.rpc.call("thread/start", self.options(cwd))
            tid = result["thread"]["id"]
            self.store.execute("INSERT INTO threads(id,title,cwd,created) VALUES(?,?,?,?)",
                               (tid, title[:100] or "New conversation", cwd, time.time()))
            self.loaded.add(tid)
            return self.thread(tid)

    def send(self, tid, text, submission):
        if not isinstance(text, str) or not text.strip() or len(text) > 50000:
            raise APIError(400, "Messages must contain 1–50,000 characters.")
        if not isinstance(submission, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", submission):
            raise APIError(400, "A valid message ID is required.")
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.op_lock:
            row = self.thread(tid)
            previous = self.store.rows("SELECT * FROM submissions WHERE id=?", (submission,))
            if previous:
                prev = previous[0]
                if prev["thread_id"] != tid or prev["digest"] != digest:
                    raise APIError(409, "The message ID was already used for another request.")
                if prev["result"] is None:
                    raise APIError(409, "This message was submitted but its result is uncertain. Check the conversation before proceeding.")
                return json.loads(prev["result"])
            if row["turn_id"]:
                raise APIError(409, "A task is still running. Wait for it to finish or stop it first.")
            if tid not in self.loaded:
                self.rpc.call("thread/resume", {"threadId": tid, **self.options(row["cwd"])})
                self.loaded.add(tid)
            self.store.execute("INSERT INTO submissions(id,thread_id,digest) VALUES(?,?,?)",
                               (submission, tid, digest))
            self.store.execute("UPDATE threads SET status='running', turn_id='starting' WHERE id=?", (tid,))
            try:
                result = self.rpc.call("turn/start", {
                    "threadId": tid, "input": [{"type": "text", "text": text}],
                    "clientUserMessageId": submission,
                })
            except APIError as error:
                # A timeout is ambiguous: preserve the starting state to prevent duplicate execution.
                if error.status != 504:
                    self.store.execute("UPDATE threads SET status='failed', turn_id=NULL WHERE id=? AND turn_id='starting'", (tid,))
                self.store.event(tid, "gateway/error", {"message": str(error)})
                raise
            turn = result.get("turn", {})
            self.store.execute("UPDATE threads SET turn_id=? WHERE id=? AND turn_id='starting'",
                               (turn.get("id"), tid))
            if row["title"] == "New conversation":
                self.store.execute("UPDATE threads SET title=? WHERE id=?", (text.strip()[:40], tid))
            response = {"turnId": turn.get("id")}
            self.store.execute("UPDATE submissions SET result=? WHERE id=?", (json.dumps(response), submission))
            return response

    def interrupt(self, tid):
        row = self.thread(tid)
        if row["turn_id"] == "starting":
            raise APIError(409, "Still confirming the turn ID. Try again shortly.")
        if row["turn_id"]:
            self.rpc.call("turn/interrupt", {"threadId": tid, "turnId": row["turn_id"]})
        return {"ok": True}

    def on_message(self, message):
        method, params = message["method"], message.get("params") or {}
        tid = params.get("threadId")
        known = bool(tid and self.store.rows("SELECT id FROM threads WHERE id=?", (tid,)))
        if "id" in message:
            if self.shared and not known:
                return  # Requests owned by other clients must not be rejected by this gateway.
            if not known or method not in APPROVALS:
                # Unknown requests fail explicitly instead of hanging or approving silently.
                if not self.shared:
                    self.rpc.reply(message["id"], error={"code": -32601, "message": "This mobile client does not support this request."})
                if known:
                    self.store.event(tid, "gateway/error", {"message": "Unsupported interaction: " + method})
                return
            key = secrets.token_urlsafe(18)
            with self.request_lock:
                self.requests[key] = {"key": key, "rpcId": message["id"], "method": method, "params": params}
            self.store.event(tid, "gateway/request", {"key": key, "method": method, "params": params})
        elif known:
            if method == "turn/started":
                self.store.execute("UPDATE threads SET status='running', turn_id=? WHERE id=?", (params["turn"]["id"], tid))
            elif method == "turn/completed":
                self.store.execute("UPDATE threads SET status=?, turn_id=NULL WHERE id=?", (params["turn"].get("status", "completed"), tid))
                with self.request_lock:
                    self.requests = {k: v for k, v in self.requests.items() if v["params"].get("turnId") != params["turn"]["id"]}
            elif method == "serverRequest/resolved":
                with self.request_lock:
                    self.requests = {k: v for k, v in self.requests.items() if v["rpcId"] != params.get("requestId")}
            # Persist only per-thread events. Account/auth/config notifications never reach browsers.
            if method.startswith(("item/", "turn/", "thread/", "serverRequest/")) or method in ("error", "warning"):
                self.store.event(tid, method, params, transport_seq=message.get("_transportSeq", 0),
                                 transport_epoch=message.get("_transportEpoch"))
        with self.wake:
            self.wake.notify_all()

    def pending_requests(self, tid):
        with self.request_lock:
            return [{k: v for k, v in r.items() if k != "rpcId"}
                    for r in self.requests.values() if r["params"]["threadId"] == tid]

    def respond(self, key, body):
        with self.request_lock:
            request = self.requests.get(key)
            if not request:
                raise APIError(409, "This request was already handled or has expired.")
            self.thread(request["params"]["threadId"])
            method = request["method"]
            if method == "item/tool/requestUserInput":
                answers = body.get("answers")
                if not isinstance(answers, dict):
                    raise APIError(400, "Enter an answer.")
                expected = {q["id"] for q in request["params"]["questions"]}
                if set(answers) != expected or any(not isinstance(v, str) or not v.strip() or len(v) > 10000 for v in answers.values()):
                    raise APIError(400, "Provide a valid answer for each question.")
                result = {"answers": {k: {"answers": [v]} for k, v in answers.items()}}
            else:
                decision = body.get("decision")
                if decision not in ("accept", "decline"):
                    raise APIError(400, "Choose Allow once or Deny.")
                if method == "item/permissions/requestApproval":
                    result = {"permissions": request["params"].get("permissions", {}) if decision == "accept" else {}, "scope": "turn"}
                else:
                    result = {"decision": decision}
            self.rpc.reply(request["rpcId"], result=result)
            self.requests.pop(key, None)
            self.store.event(request["params"]["threadId"], "gateway/resolved", {"key": key})
        with self.wake:
            self.wake.notify_all()
        return {"ok": True}


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, gateway, token, origin, handler=None):
        super().__init__(address, handler or Handler)
        self.gateway, self.token, self.origin = gateway, token, origin.rstrip("/")
        self.sessions = {}
        self.auth_lock = threading.Lock()
        self.failures = collections.deque()
        self.stopping = threading.Event()
        self.stream_slots = threading.BoundedSemaphore(12)
        self.http_slots = threading.BoundedSemaphore(32)


    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if not isinstance(error, (ConnectionError, TimeoutError)):
            print("HTTP handler error: " + type(error).__name__, file=sys.stderr)

    def process_request(self, request, address):
        if not self.http_slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 5\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.http_slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.http_slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_):
        pass  # Do not log messages, credentials, cookies, or URL arguments.

    def headers_out(self, status, mime="application/json; charset=utf-8", length=None, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # KaTeX positions symbols via style attributes; scripts and stylesheets
        # remain same-origin. Model HTML is disabled and KaTeX trust is false.
        policy = "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if urlsplit(self.path).path == '/mermaid-frame.html':
            # Mermaid needs generated style elements. Keep them in an opaque
            # sandbox with no API/network access; the app only receives an image.
            policy = "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'; sandbox allow-scripts"
        self.send_header("Content-Security-Policy", policy)
        if length is not None:
            self.send_header("Content-Length", str(length))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def json(self, status, payload, extra=None):
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            payload = {**payload, "error": translate(payload["error"], locale(self.headers.get("Accept-Language", "")))}
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.headers_out(status, length=len(data), extra=extra)
        self.wfile.write(data)

    def cookie_value(self):
        try:
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return cookie["cr_session"].value if "cr_session" in cookie else ""
        except http.cookies.CookieError:
            return ""

    def authorized(self):
        now = time.time()
        with self.server.auth_lock:
            self.server.sessions = {k: v for k, v in self.server.sessions.items() if v > now}
            return self.server.sessions.get(self.cookie_value(), 0) > now

    def check_origin(self):
        # The opaque Mermaid sandbox legitimately requests only these public
        # scripts with a cross-site/null origin. It still cannot call any API.
        renderer_script = self.command == 'GET' and urlsplit(self.path).path in (
            '/mermaid-frame.js', '/vendor/mermaid/mermaid.min.js')
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin and not renderer_script:
            raise APIError(403, "Origin mismatch. Use the configured access URL.")
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            raise APIError(403, "Host does not match the configured origin.")
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not renderer_script:
            raise APIError(403, "Cross-site request rejected.")

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError(400, "Invalid request length.")
        if not 0 < size <= 262144:
            raise APIError(413, "Request body is empty or too large.")
        if self.headers.get_content_type() != "application/json":
            raise APIError(415, "A JSON request is required.")
        try:
            result = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeDecodeError):
            raise APIError(400, "Invalid JSON.")
        if not isinstance(result, dict):
            raise APIError(400, "A JSON object is required.")
        return result

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def handle_request(self, post):
        try:
            self.check_origin()
            path = urlsplit(self.path).path
            if not post and path == "/healthz":
                return self.json(200, {"ok": True})
            body = self.body() if post else {}
            if post and path == "/api/login":
                return self.login(body)
            if path.startswith("/api/"):
                if not self.authorized():
                    raise APIError(401, "Sign in first.")
                return self.api(path, post, body)
            if post:
                raise APIError(404, "API endpoint not found.")
            files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/sw.js": "sw.js",
                     "/i18n.js": "i18n.js", "/locales/zh-CN.js": "locales/zh-CN.js",
                     "/features.js": "features.js", "/theme.js": "theme.js", "/activity.js": "activity.js", "/markdown.js": "markdown.js",
                     "/commands.js": "commands.js", "/commands.css": "commands.css",
                     "/diagrams.js": "diagrams.js", "/mermaid-frame.html": "mermaid-frame.html", "/mermaid-frame.js": "mermaid-frame.js",
                     "/manifest.webmanifest": "manifest.webmanifest", "/icon.svg": "icon.svg",
                     "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png"}
            if path in VENDOR_FILES:
                files[path] = path.lstrip("/")
            if path not in files:
                raise APIError(404, "Page not found.")
            file = STATIC / files[path]
            data = file.read_bytes()
            mime = mimetypes.guess_type(file)[0] or "application/octet-stream"
            self.headers_out(200, mime, len(data))
            self.wfile.write(data)
        except APIError as error:
            from codex_remote.diagnostics import explain_error, storage_error, writer_conflict
            self.close_connection = True
            status = 507 if storage_error(error) else 409 if writer_conflict(error) else error.status
            self.json(status, {"error": explain_error(error)})
        except (sqlite3.Error, OSError) as error:
            from codex_remote.diagnostics import explain_error, storage_error
            self.close_connection = True
            if storage_error(error):
                self.json(507, {"error": explain_error(error)})
            elif isinstance(error, (BrokenPipeError, ConnectionResetError, TimeoutError)):
                return
            else:
                self.json(500, {"error": "Gateway storage or connection error. Check server health."})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.json(500, {"error": "Internal gateway error. Check server health."})

    def login(self, body):
        now = time.time()
        supplied = body.get("token", "")
        if not isinstance(supplied, str) or len(supplied) > 1000:
            raise APIError(400, "Invalid access token.")
        with self.server.auth_lock:
            failures = self.server.failures
            while failures and failures[0] < now - 60:
                failures.popleft()
            if not hmac.compare_digest(supplied.encode(), self.server.token.encode()):
                if len(failures) >= 10:
                    raise APIError(429, "Too many attempts. Try again in one minute.")
                failures.append(now)
                raise APIError(401, "Incorrect access token.")
            session = secrets.token_urlsafe(32)
            self.server.sessions[session] = now + 7 * 86400
        secure = "; Secure" if self.server.origin.startswith("https:") else ""
        self.json(200, {"ok": True}, {"Set-Cookie": f"cr_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800{secure}"})

    def api(self, path, post, body):
        g = self.server.gateway
        if post and path == "/api/logout":
            with self.server.auth_lock:
                self.server.sessions.pop(self.cookie_value(), None)
            return self.json(200, {"ok": True}, {"Set-Cookie": "cr_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        if not post and path == "/api/bootstrap":
            return self.json(200, {"online": g.online, "workspaces": g.workspaces, "sandbox": g.sandbox,
                                   "threads": g.list_threads()})
        if post and path == "/api/threads":
            title = body.get("title", "New conversation")
            if not isinstance(title, str):
                raise APIError(400, "Invalid conversation name.")
            return self.json(201, g.new_thread(body.get("cwd"), title))
        match = re.fullmatch(r"/api/threads/([A-Za-z0-9_-]+)/([a-z]+)", path)
        if match:
            tid, action = match.groups()
            g.thread(tid)
            if post and action == "message":
                return self.json(200, g.send(tid, body.get("text"), body.get("submissionId")))
            if post and action == "interrupt":
                return self.json(200, g.interrupt(tid))
            if not post and action == "events":
                return self.events(tid)
        match = re.fullmatch(r"/api/requests/([A-Za-z0-9_-]+)/respond", path)
        if post and match:
            return self.json(200, g.respond(match[1], body))
        raise APIError(404, "API endpoint not found.")

    def events(self, tid):
        try:
            after = int(self.headers.get("Last-Event-ID") or parse_qs(urlsplit(self.path).query).get("after", [0])[0])
            if after < 0:
                raise ValueError()
        except ValueError:
            raise APIError(400, "Invalid event cursor.")
        if not self.server.stream_slots.acquire(blocking=False):
            raise APIError(429, "Too many connections. Close unused pages.")
        self.close_connection = True
        try:
            self.headers_out(200, "text/event-stream; charset=utf-8", extra={"X-Accel-Buffering": "no", "Connection": "close"})
            while not self.server.stopping.is_set() and self.authorized():
                with self.server.gateway.store.lock:
                    expired = self.server.gateway.store.replay_expired(tid, after)
                    rows = self.server.gateway.store.rows("SELECT seq,payload FROM events WHERE thread_id=? AND seq>? ORDER BY seq LIMIT 250", (tid, after))
                if expired:
                    self.wfile.write(b'event: reset\ndata: {"reason":"replay_expired"}\n\n')
                    self.wfile.flush()
                    return
                for row in rows:
                    self.wfile.write(f'id: {row["seq"]}\nevent: update\ndata: {row["payload"]}\n\n'.encode())
                    after = row["seq"]
                if len(rows) < 250:
                    g = self.server.gateway
                    snapshot = {"online": g.online, "thread": g.thread(tid), "requests": g.pending_requests(tid)}
                    self.wfile.write(("event: state\ndata: " + json.dumps(snapshot, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
                if len(rows) < 250:
                    with self.server.gateway.wake:
                        self.server.gateway.wake.wait(2)
        finally:
            self.server.stream_slots.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", action="append", required=True, help="Allowed working directory; repeat for multiple projects")
    parser.add_argument("--state", type=Path, default=Path.home() / ".local/state/codex-remote")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--origin", help="Exact browser origin, e.g. https://codex.example.com")
    parser.add_argument("--codex", default="codex", help="Codex executable path")
    parser.add_argument("--transport", choices=["shared", "isolated"], default="isolated", help="Private child process (default); shared requires explicit opt-in")
    parser.add_argument("--socket", help="Explicit shared app-server Unix socket path")
    parser.add_argument("--browse-existing", action="store_true", help="Read-only discovery of existing sessions; requires shared transport")
    parser.add_argument("--push-subject", help="Web Push contact, e.g. mailto:you@example.com or your HTTPS origin")
    parser.add_argument("--sandbox", choices=["read-only", "workspace-write", "danger-full-access"], default="workspace-write")
    parser.add_argument("--approval", choices=["on-request", "untrusted", "never"], default="on-request")
    args = parser.parse_args()
    if args.socket and args.transport != "shared":
        parser.error("--socket requires explicit --transport shared.")
    if args.browse_existing and args.transport != "shared":
        parser.error("--browse-existing requires --transport shared.")
    if args.host not in ("127.0.0.1", "localhost") and not args.origin:
        parser.error("Non-loopback listeners require --origin with the actual browser access URL.")
    origin = (args.origin or f"http://127.0.0.1:{args.port}").rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error("--origin must be http(s)://host[:port], without a path or credentials.")
    os.umask(0o077)
    args.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.state.chmod(0o700)
    lockfile = (args.state / "gateway.lock").open("a")
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("A gateway is already using this state directory.")
    token_file = args.state / "access-token"
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(32) + "\n")
    token = token_file.read_text().strip()
    token_file.chmod(0o600)
    if len(token) < 32:
        parser.error("Access tokens must contain at least 32 characters.")
    # Ensure feature modules share these exception and RPC classes when launched as a script.
    sys.modules.setdefault("codex_remote.server", sys.modules[__name__])
    from codex_remote.features import RemoteGateway
    from codex_remote.web import RemoteWebServer
    rpc_factory = None
    command = [args.codex, "app-server", "--listen", "stdio://"]
    if args.transport == "shared":
        from codex_remote.transport import SocketRPC
        rpc_factory = SocketRPC
        if args.socket:
            command = str(Path(args.socket).expanduser().resolve())
        else:
            version = json.loads(subprocess.check_output([args.codex, "app-server", "daemon", "version"], timeout=15))
            if version.get("status") != "running":
                parser.error("The shared Codex daemon is not running. This tool will not start or restart it. You can use the default isolated mode.")
            command = version["socketPath"]
    g = RemoteGateway(args.state, args.workspace, command, args.sandbox, args.approval, rpc_factory, args.transport == "shared", browse_existing=args.browse_existing)
    web = RemoteWebServer((args.host, args.port), g, token, origin)
    push_subject = args.push_subject or (origin if origin.startswith("https://") else None)
    if push_subject:
        if not push_subject.startswith(("mailto:", "https://")):
            parser.error("--push-subject must be a mailto: contact or HTTPS address.")
        from codex_remote.push import PushManager
        g.push = PushManager(g.store, args.state, push_subject)
    def stop(*_):
        web.stopping.set()
        threading.Thread(target=web.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Codex Remote: {origin}", flush=True)
    print(f"Access token saved in {token_file} (this is the gateway token, not a model API key).", flush=True)
    try:
        web.serve_forever(poll_interval=0.3)
    finally:
        web.stopping.set()
        web.server_close()
        g.close()
        lockfile.close()


if __name__ == "__main__":
    main()
