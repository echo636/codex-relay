"""Authenticated HTTP API for the complete mobile client."""
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, quote, urlsplit

from codex_remote.server import Handler, WebServer, APIError
from codex_remote.storage import clean_uploads
from codex_remote.i18n import locale
from codex_remote.commands import slash_input


class RemoteWebServer(WebServer):
    def __init__(self, address, gateway, token, origin):
        super().__init__(address, gateway, token, origin, RemoteHandler)
        gateway.store.execute("CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,label TEXT,created REAL,expires REAL)")


class RemoteHandler(Handler):
    def device_id(self):
        return hashlib.sha256(self.cookie_value().encode()).hexdigest()

    def authorized(self):
        return bool(self.server.gateway.store.rows("SELECT id FROM devices WHERE id=? AND expires>?", (self.device_id(), time.time())))

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
            sid = hashlib.sha256(session.encode()).hexdigest()
            self.server.gateway.store.execute("DELETE FROM devices WHERE expires<?", (now,))
            self.server.gateway.store.execute("INSERT INTO devices VALUES(?,?,?,?)", (sid, self.headers.get("User-Agent", "Browser")[:300], now, now + 7 * 86400))
        secure = "; Secure" if self.server.origin.startswith("https:") else ""
        self.json(200, {"ok": True}, {"Set-Cookie": f"cr_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800{secure}"})

    def body(self):
        if urlsplit(self.path).path != "/api/uploads":
            return super().body()
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 15 * 1024 * 1024:
                raise APIError(413, "Attachment request exceeds the size limit.")
            if self.headers.get_content_type() != "application/json":
                raise APIError(415, "A JSON request is required.")
            # Reject unauthenticated uploads before allocating the body.
            if not self.authorized():
                raise APIError(401, "Sign in first.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except ValueError:
            raise APIError(400, "Invalid attachment request.")

    def file_response(self, path, name, mime="application/octet-stream", inline=False):
        data = Path(path).read_bytes()
        disposition = ("inline" if inline else "attachment") + "; filename*=UTF-8''" + quote(name, safe="")
        self.headers_out(200, mime, len(data), {"Content-Disposition": disposition})
        self.wfile.write(data)

    def api(self, path, post, body):
        g = self.server.gateway
        query = parse_qs(urlsplit(self.path).query)
        if path == "/api/logout" and post:
            self.revoke(self.device_id())
            return self.json(200, {"ok": True}, {"Set-Cookie": "cr_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        if path == "/api/bootstrap" and not post:
            if g.push:
                g.store.execute("UPDATE push_subscriptions SET language=? WHERE device=?", (locale(self.headers.get("Accept-Language", "")), self.device_id()))
            notices = g.store.rows("SELECT * FROM notices WHERE seen=0 ORDER BY id DESC LIMIT 100")
            return self.json(200, {"online": g.online, "workspaces": g.workspaces, "sandbox": g.sandbox,
                                   "threads": g.list_threads(), "shared": g.shared, "notices": notices,
                                   "browseExisting": g.browse_existing,
                                   "backgroundError": g.background_error,
                                   "threadErrors": {tid: retry[2] for tid, retry in list(g.attach_retries.items()) if tid not in g.loaded},
                                   "pushPublicKey": g.push.public_key if g.push else None})
        if path == "/api/workspaces/directories" and not post:
            return self.json(200, g.workspace_directories(query.get("path", [""])[0],
                                   query.get("cursor", [None])[0], query.get("hidden") == ["1"]))
        if path == "/api/storage" and not post:
            total = g.store.rows("SELECT COUNT(*) AS files,COALESCE(SUM(size),0) AS bytes FROM uploads")[0]
            with g.op_lock:
                eligible = clean_uploads(g.store, g.upload_dir)
            return self.json(200, {**total, **eligible, "limitBytes": 1024**3})
        if path == "/api/storage/cleanup" and post:
            with g.op_lock:
                result = clean_uploads(g.store, g.upload_dir, apply=True)
            return self.json(200, result)
        if path == "/api/models" and not post:
            return self.json(200, {"data": g.models()})
        if path == "/api/discover" and post:
            return self.json(200, g.discover(bool(body.get("archived")), body.get("cursor"), bool(body.get("includeAutomation"))))
        if path == "/api/import" and post:
            return self.json(200, g.import_thread(body.get("threadId")))
        if path == "/api/uploads" and post:
            return self.json(201, g.upload(body.get("name"), body.get("data")))
        match = re.fullmatch(r"/api/uploads/([a-f0-9]{32})", path)
        if match and not post:
            upload = g.upload_file(match[1])
            return self.file_response(upload["path"], upload["name"], upload["mime"], upload["mime"].startswith("image/"))
        if path == "/api/files" and not post:
            entry = g.files(query.get("threadId", [""])[0], query.get("path", [""])[0])
            if entry["directory"]:
                return self.json(200, entry)
            if query.get("download") == ["1"]:
                return self.file_response(entry["path"], entry["name"])
            raw = Path(entry["path"]).read_bytes()
            try:
                content = raw[:1024 * 1024].decode("utf-8")
                binary = "\x00" in content
            except UnicodeDecodeError:
                content, binary = "", True
            return self.json(200, {"directory": False, "name": entry["name"], "content": "" if binary else content,
                                   "binary": binary, "truncated": len(raw) > 1024 * 1024})
        if path == "/api/devices" and not post:
            devices = g.store.rows("SELECT * FROM devices WHERE expires>? ORDER BY created DESC", (time.time(),))
            return self.json(200, {"devices": [{**d, "current": d["id"] == self.device_id()} for d in devices]})
        if path == "/api/devices/revoke" and post:
            self.revoke(body.get("id"))
            return self.json(200, {"ok": True})
        if path == "/api/notifications/seen" and post:
            before = body.get("before")
            if not isinstance(before, int):
                raise APIError(400, "Invalid notification ID.")
            g.store.execute("UPDATE notices SET seen=1 WHERE id<=?", (before,))
            return self.json(200, {"ok": True})
        if path == "/api/push/status" and not post:
            return self.json(200, {"configured": bool(g.push),
                                   "subscribed": bool(g.push and g.push.subscribed(self.device_id()))})
        if path == "/api/push/test" and post:
            if not g.push:
                raise APIError(503, "Web Push is not enabled. Configure an HTTPS origin first.")
            return self.json(200, g.push.test(self.device_id()))
        if path == "/api/push/subscribe" and post:
            if not g.push:
                raise APIError(503, "A Web Push contact is not configured on the server.")
            return self.json(200, g.push.subscribe(self.device_id(), body.get("subscription"), locale(self.headers.get("Accept-Language", ""))))
        if path == "/api/push/unsubscribe" and post:
            if g.push:
                g.push.unsubscribe(self.device_id())
            return self.json(200, {"ok": True})
        match = re.fullmatch(r"/api/threads/([A-Za-z0-9_-]+)/([a-z]+)", path)
        if match:
            tid, action = match.groups()
            g.thread(tid)
            if action == "goal" and not post:
                return self.json(200, g.get_goal(tid))
            if action == "permissions" and not post:
                return self.json(200, g.permissions(tid))
            if action == "command" and post:
                return self.json(200, g.run_command(tid, body))
            if action == "preview" and not post:
                return self.json(200, g.preview(tid))
            if action == "sync" and not post:
                return self.json(200, g.sync_thread(tid))
            if action == "image" and not post:
                picture = g.viewed_image(tid, query.get("itemId", [""])[0])
                disposition = "inline; filename*=UTF-8''" + quote(picture["name"], safe="")
                self.headers_out(200, picture["mime"], len(picture["data"]), {"Content-Disposition": disposition})
                self.wfile.write(picture["data"])
                return
            if action == "control" and post:
                return self.json(200, g.enable_control(tid))
            if action == "visibility" and post:
                return self.json(200, g.set_list_visibility(tid, body.get("visibility")))
            if action == "attach" and post:
                g.require_control(tid)
                g.attach(tid, bool(body.get("refresh")))
                return self.json(200, g.thread(tid))
            if action == "message" and post:
                if slash_input(body.get("text")) and body.get("literal") is not True:
                    raise APIError(400, "Use the command menu for slash commands. Refresh the page, or start with // to send literal text.")
                return self.json(200, g.send(tid, body.get("text", ""), body.get("submissionId"), body.get("mode", "send"),
                                            body.get("attachments"), body.get("model"), body.get("effort")))
            if action == "rename" and post:
                return self.json(200, g.rename(tid, body.get("title")))
            if action == "archive" and post:
                return self.json(200, g.archive(tid, bool(body.get("archived", True))))
            if action == "fork" and post:
                return self.json(201, g.fork(tid))
            if action == "history" and not post:
                return self.json(200, g.history(tid, query.get("cursor", [None])[0]))
            if action == "queue" and not post:
                return self.json(200, {"queue": g.queue_for(tid)})
            if action == "cancel" and post:
                return self.json(200, g.cancel_queue(tid, body.get("id")))
            if action == "export" and not post:
                turns, cursor = [], None
                while True:
                    page = g.history(tid, cursor)
                    turns = page["turns"] + turns
                    cursor = page["nextCursor"]
                    if not cursor:
                        break
                return self.json(200, {"thread": g.thread(tid), "turns": turns, "events": [json.loads(r["payload"]) for r in g.store.rows("SELECT payload FROM events WHERE thread_id=? ORDER BY seq", (tid,))]},
                                 {"Content-Disposition": 'attachment; filename="codex-' + tid + '.json"'})
        return super().api(path, post, body)

    def revoke(self, device):
        if not isinstance(device, str) or not re.fullmatch(r"[a-f0-9]{64}", device):
            raise APIError(400, "Invalid device ID.")
        self.server.gateway.store.execute("DELETE FROM devices WHERE id=?", (device,))
        if self.server.gateway.push:
            self.server.gateway.push.unsubscribe(device)
