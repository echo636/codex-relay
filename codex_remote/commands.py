"""Native slash-command operations; never interpret them as model prompts."""
import hashlib
import json
import re

from codex_remote.server import APIError


def slash_input(text):
    return isinstance(text, str) and re.match(r"^/[A-Za-z][A-Za-z0-9_-]*(?:\s|$)", text.lstrip()) is not None


class CommandMixin:
    def permissions(self, tid):
        row = self.thread(tid)
        connected = not row['read_only'] and (tid in self.loaded or tid in self.awaiting_materialization)
        settings = json.loads(row.get('permission_state') or '{}') if connected else {}
        return {**settings, 'known': bool(settings), 'needsConnection': not connected,
                'fullAccess': settings.get('sandboxPolicy', {}).get('type') == 'dangerFullAccess' and settings.get('approvalPolicy') == 'never',
                'nextTurn': bool(row['turn_id'] or row['status'] == 'running'),
                'defaultFullAccess': self.sandbox == 'danger-full-access' and self.approval == 'never'}

    def remember_permissions(self, tid, settings):
        sandbox = settings.get('sandboxPolicy', settings.get('sandbox'))
        if not isinstance(sandbox, dict) or 'approvalPolicy' not in settings:
            return
        value = {'sandboxPolicy': sandbox, 'approvalPolicy': settings['approvalPolicy']}
        self.store.execute('UPDATE threads SET permission_state=? WHERE id=?', (json.dumps(value), tid))

    def full_access(self, tid):
        params = {'threadId': tid, 'sandboxPolicy': {'type': 'dangerFullAccess'}, 'approvalPolicy': 'never'}
        try:
            self.rpc.call('thread/settings/update', params)
        except APIError as error:
            raise self._command_error(error)
        # The native response is an empty acknowledgement, not a settings object.
        # Record only acknowledged updates; errors never become successful UI state.
        self.remember_permissions(tid, params)
        result = self.permissions(tid)
        self.store.event(tid, 'gateway/permissions', {'threadId': tid, 'permissions': result})
        return result

    def apply_default_permissions(self, tid):
        if self.sandbox == 'danger-full-access' and self.approval == 'never' and not self.permissions(tid)['fullAccess']:
            self.full_access(tid)

    @staticmethod
    def _command_error(error):
        message = str(error)
        if error.status == 502 and any(s in message.lower() for s in
                ("unknown method", "method not found", "unrecognized request", "unknown variant")):
            return APIError(501, "This Codex daemon does not support the command. It was not sent as a chat message.")
        return error

    def get_goal(self, tid):
        row = self.thread(tid)
        # Passive preview must never subscribe, resume, or probe extra APIs on
        # an unrelated terminal thread. The user can explicitly connect first.
        if row["read_only"] or (tid not in self.loaded and tid not in self.awaiting_materialization):
            return {"goal": None, "needsConnection": True}
        try:
            result = self.rpc.call("thread/goal/get", {"threadId": tid})
        except APIError as error:
            raise self._command_error(error)
        return {"goal": result.get("goal"), "needsConnection": False}

    def run_command(self, tid, body):
        self.require_control(tid)
        name, action, submission = body.get("command"), body.get("action"), body.get("submissionId")
        if name not in ("goal", "compact", "permissions"):
            raise APIError(400, "This web client does not support that command. Use /help for the command list.")
        if not isinstance(submission, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", submission):
            raise APIError(400, "A valid command ID is required.")
        params = {"threadId": tid}
        if name == "goal":
            if action not in ("set", "edit", "pause", "resume", "clear"):
                raise APIError(400, "Invalid goal operation.")
            if action in ("set", "edit"):
                objective = body.get("objective")
                if not isinstance(objective, str) or not 1 <= len(objective.strip()) <= 4000:
                    raise APIError(400, "Objectives must contain 1–4,000 characters.")
                params["objective"] = objective.strip()
                if "tokenBudget" in body:
                    budget = body["tokenBudget"]
                    if type(budget) is not int or not 1 <= budget <= 9007199254740991:
                        raise APIError(400, "Token budget must be a positive integer.")
                    params["tokenBudget"] = budget
            if action == "set" or action == "resume":
                params["status"] = "active"
            elif action == "pause":
                params["status"] = "paused"
            method = "thread/goal/clear" if action == "clear" else "thread/goal/set"
        elif name == "permissions":
            if action != "full-access":
                raise APIError(400, "The permission command currently supports full-access.")
            method = "thread/settings/update"
        else:
            method = "thread/compact/start"
        digest = hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != "submissionId"}, sort_keys=True).encode()).hexdigest()
        with self.op_lock:
            prior = self.store.rows("SELECT * FROM submissions WHERE id=?", (submission,))
            if prior:
                row = prior[0]
                if row["thread_id"] != tid or row["digest"] != digest:
                    raise APIError(409, "The command ID was already used for another request.")
                if row["result"] is None:
                    raise APIError(409, "The previous command result is unconfirmed. Refresh goal or conversation status first; it will not be submitted again.")
                return json.loads(row["result"])
            self.attach(tid)
            row = self.thread(tid)
            if name == "compact" and (row["turn_id"] or row["status"] == "running"):
                raise APIError(409, "Wait for the current task to finish before compacting context.")
            if name == "goal":
                current = self.get_goal(tid)["goal"]
                if action in ("edit", "pause", "resume") and not current:
                    raise APIError(409, "No goal is set. Use /goal <objective> to create one.")
                if action == "resume" and current["status"] == "complete":
                    raise APIError(409, "This goal is complete. Create a new goal.")
                if action == "edit":
                    params["status"] = current["status"]
                    if "tokenBudget" not in params and current.get("tokenBudget") is not None:
                        params["tokenBudget"] = current["tokenBudget"]
                if "expectedGoal" in body:
                    identity = {k: current.get(k) for k in ("objective", "createdAt")} if current else None
                    if body["expectedGoal"] != identity:
                        raise APIError(409, "The goal changed on another device. Reopen the goal panel before editing.")
            self.store.execute("INSERT INTO submissions(id,thread_id,digest) VALUES(?,?,?)", (submission, tid, digest))
            try:
                result = self.full_access(tid) if name == 'permissions' else self.rpc.call(method, params)
            except APIError as error:
                if error.status in (501, 502):
                    self.store.execute("DELETE FROM submissions WHERE id=? AND result IS NULL", (submission,))
                # Keep ambiguous transport failures reserved, including after a
                # gateway restart. Never re-send goal activation or compaction.
                raise self._command_error(error)
            response = {"ok": True, "command": name}
            if name == 'permissions':
                response['permissions'] = result
            if name == "goal":
                response["goal"] = None if action == "clear" else result.get("goal")
                if action == "clear":
                    response["cleared"] = result.get("cleared", False)
            self.store.execute("UPDATE submissions SET result=? WHERE id=?", (json.dumps(response), submission))
            # Native notifications drive SSE; no synthetic chat turn or separate
            # goal scheduler is started by this gateway.
            return response
