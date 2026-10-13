'use strict';
(() => {
  const phases = {
    reasoning: I18n.t("Thinking…"), commandExecution: I18n.t("Running a command…"), fileChange: I18n.t("Editing files…"),
    mcpToolCall: I18n.t("Calling a tool…"), dynamicToolCall: I18n.t("Calling a tool…"), collabAgentToolCall: I18n.t("Waiting for collaborative tasks…"),
    webSearch: I18n.t("Searching…"), imageView: I18n.t("Viewing an image…"), agentMessage: I18n.t("Responding…"), plan: I18n.t("Updating the plan…"), contextCompaction: I18n.t("Compacting context…")
  };
  let turnId = null, startedAt = null, observedAt = null, lastSync = 0, active = false, waiting = false, retrying = false, retryDetail = '';
  const items = new Map();
  const done = item => ['completed', 'failed', 'declined', 'interrupted', 'cancelled'].includes(item.status);
  const duration = ms => { const seconds = Math.max(0, Math.floor(ms / 1000)); return seconds < 60 ? seconds + I18n.t(" sec") : Math.floor(seconds / 60) + I18n.t(" min ") + seconds % 60 + I18n.t(" sec"); };
  function begin(turn = {}) {
    if (turn.id && turn.id !== turnId) { items.clear(); startedAt = null; observedAt = Date.now(); retrying = false; }
    turnId = turn.id || turnId;
    if (typeof turn.startedAt === 'number' && turn.startedAt > 0) startedAt = turn.startedAt * 1000;
    observedAt = observedAt || Date.now(); active = true;
  }
  function track(item) {
    if (!item || !item.id || !phases[item.type] || done(item)) return;
    // Only phase/type and tool labels are needed. Do not store or display reasoning text.
    items.set(item.id, {type: item.type, command: item.command, tool: item.tool, server: item.server});
  }
  function history(turns) {
    const turn = [...(turns || [])].reverse().find(t => t.status === 'inProgress');
    items.clear();
    if (!turn) { active = false; turnId = null; startedAt = null; observedAt = null; return; }
    begin(turn);
    const list = turn.items || [];
    for (const item of list) if (['inProgress', 'in_progress'].includes(item.status)) track(item);
    if (!items.size && list.length) track(list[list.length - 1]);
  }
  function render() {
    const visible = !!state.current && (active || state.active || state.sending);
    $('activity').hidden = !visible;
    if (!visible) return;
    const now = Date.now(), latest = [...items.values()].pop();
    const stale = !!lastSync && now - lastSync > 12000;
    let title = I18n.t("Working…"), detail = '', mode = 'working';
    if (navigator.onLine && state.online && !state.ready && !lastSync) {
      title = I18n.t("Syncing task status…"); detail = I18n.t("Connecting to the server and loading progress.");
    } else if (!navigator.onLine || !state.online || !state.ready || stale) {
      title = stale && navigator.onLine && state.ready && state.online ? I18n.t("Waiting for a connection update…") : I18n.t("Disconnected. Reconnecting…");
      detail = I18n.t("Unable to confirm progress. The server task may still be running."); mode = 'disconnected';
    } else if (state.sending && !active && !state.active) {
      title = I18n.t("Sending…"); detail = I18n.t("Submitting to the server.");
    } else if (waiting) {
      title = I18n.t("Waiting for your response…"); detail = I18n.t("Respond to the approvals or questions below."); mode = 'waiting';
    } else if (retrying) {
      title = I18n.t("Retrying model connection…"); detail = retryDetail || I18n.t("The server is still trying to connect to the model."); mode = 'waiting';
    } else if (latest) {
      title = phases[latest.type];
      if (latest.type === 'commandExecution') detail = (latest.command || '').replace(/\s+/g, ' ').slice(0, 120);
      else if (latest.tool) detail = [latest.server, latest.tool].filter(Boolean).join(' / ');
    }
    observedAt = observedAt || now;
    const elapsed = (startedAt ? I18n.t("Turn running for ") : I18n.t("Observed on this page for ")) + duration(now - (startedAt || observedAt));
    const sync = lastSync ? I18n.t("Last synced ") + duration(now - lastSync) + I18n.t(" ago") : I18n.t("Syncing");
    $('activity').dataset.mode = mode;
    if ($('activity-title').textContent !== title) $('activity-title').textContent = title;
    $('activity-detail').textContent = detail;
    $('activity-detail').hidden = !detail;
    $('activity-time').textContent = elapsed + ' · ' + sync;
  }
  window.runActivity = {
    reset() { turnId = null; startedAt = null; observedAt = null; lastSync = 0; active = false; waiting = false; retrying = false; retryDetail = ''; items.clear(); render(); },
    render,
    sync(thread, requests = []) {
      lastSync = Date.now(); active = !!thread.turn_id || thread.status === 'running';
      waiting = requests.some(r => (r.params || {}).isBlocking !== false);
      retrying = !!thread.retry_error; retryDetail = thread.retry_error || '';
      if (!active) { items.clear(); turnId = null; startedAt = null; observedAt = null; retrying = false; }
      render();
    },
    preview(data) { history(data.turns); this.sync(data.thread); },
    event(message) {
      lastSync = Date.now();
      const m = message.method, p = message.params || {};
      if (m === 'gateway/snapshot') history(p.turns);
      else if (m === 'turn/started') { items.clear(); waiting = false; retrying = false; begin(p.turn); }
      else if (m === 'turn/completed' || m === 'gateway/interrupted') { active = false; items.clear(); waiting = false; retrying = false; }
      else if (m === 'item/started') { retrying = false; track(p.item); }
      else if (m === 'item/completed') items.delete((p.item || {}).id);
      else if (m === 'item/agentMessage/delta') { retrying = false; track({id: p.itemId, type: 'agentMessage'}); }
      else if (m.startsWith('item/reasoning/')) { retrying = false; track({id: p.itemId, type: 'reasoning'}); }
      else if (m === 'item/commandExecution/outputDelta') { retrying = false; if (!items.has(p.itemId)) track({id: p.itemId, type: 'commandExecution'}); }
      else if (m === 'error' && p.willRetry) { retrying = true; retryDetail = modelErrorText(p.error || p.message); }
      else if (m === 'error' && !p.willRetry) { active = false; items.clear(); waiting = false; retrying = false; }
      render();
    }
  };
  window.addEventListener('offline', render);
  window.addEventListener('online', render);
  setInterval(() => { if (!document.hidden) render(); }, 1000);
})();
