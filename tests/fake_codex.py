"""Protocol fixture: executes no commands and makes no model calls."""
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from markdown_samples import MARKDOWN_SAMPLE, MARKDOWN_PREFIX

lock = threading.RLock()
waiting = {}
queues = {}
state_file = Path(sys.argv[1]) if len(sys.argv) > 1 else None
threads = json.loads(state_file.read_text()) if state_file and state_file.exists() else {}


def save():
    if state_file:
        state_file.write_text(json.dumps(threads))


def emit(message):
    with lock:
        print(json.dumps(message), flush=True)


def event(method, params):
    with lock:
        tid = params.get('threadId')
        thread = threads.get(tid)
        if thread:
            if method == 'turn/started':
                thread['turns'].append({**params['turn'], 'items': []})
                thread['status'] = {'type': 'active'}
            elif method in ('item/started', 'item/completed'):
                turn = next(t for t in thread['turns'] if t['id'] == params['turnId'])
                item = next((i for i in turn['items'] if i['id'] == params['item']['id']), None)
                if item is None:
                    turn['items'].append(params['item'])
                else:
                    item.update(params['item'])
            elif method == 'item/agentMessage/delta':
                turn = next(t for t in thread['turns'] if t['id'] == params['turnId'])
                item = next(i for i in turn['items'] if i['id'] == params['itemId'])
                item['text'] += params['delta']
            elif method == 'turn/completed':
                turn = next(t for t in thread['turns'] if t['id'] == params['turn']['id'])
                turn.update({key: value for key, value in params['turn'].items() if key != 'items'})
                thread['status'] = {'type': 'idle'}
            elif method == 'thread/settings/updated':
                thread.setdefault('settings', {}).update(params['threadSettings'])
            save()
    emit({"method": method, "params": params})


def finish(tid, turn, status="completed", text="Fixture response"):
    event("item/started", {"threadId": tid, "turnId": turn, "item": {"id": "answer-" + turn, "type": "agentMessage", "text": ""}})
    event("item/agentMessage/delta", {"threadId": tid, "turnId": turn, "itemId": "answer-" + turn, "delta": text})
    event("item/completed", {"threadId": tid, "turnId": turn, "item": {"id": "answer-" + turn, "type": "agentMessage", "text": text}})
    event("turn/completed", {"threadId": tid, "turn": {"id": turn, "status": status}})


def delayed(tid, turn):
    time.sleep(0.15)
    finish(tid, turn)


