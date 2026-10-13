"""Conversation lifecycle, shared-daemon sync, uploads, queues and notifications."""
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import stat
import sqlite3
import threading
import time

from codex_remote.storage import initialize_uploads, clean_uploads

from jsonschema import Draft202012Validator, ValidationError, SchemaError
from codex_remote.server import Gateway, APIError, APPROVALS
from codex_remote.commands import CommandMixin
from codex_remote.diagnostics import explain_error, writer_conflict, model_error_text
from codex_remote.conversation_list import visibility_fields

ELICITATION = "mcpServer/elicitation/request"
APPROVALS.add(ELICITATION)


class RemoteGateway(CommandMixin, Gateway):
    def __init__(self, state, workspaces, command, sandbox="workspace-write", approval="on-request",
                 rpc_factory=None, shared=False, background=True, browse_existing=False):
        self.state_path = state
        self.stopped = threading.Event()
        self.sync_lock = threading.RLock()
        self.hydrating = {}
        self.awaiting_materialization = set()
        self.model_cache = None
        self.push = None
        self.background_error = None
        self.attach_retries = {}
        self.browse_existing = browse_existing
        super().__init__(state, workspaces, command, sandbox, approval, rpc_factory, shared)
        with self.store.lock, self.store.db:
            columns = {r[1] for r in self.store.db.execute("PRAGMA table_info(threads)")}
            for name, declaration in {"archived": "INTEGER DEFAULT 0", "model": "TEXT", "effort": "TEXT",
                                      "provider": "TEXT", "source": "TEXT", "history_cursor": "TEXT",
                                      "selected": "INTEGER DEFAULT 0", "owner_transport": "TEXT",
                                      "native_status": "TEXT", "permission_state": "TEXT",
                                      "list_visibility": "TEXT DEFAULT 'auto'", "has_content": "INTEGER",
                                      "last_error": "TEXT", "retry_error": "TEXT"}.items():
                if name not in columns:
                    self.store.db.execute(f"ALTER TABLE threads ADD COLUMN {name} {declaration}")
            self.store.db.executescript("""
                CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY,thread_id TEXT,body TEXT,status TEXT,created REAL);
                CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,name TEXT,path TEXT,mime TEXT,size INTEGER);
                CREATE TABLE IF NOT EXISTS viewed_images(thread_id TEXT,item_id TEXT,path TEXT,PRIMARY KEY(thread_id,item_id));
                CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY AUTOINCREMENT,thread_id TEXT,kind TEXT,created REAL,seen INTEGER DEFAULT 0);
            """)
        initialize_uploads(self.store)
        self.upload_dir = state / "uploads"
        self.upload_dir.mkdir(mode=0o700, exist_ok=True)
        # Never silently resend a queued operation whose delivery was ambiguous at process loss.
        self.store.execute("UPDATE queue SET status='uncertain' WHERE status='sending'")
        self.worker = threading.Thread(target=self._background, daemon=True)
        if background:
            self.worker.start()

    def allowed_cwd(self, cwd):
        try:
            path = Path(cwd).resolve(strict=True)
            return path.is_dir() and any(path == Path(root) or Path(root) in path.parents for root in self.workspaces)
        except (ValueError, OSError, TypeError, RuntimeError):
            return False

    def workspace_path(self, cwd):
        if not isinstance(cwd, str) or not cwd or len(cwd) > 4096 or "\x00" in cwd:
            raise APIError(400, "Enter an absolute directory path on the server.")
        try:
            path = Path(cwd).expanduser()
            if not path.is_absolute():
                raise APIError(400, "Use a server directory path starting with / or ~/.")
            path = path.resolve(strict=True)
            if not path.is_dir():
                raise APIError(400, "This path is a file. Choose a directory.")
        except FileNotFoundError:
            raise APIError(400, "Directory not found. Check the server path.")
        except PermissionError:
            raise APIError(403, "The server user cannot access this directory.")
        except (OSError, ValueError, RuntimeError):
            raise APIError(400, "Unable to read the directory. Check the path or symlink.")
        if not self.allowed_cwd(str(path)):
            raise APIError(400, "This directory is outside the allowed workspaces. Add it to the server --workspace configuration first.")
        return path

    def workspace_directories(self, cwd, cursor=None, hidden=False):
        path = self.workspace_path(cwd)
        try:
            offset = int(cursor or 0)
            if not 0 <= offset <= 1000000:
                raise ValueError()
        except (ValueError, TypeError):
            raise APIError(400, "Invalid directory pagination parameters.")
        entries, next_cursor = [], None
        try:
            # Read only this directory level, in bounded pages. Never crawl projects.
            with os.scandir(path) as children:
                for index, child in enumerate(children):
                    if index < offset:
                        continue
                    if hidden or not child.name.startswith("."):
                        try:
                            if child.is_dir() and (not child.is_symlink() or self.allowed_cwd(child.path)):
                                entries.append({"name": child.name, "path": child.path})
                        except (OSError, RuntimeError):
                            pass  # An unreadable or disappearing entry must not break navigation.
                    if len(entries) >= 200 or index - offset >= 999:
                        next_cursor = str(index + 1)
                        break
        except PermissionError:
            raise APIError(403, "The server user cannot list this directory. Enter an accessible subdirectory directly.")
        except OSError:
            raise APIError(400, "Directory temporarily unavailable. Check its mount or choose another directory.")
        parent = str(path.parent) if path.parent != path and self.allowed_cwd(str(path.parent)) else None
        return {"path": str(path), "parent": parent, "entries": sorted(entries, key=lambda item: item["name"].casefold()),
                "nextCursor": next_cursor}

    def thread(self, tid):
        rows = self.store.rows("SELECT * FROM threads WHERE id=?", (tid,))
        if not rows or not self.allowed_cwd(rows[0]["cwd"]):
            raise APIError(404, "Conversation not found, or its directory is outside the allowed workspaces.")
        if not self.shared and rows[0]["owner_transport"] != "isolated":
            raise APIError(403, "Isolated mode only controls conversations created here, not terminal conversations.")
        return self.public_thread(rows[0])

    def public_thread(self, row):
        return {**row, **visibility_fields(row), "read_only": self.shared and not bool(row["selected"])}

    def set_list_visibility(self, tid, visibility):
        self.thread(tid)
        if visibility not in ('auto', 'show', 'hide'):
            raise APIError(400, "Invalid conversation visibility setting.")
        # This preference belongs to the web list, not to the native conversation.
        self.store.execute("UPDATE threads SET list_visibility=? WHERE id=?", (visibility, tid))
        return self.thread(tid)

    def require_control(self, tid):
        row = self.thread(tid)
        if row["read_only"]:
            raise APIError(409, "Connect to this conversation first by selecting it or sending a message.")
        return row

    def list_threads(self):
        return [self.public_thread(r) for r in self.store.rows("SELECT * FROM threads ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END,created DESC")
                if self.allowed_cwd(r["cwd"]) and (self.shared or r["owner_transport"] == "isolated")]

    def _adopt(self, thread, archived=False):
        if not self.allowed_cwd(thread.get("cwd")):
            raise APIError(403, "This conversation is outside the allowed workspaces.")
        tid = thread["id"]
        title = thread.get("name") or thread.get("preview") or "New conversation"
        has_content = None
        if 'preview' in thread:
            has_content = int(bool((thread.get('preview') or '').strip()))
        if any(item.get('type') == 'userMessage' for turn in thread.get('turns', []) for item in turn.get('items', [])):
            has_content = 1
        self.store.execute("""INSERT INTO threads(id,title,cwd,created,archived,model,effort,provider,source,has_content)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,archived=excluded.archived,
            model=COALESCE(excluded.model,threads.model),
            effort=COALESCE(excluded.effort,threads.effort),
            provider=COALESCE(excluded.provider,threads.provider),source=excluded.source,
            has_content=CASE WHEN threads.has_content=1 THEN 1 ELSE COALESCE(excluded.has_content,threads.has_content) END""",
            (tid, title[:100], thread["cwd"], thread.get("createdAt", time.time()), int(archived),
             thread.get("model"), thread.get("reasoningEffort"), thread.get("modelProvider"),
             json.dumps(thread.get("source", "appServer")), has_content))
        runtime = thread.get("status", {})
        runtime = runtime.get("type") if isinstance(runtime, dict) else runtime
        if runtime and tid not in self.loaded:
            status = {"active": "running", "idle": "idle", "notLoaded": "notLoaded", "systemError": "failed"}.get(runtime, "idle")
            self.store.execute("UPDATE threads SET native_status=?,status=? WHERE id=?", (runtime, status, tid))
        return tid

    def discover(self, archived=False, cursor=None, include_automation=False):
        if not self.shared:
            return {"ids": [r["id"] for r in self.list_threads() if bool(r["archived"]) == archived], "nextCursor": None}
        # Read the indexed metadata only: exact cwd filters omit child project directories.
        # No JSONL repair scan, resume, subscription or configuration change is performed here.
        result = self.rpc.call("thread/list", {"limit": 100, "cursor": cursor, "useStateDbOnly": True,
                                               "sortKey": "updated_at", "archived": archived, "modelProviders": [],
                                               "sourceKinds": ["cli", "vscode", "appServer"] + (["exec"] if include_automation else [])})
        found = []
        for thread in result.get("data", []):
            if self.allowed_cwd(thread.get("cwd")):
                tid = self._adopt(thread, archived)
                found.append(tid)
        if not archived and cursor is None:
            loaded_cursor = None
            while True:
                page = self.rpc.call("thread/loaded/list", {"cursor": loaded_cursor, "limit": 100})
                for tid in page.get("data", []):
                    if tid in found:
                        continue
                    try:
                        meta = self.rpc.call("thread/read", {"threadId": tid, "includeTurns": False})["thread"]
                        if self.allowed_cwd(meta.get("cwd")) and not meta.get("ephemeral"):
                            self._adopt(meta)
                            found.append(tid)
                    except APIError:
                        continue
                loaded_cursor = page.get("nextCursor")
                if not loaded_cursor:
                    break
        return {"ids": found, "nextCursor": result.get("nextCursor")}

    def import_thread(self, tid):
        if not self.shared:
            raise APIError(403, "Isolated mode does not connect to existing terminal conversations. Browsers can share new conversations created here.")
        if not isinstance(tid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", tid):
            raise APIError(400, "Invalid conversation ID.")
        thread = self.rpc.call("thread/read", {"threadId": tid, "includeTurns": False})["thread"]
        self._adopt(thread)
        return self.thread(tid)

    def preview(self, tid):
        self.thread(tid)
        meta = self.rpc.call("thread/read", {"threadId": tid, "includeTurns": False})["thread"]
        self._adopt(meta, bool(self.thread(tid)["archived"]))
        page = self.history(tid, limit=10)
        return {"thread": self.thread(tid), "online": self.online, **page}

    def enable_control(self, tid):
        self.thread(tid)
        # notLoaded is an ordinary saved session, not evidence of another owner.
        # Codex's writer lock rejects a resume if an independent process owns it.
        # Never remove that lock, signal the owner, or retry with different config.
        try:
            self.attach(tid)
        except APIError as error:
            if writer_conflict(error):
                raise APIError(409, explain_error(error)) from error
            raise
        return self.thread(tid)

    def history(self, tid, cursor=None, limit=30):
        self.thread(tid)
        try:
            result = self.rpc.call("thread/turns/list", {"threadId": tid, "limit": limit, "sortDirection": "desc",
                                                         "itemsView": "full", "cursor": cursor})
        except APIError as error:
            # Codex 0.160 hasn't created the durable history until the first user
            # message. This is a valid empty thread, not a failed conversation.
            empty_message = f"thread {tid} is not materialized yet; thread/turns/list is unavailable before first user message"
            if error.status != 502 or cursor is not None or str(error) != empty_message:
                raise
            # Use the error response's sequence, not the reader's current counter:
            # a first turn may already be streaming while this RPC is unwinding.
            return {"turns": [], "nextCursor": None, "snapshotSeq": error.transport_seq, "materialized": False}
        turns = list(reversed(result.get("data", [])))
        if any(item.get('type') == 'userMessage' for turn in turns for item in turn.get('items', [])):
            self.store.execute("UPDATE threads SET has_content=1 WHERE id=?", (tid,))
        self._remember_images(tid, turns)
        return {"turns": turns, "nextCursor": result.get("nextCursor"), "snapshotSeq": result.get("_transportSeq", 0)}

    def sync_thread(self, tid):
        self.require_control(tid)
        with self.op_lock:
            epoch = self.event_epoch
            # Start the replay window BEFORE reading native history. Its transport
            # watermark identifies events already included, even if their database
            # writes race this request. Never advance straight to the newest event.
            after = self.store.rows("SELECT MAX(COALESCE((SELECT MAX(seq) FROM events WHERE thread_id=?),0), COALESCE((SELECT seq FROM event_floors WHERE thread_id=?),0)) AS seq", (tid, tid))[0]["seq"]
            page = self.history(tid, limit=10)
            if epoch != self.event_epoch or not page["snapshotSeq"]:
                raise APIError(503, "The connection changed during sync. Reconnect to the conversation.")
            return {**page, "after": after, "transportEpoch": epoch}

    def attach(self, tid, force=False):
        self.thread(tid)
        with self.op_lock:
            if (tid in self.loaded or tid in self.awaiting_materialization) and not force:
                self.apply_default_permissions(tid)
                return
            with self.sync_lock:
                self.hydrating[tid] = []
            try:
                # Resume preserves model/cwd; the explicit gateway permission
                # preference is applied separately through the native settings API.
                deferred = False
                page = None
                try:
                    result = self.rpc.call("thread/resume", {"threadId": tid, "excludeTurns": True})
                except APIError as error:
                    # An existing in-memory empty thread cannot be resumed by ID
                    # until its first message creates the rollout. Allow that first
                    # message on the SAME ID; never create a replacement thread.
                    if not self.shared or error.status != 502 or str(error) != f"no rollout found for thread id {tid}":
                        raise
                    meta = self.rpc.call("thread/read", {"threadId": tid, "includeTurns": False})["thread"]
                    page = self.history(tid)
                    if page.get("materialized") is not False or meta.get("status", {}).get("type") not in ("idle", "active"):
                        raise error
                    result = {"thread": meta, "_transportSeq": page["snapshotSeq"]}
                    deferred = True
                thread = result["thread"]
                metadata_seq = result.get('_transportSeq', 0)
                if thread.get("cwd") and not self.allowed_cwd(thread["cwd"]):
                    raise APIError(403, "The conversation directory is no longer in an allowed workspace.")
                turns = thread.get("turns", [])
                cursor = None
                if not deferred:
                    # Hydrate only the visible tail. Resuming a long CLI thread
                    # with its entire history can block every client for seconds.
                    page = page or self.history(tid, limit=10)
                    turns, cursor = page["turns"], page["nextCursor"]
                    result["_transportSeq"] = page["snapshotSeq"]
                active = next((t["id"] for t in reversed(turns) if t.get("status") == "inProgress"), None)
                latest = turns[-1] if turns else {}
                status = "running" if active else latest.get('status', 'idle')
                if status not in ('running', 'completed', 'failed', 'interrupted'):
                    status = 'idle'
                runtime = thread.get("status", {})
                runtime = runtime.get("type") if isinstance(runtime, dict) else runtime
                if page and page.get('materialized') is not False:
                    runtime = 'active' if active else 'idle'
                if runtime == "active" and not active:
                    # Avoid inventing a turn ID; authoritative history will be refreshed next cycle.
                    status = "running"
                self.store.execute("UPDATE threads SET status=?,turn_id=?,model=?,effort=?,history_cursor=?,native_status=? WHERE id=?",
                                   (status, active, result.get("model", thread.get("model")),
                                    result.get("reasoningEffort", thread.get("reasoningEffort")), cursor, runtime or ("active" if active else "idle"), tid))
                self.store.execute("UPDATE threads SET selected=1,permission_state=NULL WHERE id=?", (tid,))
                self.store.execute("UPDATE threads SET last_error=?,retry_error=NULL WHERE id=?",
                                   (model_error_text(latest.get('error')), tid))
                self.remember_permissions(tid, result)
                if deferred:
                    self.awaiting_materialization.add(tid)
                else:
                    self.awaiting_materialization.discard(tid)
                    self.loaded.add(tid)
                with self.sync_lock:
                    self._remember_images(tid, turns)
                    self.store.event(tid, "gateway/snapshot", {"turns": turns, "nextCursor": cursor})
                    buffered = self.hydrating.pop(tid, [])
                    # Snapshot supersedes notifications already received before the resume response.
                    for message in buffered:
                        # The turns page covers transcript events, while thread
                        # settings/name/status are only covered by the earlier resume.
                        watermark = metadata_seq if message.get('method', '').startswith('thread/') else result.get('_transportSeq', 0)
                        if message.get("_transportSeq", 0) > watermark:
                            self.on_message(message)
            finally:
                with self.sync_lock:
                    self.hydrating.pop(tid, None)
            with self.wake:
                self.wake.notify_all()
            self.apply_default_permissions(tid)
            self.attach_retries.pop(tid, None)

    def _connect_after_first_message(self, tid):
        if tid in self.awaiting_materialization:
            try:
                self.attach(tid, force=True)
            except APIError:
                # Submission already succeeded: never turn a sync failure into an
                # ambiguous send or retry it. The worker retries subscription only.
                self.store.event(tid, "gateway/error", {"message": "Message sent. Restoring live sync; do not send it again."})

    def on_exit(self):
        self.awaiting_materialization.clear()
        super().on_exit()

    def new_thread(self, cwd, title):
        cwd = str(self.workspace_path(cwd))
        with self.op_lock:
            result = self.rpc.call("thread/start", self.options(cwd))
            thread = result["thread"]
            thread.setdefault("cwd", cwd)
            thread["name"] = title or "New conversation"
            tid = self._adopt(thread)
            self.store.execute("UPDATE threads SET selected=1,owner_transport=? WHERE id=?",
                               ("shared" if self.shared else "isolated", tid))
            self.loaded.add(tid)
            self.remember_permissions(tid, result)
            if title and title != "New conversation":
                self.rpc.call("thread/name/set", {"threadId": tid, "name": title[:100]})
            return self.thread(tid)

    def models(self):
        if self.model_cache is None:
            data, cursor = [], None
            while True:
                page = self.rpc.call("model/list", {"limit": 100, "cursor": cursor})
                data.extend(page.get("data", []))
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            self.model_cache = data
        return self.model_cache

    def _inputs(self, text, attachments):
        if not isinstance(text, str) or len(text) > 50000 or not (text.strip() or attachments):
            raise APIError(400, "Enter a message or add an attachment. Text is limited to 50,000 characters.")
        if not isinstance(attachments, list) or len(attachments) > 8 or any(not isinstance(a, str) for a in attachments):
            raise APIError(400, "Up to 8 attachments are allowed per message.")
        inputs = [{"type": "text", "text": text}] if text else []
        for aid in attachments:
            rows = self.store.rows("SELECT * FROM uploads WHERE id=?", (aid,))
            if not rows:
                raise APIError(404, "Attachment not found.")
            upload = rows[0]
            self.store.execute("UPDATE upload_lifecycle SET used=1 WHERE id=?", (aid,))
            if upload["mime"].startswith("image/"):
                inputs.append({"type": "localImage", "path": upload["path"]})
            else:
                inputs.append({"type": "text", "text": "User attachment (server file): " + json.dumps({"id": aid, "name": upload["name"], "path": upload["path"]}, ensure_ascii=False)})
        return inputs

    def send(self, tid, text, submission, mode="send", attachments=None, model=None, effort=None):
        self.require_control(tid)
        attachments = attachments or []
        with self.op_lock:
            inputs = self._inputs(text, attachments)
        if mode not in ("auto", "send", "steer", "queue"):
            raise APIError(400, "Invalid send mode.")
        if not isinstance(submission, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", submission):
            raise APIError(400, "A valid message ID is required.")
        if model is not None and (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model)):
            raise APIError(400, "Invalid model name.")
        if effort is not None and effort not in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            raise APIError(400, "Invalid reasoning effort.")
        body = {"text": text, "attachments": attachments, "model": model, "effort": effort}
        digest = hashlib.sha256(json.dumps({**body, "mode": mode}, sort_keys=True).encode()).hexdigest()
        with self.op_lock:
            row = self.thread(tid)
            prior = self.store.rows("SELECT * FROM submissions WHERE id=?", (submission,))
            if prior:
                p = prior[0]
                if p["thread_id"] != tid or p["digest"] != digest:
                    raise APIError(409, "The message ID was already used for another request.")
                if p["result"] is None:
                    raise APIError(409, "The original request result is unconfirmed. Check conversation status.")
                return json.loads(p["result"])
            queued = self.store.rows("SELECT * FROM queue WHERE id=?", (submission,))
            if queued:
                if queued[0]["thread_id"] != tid or json.loads(queued[0]["body"]) != body:
                    raise APIError(409, "Queue ID conflict.")
                return {"queued": True, "id": submission}
            self.attach(tid)
            row = self.thread(tid)
            if mode == "queue":
                if self.shared:
                    if model or effort:
                        raise APIError(400, "Shared queues use the current conversation model. Set the model with a new turn first.")
                    self.store.execute("INSERT INTO submissions(id,thread_id,digest) VALUES(?,?,?)", (submission, tid, digest))
                    result = self.rpc.call("thread/queue/add", {"threadId": tid, "clientUserMessageId": submission, "input": inputs})
                    response = {"queued": True, "id": result["queuedSubmission"]["id"]}
                    self.store.execute("UPDATE submissions SET result=? WHERE id=?", (json.dumps(response), submission))
                    self._connect_after_first_message(tid)
                    return response
                self.store.execute("INSERT INTO queue VALUES(?,?,?,?,?)", (submission, tid, json.dumps(body), "pending", time.time()))
                return {"queued": True, "id": submission}
            active = row["turn_id"]
            if mode == "auto" and row["status"] == "running" and (not active or active == "starting"):
                turns = self.history(tid)["turns"]
                active = next((t["id"] for t in reversed(turns) if t.get("status") == "inProgress"), None)
                if not active:
                    raise APIError(409, "Syncing the running task. Try again shortly; your draft is retained.")
            delivery = ("steer" if active else "send") if mode == "auto" else mode
            if delivery == "steer" and (not active or active == "starting"):
                raise APIError(409, "There is no active turn to follow up on. Send a new message instead.")
            if delivery == "send" and (active or row["status"] == "running"):
                raise APIError(409, "A task is running. Choose Follow up now or After this turn.")
            params = {"threadId": tid, "input": inputs, "clientUserMessageId": submission}
            if delivery == "steer":
                if mode != "auto" and (model or effort):
                    raise APIError(400, "Follow-ups cannot change model or reasoning effort during a turn.")
                params["expectedTurnId"] = active
            else:
                if model:
                    params["model"] = model
                if effort:
                    params["effort"] = effort
            self.store.execute("INSERT INTO submissions(id,thread_id,digest) VALUES(?,?,?)", (submission, tid, digest))
            if delivery != "steer":
                self.store.execute("UPDATE threads SET status='running',turn_id='starting' WHERE id=?", (tid,))
            try:
                try:
                    result = self.rpc.call("turn/steer" if delivery == "steer" else "turn/start", params)
                except APIError as error:
                    # A turn can finish between the browser's send and the RPC.
                    # Fall back only after a definite rejection AND idle history;
                    # a timeout/disconnect can mean the input was already accepted.
                    inactive = any(s in str(error).lower() for s in ("no active turn", "no matching active turn"))
                    if mode != "auto" or delivery != "steer" or error.status != 502 or not inactive:
                        raise
                    if any(t.get("status") == "inProgress" for t in self.history(tid)["turns"]):
                        raise
                    delivery = "send"
                    params.pop("expectedTurnId", None)
                    if model: params["model"] = model
                    if effort: params["effort"] = effort
                    self.store.execute("UPDATE threads SET status='running',turn_id='starting' WHERE id=?", (tid,))
                    result = self.rpc.call("turn/start", params)
            except APIError as error:
                if delivery != "steer" and error.status != 504:
                    self.store.execute("UPDATE threads SET status='failed',turn_id=NULL WHERE id=? AND turn_id='starting'", (tid,))
                if mode == "auto" and error.status == 502:
                    # JSON-RPC explicitly rejected the submission. Keep the draft
                    # retryable; transport failures remain reserved as uncertain.
                    self.store.execute("DELETE FROM submissions WHERE id=? AND result IS NULL", (submission,))
                self.store.event(tid, "gateway/error", {"message": str(error)})
                raise
            turn_id = result.get("turnId") or result.get("turn", {}).get("id")
            self.store.execute("UPDATE threads SET turn_id=? WHERE id=? AND turn_id='starting'", (turn_id, tid))
            if row["title"] == "New conversation" and text.strip():
                self.rename(tid, text.strip()[:40])
            if delivery == "send" and (model or effort):
                self.store.execute("UPDATE threads SET model=COALESCE(?,model),effort=COALESCE(?,effort) WHERE id=?", (model, effort, tid))
            response = {"turnId": turn_id, "delivery": delivery}
            self.store.execute("UPDATE submissions SET result=? WHERE id=?", (json.dumps(response), submission))
            self._connect_after_first_message(tid)
            return response

    def queue_for(self, tid):
        self.thread(tid)
        if self.shared:
            entries, cursor = [], None
            while True:
                page = self.rpc.call("thread/queue/list", {"threadId": tid, "limit": 100, "cursor": cursor})
                for item in page.get("data", []):
                    entries.append({"id": item["id"], "thread_id": tid, "status": "pending", "body": {"text": "\n".join(i.get("text", "[Image attachment]") for i in item["input"])}})
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            return entries
        return [{**r, "body": json.loads(r["body"])} for r in self.store.rows("SELECT * FROM queue WHERE thread_id=? AND status IN ('pending','sending','uncertain','failed') ORDER BY created", (tid,))]

    def cancel_queue(self, tid, qid):
        self.require_control(tid)
        with self.op_lock:
            if self.shared:
                self.rpc.call("thread/queue/delete", {"threadId": tid, "queuedSubmissionId": qid})
                return {"ok": True}
            self.store.execute("UPDATE queue SET status='cancelled' WHERE id=? AND thread_id=? AND status IN ('pending','failed','uncertain')", (qid, tid))
        return {"ok": True}

    def interrupt(self, tid):
        self.require_control(tid)
        with self.op_lock:
            if self.shared:
                for entry in self.queue_for(tid):
                    self.cancel_queue(tid, entry["id"])
            self.store.execute("UPDATE queue SET status='cancelled' WHERE thread_id=? AND status='pending'", (tid,))
            return super().interrupt(tid)

    def rename(self, tid, title):
        self.require_control(tid)
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            raise APIError(400, "Conversation names must contain 1–100 characters.")
        self.rpc.call("thread/name/set", {"threadId": tid, "name": title.strip()})
        self.store.execute("UPDATE threads SET title=? WHERE id=?", (title.strip(), tid))
        return {"ok": True}

    def archive(self, tid, archived):
        row = self.require_control(tid)
        if row["turn_id"] or row["status"] == "running" or self.queue_for(tid):
            raise APIError(409, "Stop the running task first.")
        self.rpc.call("thread/archive" if archived else "thread/unarchive", {"threadId": tid})
        self.store.execute("UPDATE threads SET archived=? WHERE id=?", (int(archived), tid))
        self.loaded.discard(tid)
        return {"ok": True}

    def fork(self, tid):
        source = self.thread(tid)
        # Explicitly copying saved history does not require owning or resuming
        # the source. This also works when that thread's writer is unavailable.
        with self.op_lock:
            result = self.rpc.call("thread/fork", {"threadId": tid, "excludeTurns": True})
            fork = self._adopt(result["thread"])
            self.store.execute("UPDATE threads SET selected=1,owner_transport=? WHERE id=?",
                               ("shared" if self.shared else "isolated", fork))
            try:
                self.rename(fork, source["title"][:90] + " · History fork")
                self.attach(fork)
            except (APIError, OSError, sqlite3.Error) as error:
                # The copy already exists. Return its ID even if connecting it
                # fails, so the user can retry that copy without creating another.
                return {**self.thread(fork), "connection_error": explain_error(error)}
            return self.thread(fork)

    def upload(self, name, data):
        if not isinstance(name, str) or not name or len(name) > 200 or Path(name).name != name or any(ord(c) < 32 for c in name):
            raise APIError(400, "Invalid filename.")
        try:
            if not isinstance(data, str) or len(data) > 14 * 1024 * 1024:
                raise ValueError()
            raw = base64.b64decode(data, validate=True)
            if not raw or len(raw) > 10 * 1024 * 1024:
                raise ValueError()
        except ValueError:
            raise APIError(400, "Invalid attachment. Each file must be at most 10 MiB.")
        with self.op_lock:
            total = self.store.rows("SELECT COALESCE(SUM(size),0) AS size FROM uploads")[0]["size"]
            if total + len(raw) > 1024 * 1024 * 1024:
                raise APIError(413, "Attachment storage reached 1 GiB. Clean up unsent attachments older than 7 days in Devices and notifications. Submitted attachments are retained.")
            aid = secrets.token_hex(16)
            folder = self.upload_dir / aid
            folder.mkdir(mode=0o700)
            path = folder / name
            path.write_bytes(raw)
            path.chmod(0o600)
            mime = "application/octet-stream"
            if raw.startswith(b'\x89PNG\r\n\x1a\n'): mime = "image/png"
            elif raw.startswith(b'\xff\xd8\xff'): mime = "image/jpeg"
            elif raw.startswith(b'RIFF') and raw[8:12] == b'WEBP': mime = "image/webp"
            elif raw.startswith((b'GIF87a', b'GIF89a')): mime = "image/gif"
            self.store.execute("INSERT INTO uploads VALUES(?,?,?,?,?)", (aid, name, str(path), mime, len(raw)))
            self.store.execute("INSERT INTO upload_lifecycle VALUES(?,?,0)", (aid, time.time()))
            return {"id": aid, "name": name, "mime": mime, "size": len(raw)}

    def upload_file(self, aid):
        rows = self.store.rows("SELECT * FROM uploads WHERE id=?", (aid,))
        if not rows:
            raise APIError(404, "Attachment not found.")
        return rows[0]

    def _remember_images(self, tid, turns):
        for turn in turns:
            for item in turn.get("items", []):
                if item.get("type") == "imageView" and isinstance(item.get("path"), str) and item.get("id"):
                    self.store.execute("INSERT INTO viewed_images VALUES(?,?,?) ON CONFLICT(thread_id,item_id) DO UPDATE SET path=excluded.path",
                                       (tid, item["id"], item["path"]))

    def viewed_image(self, tid, item_id):
        row = self.thread(tid)
        if not isinstance(item_id, str) or not 1 <= len(item_id) <= 200:
            raise APIError(400, "Invalid image item.")
        def reference():
            return self.store.rows("SELECT path FROM viewed_images WHERE thread_id=? AND item_id=?", (tid, item_id))
        images = reference()
        # Older saved snapshots may predate the image index. Read history only;
        # viewing an image must never resume or take control of a conversation.
        cursor = None
        while not images:
            page = self.history(tid, cursor)
            images = reference()
            cursor = page["nextCursor"]
            if not cursor:
                break
        if not images:
            raise APIError(404, "No matching image-view item exists in this conversation.")
        try:
            path = (Path(row["cwd"]) / images[0]["path"]).resolve(strict=True)
            # Only the exact file referenced by an imageView event can be read,
            # including temporary images outside the project. No arbitrary path API.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise APIError(415, "The image path is not a regular file.")
                limit = 32 * 1024 * 1024
                if info.st_size > limit:
                    raise APIError(413, "Image exceeds the 32 MiB preview limit.")
                data = file.read(limit + 1)
            if len(data) > limit:
                raise APIError(413, "Image exceeds the 32 MiB preview limit.")
        except (OSError, ValueError, RuntimeError):
            raise APIError(404, "The original image was moved, removed or cannot be read.")
        mime = None
        if data.startswith(b'\x89PNG\r\n\x1a\n'): mime = "image/png"
        elif data.startswith(b'\xff\xd8\xff'): mime = "image/jpeg"
        elif data.startswith(b'RIFF') and data[8:12] == b'WEBP': mime = "image/webp"
        elif data.startswith((b'GIF87a', b'GIF89a')): mime = "image/gif"
        elif data.startswith(b'BM'): mime = "image/bmp"
        if not mime:
            raise APIError(415, "Image previews support PNG, JPEG, WebP, GIF and BMP.")
        return {"data": data, "mime": mime, "name": path.name}

    def files(self, tid, relative=""):
        row = self.thread(tid)
        root = Path(row["cwd"]).resolve()
        try:
            path = (root / relative).resolve(strict=True)
            if path != root and root not in path.parents:
                raise APIError(403, "Files outside the working directory are not accessible.")
            if path.is_dir():
                entries = []
                for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
                    if len(entries) == 1000:
                        break
                    resolved = child.resolve()
                    if root not in resolved.parents and resolved != root:
                        continue
                    entries.append({"name": child.name, "directory": child.is_dir(), "path": str(child.relative_to(root))})
                return {"directory": True, "path": str(path.relative_to(root)), "entries": entries, "limited": len(entries) == 1000}
            if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
                raise APIError(413, "File is unreadable or exceeds 10 MiB.")
            return {"directory": False, "path": str(path), "name": path.name}
        except (OSError, ValueError):
            raise APIError(404, "File or directory not found.")

    def on_message(self, message):
        method = message["method"]
        params = message.get("params") or {}
        tid = params.get("threadId")
        if self.shared and tid and tid not in self.loaded and tid not in self.hydrating and tid not in self.awaiting_materialization:
            return  # Merely listing a terminal session never makes this gateway its controller.
        with self.sync_lock:
            if tid in self.hydrating and "id" not in message:
                self.hydrating[tid].append(message)
                return
        super().on_message(message)
        if not tid or not self.store.rows("SELECT id FROM threads WHERE id=?", (tid,)):
            return
        if method == "thread/name/updated" and params.get("threadName"):
            self.store.execute("UPDATE threads SET title=? WHERE id=?", (params["threadName"], tid))
        # The base constructor may still be initializing the extended schema.
        if not hasattr(self, "upload_dir"):
            return
        if method == 'turn/started':
            self.store.execute('UPDATE threads SET last_error=NULL,retry_error=NULL WHERE id=?', (tid,))
        elif method == 'turn/completed':
            self.store.execute('UPDATE threads SET last_error=?,retry_error=NULL WHERE id=?',
                               (model_error_text(params.get('turn', {}).get('error')), tid))
        elif method == 'error':
            text = model_error_text(params.get('error') or params.get('message'))
            if params.get('willRetry'):
                self.store.execute('UPDATE threads SET retry_error=? WHERE id=? AND turn_id=?', (text, tid, params.get('turnId')))
            else:
                self.store.execute("UPDATE threads SET status='failed',turn_id=NULL,last_error=?,retry_error=NULL WHERE id=? AND turn_id=?",
                                   (text, tid, params.get('turnId')))
        elif method == 'item/started' or method in ('item/agentMessage/delta', 'item/commandExecution/outputDelta') or method.startswith('item/reasoning/'):
            # A retry can recover within the same turn. Do not re-display its
            # old error when the browser receives its next state refresh.
            self.store.execute('UPDATE threads SET retry_error=NULL WHERE id=? AND retry_error IS NOT NULL', (tid,))
        if method in ("item/started", "item/completed"):
            self._remember_images(tid, [{"items": [params.get("item", {})]}])
            if params.get('item', {}).get('type') == 'userMessage':
                self.store.execute("UPDATE threads SET has_content=1 WHERE id=?", (tid,))
        if method == "thread/settings/updated":
            settings = params.get("threadSettings", {})
            self.remember_permissions(tid, settings)
            self.store.execute("UPDATE threads SET model=?,effort=?,provider=?,cwd=COALESCE(?,cwd) WHERE id=?",
                               (settings.get("model"), settings.get("effort"), settings.get("modelProvider"), settings.get("cwd"), tid))
        elif method in ("thread/archived", "thread/unarchived"):
            self.store.execute("UPDATE threads SET archived=? WHERE id=?", (int(method == "thread/archived"), tid))
        elif method == "thread/status/changed":
            status = params.get("status", {}).get("type")
            if status == 'active':
                self.store.execute("UPDATE threads SET status='running',native_status='active' WHERE id=?", (tid,))
            elif status == 'idle':
                self.store.execute("""UPDATE threads SET native_status='idle',turn_id=NULL,retry_error=NULL,
                    status=CASE WHEN status IN ('completed','failed','interrupted') THEN status ELSE 'idle' END WHERE id=?""", (tid,))
            elif status == 'systemError':
                self.store.execute("UPDATE threads SET status='failed',native_status='systemError',turn_id=NULL,retry_error=NULL WHERE id=?", (tid,))
            elif status == "notLoaded":
                self.loaded.discard(tid)
                self.store.execute("UPDATE threads SET status='notLoaded',native_status='notLoaded',turn_id=NULL WHERE id=?", (tid,))
        elif method == "thread/closed":
            self.loaded.discard(tid)
        if method == "turn/completed" or ("id" in message and method in APPROVALS):
            kind = "completed" if method == "turn/completed" else "approval"
            self.store.execute("INSERT INTO notices(thread_id,kind,created) VALUES(?,?,?)", (tid, kind, time.time()))
            if self.push:
                self.push.notify(tid, kind)

    def respond(self, key, body):
        with self.request_lock:
            request = self.requests.get(key)
            if request and request["method"] == ELICITATION:
                action = body.get("action")
                if action not in ("accept", "decline", "cancel"):
                    raise APIError(400, "Choose Continue, Deny or Cancel.")
                params = request["params"]
                content = body.get("content")
                if action == "accept" and params.get("mode") != "url":
                    try:
                        schema = params["requestedSchema"]
                        # Remote schema references are not fetched by this client.
                        if '"$ref"' in json.dumps(schema):
                            raise APIError(400, "This form contains schema references. Complete it in the original client.")
                        Draft202012Validator.check_schema(schema)
                        Draft202012Validator(schema).validate(content)
                    except (ValidationError, SchemaError) as error:
                        raise APIError(400, "Form validation failed: " + error.message[:300])
                self.rpc.reply(request["rpcId"], result={"action": action, "content": content if action == "accept" else None})
                self.requests.pop(key, None)
                self.store.event(params["threadId"], "gateway/resolved", {"key": key})
                return {"ok": True}
        return super().respond(key, body)

    def _drain_queue(self):
        if self.shared:
            # The daemon owns scheduling and drains its native queue itself, including after
            # this gateway disconnects. A second scheduler would race the daemon and CLI.
            return
        for entry in self.store.rows("SELECT * FROM queue WHERE status='pending' ORDER BY created"):
            with self.op_lock:
                row = self.thread(entry["thread_id"])
                if row["turn_id"] or row["status"] == "running" or row["archived"]:
                    continue
                self.store.execute("UPDATE queue SET status='sending' WHERE id=? AND status='pending'", (entry["id"],))
                try:
                    # Separate delivery key keeps the queue request idempotent after completion.
                    body = json.loads(entry["body"])
                    self.send(entry["thread_id"], submission="delivery-" + entry["id"], **body)
                    self.store.execute("UPDATE queue SET status='sent' WHERE id=?", (entry["id"],))
                except APIError as error:
                    self.store.execute("UPDATE queue SET status=? WHERE id=?", ("uncertain" if error.status == 504 else "failed", entry["id"]))
                    self.store.event(entry["thread_id"], "gateway/error", {"message": "Queued message delivery failed: " + str(error)})

    def _reconnect_threads(self):
        errors = []
        for row in self.list_threads():
            tid = row['id']
            if not row['selected'] or row['archived'] or tid in self.loaded:
                continue
            retry_at, attempts, previous = self.attach_retries.get(tid, (0, 0, ''))
            if time.monotonic() < retry_at:
                errors.append(previous)
                continue
            try:
                self.attach(tid, force=tid in self.awaiting_materialization)
            except (APIError, OSError, sqlite3.Error) as error:
                message = explain_error(error)[:400]
                delay = min(120, 5 * 2 ** min(attempts, 5))
                self.attach_retries[tid] = (time.monotonic() + delay, attempts + 1, message)
                errors.append(message)
        return errors

    def _background(self):
        next_discovery = 0
        failures = 0
        while not self.stopped.wait(2):
            try:
                if not self.online:
                    with self.op_lock:
                        self.rpc.close()
                        self.connect()
                        if self.stopped.is_set():
                            self.rpc.close()
                            return
                        self.model_cache = None
                        next_discovery = 0
                if self.shared and self.browse_existing and time.monotonic() >= next_discovery:
                    self.discover()
                    next_discovery = time.monotonic() + 15
                self._reconnect_threads()
                self._drain_queue()
                # A single writer conflict is local to that thread. Do not show
                # it as a global failure while other conversations work normally.
                self.background_error = None
                failures = 0
            except Exception as error:
                self.background_error = explain_error(error)[:400]
                failures += 1
                if self.stopped.wait(min(60, 5 * 2 ** min(failures - 1, 4))):
                    return

    def close(self):
        self.stopped.set()
        self.rpc.close()
        if self.worker.is_alive():
            self.worker.join(5)
        if self.push:
            self.push.close()
