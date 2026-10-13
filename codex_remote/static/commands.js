'use strict';
(() => {
  const commands = [
    ['goal', I18n.t("Ongoing goal"), I18n.t("Create, view and manage this conversation goal")],
    ['goal pause', I18n.t("Pause goal"), I18n.t("Pause automatic goal progress")],
    ['goal resume', I18n.t("Resume goal"), I18n.t("Resume a paused or blocked goal")],
    ['goal edit', I18n.t("Edit goal"), I18n.t("Edit the objective and optional token budget")],
    ['goal clear', I18n.t("Clear goal"), I18n.t("Remove the current goal")],
    ['model', I18n.t("Model settings"), I18n.t("Choose the model and reasoning effort for the next turn")],
    ['reasoning', I18n.t("Reasoning effort"), I18n.t("Open model and reasoning settings")],
    ['permission', I18n.t("Permissions"), I18n.t("View permissions and enable Full Access")],
    ['permissions', I18n.t("Permissions"), I18n.t("Alias for /permission")],
    ['status', I18n.t("Conversation status"), I18n.t("View conversation, model, execution and goal status")],
    ['compact', I18n.t("Compact context"), I18n.t("Compact conversation context while idle")],
    ['new', I18n.t("New conversation"), I18n.t("Choose a project and start a conversation")],
    ['resume', I18n.t("Continue conversation"), I18n.t("Open the conversation list or connect by ID")],
    ['rename', I18n.t("Rename"), I18n.t("Rename this conversation")],
    ['fork', I18n.t("Fork conversation"), I18n.t("Fork this conversation")],
    ['stop', I18n.t("Stop task"), I18n.t("Stop the task and cancel queued messages")],
    ['help', I18n.t("Command help"), I18n.t("View supported web commands")],
  ];
  const goalLabels = {active: I18n.t("Goal active"), paused: I18n.t("Goal paused"), blocked: I18n.t("Goal blocked"), usageLimited: I18n.t("Usage limit reached"), budgetLimited: I18n.t("Budget reached"), complete: I18n.t("Goal completed")};
  let goal = null, goalThread = null, goalVersion = 0, busy = false, pending = null;
  let visible = [], selected = 0, dismissed = null, lastQuery = null;
  let dialogThread = null, dialogIdentity = null, editing = false, polling = false;
  let draftVersion = 0;
  let permissionThread = null;
  $('prompt').addEventListener('input', () => { draftVersion++; dismissed = null; renderMenu(); });
  const parsed = value => /^\/([a-z][a-z0-9_-]*)(?:\s+([\s\S]*))?$/i.exec(value.trim());
  const isCommand = value => value.trim() === '/' || !!parsed(value);
  const identity = value => value ? {objective: value.objective, createdAt: value.createdAt} : null;
  const number = value => Number(value || 0).toLocaleString();
  function usage(value) {
    return number(value.tokensUsed) + (value.tokenBudget ? ' / ' + number(value.tokenBudget) : '') + ' tokens · ' + Math.floor((value.timeUsedSeconds || 0) / 60) + I18n.t(" min ") + (value.timeUsedSeconds || 0) % 60 + I18n.t(" sec");
  }
  function renderGoal() {
    const present = goal && goalThread === state.current;
    $('goal-panel').hidden = !present;
    if (!present) return;
    $('goal-status').textContent = goalLabels[goal.status] || goal.status;
    $('goal-objective-preview').textContent = goal.objective;
    $('goal-usage').textContent = usage(goal);
    $('goal-panel').dataset.status = goal.status;
    $('goal-toggle').textContent = goal.status === 'active' ? I18n.t("Pause") : I18n.t("Resume");
    $('goal-toggle').hidden = goal.status === 'complete';
    for (const id of ['goal-toggle', 'goal-edit', 'goal-clear']) $(id).disabled = busy || !state.online || !state.ready || state.readOnly;
    if ($('goal-dialog').open && dialogThread === goalThread) $('goal-dialog-status').textContent = (goalLabels[goal.status] || goal.status) + ' · ' + usage(goal);
  }
  function acceptGoal(tid, value) {
    if (tid !== state.current) return;
    goal = value; goalThread = tid; goalVersion++; renderGoal();
  }
  async function refreshGoal(report = false) {
    const tid = state.current, version = goalVersion;
    if (!tid || state.readOnly || (!state.ready && !report) || !state.online) return;
    try {
      const result = await api('/api/threads/' + tid + '/goal');
      if (tid === state.current && version === goalVersion && !result.needsConnection) acceptGoal(tid, result.goal);
    } catch (error) { if (report) throw error; }
  }
  async function connected(tid) {
    if (!tid) throw new Error(I18n.t("Choose or create a conversation first."));
    if (state.current !== tid) throw new Error(I18n.t("The conversation changed. Run this again in the intended conversation."));
    if (state.readOnly) await connectThread(tid);
    if (state.current !== tid) throw new Error(I18n.t("The conversation changed. Run this again in the intended conversation."));
  }
  async function native(tid, body) {
    await connected(tid);
    const signature = JSON.stringify({tid, body});
    if (!pending || pending.signature !== signature) {
      pending = {signature, id: Array.from(crypto.getRandomValues(new Uint8Array(20)), v => v.toString(16).padStart(2, '0')).join('')};
    }
    const result = await api('/api/threads/' + tid + '/command', {...body, submissionId: pending.id});
    pending = null;
    // A retried request may have a historical result. Read current native state
    // instead of overwriting later updates with that older result.
    if (body.command === 'goal') await refreshGoal();
    return result;
  }
  async function action(work, errorId = 'notice') {
    if (busy) return false;
    busy = true; buttons(); $(errorId).textContent = '';
    try { await work(); return true; }
    catch (error) { $(errorId).textContent = error.message; return false; }
    finally { busy = false; buttons(); }
  }
  function closeMenu() {
    $('slash-menu').hidden = true; $('prompt').setAttribute('aria-expanded', 'false'); $('prompt').removeAttribute('aria-activedescendant');
  }
  function renderMenu() {
    const input = $('prompt').value.trim(), query = input.toLowerCase();
    if (!input.startsWith('/') || input.startsWith('//') || input.includes('\n') || dismissed === input) { closeMenu(); return; }
    const matches = commands.filter(c => c[0].startsWith(query.slice(1)));
    if (!matches.length) { closeMenu(); return; }
    if (lastQuery !== query) { selected = 0; lastQuery = query; }
    visible = matches;
    selected = Math.min(selected, visible.length - 1);
    $('slash-options').replaceChildren();
    visible.forEach((command, index) => {
      const row = el('button', 'slash-option'); row.type = 'button'; row.id = 'slash-option-' + index;
      row.setAttribute('role', 'option'); row.setAttribute('aria-selected', String(index === selected)); row.tabIndex = -1;
      const name = el('span', 'slash-name', '/' + command[0]), info = el('span', 'slash-description', command[2]);
      row.append(name, info); row.onclick = () => choose(command); $('slash-options').append(row);
    });
    $('slash-menu').hidden = false; $('prompt').setAttribute('aria-expanded', 'true'); $('prompt').setAttribute('aria-activedescendant', 'slash-option-' + selected);
  }
  function choose(command) {
    $('prompt').value = '/' + command[0]; draftVersion++; dismissed = $('prompt').value; closeMenu(); buttons();
    $('composer').requestSubmit();
  }
  function help() {
    $('command-help-list').replaceChildren();
    for (const command of commands) {
      const button = el('button', 'help-command'); button.type = 'button';
      button.append(el('code', '', '/' + command[0]), el('span', '', command[2]));
      button.onclick = () => { $('command-help').close(); choose(command); };
      $('command-help-list').append(button);
    }
    $('command-help').showModal();
  }
  async function openGoal(tid, objective) {
    await connected(tid); await refreshGoal(true);
    if (state.current !== tid) return;
    dialogThread = tid; dialogIdentity = identity(goal); editing = !!goal && objective === undefined;
    $('goal-dialog-status').textContent = goal ? (goalLabels[goal.status] || goal.status) + ' · ' + usage(goal) : I18n.t("This conversation has no goal yet");
    $('goal-objective').value = objective === undefined ? (goal ? goal.objective : '') : objective;
    $('goal-budget').value = goal && goal.tokenBudget ? goal.tokenBudget : '';
    $('goal-save').textContent = editing ? I18n.t("Save goal") : I18n.t("Start goal");
    $('goal-error').textContent = ''; $('goal-dialog').showModal();
  }
  function renderPermissions(value) {
    $('permission-current').textContent = value.needsConnection ? I18n.t("Connect to view permissions") : value.fullAccess ? I18n.t("Full Access") : value.known ? I18n.t("Sandbox: ") + value.sandboxPolicy.type + I18n.t(" · Approval: ") + (typeof value.approvalPolicy === 'string' ? value.approvalPolicy : I18n.t("Custom")) : I18n.t("Permission state has not arrived yet");
    $('permission-timing').textContent = value.nextTurn ? I18n.t("The task is still running. Permissions apply to the next turn; current approvals are unchanged.") : I18n.t("Permissions apply to subsequent messages.");
    $('permission-default').textContent = value.defaultFullAccess ? I18n.t("Gateway default: Full Access. Applied to new and explicitly connected conversations.") : I18n.t("Full Access can be enabled for this conversation.");
  }
  async function refreshPermissions(tid) {
    const value = await api('/api/threads/' + tid + '/permissions');
    if (state.current === tid && permissionThread === tid) renderPermissions(value);
    return value;
  }
  async function openPermissions(tid) {
    await connected(tid);
    permissionThread = tid;
    await refreshPermissions(tid);
    if (state.current !== tid) return;
    $('permission-error').textContent = ''; $('permission-dialog').showModal();
  }
  async function enableFullAccess(tid) {
    await native(tid, {command: 'permissions', action: 'full-access'});
    const value = await refreshPermissions(tid);
    notice(value.nextTurn ? I18n.t("Full Access is set for the next turn. The current task continues.") : I18n.t("Full Access enabled."));
  }
  async function status(tid) {
    if (!tid) throw new Error(I18n.t("Choose a conversation first."));
    const data = await refresh();
    if (state.current !== tid) return;
    const thread = data.threads.find(t => t.id === tid);
    await refreshGoal();
    const permissions = await refreshPermissions(tid);
    if (state.current !== tid) return;
    const rows = [ [I18n.t("Conversation"), thread.title], [I18n.t("Conversation ID"), tid], [I18n.t("Project"), thread.cwd],
      [I18n.t("Status"), labels[thread.status] || thread.status], [I18n.t("Model"), thread.model || I18n.t("Use server settings")],
      [I18n.t("Reasoning effort"), thread.effort || I18n.t("Use server settings")], [I18n.t("Connect"), state.readOnly ? I18n.t("History preview") : I18n.t("Live connection")],
      [I18n.t("Goal"), state.readOnly ? I18n.t("Connect to view") : goal ? goal.objective : I18n.t("Not set")],
      [I18n.t("Permissions (subsequent turns)"), permissions.needsConnection ? I18n.t("Connect to view") : permissions.fullAccess ? I18n.t("Full Access") : permissions.known ? permissions.sandboxPolicy.type + ' / ' + JSON.stringify(permissions.approvalPolicy) : I18n.t("Not synced yet")],
    ];
    if (goal && !state.readOnly) rows.push([I18n.t("Goal status"), goalLabels[goal.status] || goal.status], [I18n.t("Goal usage"), usage(goal)]);
    $('command-status-content').replaceChildren();
    for (const [name, value] of rows) $('command-status-content').append(el('dt', '', name), el('dd', '', value));
    $('command-status').showModal();
  }
  async function execute(text) {
    const original = text, tid = state.current, version = draftVersion;
    if ((state.attachments || []).length) { notice(I18n.t("Commands do not accept attachments. Remove them before running a command; your draft is still here.")); return; }
    const match = parsed(text), name = match ? match[1].toLowerCase() : 'help', args = match ? (match[2] || '').trim() : '';
    const success = await action(async () => {
      if (!commands.some(c => c[0] === name)) throw new Error(I18n.t("This web client does not support /") + name + I18n.t(". Use /help for supported commands or // to send literal slash text."));
      if (!['goal', 'rename', 'resume', 'permission', 'permissions'].includes(name) && args) throw new Error('/' + name + I18n.t(" does not accept text arguments. Run it to open its panel."));
      if (name === 'goal') {
        const sub = /^(pause|resume|clear|edit)(?:\s+([\s\S]*))?$/.exec(args);
        if (!args || (sub && sub[1] === 'edit' && !sub[2])) await openGoal(tid);
        else {
          const operation = sub ? sub[1] : 'set';
          if (sub && operation !== 'edit' && sub[2]) throw new Error('/goal ' + operation + I18n.t(" does not accept additional arguments."));
          const body = {command: 'goal', action: operation};
          if (operation === 'set' || operation === 'edit') body.objective = sub ? sub[2] : args;
          await native(tid, body);
          notice(({set: I18n.t("Goal set. Codex will continue working on it."), edit: I18n.t("Goal updated."), pause: I18n.t("Goal paused."), resume: I18n.t("Goal resume requested."), clear: I18n.t("Goal cleared.")})[operation]);
        }
      } else if (name === 'help') help();
      else if (name === 'new') newDialog();
      else if (name === 'model' || name === 'reasoning') $('model-open').click();
      else if (name === 'permission' || name === 'permissions') {
        if (!args) await openPermissions(tid);
        else if (/^(full|full-access|full access)$/i.test(args)) await enableFullAccess(tid);
        else throw new Error(I18n.t("Use /permission to view permissions, or /permission full to enable Full Access."));
      }
      else if (name === 'status') await status(tid);
      else if (name === 'resume') {
        if (args) {
          const existing = state.threads.find(t => t.id === args);
          const thread = existing || await api('/api/import', {threadId: args});
          await refresh(); await selectThread(thread.id, true);
        } else { setDrawer(true); $('thread-search').focus(); }
      } else {
        await connected(tid);
        if (name === 'rename') {
          if (!args) $('thread-tools').click();
          else { await api('/api/threads/' + tid + '/rename', {title: args}); await refresh(); if (state.current === tid) $('chat-title').textContent = args; notice(I18n.t("Conversation renamed.")); }
        } else if (name === 'fork') {
          const thread = await api('/api/threads/' + tid + '/fork', {}); await refresh(); await selectThread(thread.id);
        } else if (name === 'compact') { await native(tid, {command: 'compact'}); notice(I18n.t("Context compaction requested. Progress will appear in the conversation.")); }
        else if (name === 'stop') { await api('/api/threads/' + tid + '/interrupt', {}); notice(I18n.t("Requested task interruption and queue cancellation.")); }
      }
    });
    if (success && version === draftVersion && state.current === tid && $('prompt').value === original) $('prompt').value = '';
    if (success && version === draftVersion && window.remoteUI) window.remoteUI.commandSent(tid, original);
    closeMenu(); buttons();
  }
  window.slashCommands = {
    isCommand, execute,
    literal(value) { const trimmed = value.trimStart(); return trimmed.startsWith('//') ? {text: value.replace('//', '/'), literal: true} : {text: value}; },
    render() {
      renderMenu(); renderGoal();
      $('goal-save').disabled = busy || !state.online;
      $('permission-full').disabled = busy || !state.online || state.readOnly;
      if (busy) $('send').disabled = true;
      if (isCommand($('prompt').value)) { $('composer-hint').textContent = I18n.t("Enter to run · Ctrl / ⌘ + Enter for a new line · ↑↓ to choose"); $('send').setAttribute('aria-label', I18n.t("Run command")); $('send').title = I18n.t("Run command"); }
    },
    key(event) {
      if (event.isComposing) return false;
      if (event.key === 'Escape' && !$('slash-menu').hidden) { dismissed = $('prompt').value.trim(); closeMenu(); return true; }
      if (!$('slash-menu').hidden && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
        selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length; renderMenu(); $('slash-option-' + selected).scrollIntoView({block: 'nearest'}); return true;
      }
      if (event.key === 'Tab' && !$('slash-menu').hidden) { $('prompt').value = '/' + visible[selected][0]; dismissed = null; buttons(); return true; }
      if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.repeat && isCommand($('prompt').value)) {
        if (!$('slash-menu').hidden && !commands.some(c => '/' + c[0] === $('prompt').value.trim())) choose(visible[selected]);
        else $('composer').requestSubmit();
        return true;
      }
      dismissed = null; return false;
    },
    switched() { goal = null; goalThread = null; goalVersion++; dismissed = null; lastQuery = null; permissionThread = null; renderGoal(); $('goal-dialog').close(); $('permission-dialog').close(); $('command-status').close(); },
    event(message) {
      const p = message.params || {};
      if (p.threadId !== state.current) return;
      if (message.method === 'thread/goal/updated') acceptGoal(p.threadId, p.goal);
      if (message.method === 'thread/goal/cleared') acceptGoal(p.threadId, null);
      if (permissionThread === p.threadId && $('permission-dialog').open && ['thread/settings/updated', 'gateway/permissions', 'turn/completed', 'turn/started'].includes(message.method)) refreshPermissions(p.threadId).catch(() => {});
    },
    sync() { if (!polling) { polling = true; refreshGoal().finally(() => { polling = false; }); } },
    clear() {
      goal = null; goalThread = null; goalVersion++; pending = null; dialogThread = null; dialogIdentity = null; permissionThread = null;
      $('permission-dialog').close(); $('permission-current').textContent = ''; $('permission-error').textContent = '';
      $('goal-objective').value = ''; $('goal-budget').value = ''; $('goal-dialog-status').textContent = '';
      $('goal-objective-preview').textContent = ''; $('goal-usage').textContent = ''; $('goal-error').textContent = '';
      $('command-status-content').replaceChildren(); renderGoal(); closeMenu();
    },
  };
  $('goal-form').onsubmit = event => {
    event.preventDefault();
    action(async () => {
      const body = {command: 'goal', action: editing ? 'edit' : 'set', objective: $('goal-objective').value, expectedGoal: dialogIdentity};
      if ($('goal-budget').value) body.tokenBudget = Number($('goal-budget').value);
      await native(dialogThread, body); $('goal-dialog').close(); notice(I18n.t("Goal saved."));
    }, 'goal-error');
  };
  $('permission-full').onclick = () => action(() => enableFullAccess(permissionThread), 'permission-error');
  $('goal-edit').onclick = () => action(() => openGoal(state.current));
  $('goal-toggle').onclick = () => action(async () => { const operation = goal.status === 'active' ? 'pause' : 'resume'; await native(state.current, {command: 'goal', action: operation, expectedGoal: identity(goal)}); });
  $('goal-clear').onclick = () => action(() => native(state.current, {command: 'goal', action: 'clear', expectedGoal: identity(goal)}));
  $('slash-open').onclick = () => { if (state.current && !$('prompt').value.trim()) { $('prompt').value = '/'; dismissed = null; buttons(); $('prompt').focus(); } else help(); };
  setInterval(() => { if (!$('app').hidden && !document.hidden) window.slashCommands.sync(); }, 5000);
})();