for line in sys.stdin:
    message = json.loads(line)
    if "method" not in message:
        req = waiting.pop(message["id"], None)
        if req:
            finish(req[0], req[1], text=json.dumps(message.get("result", message.get("error"))))
        continue
    method, p, rid = message["method"], message.get("params", {}), message.get("id")
    if rid is None:
        continue
    result = {}
    if method == "initialize":
        result = {"userAgent": "fixture"}
    elif method == "thread/start":
        tid = 'thread-' + uuid.uuid4().hex
        threads[tid] = {'id': tid, 'cwd': p.get('cwd', '/tmp'), 'name': p.get('fixtureName'), 'preview': p.get('fixturePreview', ''), 'createdAt': time.time(),
                        'model': 'fixture-model', 'modelProvider': 'fixture', 'reasoningEffort': 'medium',
                        'status': {'type': 'idle'}, 'source': p.get('fixtureSource', 'cli'), 'turns': [], 'historyMode': p.get('historyMode', 'legacy'), 'archived': False}
        threads[tid]['settings'] = {'sandboxPolicy': {'type': {'danger-full-access': 'dangerFullAccess', 'read-only': 'readOnly'}.get(p.get('sandbox'), 'workspaceWrite')},
                                   'approvalPolicy': p.get('approvalPolicy', 'on-request'), 'model': 'fixture-model', 'effort': 'medium',
                                   'modelProvider': 'fixture', 'cwd': threads[tid]['cwd']}
        save(); result = {"thread": threads[tid], 'sandbox': threads[tid]['settings']['sandboxPolicy'], 'approvalPolicy': threads[tid]['settings']['approvalPolicy']}
    elif method == "thread/resume":
        thread = threads.get(p['threadId'])
        if thread and thread['historyMode'] == 'paginated' and not thread['turns']:
            emit({'id': rid, 'error': {'code': -32600, 'message': f"no rollout found for thread id {p['threadId']}"}})
            continue
        result = {"thread": threads.get(p['threadId'], {'id': p['threadId'], 'turns': []})}
        if result['thread'].get('status', {}).get('type') == 'notLoaded':
            result['thread']['status'] = {'type': 'idle'}
        if thread and thread.get('settings'):
            result.update(sandbox=thread['settings']['sandboxPolicy'], approvalPolicy=thread['settings']['approvalPolicy'])
        if p.get('excludeTurns'):
            result['thread'] = {k: v for k, v in result['thread'].items() if k != 'turns'}
    elif method == 'thread/settings/update':
        tid = p['threadId']
        settings = {**threads[tid].get('settings', {}), **{k: v for k, v in p.items() if k != 'threadId'}}
        event('thread/settings/updated', {'threadId': tid, 'threadSettings': settings})
    elif method == 'thread/read':
        result = {'thread': threads[p['threadId']]}
    elif method == 'thread/goal/get':
        result = {'goal': threads[p['threadId']].get('goal')}
    elif method == 'thread/goal/set':
        tid = p['threadId']; old = threads[tid].get('goal')
        objective = p.get('objective') or (old or {}).get('objective')
        if not objective:
            emit({'id': rid, 'error': {'code': -32602, 'message': 'No goal to update'}}); continue
        if not old or old['objective'] != objective or old['status'] == 'complete':
            goal = {'threadId': tid, 'objective': objective, 'status': 'active', 'tokensUsed': 0,
                    'timeUsedSeconds': 0, 'tokenBudget': None, 'createdAt': int(time.time())}
        else: goal = dict(old)
        for key in ('status', 'tokenBudget'):
            if key in p: goal[key] = p[key]
        goal['updatedAt'] = int(time.time()); threads[tid]['goal'] = goal; save()
        result = {'goal': goal}; event('thread/goal/updated', {'threadId': tid, 'goal': goal})
    elif method == 'thread/goal/clear':
        tid = p['threadId']; result = {'cleared': threads[tid].pop('goal', None) is not None}; save()
        event('thread/goal/cleared', {'threadId': tid})
    elif method == 'fixture/event':
        event(p['method'], p['params'])
    elif method == 'fixture/goal/progress':
        tid = p['threadId']; goal = threads[tid]['goal']; goal.update(p['progress']); save()
        result = {'goal': goal}; event('thread/goal/updated', {'threadId': tid, 'goal': goal})
    elif method == 'thread/compact/start':
        tid = p['threadId']; turn = 'compact-' + uuid.uuid4().hex
        event('turn/started', {'threadId': tid, 'turn': {'id': turn, 'status': 'inProgress'}})
        event('item/completed', {'threadId': tid, 'turnId': turn, 'item': {'id': turn, 'type': 'contextCompaction'}})
        event('turn/completed', {'threadId': tid, 'turn': {'id': turn, 'status': 'completed'}})
    elif method == 'thread/list':
        result = {'data': [t for t in threads.values() if t['archived'] == bool(p.get('archived')) and (not p.get('cwd') or t['cwd'] in p['cwd'])], 'nextCursor': None}
    elif method == 'thread/loaded/list':
        result = {'data': [tid for tid, t in threads.items() if t['status']['type'] != 'notLoaded'], 'nextCursor': None}
    elif method == 'fixture/unload':
        threads[p['threadId']]['status'] = {'type': 'notLoaded'}; save()
    elif method == 'fixture/markdown/finish':
        tid = p['threadId']; turn = threads[tid]['turns'][-1]['id']
        event('item/agentMessage/delta', {'threadId': tid, 'turnId': turn, 'itemId': 'answer-'+turn, 'delta': MARKDOWN_SAMPLE[len(MARKDOWN_PREFIX):]})
        event('item/completed', {'threadId': tid, 'turnId': turn, 'item': {'id': 'answer-'+turn, 'type': 'agentMessage', 'text': MARKDOWN_SAMPLE}})
        event('turn/completed', {'threadId': tid, 'turn': {'id': turn, 'status': 'completed'}})
    elif method == 'thread/name/set':
        threads[p['threadId']]['name'] = p['name']; save()
        event('thread/name/updated', {'threadId': p['threadId'], 'threadName': p['name']})
    elif method in ('thread/archive', 'thread/unarchive'):
        threads[p['threadId']]['archived'] = method == 'thread/archive'; save()
    elif method == 'thread/fork':
        tid = 'thread-' + uuid.uuid4().hex
        threads[tid] = json.loads(json.dumps(threads[p['threadId']]))
        threads[tid]['id'] = tid; save(); result = {'thread': threads[tid]}
    elif method == 'thread/turns/list':
        if threads[p['threadId']]['historyMode'] == 'paginated' and not threads[p['threadId']]['turns']:
            emit({'id': rid, 'error': {'code': -32600, 'message': f"thread {p['threadId']} is not materialized yet; thread/turns/list is unavailable before first user message"}})
            continue
        turns = list(reversed(threads[p['threadId']]['turns']))
        start, size = int(p.get('cursor') or 0), p.get('limit', 30)
        result = {'data': turns[start:start+size], 'nextCursor': str(start+size) if len(turns)>start+size else None}
    elif method == 'model/list':
        result = {'data': [{'id': 'fixture-model', 'model': 'fixture-model', 'displayName': 'Fixture Model', 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}, {'reasoningEffort': 'high'}]}], 'nextCursor': None}
    elif method == 'thread/queue/add':
        item = {'id': 'queued-'+uuid.uuid4().hex, 'input': p['input'], 'clientUserMessageId': p['clientUserMessageId']}
        queues.setdefault(p['threadId'], []).append(item)
        result = {'queuedSubmission': item}
        event('thread/queue/changed', {'threadId': p['threadId']})
    elif method == 'thread/queue/list':
        result = {'data': queues.get(p['threadId'], []), 'nextCursor': None}
    elif method == 'thread/queue/delete':
        queues[p['threadId']] = [q for q in queues.get(p['threadId'], []) if q['id'] != p['queuedSubmissionId']]
        event('thread/queue/changed', {'threadId': p['threadId']})
    elif method == "turn/start":
        tid, turn = p["threadId"], "turn-" + uuid.uuid4().hex
        text = p["input"][0].get("text", "image")
        event("turn/started", {"threadId": tid, "turn": {"id": turn, "status": "inProgress", "startedAt": int(time.time())}})
        event("item/completed", {"threadId": tid, "turnId": turn, "item": {"id": "user-" + turn, "type": "userMessage", "content": p["input"]}})
        event("account/updated", {"apiKey": "AUTH_SENTINEL_DO_NOT_STORE"})
        result = {"turn": {"id": turn, "status": "inProgress"}}
        if text.startswith(("approval", "question", "elicitation")):
            reqid = "request-" + turn
            waiting[reqid] = (tid, turn)
            params = {"threadId": tid, "turnId": turn, "itemId": "command-" + turn}
            request_method = "item/commandExecution/requestApproval"
            if text.startswith("question"):
                request_method = "item/tool/requestUserInput"
                params["questions"] = [{"id": "choice", "header": "Choice", "question": "Which?"}]
                params["isBlocking"] = True
            elif text.startswith('elicitation'):
                request_method = 'mcpServer/elicitation/request'
                params.update({'serverName': 'fixture-plugin', 'mode': 'form', 'message': 'Pick a number', 'requestedSchema': {'type': 'object', 'properties': {'count': {'type': 'integer', 'minimum': 1}}, 'required': ['count']}})
            else:
                params["command"] = "echo fixture"
            emit({"id": reqid, "method": request_method, "params": params})
        elif text == "hang":
            pass
        elif text == "image":
            event('item/completed', {'threadId': tid, 'turnId': turn, 'item': {'id': 'image-'+turn, 'type': 'imageView', 'path': str(Path(threads[tid]['cwd']) / 'preview.png')}})
            finish(tid, turn, text='Image preview fixture')
        elif text == "stream":
            event('item/started', {'threadId': tid, 'turnId': turn, 'item': {'id': 'answer-'+turn, 'type': 'agentMessage', 'text': ''}})
            event('item/agentMessage/delta', {'threadId': tid, 'turnId': turn, 'itemId': 'answer-'+turn, 'delta': 'Streaming before completion'})
        elif text == 'markdown-stream':
            event('item/started', {'threadId': tid, 'turnId': turn, 'item': {'id': 'answer-'+turn, 'type': 'agentMessage', 'text': ''}})
            event('item/agentMessage/delta', {'threadId': tid, 'turnId': turn, 'itemId': 'answer-'+turn, 'delta': MARKDOWN_PREFIX})
        else:
            threading.Thread(target=delayed, args=(tid, turn), daemon=True).start()
    elif method == "turn/interrupt":
        finish(p["threadId"], p["turnId"], "interrupted")
    elif method == 'turn/steer':
        thread = threads[p['threadId']]
        turn = next((t for t in thread['turns'] if t['status'] == 'inProgress'), None)
        if not turn or turn['id'] != p['expectedTurnId']:
            emit({'id': rid, 'error': {'code': -1, 'message': 'No matching active turn'}});continue
        event('item/completed', {'threadId': p['threadId'], 'turnId': turn['id'], 'item': {'id': 'steer-'+uuid.uuid4().hex, 'type': 'userMessage', 'content': p['input']}})
        result = {'turnId': turn['id']}
    emit({"id": rid, "result": result})
