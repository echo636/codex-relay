'use strict';
const $ = id => document.getElementById(id);
const state = {threads: [], workspaces: [], current: null, stream: null, items: new Map(), turnMarkers: new Map(), online: false, active: false, ready: false, sending: false, lastSeq: 0, requestSignature: '', readOnly: false, previewBusy: false};
const labels = {running: I18n.t("Running"), idle: I18n.t("Ready"), completed: I18n.t("Completed"), interrupted: I18n.t("Stopped"), failed: I18n.t("Execution failed"), inProgress: I18n.t("Running"), notLoaded: I18n.t("Saved · Ready to resume")};
function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
function notice(text) { $('notice').textContent = I18n.t(text || ''); }
async function api(path, body) {
  const options = {credentials: 'same-origin', cache: 'no-store', headers: {'Accept-Language': I18n.language}};
  if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) { if (response.status === 401 && path !== '/api/login') showLogin(); throw new Error(data.error || I18n.t("Request failed")); }
  return data;
}
function showLogin() {
  state.selection = (state.selection || 0) + 1;
  if (state.stream) state.stream.close(); state.stream = null;
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
  $('app').hidden = true; $('login').hidden = false;
  resetTranscript(); $('requests').replaceChildren(); $('prompt').value = '';
  state.items.clear(); state.current = null; state.ready = false; state.readOnly = false;
  if (window.runActivity) window.runActivity.reset();
  if (window.remoteUI) window.remoteUI.clearPrivate();
  if (window.slashCommands) window.slashCommands.clear();
}
function setDrawer(open) { $('sidebar').classList.toggle('open', open); $('scrim').hidden = !open; }
function buttons() {
  $('composer').hidden = false;
  $('view-mode').hidden = !state.readOnly;
  $('enable-control').disabled = !state.online || (!state.ready && !state.connectionFailed) || state.sending;
  for (const id of ['rename-chat', 'archive-chat', 'resync-chat']) $(id).disabled = state.readOnly;
  for (const id of ['fork-chat', 'recover-chat']) $(id).disabled = !state.current || !state.online || !!state.forking;
  $('recover-chat').hidden = !state.connectionFailed;
  $('prompt').disabled = !state.current || !state.online || !state.ready;
  const mode = $('message-mode').value;
  const uploading = window.remoteUI ? window.remoteUI.uploadCount() : 0;
  $('upload-status').hidden = !uploading;
  $('upload-status').textContent = uploading ? I18n.t("Uploading ") + uploading + I18n.t(" attachments… You can send once uploads finish.") : '';
  const command = window.slashCommands && window.slashCommands.isCommand($('prompt').value);
  $('send').disabled = !state.online || (!!state.current && !state.ready) || (!state.current && !command) || (!command && state.active && mode === 'send') || state.sending || !!uploading || (!$('prompt').value.trim() && !(state.attachments || []).length);
  $('thread-tools').disabled = !state.current;
  $('stop').hidden = !state.active || state.readOnly;
  $('stop').disabled = !state.online;
  $('composer-hint').textContent = !state.current ? I18n.t("Choose or create a conversation first") : !state.ready ? I18n.t("Syncing conversation…") : mode === 'queue' ? I18n.t("Send this message after the current turn finishes") : state.active && mode === 'auto' ? I18n.t("Add to the current task when Codex can accept new instructions") : state.active ? I18n.t("A task is running. Switch to Follow up now.") : I18n.t("Enter to send · Ctrl / ⌘ + Enter for a new line");
  const sendLabel = mode === 'queue' ? I18n.t("Queue for next turn") : state.active && mode === 'auto' ? I18n.t("Follow up on the current task") : I18n.t("Send message");
  $('send').setAttribute('aria-label', sendLabel); $('send').title = sendLabel;
  if (window.runActivity) window.runActivity.render();
  if (window.slashCommands) window.slashCommands.render();
}
function renderThreads() {
  $('threads').replaceChildren();
  const inArchive = state.threads.filter(thread => !!thread.archived === $('show-archived').checked);
  $('hidden-thread-count').textContent = inArchive.filter(thread => thread.list_hidden).length;
  const visible = inArchive.filter(thread => ($('show-hidden').checked || !thread.list_hidden || (thread.list_hidden_reason === 'empty' && thread.id === state.current)) && $('thread-search').value.toLowerCase().split(' ').every(term => thread.title.toLowerCase().includes(term)));
  $('thread-count').textContent = visible.length;
  for (const thread of visible) {
    const b = el('button', 'thread' + (thread.id === state.current ? ' active' : ''));
    b.dataset.threadId = thread.id;
    b.append(el('span', 'thread-title', thread.title));
    const meta = el('span', 'thread-meta');
    const status = el('span', 'thread-status', labels[thread.status] || thread.status); status.dataset.status = thread.status;
    meta.append(el('span', 'thread-project', thread.cwd.split('/').pop()), status); b.append(meta);
    if (thread.list_hidden && $('show-hidden').checked) b.append(el('span', 'thread-filter-reason', {automation: I18n.t("Automated / batch task"), subagent: I18n.t("Subagent conversation"), empty: I18n.t("Empty conversation"), manual: I18n.t("Manually hidden")}[thread.list_hidden_reason] || I18n.t("Hidden")));
    b.onclick = () => selectThread(thread.id, true); $('threads').append(b);
  }
}
async function refresh() {
  const data = await api('/api/bootstrap');
  state.threads = data.threads; state.workspaces = data.workspaces; state.online = data.online;
  if (window.remoteUI) window.remoteUI.bootstrap(data);
  $('host-dot').classList.toggle('online', data.online); $('host-state').textContent = data.online ? I18n.t("Connected to server") : I18n.t("Codex is disconnected");
  renderThreads(); buttons(); return data;
}
async function enter() {
  const data = await refresh(); $('login').hidden = true; $('app').hidden = false;
  const requested = new URLSearchParams(location.search).get('thread') || (window.remoteUI && window.remoteUI.restoredThread);
  const last = requested || localStorage.getItem('codex-remote-thread');
  // Hidden entries are still reachable by an explicit deep link, never selected
  // automatically just because a stale browser preference points to one.
  const eligible = state.threads.filter(t => t.id === requested || (!t.archived && ($('show-hidden').checked || !t.list_hidden)));
  if (eligible.length) selectThread(eligible.some(t => t.id === last) ? last : eligible[0].id);
}
function nearBottom() { const t = $('timeline'); return t.scrollHeight - t.scrollTop - t.clientHeight < 150; }
let followLatest = true, tailFrame = 0, renderFrame = 0;
let scrollPosition = {top: 0, height: 0, viewport: 0};
const pendingRenders = new Set();
function rememberScroll() { const t = $('timeline'); scrollPosition = {top: t.scrollTop, height: t.scrollHeight, viewport: t.clientHeight}; }
function scrollDown() {
  followLatest = true;
  const t = $('timeline'); t.scrollTop = t.scrollHeight;
  rememberScroll(); updateJumpBottom();
}
function scheduleTail() {
  if (tailFrame) return;
  tailFrame = requestAnimationFrame(() => { tailFrame = 0; if (followLatest) scrollDown(); else updateJumpBottom(); });
}
function resetTranscript() {
  cancelAnimationFrame(renderFrame); renderFrame = 0; pendingRenders.clear();
  state.items.clear(); state.turnMarkers.clear(); $('messages').replaceChildren(); followLatest = true; rememberScroll(); scheduleTail();
}
function flushRenders() {
  renderFrame = 0;
  const deadline = performance.now() + 8;
  for (const record of pendingRenders) {
    pendingRenders.delete(record);
    if (state.items.get(record.item.id) === record) renderItem(record);
    if (performance.now() >= deadline) break;
  }
  scheduleTail();
  if (pendingRenders.size) renderFrame = requestAnimationFrame(flushRenders);
}
function updateJumpBottom() {
  const t = $('timeline');
  $('jump-bottom').hidden = !$('messages').childElementCount || t.clientHeight === 0 || t.scrollHeight - t.scrollTop - t.clientHeight <= 80;
}
function upsert(item, completed) {
  if (!item || !item.id) return;
  // Private model reasoning is not needed in the mobile transcript.
  if (item.type === 'reasoning') return;
  let record = state.items.get(item.id);
  if (!record) {
    const node = el('article', 'message'); record = {item: {}, node, done: false}; state.items.set(item.id, record); $('messages').append(node);
  }
  if (record.done && !completed) return;
  record.item = Object.assign({}, record.item, item); record.done = !!completed;
  pendingRenders.add(record);
  if (!renderFrame) renderFrame = requestAnimationFrame(flushRenders);
}
function renderItem(record) {
  const i = record.item, node = record.node;
  const wasOpen = node.firstChild && node.firstChild.open;
  node.replaceChildren();
  if (i.type === 'agentMessage' || i.type === 'userMessage') {
    node.className = 'message ' + (i.type === 'userMessage' ? 'user' : 'agent');
    node.append(el('div', 'role', i.type === 'userMessage' ? I18n.t("You") : 'CODEX'));
    const content = i.type === 'userMessage' ? (i.content || []).map(c => c.text || '[' + c.type + ']').join('\n') : i.text || '';
    const contentNode = el('div', 'content', content);
    if (i.type === 'userMessage' && window.remoteUI) window.remoteUI.userContent(contentNode, i.content || []);
    if (i.type === 'agentMessage' && window.remoteUI) window.remoteUI.markdown(contentNode, content, record.done);
    node.append(contentNode);
  } else {
    node.className = 'message tool'; node.dataset.type = i.type; node.dataset.itemId = i.id;
    const details = el('details'); details.open = !!wasOpen;
    let title = i.type;
    if (i.type === 'commandExecution') { title = (record.done ? I18n.t("Execution completed") : I18n.t("Run command")) + (i.exitCode !== undefined && i.exitCode !== null ? I18n.t(" · Exit code ") + i.exitCode : ''); }
    else if (i.type === 'fileChange') { title = I18n.t("File changes · ") + (i.changes || []).length + I18n.t(" files"); }
    else if (i.type === 'mcpToolCall') { title = I18n.t("Tool · ") + i.server + ' / ' + i.tool; }
    else if (i.type === 'plan') { title = I18n.t("Execution plan"); }
    else if (i.type === 'webSearch') { title = I18n.t("Search · ") + (i.query || ''); }
    else if (i.type === 'contextCompaction') { title = I18n.t("Conversation context compacted"); }
    else if (i.type === 'imageView') { title = I18n.t("View image"); }
    else if (i.type === 'dynamicToolCall') { title = I18n.t("Tool · ") + i.tool; }
    else if (i.type === 'collabAgentToolCall' || i.type === 'collabToolCall') { title = I18n.t("Collaborative task"); }
    const summary = el('summary'); summary.append(el('span', 'tool-mark', i.type === 'imageView' ? '▧' : i.type === 'commandExecution' ? '⌘' : '↗'), el('span', 'tool-title', title));
    if (i.type === 'imageView') summary.append(el('span', 'tool-filename', (i.path || '').split('/').pop()));
    details.append(summary);
    if (i.type === 'imageView' && window.remoteUI) window.remoteUI.imageView(details, i);
    else {
      let expanded = false;
      const expand = () => {
        if (!details.open || expanded) return;
        expanded = true;
        let text = '';
        if (i.type === 'commandExecution') text = '$ ' + (i.command || '') + '\n\n' + (i.aggregatedOutput || '');
        else if (i.type === 'fileChange') text = (i.changes || []).map(c => c.path + '\n' + (c.diff || JSON.stringify(c.kind))).join('\n\n');
        else if (i.type === 'mcpToolCall') text = JSON.stringify({arguments: i.arguments, result: i.result, error: i.error}, null, 2);
        else if (i.type === 'plan') text = i.text || '';
        else if (i.type === 'webSearch') text = JSON.stringify(i.results || i.action || {}, null, 2);
        else if (i.type === 'dynamicToolCall') text = JSON.stringify(i.contentItems || i.arguments || {}, null, 2);
        else if (i.type !== 'contextCompaction') text = JSON.stringify(i, null, 2);
        if (window.remoteUI) window.remoteUI.toolContent(details, i, text);
        else details.append(el('div', 'content', text));
        scheduleTail();
      };
      details.addEventListener('toggle', expand); expand();
    }
    node.append(details);
  }
}
function systemMessage(text) { $('messages').append(el('div', 'message system', text)); scheduleTail(); }
function modelErrorText(error) {
  if (!error) return '';
  if (typeof error === 'string') return error;
  const text = [error.message, error.additionalDetails].filter(Boolean).join('\n');
  return /client not allowed/i.test(text) ? I18n.t("The model provider rejected this Codex client (HTTP 403 / Client not allowed).") + (error.message && error.message.startsWith('Reconnecting') ? ' ' + error.message : '') : text || I18n.t("Model request failed");
}
function turnMarker(turn) {
  if (!turn || !turn.id || !['completed', 'failed', 'interrupted'].includes(turn.status)) return null;
  let node = state.turnMarkers.get(turn.id);
  if (!node) { node = el('section', 'turn-result'); node.dataset.turnId = turn.id; state.turnMarkers.set(turn.id, node); $('messages').append(node); }
  const lastItem = [...(turn.items || [])].reverse().map(item => state.items.get(item.id)).find(Boolean);
  if (lastItem && lastItem.node.nextSibling !== node) lastItem.node.after(node);
  const error = modelErrorText(turn.error);
  const signature = JSON.stringify([turn.status, turn.durationMs, error]);
  if (node.dataset.signature === signature) return node;
  node.dataset.signature = signature; node.dataset.status = turn.status; node.replaceChildren();
  const titles = {completed: I18n.t("✓ Turn completed"), failed: I18n.t("✕ Turn failed"), interrupted: I18n.t("■ Turn interrupted")};
  node.append(el('span', 'turn-result-title', titles[turn.status]));
  if (typeof turn.durationMs === 'number' && turn.durationMs >= 0) {
    const seconds = Math.round(turn.durationMs / 1000);
    node.append(el('span', 'turn-result-time', I18n.t("Duration: ") + (seconds < 60 ? seconds + I18n.t(" sec") : Math.floor(seconds / 60) + I18n.t(" min ") + seconds % 60 + I18n.t(" sec"))));
  }
  if (error) node.append(el('p', 'turn-result-error', error));
  scheduleTail(); return node;
}
function updateEvent(message) {
  if (window.runActivity) window.runActivity.event(message);
  if (window.slashCommands) window.slashCommands.event(message);
  const m = message.method, p = message.params || {};
  if (m === 'gateway/snapshot') {
    resetTranscript();
    for (const turn of p.turns || []) {
      for (const item of turn.items || []) upsert(item, turn.status !== 'inProgress' || item.status === 'completed' || item.type === 'userMessage');
      turnMarker(turn);
    }
    if (window.remoteUI) window.remoteUI.historyCursor(p.nextCursor);
    scrollDown();
  }
  else if (m === 'item/started' || m === 'item/completed') upsert(p.item, m === 'item/completed');
  else if (m === 'item/agentMessage/delta' || m === 'item/commandExecution/outputDelta' || m === 'item/plan/delta') {
    const old = state.items.get(p.itemId); if (old && old.done) return;
    const field = m.includes('commandExecution') ? 'aggregatedOutput' : 'text';
    const type = m.includes('commandExecution') ? 'commandExecution' : m.includes('/plan/') ? 'plan' : 'agentMessage';
    const item = Object.assign({id: p.itemId, type}, old ? old.item : {}); item[field] = (item[field] || '') + (p.delta || ''); upsert(item, false);
  } else if (m === 'gateway/error' || m === 'gateway/interrupted') systemMessage(p.message);
  else if (m === 'error') {
    if (p.willRetry) notice(modelErrorText(p.error || p.message));
    else { state.active = false; if (p.turnId) turnMarker({id: p.turnId, status: 'failed', error: p.error || p.message}); else systemMessage(modelErrorText(p.error || p.message)); buttons(); }
  }
  else if (m === 'turn/completed' && p.turn) {
    for (const item of p.turn.items || []) upsert(item, true);
    turnMarker(p.turn); state.active = false; buttons();
  }
  else if (m === 'turn/diff/updated' && p.diff) upsert({id: 'diff-' + p.turnId, type: 'fileChange', changes: [{path: I18n.t("Full diff for this turn"), diff: p.diff}]}, true);
}
function renderRequests(requests) {
  const signature = JSON.stringify(requests); if (signature === state.requestSignature) return;
  state.requestSignature = signature; $('requests').replaceChildren();
  for (const request of requests) {
    if (window.remoteUI && window.remoteUI.specialRequest(request)) continue;
    const card = el('section', 'request'); const p = request.params;
    const question = request.method === 'item/tool/requestUserInput';
    card.append(el('h3', '', question ? I18n.t("Codex needs your answer") : I18n.t("Codex requests approval")));
    if (p.reason) card.append(el('p', '', p.reason));
    if (p.command) card.append(el('pre', '', p.command));
    if (p.cwd) card.append(el('p', 'muted', I18n.t("Directory: ") + p.cwd));
    const fields = [];
    if (question) {
      for (const q of p.questions || []) {
        const id = 'answer-' + request.key + '-' + fields.length;
        const label = el('label', '', q.question); label.htmlFor = id; card.append(label);
        if (q.options && q.options.length) {
          const choices = el('select'); choices.append(el('option', '', I18n.t("Choose a suggested answer, or enter your own below")));
          for (const o of q.options) { const option = el('option', '', o.label + (o.description ? ' — ' + o.description : '')); option.value = o.label; choices.append(option); }
          choices.setAttribute('aria-label', q.question + I18n.t(": suggested answer")); card.append(choices);
          const input = el('input'); input.id = id; input.type = q.isSecret ? 'password' : 'text'; input.autocomplete = 'off'; input.placeholder = I18n.t("Enter an answer"); input.maxLength = 10000;
          choices.onchange = () => { if (choices.selectedIndex) input.value = choices.value; }; card.append(input); fields.push([q.id, input]);
        } else { const input = el('input'); input.id = id; input.type = q.isSecret ? 'password' : 'text'; input.autocomplete = 'off'; input.maxLength = 10000; card.append(input); fields.push([q.id, input]); }
      }
    } else {
      const details = el('details'); details.append(el('summary', '', I18n.t("View request details")), el('pre', '', JSON.stringify(p, null, 2))); card.append(details);
      if (request.method.includes('fileChange')) {
        const item = state.items.get(p.itemId);
        if (item) card.append(el('pre', '', JSON.stringify(item.item.changes || item.item, null, 2)));
      }
    }
    const actions = el('div', 'request-actions');
    const submit = async body => {
      for (const b of actions.querySelectorAll('button')) b.disabled = true;
      try { await api('/api/requests/' + request.key + '/respond', body); card.remove(); notice(''); }
      catch (error) { notice(error.message); for (const b of actions.querySelectorAll('button')) b.disabled = false; }
    };
    const accept = el('button', 'approve', question ? I18n.t("Send answer") : request.method === 'item/permissions/requestApproval' ? I18n.t("Allow for this turn") : I18n.t("Allow once"));
    accept.onclick = () => {
      if (question) { const answers = {}; for (const [id, input] of fields) answers[id] = input.value; submit({answers}); }
      else submit({decision: 'accept'});
    }; actions.append(accept);
    if (!question) { const deny = el('button', 'decline', I18n.t("Deny")); deny.onclick = () => submit({decision: 'decline'}); actions.append(deny); }
    card.append(actions); $('requests').append(card);
  }
}
async function selectThread(tid, connect = false) {
  const selection = state.selection = (state.selection || 0) + 1;
  if (state.stream) state.stream.close();
  state.stream = null;
  if (window.remoteUI) window.remoteUI.beforeSwitch(tid);
  state.current = tid; state.items.clear(); state.lastSeq = 0; state.requestSignature = ''; state.ready = false; state.active = false; state.connectionFailed = false; state.previewRetryAt = 0; state.previewFailures = 0;
  if (window.slashCommands) window.slashCommands.switched();
  if (window.runActivity) window.runActivity.reset();
  resetTranscript(); $('requests').replaceChildren(); $('empty').hidden = true; notice('');
  const thread = state.threads.find(t => t.id === tid);
  state.readOnly = !!(thread && thread.read_only);
  $('chat-title').textContent = thread ? thread.title : I18n.t("Conversation"); $('chat-path').textContent = thread ? thread.cwd : '';
  localStorage.setItem('codex-remote-thread', tid); renderThreads(); setDrawer(false); buttons();
  if (state.readOnly) {
    if (connect) {
      try { await connectThread(tid); }
      catch (error) { if (selection === state.selection) { state.connectionFailed = true; buttons(); notice(error.message); await previewThread(true).catch(() => {}); } }
    } else {
      try { await previewThread(true); } catch (error) { if (selection === state.selection) notice(error.message); }
    }
    return;
  }
  let snapshot;
  try {
    await api('/api/threads/' + tid + '/attach', {});
    if (selection !== state.selection) return;
    snapshot = await api('/api/threads/' + tid + '/sync');
  } catch (error) {
    if (selection === state.selection) {
      state.readOnly = true; state.connectionFailed = true; buttons(); notice(error.message);
      $('run-state').textContent = I18n.t("Connection failed"); $('view-description').textContent = I18n.t("Loading saved history. Choose Connect to retry when the issue is resolved.");
      await previewThread(true).catch(() => {});
    }
    return;
  }
  if (selection !== state.selection) return;
  const syncEpoch = snapshot.transportEpoch, syncSequence = snapshot.snapshotSeq;
  state.lastSeq = snapshot.after;
  updateEvent({method: 'gateway/snapshot', params: snapshot});
  const source = new EventSource('/api/threads/' + tid + '/events?after=' + snapshot.after); state.stream = source;
  snapshot = null;
  source.addEventListener('update', event => {
    if (selection !== state.selection) return;
    const seq = Number(event.lastEventId); if (seq <= state.lastSeq) return;
    state.lastSeq = seq;
    const message = JSON.parse(event.data);
    // The native snapshot already contains these events. A different transport
    // epoch must never be filtered using an old connection's sequence counter.
    if (message.transportEpoch === syncEpoch && message.transportSeq <= syncSequence) return;
    updateEvent(message);
  });
  source.addEventListener('reset', () => {
    if (selection !== state.selection) return;
    source.close(); selectThread(tid).catch(error => notice(error.message));
  });
  source.addEventListener('state', event => {
    if (selection !== state.selection) return;
    const snapshot = JSON.parse(event.data), wasReady = state.ready; state.ready = true; state.online = snapshot.online;
    state.active = !!snapshot.thread.turn_id || snapshot.thread.status === 'running';
    $('run-state').textContent = snapshot.online ? (labels[snapshot.thread.status] || snapshot.thread.status) : I18n.t("Server offline");
    $('run-state').classList.toggle('busy', state.active);
    $('chat-title').textContent = snapshot.thread.title;
    if (window.runActivity) window.runActivity.sync(snapshot.thread, snapshot.requests);
    renderRequests(snapshot.requests); buttons();
    if (!wasReady && window.slashCommands) window.slashCommands.sync();
    if (notice.reconnecting) { notice(''); notice.reconnecting = false; }
  });
  source.onerror = () => { if (selection !== state.selection) return; state.ready = false; notice.reconnecting = true; notice(I18n.t("Disconnected. Reconnecting. Server tasks continue running.")); buttons(); };
}
async function previewThread(initial = false) {
  const tid = state.current, selection = state.selection;
  if (!tid || !state.readOnly || (!initial && (state.previewBusy || Date.now() < (state.previewRetryAt || 0)))) return;
  state.previewBusy = true;
  try {
    const data = await api('/api/threads/' + tid + '/preview');
    if (selection !== state.selection || !state.readOnly) return;
    state.previewFailures = 0; state.previewRetryAt = 0;
    if (!data.thread.read_only && !state.connectionFailed) { await selectThread(tid); return; }
    state.online = data.online; state.ready = true;
    state.active = data.thread.status === 'running';
    $('view-description').textContent = state.connectionFailed ? I18n.t("Live connection failed. Showing saved history. Retry or fork the saved history to continue. The original remains intact; unsaved content cannot be copied.") : data.materialized === false ? I18n.t("This is a new conversation. Send your first message to begin.") : I18n.t("Progress refreshes automatically. Connect or send a message to continue with live updates.");
    $('chat-title').textContent = data.thread.title;
    $('run-state').textContent = labels[data.thread.status] || data.thread.status;
    $('run-state').classList.toggle('busy', state.active);
    for (const turn of data.turns || []) {
      for (const item of turn.items || []) {
        const old = state.items.get(item.id);
        const completed = turn.status !== 'inProgress' || item.status === 'completed' || item.type === 'userMessage';
        if (!old || old.done !== completed || JSON.stringify(old.item) !== JSON.stringify(item)) upsert(item, completed);
      }
      turnMarker(turn);
    }
    if (initial) { if (window.remoteUI) window.remoteUI.historyCursor(data.nextCursor); scrollDown(); }
    if (window.runActivity) window.runActivity.preview(data);
    buttons();
  } catch (error) {
    if (selection === state.selection) {
      state.previewFailures = (state.previewFailures || 0) + 1;
      state.previewRetryAt = Date.now() + Math.min(60000, 5000 * 2 ** Math.min(state.previewFailures - 1, 4));
    }
    throw error;
  } finally { state.previewBusy = false; }
}
const connecting = new Map();
async function connectThread(tid) {
  if (connecting.has(tid)) return connecting.get(tid);
  const work = (async () => {
    await api('/api/threads/' + tid + '/control', {});
    await refresh();
    if (state.current === tid) await selectThread(tid);
  })();
  connecting.set(tid, work);
  try { await work; } finally { connecting.delete(tid); }
}
$('enable-control').onclick = async () => {
  const tid = state.current; $('enable-control').disabled = true;
  try { await connectThread(tid); } catch (error) { notice(error.message); buttons(); }
};
setInterval(() => { if (state.readOnly && !$('app').hidden && !document.hidden) previewThread().catch(error => notice(error.message)); }, 2500);
let workspaceRequest = 0, workspaceListing = null;
function matchWorkspaceQuick() {
  const value = $('workspace').value.trim();
  $('workspace-quick').value = Array.from($('workspace-quick').options).some(o => o.value === value) ? value : '';
}
async function browseWorkspace(path, more = false) {
  const request = ++workspaceRequest;
  const previous = more && workspaceListing && workspaceListing.path === path ? workspaceListing : null;
  $('workspace').value = path; matchWorkspaceQuick();
  $('workspace-browser').hidden = false; $('workspace-location').textContent = path;
  $('workspace-status').className = 'fine'; $('workspace-status').textContent = I18n.t("Loading directories…");
  $('workspace-parent').disabled = true; $('workspace-more').disabled = true;
  if (!previous) { workspaceListing = null; $('workspace-entries').replaceChildren(); $('workspace-more').hidden = true; }
  const query = new URLSearchParams({path, hidden: $('workspace-hidden').checked ? '1' : '0'});
  if (previous && previous.nextCursor) query.set('cursor', previous.nextCursor);
  try {
    const data = await api('/api/workspaces/directories?' + query);
    if (request !== workspaceRequest || !$('new-dialog').open) return;
    const entries = new Map((previous ? previous.entries : []).map(entry => [entry.path, entry]));
    for (const entry of data.entries) entries.set(entry.path, entry);
    workspaceListing = {...data, entries: Array.from(entries.values()).sort((a, b) => a.name.localeCompare(b.name))};
    $('workspace').value = data.path; matchWorkspaceQuick(); $('workspace-location').textContent = data.path;
    const scroll = previous ? $('workspace-entries').scrollTop : 0;
    $('workspace-entries').replaceChildren();
    for (const entry of workspaceListing.entries) {
      const b = el('button', 'workspace-entry'); b.type = 'button'; b.title = entry.path;
      b.setAttribute('aria-label', I18n.t("Enter directory: ") + entry.name);
      const icon = el('span', 'workspace-folder', '▸'); icon.setAttribute('aria-hidden', 'true');
      b.append(icon, el('span', 'workspace-name', entry.name));
      b.onclick = () => browseWorkspace(entry.path); $('workspace-entries').append(b);
    }
    $('workspace-entries').scrollTop = scroll;
    $('workspace-status').textContent = workspaceListing.entries.length ? I18n.t("Select a subdirectory to enter it. New conversations use the directory above.") : data.nextCursor ? I18n.t("No subdirectories on this page. Load more to continue.") : I18n.t("No subdirectories to show. You can create a conversation here.");
    $('workspace-parent').disabled = !data.parent;
    $('workspace-more').hidden = !data.nextCursor;
  } catch (error) {
    if (request !== workspaceRequest || !$('new-dialog').open) return;
    $('workspace-status').className = 'error'; $('workspace-status').textContent = error.message;
    $('workspace-parent').disabled = !(workspaceListing && workspaceListing.parent);
  } finally {
    if (request === workspaceRequest) $('workspace-more').disabled = false;
  }
}
function newDialog() {
  ++workspaceRequest; workspaceListing = null;
  const quick = $('workspace-quick'); quick.replaceChildren();
  const roots = el('optgroup'); roots.label = I18n.t("Configured workspaces"); quick.append(roots);
  const recent = el('optgroup'); recent.label = I18n.t("Recent projects");
  const seen = new Set();
  for (const path of state.workspaces) { const option = el('option', '', path); option.value = path; roots.append(option); seen.add(path); }
  for (const thread of state.threads) {
    if (!thread.cwd || seen.has(thread.cwd)) continue;
    const option = el('option', '', thread.cwd); option.value = thread.cwd; recent.append(option); seen.add(thread.cwd);
    if (recent.childElementCount >= 20) break;
  }
  if (recent.childElementCount) quick.append(recent);
  const custom = el('option', '', I18n.t("Custom path…")); custom.value = ''; quick.append(custom);
  $('workspace').value = (state.threads.find(t => t.id === state.current) || {}).cwd || state.workspaces[0] || '';
  matchWorkspaceQuick(); $('workspace-hidden').checked = false;
  $('chat-name').value = ''; $('new-error').textContent = ''; $('workspace-browser').hidden = true;
  $('new-dialog').showModal(); setDrawer(false);
  if ($('workspace').value) browseWorkspace($('workspace').value);
}
$('workspace-quick').onchange = () => {
  if ($('workspace-quick').value) browseWorkspace($('workspace-quick').value);
  else { ++workspaceRequest; $('workspace-browser').hidden = true; $('workspace').focus(); $('workspace').select(); }
};
$('workspace').oninput = () => { ++workspaceRequest; workspaceListing = null; $('workspace-browser').hidden = true; $('new-error').textContent = ''; matchWorkspaceQuick(); };
$('workspace-browse').onclick = () => browseWorkspace($('workspace').value.trim());
$('workspace-parent').onclick = () => { if (workspaceListing && workspaceListing.parent) browseWorkspace(workspaceListing.parent); };
$('workspace-more').onclick = () => { if (workspaceListing && workspaceListing.nextCursor) browseWorkspace(workspaceListing.path, true); };
$('workspace-hidden').onchange = () => browseWorkspace($('workspace').value.trim());
$('new-dialog').addEventListener('close', () => { ++workspaceRequest; });
$('login-form').onsubmit = async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button'); button.disabled = true; $('login-error').textContent = '';
  try { await api('/api/login', {token: $('token').value.trim()}); $('token').value = ''; await enter(); }
  catch (error) { $('login-error').textContent = error.message; } finally { button.disabled = false; }
};
$('new-form').onsubmit = async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector('button[type=submit]'); button.disabled = true;
  try { const thread = await api('/api/threads', {cwd: $('workspace').value.trim(), title: $('chat-name').value.trim() || 'New conversation'}); await refresh(); $('new-dialog').close(); selectThread(thread.id); }
  catch (error) { $('new-error').textContent = error.message; } finally { button.disabled = false; }
};
let pendingSubmission = null;
$('composer').onsubmit = async event => {
  event.preventDefault(); if ($('send').disabled) return;
  const originalText = $('prompt').value, tid = state.current;
  if (window.slashCommands && window.slashCommands.isCommand(originalText)) { await window.slashCommands.execute(originalText); return; }
  const input = window.slashCommands ? window.slashCommands.literal(originalText) : {text: originalText}, text = input.text;
  const extra = window.remoteUI ? window.remoteUI.messageOptions() : {};
  if (input.literal) extra.literal = true;
  const signature = JSON.stringify({text, tid, extra});
  if (!pendingSubmission || pendingSubmission.signature !== signature) {
    const bytes = crypto.getRandomValues(new Uint8Array(20));
    pendingSubmission = {tid, text, signature, id: Array.from(bytes).map(v => v.toString(16).padStart(2, '0')).join('')};
  }
  state.sending = true; buttons(); notice('');
  try { if (state.readOnly) await connectThread(tid); const result = await api('/api/threads/' + tid + '/message', {text, submissionId: pendingSubmission.id, ...extra}); if (state.current === tid && $('prompt').value === originalText) $('prompt').value = ''; if (window.remoteUI) window.remoteUI.sent(tid, originalText, extra.attachments || []); pendingSubmission = null; await refresh(); if (state.current === tid && result.delivery === 'steer') notice(I18n.t("Added to the current task.")); else if (state.current === tid && result.queued) notice(I18n.t("Queued for after the current turn finishes.")); }
  catch (error) { notice(error.message); }
  finally { state.sending = false; buttons(); }
};
$('prompt').oninput = buttons;
let promptComposing = false, compositionEndedAt = -Infinity;
$('prompt').addEventListener('compositionstart', () => { promptComposing = true; });
$('prompt').addEventListener('compositionend', event => { promptComposing = false; compositionEndedAt = event.timeStamp; });
$('prompt').onkeydown = event => {
  // Safari can end composition immediately before reporting the confirming Enter.
  if (promptComposing || event.isComposing || event.keyCode === 229 || event.timeStamp - compositionEndedAt < 50) return;
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey || event.shiftKey)) {
    event.preventDefault();
    const prompt = $('prompt');
    if (prompt.value.length - (prompt.selectionEnd - prompt.selectionStart) < prompt.maxLength) {
      prompt.setRangeText('\n', prompt.selectionStart, prompt.selectionEnd, 'end');
      prompt.dispatchEvent(new Event('input', {bubbles: true}));
    }
    return;
  }
  if (window.slashCommands && window.slashCommands.key(event)) { event.preventDefault(); return; }
  if (event.key === 'Enter' && !event.altKey) { event.preventDefault(); if (!event.repeat) $('composer').requestSubmit(); }
};
$('stop').onclick = async () => { try { await api('/api/threads/' + state.current + '/interrupt', {}); notice(I18n.t("Stop requested. Waiting for server confirmation.")); } catch (error) { notice(error.message); } };
$('logout').onclick = async () => { try { await api('/api/logout', {}); } finally { showLogin(); $('messages').replaceChildren(); $('requests').replaceChildren(); state.items.clear(); state.current = null; $('prompt').value = ''; pendingSubmission = null; } };
$('menu').onclick = () => setDrawer(true); $('scrim').onclick = () => setDrawer(false);
$('jump-bottom').onclick = () => { scrollDown(); $('timeline').focus({preventScroll: true}); };
$('timeline').addEventListener('scroll', () => {
  const t = $('timeline');
  const resized = t.scrollHeight !== scrollPosition.height || t.clientHeight !== scrollPosition.viewport;
  if (!resized && t.scrollTop < scrollPosition.top - 1) followLatest = false;
  if (t.scrollHeight - t.scrollTop - t.clientHeight <= 2) followLatest = true;
  rememberScroll(); updateJumpBottom();
}, {passive: true});
$('timeline').addEventListener('wheel', event => { if (event.deltaY < 0) followLatest = false; }, {passive: true});
let timelineTouchY = null;
$('timeline').addEventListener('touchstart', event => { timelineTouchY = event.touches[0].clientY; }, {passive: true});
$('timeline').addEventListener('touchmove', event => {
  const y = event.touches[0].clientY;
  if (timelineTouchY !== null && y > timelineTouchY + 2) followLatest = false;
  timelineTouchY = y;
}, {passive: true});
$('timeline').addEventListener('keydown', event => {
  if (['ArrowUp', 'PageUp', 'Home'].includes(event.key) || (event.key === ' ' && event.shiftKey)) followLatest = false;
  if (event.key === 'End') { event.preventDefault(); scrollDown(); }
});
// Keep following through late image/font loads, streamed output, and composer
// resizing. An explicit upward scroll opts out until the user returns to bottom.
const timelineResizeObserver = new ResizeObserver(() => { rememberScroll(); scheduleTail(); });
for (const id of ['timeline', 'messages', 'older']) timelineResizeObserver.observe($(id));
$('new-chat').onclick = newDialog; $('empty-new').onclick = newDialog; $('close-dialog').onclick = () => $('new-dialog').close();
setInterval(() => { if (!$('app').hidden && !document.hidden) refresh().catch(error => notice(error.message)); }, 10000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('app').hidden) refresh().catch(error => notice(error.message)); });
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
// Wait for all deferred UI modules before a fast history response can render.
document.addEventListener('DOMContentLoaded', () => enter().catch(() => showLogin()), {once: true});
