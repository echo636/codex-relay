'use strict';
(() => {
  let lastSyncError = '';
  let boot = {}, models = [], historyCursor = null, filePath = '', fileDirectory = '';
  const drafts = new Map(), questionDrafts = new Map();
  let restoredThread = null;
  try {
    const saved = JSON.parse(sessionStorage.getItem('codex-remote-language-drafts') || 'null');
    sessionStorage.removeItem('codex-remote-language-drafts');
    if (saved && Date.now() - saved.time < 300000) {
      for (const [id, draft] of saved.drafts) drafts.set(id, draft);
      for (const [id, draft] of saved.questionDrafts || []) questionDrafts.set(id, draft);
      restoredThread = saved.current;
      if (saved.submission) pendingSubmission = saved.submission;
    }
  } catch (_) {}
  const uploads = new Map();
  let uploadEpoch = 0;
  const settings = {model: null, effort: null};
  state.attachments = [];
  const closeDialogs = () => document.querySelectorAll('dialog[open]').forEach(d => d.close());
  const safeUrl = value => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch (_) { return null; } };
  async function action(work, errorId) { try { await work(); } catch (error) { if (errorId) $(errorId).textContent = error.message; else notice(error.message); } }
  function markdown(parent, text, complete = true, context = {}) { window.messageRenderer.markdown(parent, text, complete, context); }
  function toolContent(details, item, text) {
    const content = el('div', 'content tool-content');
    const block = (source, language, label) => {
      const node = el('div'); window.messageRenderer.code(node, source, language, label); content.append(node);
    };
    if (item.type === 'commandExecution') {
      block(item.command || '', 'bash', I18n.t("Shell · Command"));
      if (item.aggregatedOutput) block(item.aggregatedOutput, 'text', I18n.t("Output"));
    } else if (item.type === 'fileChange') {
      for (const change of item.changes || []) block(change.diff || JSON.stringify(change.kind, null, 2) || '', change.diff ? 'diff' : 'json', change.path || I18n.t("File changes"));
    } else if (item.type === 'plan') markdown(content, text);
    else block(text, 'json', I18n.t("JSON · Tool data"));
    details.append(content);
  }
  function renderAttachments() {
    $('attachments').replaceChildren();
    for (const file of state.attachments) {
      const chip = el('span', 'attachment-chip');
      if (file.mime.startsWith('image/')) { const img = el('img'); img.src = '/api/uploads/' + file.id; img.alt = ''; chip.append(img); }
      const a = el('a', '', file.name); a.href = '/api/uploads/' + file.id; a.target = '_blank'; a.rel = 'noopener'; chip.append(a);
      const remove = el('button', '', '×'); remove.type = 'button'; remove.setAttribute('aria-label', I18n.t("Remove attachment ") + file.name); remove.onclick = () => { state.attachments = state.attachments.filter(f => f.id !== file.id); renderAttachments(); }; chip.append(remove); $('attachments').append(chip);
    }
    buttons();
  }
  function attachmentsFor(tid) {
    if (tid === state.current) return state.attachments;
    if (!drafts.has(tid)) drafts.set(tid, {text: '', attachments: []});
    return drafts.get(tid).attachments;
  }
  function transferFiles(data) {
    if (!data) return [];
    // Use one representation only: browsers often expose each file in both.
    const files = Array.from(data.files || []);
    return files.length ? files : Array.from(data.items || []).filter(item => item.kind === 'file').map(item => item.getAsFile()).filter(Boolean);
  }
  function addFiles(files) {
    if (!files.length) return;
    const tid = state.current, epoch = uploadEpoch;
    if (!tid || $('app').hidden) { notice(I18n.t("Choose a conversation before adding attachments.")); return; }
    const job = uploads.get(tid) || {count: 0, chain: Promise.resolve()};
    if (attachmentsFor(tid).length + job.count + files.length > 8) { notice(I18n.t("Up to 8 attachments per message, including uploads in progress.")); return; }
    if (files.some(file => file.size > 10 * 1024 * 1024)) { notice(I18n.t("Each attachment must be at most 10 MiB. Choose a smaller file.")); return; }
    job.count += files.length; uploads.set(tid, job); buttons();
    // Reserve slots immediately and serialize each conversation's uploads.
    // A delayed upload always returns to its original draft, even after switching.
    job.chain = job.chain.then(async () => {
      for (const file of files) {
        if (epoch !== uploadEpoch) return;
        const extension = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/gif': '.gif'}[file.type] || '';
        const name = file.name || 'clipboard-' + Date.now() + extension;
        try {
          const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = reader.onabort = () => reject(new Error(I18n.t("Unable to read the file. Copy or select it again.")));
            reader.readAsDataURL(file);
          });
          if (epoch !== uploadEpoch) return;
          const result = await api('/api/uploads', {name, data: dataUrl.split(',')[1]});
          if (epoch !== uploadEpoch) return;
          attachmentsFor(tid).push(result);
          if (state.current === tid) renderAttachments();
        } catch (error) {
          if (epoch === uploadEpoch) notice(name + I18n.t(" failed to upload: ") + error.message);
        } finally {
          if (uploads.get(tid) === job) { job.count--; buttons(); }
        }
      }
    }).finally(() => {
      if (uploads.get(tid) === job && !job.count) uploads.delete(tid);
      buttons();
    });
  }
  function inputForSchema(schema, container, prefix) {
    // Complex plugin forms use a JSON editor with authoritative server-side JSON Schema validation.
    const properties = schema.properties || {};
    const simple = schema.type === 'object' && Object.values(properties).every(p => ['string', 'number', 'integer', 'boolean'].includes(p.type));
    if (!simple) {
      const input = el('textarea', 'json-editor'); input.rows = 7; input.value = JSON.stringify(schema.default || {}, null, 2); input.setAttribute('aria-label', I18n.t("Form JSON")); container.append(input);
      const details = el('details'); details.append(el('summary', '', I18n.t("View form requirements")), el('pre', '', JSON.stringify(schema, null, 2))); container.append(details);
      return () => JSON.parse(input.value);
    }
    const fields = [];
    for (const [key, spec] of Object.entries(properties)) {
      const id = prefix + '-' + fields.length;
      const label = el('label', '', (spec.title || key) + ((schema.required || []).includes(key) ? ' *' : '')); label.htmlFor = id; container.append(label);
      let input;
      if (spec.enum || spec.oneOf) { input = el('select'); const values = spec.enum || spec.oneOf.map(o => o.const); for (let i = 0; i < values.length; i++) { const o = el('option', '', spec.enumNames ? spec.enumNames[i] : spec.oneOf ? spec.oneOf[i].title : String(values[i])); o.value = values[i]; input.append(o); } }
      else { input = el('input'); input.type = spec.type === 'boolean' ? 'checkbox' : ['number', 'integer'].includes(spec.type) ? 'number' : spec.format === 'email' ? 'email' : 'text'; if (spec.minimum !== undefined) input.min = spec.minimum; if (spec.maximum !== undefined) input.max = spec.maximum; }
      input.id = id; if (spec.default !== undefined) { if (spec.type === 'boolean') input.checked = spec.default; else input.value = spec.default; } container.append(input);
      if (spec.description) container.append(el('p', 'fine', spec.description)); fields.push([key, spec, input]);
    }
    return () => { const value = {}; for (const [key, spec, input] of fields) { if (spec.type === 'boolean') value[key] = input.checked; else if (input.value !== '' || (schema.required || []).includes(key)) value[key] = ['number', 'integer'].includes(spec.type) ? Number(input.value) : input.value; } return value; };
  }
  function specialRequest(request) {
    if (request.method !== 'mcpServer/elicitation/request') return false;
    const p = request.params, card = el('section', 'request');
    card.append(el('h3', '', I18n.t("A plugin needs your response · ") + p.serverName), el('p', '', p.message));
    let value = () => null;
    if (p.mode === 'url') { const url = safeUrl(p.url); if (url) { const a = el('a', 'small-button', I18n.t("Open verification page ↗")); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; card.append(a); } else card.append(el('p', 'error', I18n.t("Invalid verification link."))); }
    else value = inputForSchema(p.requestedSchema || {}, card, request.key);
    const errorNode = el('p', 'error'), actions = el('div', 'request-actions');
    for (const [decision, label] of [['accept', p.mode === 'url' ? I18n.t("Done, continue") : I18n.t("Submit form")], ['decline', I18n.t("Deny")], ['cancel', I18n.t("Cancel")]]) {
      const b = el('button', decision === 'accept' ? 'approve' : 'decline', label);
      b.onclick = async () => { try { const content = decision === 'accept' ? value() : null; b.disabled = true; await api('/api/requests/' + request.key + '/respond', {action: decision, content}); card.remove(); } catch (error) { errorNode.textContent = error.message; b.disabled = false; } }; actions.append(b);
    }
    card.append(errorNode, actions); $('requests').append(card); return true;
  }
  async function refreshQueue() {
    if (!state.current || $('app').hidden) return;
    const tid = state.current, data = await api('/api/threads/' + tid + '/queue'); if (state.current !== tid) return;
    $('queue-list').replaceChildren();
    for (const item of data.queue) { const row = el('div', 'queue-row'); row.append(el('span', '', ({pending: I18n.t("After this turn"), sending: I18n.t("Sending"), uncertain: I18n.t("Result unconfirmed"), failed: I18n.t("Send failed")}[item.status] || item.status) + ' · ' + (item.body.text || I18n.t("Message with attachments")).slice(0, 65))); const b = el('button', 'small-button', I18n.t("Cancel")); b.disabled = state.readOnly || item.status === 'sending'; b.onclick = () => action(async () => { await api('/api/threads/' + tid + '/cancel', {id: item.id}); await refreshQueue(); }); row.append(b); $('queue-list').append(row); }
  }
  function imageView(details, item) {
    const url = '/api/threads/' + encodeURIComponent(state.current) + '/image?itemId=' + encodeURIComponent(item.id);
    const figure = el('figure', 'image-preview'), link = el('a', 'image-link'), img = el('img');
    const name = (item.path || '').split('/').pop() || I18n.t("Viewed image");
    const caption = el('figcaption'), status = el('span', 'image-status', I18n.t("Expand to load image"));
    const original = el('a', 'image-original', I18n.t("Open original ↗"));
    for (const a of [link, original]) { a.href = url; a.target = '_blank'; a.rel = 'noopener'; }
    img.alt = name; img.decoding = 'async'; img.hidden = true;
    link.setAttribute('aria-label', I18n.t("Open original: ") + name); link.append(img);
    const retry = el('button', 'small-button', I18n.t("Reload")); retry.type = 'button'; retry.hidden = true;
    let loaded = false;
    function load() { loaded = true; status.textContent = I18n.t("Loading image…"); retry.hidden = true; img.hidden = true; img.src = url; }
    img.onload = () => { img.hidden = false; status.textContent = img.naturalWidth + ' × ' + img.naturalHeight; if (details.open && details.isConnected) details.scrollIntoView({block: 'nearest'}); };
    img.onerror = () => { img.hidden = true; status.textContent = I18n.t("Image unavailable. The original file may have moved or been removed."); retry.hidden = false; };
    retry.onclick = load;
    caption.append(status, original); figure.append(link, caption, retry); details.append(figure);
    details.addEventListener('toggle', () => { if (details.open && !loaded) load(); });
    if (details.open) load();
  }
  const questionEntries = new Map(), answerHistory = new Map();
  function questionReplies(text) {
    if (typeof text !== 'string') return null;
    const match = /^\s*<send_user_message_question_reply>\s*([\s\S]*?)\s*<\/send_user_message_question_reply>\s*$/.exec(text);
    if (!match) return null;
    try {
      const replies = JSON.parse(match[1]);
      if (!Array.isArray(replies) || !replies.length) return null;
      for (const reply of replies) {
        if (!reply || typeof reply.answer !== 'string' || typeof reply.question !== 'string' || typeof reply.questionItemId !== 'string') return null;
        const id = JSON.parse(reply.questionItemId);
        if (!Array.isArray(id) || id.length !== 3 || id[0] !== 'request_user_input_async' || typeof id[1] !== 'string' || !Number.isSafeInteger(id[2]) || id[2] < 0) return null;
        reply.questionItemId = JSON.stringify(id);
      }
      return replies;
    } catch (_) { return null; }
  }
  function rememberAnswers(tid, replies) {
    if (!answerHistory.has(tid)) answerHistory.set(tid, new Map());
    for (const reply of replies) answerHistory.get(tid).set(reply.questionItemId, reply);
    questionButtons();
  }
  function observeAnswers(item) {
    if (item.type !== 'userMessage') return;
    for (const content of item.content || []) {
      const replies = content.type === 'text' && questionReplies(content.text);
      if (replies) rememberAnswers(state.current, replies);
    }
  }
  function updateQuestion(entry) {
    const saved = answerHistory.get(entry.tid);
    let remaining = 0, hasAnswer = false;
    for (const field of entry.fields) {
      const reply = saved?.get(field.key), answered = reply && reply.question === field.question.title;
      field.answered = !!answered;
      field.controls.hidden = !!answered; field.summary.hidden = !answered;
      field.fieldset.disabled = !!answered || entry.sending;
      if (answered) field.summary.textContent = I18n.t('Answered: ') + reply.answer;
      else { remaining++; if (field.value().trim()) hasAnswer = true; }
    }
    entry.form.dataset.status = remaining ? (entry.sending ? 'sending' : 'pending') : 'answered';
    entry.submit.hidden = !remaining;
    entry.submit.disabled = entry.sending || !hasAnswer || state.current !== entry.tid || !state.online || !state.ready || state.sending;
    entry.submit.textContent = entry.sending ? I18n.t('Sending…') : I18n.t('Send answer');
    entry.status.textContent = remaining ? I18n.t('You can answer while Codex keeps working.') : I18n.t('✓ Answers sent');
  }
  function questionButtons() {
    for (const entry of questionEntries.values()) if (entry.form.isConnected && entry.tid === state.current) updateQuestion(entry);
  }
  function questions(parent, item) {
    if (!Array.isArray(item.questions) || !item.questions.length || item.questions.some(q => !q || typeof q.title !== 'string')) return;
    const tid = state.current, key = JSON.stringify([tid, item.id]), signature = JSON.stringify(item.questions);
    let entry = questionEntries.get(key);
    if (!entry || entry.signature !== signature) {
      const form = el('form', 'async-questions request'); form.dataset.questionItem = item.id;
      form.append(el('h3', '', I18n.t('Codex has a question')));
      entry = {tid, form, signature, fields: [], sending: false, submission: null};
      item.questions.forEach((question, index) => {
        const fieldset = el('fieldset', 'async-question'), controls = el('div', 'question-controls');
        fieldset.append(el('legend', '', question.title));
        const options = Array.isArray(question.options) ? question.options.filter(o => typeof o === 'string') : [];
        const input = el('textarea'); input.rows = 2; input.maxLength = 10000;
        const label = el('label', 'question-freeform', options.length ? I18n.t('Or write your own answer') : I18n.t('Your answer'));
        label.append(input);
        const radios = [], choices = el('div', 'question-options');
        for (const [n, option] of options.entries()) {
          const choice = el('label', 'question-option'), radio = el('input'); radio.type = 'radio'; radio.name = 'question-' + index; radio.value = option; radio.checked = n === 0;
          choice.append(radio, el('span', '', option)); choices.append(choice); radios.push(radio);
          radio.onchange = () => { input.value = ''; updateQuestion(entry); };
        }
        input.oninput = () => { if (input.value) radios.forEach(radio => { radio.checked = false; }); updateQuestion(entry); };
        controls.append(choices, label);
        const summary = el('p', 'question-answer'); summary.hidden = true;
        fieldset.append(controls, summary); form.append(fieldset);
        entry.fields.push({question, fieldset, controls, summary, key: JSON.stringify(['request_user_input_async', item.id, index]),
          value: () => input.value.trim() || radios.find(radio => radio.checked)?.value || '',
          restore: value => { radios.forEach(radio => { radio.checked = radio.value === value; }); input.value = radios.some(radio => radio.checked) ? '' : value; }});
      });
      const draft = questionDrafts.get(key);
      if (draft && draft.signature === signature) { entry.fields.forEach((field, index) => field.restore(draft.values[index] || '')); entry.submission = draft.submission; }
      questionDrafts.delete(key);
      entry.status = el('p', 'fine'); entry.status.setAttribute('role', 'status');
      entry.error = el('p', 'error'); entry.error.setAttribute('role', 'alert');
      entry.submit = el('button', 'approve'); entry.submit.type = 'submit';
      form.append(entry.status, entry.error, entry.submit);
      form.onsubmit = async event => {
        event.preventDefault(); updateQuestion(entry); if (entry.submit.disabled || entry.submit.hidden) return;
        const replies = entry.fields.filter(field => !field.answered && field.value().trim()).map(field => ({answer: field.value(), question: field.question.title, questionItemId: field.key}));
        const text = '<send_user_message_question_reply>\n' + JSON.stringify(replies) + '\n</send_user_message_question_reply>';
        if (text.length > 50000) { entry.error.textContent = I18n.t('Your answers are too long. Shorten them and try again.'); return; }
        if (!entry.submission || entry.submission.text !== text) {
          entry.submission = {text, mode: 'auto', submissionId: Array.from(crypto.getRandomValues(new Uint8Array(20)), v => v.toString(16).padStart(2, '0')).join('')};
        }
        const epoch = uploadEpoch;
        entry.sending = true; entry.error.textContent = ''; updateQuestion(entry);
        try {
          if (state.readOnly) await connectThread(tid);
          if (epoch !== uploadEpoch) return;
          await api('/api/threads/' + encodeURIComponent(tid) + '/message', entry.submission);
          if (epoch !== uploadEpoch) return;
          rememberAnswers(tid, replies); entry.submission = null;
          refresh().catch(() => {});
        } catch (error) { if (epoch === uploadEpoch) entry.error.textContent = error.message; }
        finally { if (epoch === uploadEpoch) { entry.sending = false; updateQuestion(entry); } }
      };
      questionEntries.set(key, entry);
    }
    parent.append(entry.form); updateQuestion(entry);
  }
  window.remoteUI = {
    questions, observeAnswers, questionButtons,
    markdown, specialRequest, imageView, toolContent, restoredThread,
    saveLanguageDrafts() {
      if (state.sending || [...uploads.values()].some(job => job.count) || [...questionEntries.values()].some(entry => entry.sending)) throw new Error(I18n.t('Please wait for sending and uploads to finish before changing language.'));
      if (state.current) drafts.set(state.current, {text: $('prompt').value, attachments: state.attachments});
      for (const [key, entry] of questionEntries) questionDrafts.set(key, {signature: entry.signature, values: entry.fields.map(field => field.value()), submission: entry.submission});
      sessionStorage.setItem('codex-remote-language-drafts', JSON.stringify({time: Date.now(), drafts: [...drafts], questionDrafts: [...questionDrafts], current: state.current, submission: pendingSubmission}));
    },
    clearPrivate() { try { sessionStorage.removeItem('codex-remote-language-drafts'); } catch (_) {} uploadEpoch++; uploads.clear(); questionEntries.clear(); questionDrafts.clear(); answerHistory.clear(); window.messageRenderer.clear(); drafts.clear(); state.attachments = []; $('attachments').replaceChildren(); $('upload-status').hidden = true; $('file-preview').textContent = ''; $('devices').replaceChildren(); $('notifications').replaceChildren(); },
    uploadCount() { return (uploads.get(state.current) || {}).count || 0; },
    userContent(node, contents) {
      node.replaceChildren();
      for (const item of contents) {
        const replies = item.type === 'text' && questionReplies(item.text);
        if (replies) {
          for (const reply of replies) { const summary = el('div', 'question-reply'); summary.append(el('div', 'question-reply-title', reply.question), el('div', '', reply.answer)); node.append(summary); }
          continue;
        }
        let attachment = null;
        if (item.type === 'text' && item.text.startsWith("User attachment (server file): ")) {
          try { attachment = JSON.parse(item.text.slice("User attachment (server file): ".length)); } catch (_) {}
        }
        if (item.type === 'localImage') { const match = /\/uploads\/([a-f0-9]{32})\/([^/]+)$/.exec(item.path || ''); attachment = {id: match ? match[1] : null, name: match ? match[2] : I18n.t("Image attachment"), image: true}; }
        if (attachment) {
          const chip = el('div', 'attachment-chip');
          if (attachment.id && /^[a-f0-9]{32}$/.test(attachment.id)) { const a = el('a', '', I18n.t("Attachment · ") + attachment.name); a.href = '/api/uploads/' + attachment.id; a.target = '_blank'; a.rel = 'noopener'; if (attachment.image) { const img = el('img'); img.src = a.href; img.alt = attachment.name; chip.append(img); } chip.append(a); }
          else chip.append(el('span', '', I18n.t("Attachment · ") + attachment.name));
          node.append(chip);
        } else node.append(el('div', '', item.text || '[' + item.type + ']'));
      }
    },
    bootstrap(data) { boot = data; $('import-chat').disabled = !data.shared; $('discover').textContent = data.shared ? I18n.t("Find existing conversations") : I18n.t("Refresh conversation"); $('connection-mode').textContent = data.shared ? I18n.t("Select a conversation to connect and continue. Your phone and computer share live messages and progress.") : I18n.t("Isolated mode: browsers share conversations created here. Existing terminal conversations are separate."); const syncError = data.backgroundError ? I18n.t("Background sync: ") + data.backgroundError : (data.threadErrors || {})[state.current] && (state.readOnly || !state.ready) ? I18n.t("Conversation connection: ") + data.threadErrors[state.current] : ''; if (syncError) notice(syncError); else if ($('notice').textContent === lastSyncError) notice(''); lastSyncError = syncError; },
    historyCursor(cursor) { historyCursor = cursor; $('older').hidden = !cursor; },
    beforeSwitch(tid) { if (state.current) drafts.set(state.current, {text: $('prompt').value, attachments: state.attachments}); const draft = drafts.get(tid); $('prompt').value = draft ? draft.text : ''; state.attachments = draft ? draft.attachments : []; renderAttachments(); $('queue-list').replaceChildren(); historyCursor = null; $('older').hidden = true; },
    messageOptions() { const mode = $('message-mode').value, inherit = mode === 'steer' || (mode === 'queue' && boot.shared); return {mode, attachments: state.attachments.map(f => f.id), model: inherit ? null : settings.model, effort: inherit ? null : settings.effort}; },
    commandSent(tid, text) { const draft = drafts.get(tid); if (draft && draft.text === text) draft.text = ''; },
    sent(tid, text, sentIds) {
      const draft = drafts.get(tid);
      if (draft) { if (draft.text === text) draft.text = ''; draft.attachments = draft.attachments.filter(file => !sentIds.includes(file.id)); }
      if (state.current === tid) { state.attachments = state.attachments.filter(file => !sentIds.includes(file.id)); renderAttachments(); refreshQueue().catch(() => {}); }
    },
  };
  document.querySelectorAll('.dialog-close').forEach(b => { b.onclick = event => { event.preventDefault(); b.closest('dialog').close(); }; });
  $('thread-search').oninput = renderThreads;
  try { $('show-hidden').checked = localStorage.getItem('codex-remote-show-hidden') === '1'; } catch (_) {}
  $('show-hidden').onchange = () => { try { localStorage.setItem('codex-remote-show-hidden', $('show-hidden').checked ? '1' : '0'); } catch (_) {} renderThreads(); };
  $('show-archived').onchange = () => action(async () => { if ($('show-archived').checked) await api('/api/discover', {archived: true, includeAutomation: $('show-hidden').checked}); await refresh(); });
  try { const mode = localStorage.getItem('codex-remote-followup-mode'); if (['auto', 'queue', 'send'].includes(mode)) $('message-mode').value = mode; } catch (_) {}
  $('message-mode').onchange = () => { try { localStorage.setItem('codex-remote-followup-mode', $('message-mode').value); } catch (_) {} buttons(); };
  $('discover').onclick = () => action(async () => { const b = $('discover'); b.disabled = true; try { let cursor = null; do { const result = await api('/api/discover', {cursor, archived: $('show-archived').checked, includeAutomation: $('show-hidden').checked}); cursor = result.nextCursor; await refresh(); } while (cursor); notice(I18n.t("Server conversations synced.")); } finally { b.disabled = false; } });
  $('import-chat').onclick = () => { $('import-error').textContent = ''; $('import-dialog').showModal(); setDrawer(false); };
  $('import-form').onsubmit = event => { event.preventDefault(); action(async () => { const t = await api('/api/import', {threadId: $('import-id').value.trim()}); await refresh(); $('import-dialog').close(); await selectThread(t.id, true); }, 'import-error'); };
  $('attach-file').onclick = () => $('file-input').click();
  $('file-input').onchange = () => { const files = Array.from($('file-input').files); $('file-input').value = ''; addFiles(files); };
  $('prompt').addEventListener('paste', event => {
    if ($('prompt').disabled) return;
    // Capture File objects synchronously; clipboard data expires after the event.
    const files = transferFiles(event.clipboardData);
    if (!files.length) return; // Leave normal text/HTML-as-text pasting to the browser.
    event.preventDefault();
    const text = event.clipboardData.getData('text/plain');
    if (text) {
      const prompt = $('prompt'), room = prompt.maxLength - prompt.value.length + prompt.selectionEnd - prompt.selectionStart;
      prompt.setRangeText(text.slice(0, Math.max(0, room)), prompt.selectionStart, prompt.selectionEnd, 'end');
      prompt.dispatchEvent(new Event('input', {bubbles: true}));
    }
    addFiles(files);
  });
  $('composer').addEventListener('dragover', event => {
    if (!Array.from(event.dataTransfer.types || []).includes('Files')) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; $('composer').classList.add('drag-files');
  });
  $('composer').addEventListener('dragleave', event => { if (!$('composer').contains(event.relatedTarget)) $('composer').classList.remove('drag-files'); });
  $('composer').addEventListener('drop', event => {
    $('composer').classList.remove('drag-files');
    const files = transferFiles(event.dataTransfer);
    if (!files.length) return;
    event.preventDefault(); addFiles(files);
  });
  $('thread-tools').onclick = () => { const t = state.threads.find(t => t.id === state.current); $('rename-title').value = t ? t.title : ''; $('archive-chat').textContent = t && t.archived ? I18n.t("Restore conversation") : I18n.t("Archive conversation"); $('visibility-chat').textContent = t && t.list_hidden ? I18n.t("Show in my conversations") : I18n.t("Hide from my conversations"); $('thread-id').textContent = I18n.t("Conversation ID: ") + state.current; $('export-chat').href = '/api/threads/' + state.current + '/export'; $('tools-error').textContent = ''; $('tools-dialog').showModal(); };
  $('visibility-chat').onclick = () => action(async () => {
    const tid = state.current, t = state.threads.find(t => t.id === tid);
    const visibility = t && t.list_hidden ? 'show' : 'hide';
    $('visibility-chat').disabled = true;
    try {
      await api('/api/threads/' + tid + '/visibility', {visibility}); await refresh();
      $('tools-dialog').close();
      notice(visibility === 'hide' ? I18n.t("Hidden from the list; history is retained. Enable Show hidden conversations to find it.") : I18n.t("Visible in My conversations across devices."));
    } finally { $('visibility-chat').disabled = false; }
  }, 'tools-error');
  $('rename-chat').onclick = () => action(async () => { await api('/api/threads/' + state.current + '/rename', {title: $('rename-title').value}); await refresh(); $('chat-title').textContent = $('rename-title').value; $('tools-dialog').close(); }, 'tools-error');
  async function forkSavedHistory() {
    if (state.forking || !state.current) return;
    const tid = state.current, selection = state.selection;
    state.forking = true; buttons();
    try {
      const t = await api('/api/threads/' + tid + '/fork', {});
      await refresh();
      if (selection === state.selection) {
        $('tools-dialog').close(); await selectThread(t.id);
        if (state.current === t.id) notice(t.connection_error ? I18n.t("History fork created, but connection failed: ") + t.connection_error : I18n.t("History fork opened. The original conversation is retained. Continue here."));
      }
    } finally { state.forking = false; buttons(); }
  }
  $('fork-chat').onclick = () => action(forkSavedHistory, 'tools-error');
  $('recover-chat').onclick = () => action(forkSavedHistory);
  $('archive-chat').onclick = () => action(async () => { const t = state.threads.find(t => t.id === state.current); const archived = !t.archived; await api('/api/threads/' + state.current + '/archive', {archived}); $('show-archived').checked = archived; await refresh(); $('tools-dialog').close(); notice(archived ? I18n.t("Conversation archived. Restore it from the archive list.") : I18n.t("Conversation restored.")); }, 'tools-error');
  $('resync-chat').onclick = () => action(async () => { await api('/api/threads/' + state.current + '/attach', {refresh: true}); $('tools-dialog').close(); notice(I18n.t("Conversation synced.")); }, 'tools-error');
  function efforts(preferred = $('effort-select').value) {
    const thread = state.threads.find(t => t.id === state.current);
    const name = $('custom-model').value.trim() || $('model-select').value || thread?.model;
    const model = models.find(m => m.model === name);
    // Inheriting the conversation still has a concrete model. Only fall back
    // to protocol values when a custom/default model has no capability metadata.
    const supported = model?.supportedReasoningEfforts;
    const choices = Array.isArray(supported)
      ? supported.map(e => e.reasoningEffort).filter(v => typeof v === 'string' && v)
      : ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];
    $('effort-select').replaceChildren(el('option', '', I18n.t("Keep current settings"))); $('effort-select').firstChild.value = '';
    for (const v of new Set(choices)) { const o = el('option', '', v); o.value = v; $('effort-select').append(o); }
    $('effort-select').value = choices.includes(preferred) ? preferred : '';
  }
  $('model-open').onclick = () => action(async () => {
    models = (await api('/api/models')).data; $('model-select').replaceChildren(); const inherit = el('option', '', I18n.t("Use current conversation")); inherit.value = ''; $('model-select').append(inherit);
    for (const m of models) { const o = el('option', '', m.displayName || m.model); o.value = m.model; $('model-select').append(o); }
    $('model-select').value = models.some(m => m.model === settings.model) ? settings.model : '';
    $('custom-model').value = settings.model && !$('model-select').value ? settings.model : '';
    efforts(settings.effort || ''); const t = state.threads.find(t => t.id === state.current); $('model-current').textContent = t ? I18n.t("Current: ") + (t.model || I18n.t("Default model")) + ' · ' + (t.effort || I18n.t("Default reasoning effort")) : '';
    $('model-dialog').showModal();
  });
  $('model-select').onchange = () => { $('custom-model').value = ''; efforts(); };
  $('custom-model').oninput = () => efforts();
  $('model-save').onclick = () => { settings.model = $('custom-model').value.trim() || $('model-select').value || null; settings.effort = $('effort-select').value || null; $('model-open').textContent = [settings.model || I18n.t("Model settings"), settings.effort].filter(Boolean).join(' · '); $('model-dialog').close(); };
  $('older').onclick = () => action(async () => {
    const tid = state.current, selection = state.selection;
    $('older').disabled = true; followLatest = false;
    try {
      const page = await api('/api/threads/' + tid + '/history?cursor=' + encodeURIComponent(historyCursor));
      if (selection !== state.selection) return;
      const first = $('messages').firstChild, anchor = first && first.getBoundingClientRect().top;
      const added = document.createDocumentFragment();
      for (const turn of page.turns) {
        for (const item of turn.items || []) {
          if (state.items.has(item.id)) continue;
          upsert(item, true); const record = state.items.get(item.id);
          if (record) { pendingRenders.delete(record); renderItem(record); added.append(record.node); }
        }
        const existed = state.turnMarkers.has(turn.id), marker = turnMarker(turn);
        if (marker && !existed) added.append(marker);
      }
      $('messages').insertBefore(added, first); window.remoteUI.historyCursor(page.nextCursor);
      if (followLatest) scrollDown();
      else if (first) { $('timeline').scrollTop += first.getBoundingClientRect().top - anchor; rememberScroll(); }
    } finally { if (selection === state.selection) $('older').disabled = false; }
  });
  async function files(path) {
    const data = await api('/api/files?threadId=' + encodeURIComponent(state.current) + '&path=' + encodeURIComponent(path));
    $('file-error').textContent = ''; $('file-list').replaceChildren(); $('file-preview').hidden = true; $('file-download').hidden = true; $('file-path').textContent = path || '/'; filePath = path;
    if (data.directory) { fileDirectory = path === '.' ? '' : path; for (const entry of data.entries) { const b = el('button', 'file-entry', (entry.directory ? '▸ ' : '  ') + entry.name); b.onclick = () => action(() => files(entry.path), 'file-error'); $('file-list').append(b); } if (data.limited) $('file-error').textContent = I18n.t("Only the first 1,000 directory entries are shown."); }
    else { $('file-preview').hidden = false; $('file-preview').textContent = data.binary ? I18n.t("Binary file. Download it to view.") : data.content; if (data.truncated) $('file-error').textContent = I18n.t("Preview limited to 1 MiB. Download the complete file."); $('file-download').hidden = false; $('file-download').href = '/api/files?threadId=' + encodeURIComponent(state.current) + '&path=' + encodeURIComponent(path) + '&download=1'; }
  }
  $('browse-files').onclick = () => action(async () => { $('tools-dialog').close(); $('files-dialog').showModal(); await files(''); }, 'file-error');
  $('files-parent').onclick = () => action(() => files(filePath === fileDirectory ? fileDirectory.split('/').slice(0, -1).join('/') : fileDirectory), 'file-error');
  async function storageStatus() { const data = await api('/api/storage'); $('storage-status').textContent = I18n.format("{0} attachments · {1} MiB / 1024 MiB · {2} eligible for cleanup. Submitted attachments are retained.", [data.files,(data.bytes / 1048576).toFixed(1),data.eligibleFiles]); $('storage-cleanup').disabled = !data.eligibleFiles; }
  $('storage-cleanup').onclick = () => action(async () => { await api('/api/storage/cleanup', {}); await storageStatus(); }, 'settings-error');
  async function deviceList() { const data = await api('/api/devices'); $('devices').replaceChildren(); for (const d of data.devices) { const row = el('div', 'device-row'); row.append(el('p', 'fine', (d.current ? I18n.t("This device · ") : '') + d.label)); const b = el('button', 'small-button', d.current ? I18n.t("Sign out this device") : I18n.t("Revoke session")); b.onclick = () => action(async () => { await api('/api/devices/revoke', {id: d.id}); if (d.current) showLogin(); else await deviceList(); }, 'settings-error'); row.append(b); $('devices').append(row); } }
  function notifications() { $('notifications').replaceChildren(); for (const n of boot.notices || []) { const b = el('button', 'notification-row', (n.kind === 'approval' ? I18n.t("Response needed") : I18n.t("Task status updated")) + ' · ' + new Date(n.created * 1000).toLocaleString()); b.onclick = async () => { closeDialogs(); await selectThread(n.thread_id); await api('/api/notifications/seen', {before: n.id}); }; $('notifications').append(b); } if (!(boot.notices || []).length) $('notifications').append(el('p', 'fine', I18n.t("No unread notifications"))); }
  let pushBusy = false, pushStatusVersion = 0;
  function pushProblem() {
    if (!window.isSecureContext) return I18n.t("System notifications require HTTPS. Configure HTTPS and add that address to your home screen.");
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    if (ios && !navigator.standalone && !matchMedia('(display-mode: standalone)').matches) return I18n.t("Open the app from the iPhone home screen to enable notifications. Use the browser Share menu to add it first.");
    if (!('Notification' in window) || !('PushManager' in window) || !('serviceWorker' in navigator)) return I18n.t("Background push is not supported here. iPhone requires iOS 16.4 or later and the installed home-screen app.");
    if (!boot.pushPublicKey) return I18n.t("Push is not configured on the server. Configure an HTTPS origin first.");
    if (Notification.permission === 'denied') return I18n.t("Notifications are blocked. Allow them in iPhone Settings → Notifications → Codex Relay, or your browser site settings, then reopen the app.");
    return '';
  }
  function pushButtons(enable, test, disable) {
    $('push-enable').disabled = pushBusy || !enable;
    $('push-test').disabled = pushBusy || !test;
    $('push-disable').disabled = pushBusy || !disable;
  }
  async function refreshPushStatus() {
    const version = ++pushStatusVersion;
    const problem = pushProblem();
    pushButtons(false, false, false);
    $('push-status').textContent = problem || I18n.t("Checking notification status…");
    // Plain HTTP cannot have a browser subscription; do not prompt for permission.
    if (!window.isSecureContext) return;
    try {
      const status = await api('/api/push/status');
      const registration = 'serviceWorker' in navigator && await navigator.serviceWorker.getRegistration();
      const subscription = registration && registration.pushManager && await registration.pushManager.getSubscription();
      if (version !== pushStatusVersion || pushBusy) return;
      const enabled = !problem && status.configured && status.subscribed && !!subscription && Notification.permission === 'granted';
      pushButtons(!problem, enabled, status.subscribed || !!subscription);
      $('push-status').textContent = problem || (enabled ? I18n.t("Enabled on this device for turn completion and requests for input. Send a test to verify delivery.") : I18n.t("Not enabled on this device. Choose Enable notifications and allow the system prompt."));
    } catch (error) {
      if (version !== pushStatusVersion || pushBusy) return;
      pushButtons(!problem, false, true);
      $('push-status').textContent = problem || I18n.t("Unable to confirm notification status: ") + error.message;
    }
  }
  async function pushAction(work) {
    if (pushBusy) return;
    pushBusy = true; ++pushStatusVersion; pushButtons(false, false, false);
    $('push-feedback').textContent = ''; $('push-feedback').className = 'fine';
    try { await work(); }
    catch (error) { $('push-feedback').textContent = error.message; $('push-feedback').className = 'error'; }
    finally { pushBusy = false; await refreshPushStatus(); }
  }
  async function pushRegistration() {
    let timer;
    try {
      return await Promise.race([navigator.serviceWorker.ready, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(I18n.t("Notifications are not ready. Check your network and reopen the app."))), 15000);
      })]);
    } finally { clearTimeout(timer); }
  }
  $('settings-open').onclick = () => action(async () => { $('settings-error').textContent = ''; $('push-feedback').textContent = ''; $('settings-dialog').showModal(); setDrawer(false); notifications(); await Promise.all([deviceList(), refreshPushStatus(), storageStatus()]); }, 'settings-error');
  $('push-enable').onclick = () => pushAction(async () => {
    const problem = pushProblem(); if (problem) throw new Error(problem);
    // Request permission directly in the click handler, before network or SW waits.
    if (Notification.permission !== 'granted' && await Notification.requestPermission() !== 'granted') throw new Error(I18n.t("Allow notifications before enabling them."));
    const registration = await pushRegistration();
    const key = Uint8Array.from(atob(boot.pushPublicKey.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
    const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key});
    await api('/api/push/subscribe', {subscription: subscription.toJSON()});
  });
  $('push-test').onclick = () => pushAction(async () => {
    $('push-status').textContent = I18n.t("Sending test notification…");
    await api('/api/push/test', {});
    // Provider acceptance is not confirmation of delivery to the phone.
    $('push-feedback').textContent = I18n.t("The push service accepted the test. Check your lock screen or notification center. If it does not arrive, check notification permissions and Focus settings.");
  });
  $('push-disable').onclick = () => pushAction(async () => {
    await api('/api/push/unsubscribe', {});
    if ('serviceWorker' in navigator) {
      const registration = await navigator.serviceWorker.getRegistration();
      const subscription = registration && registration.pushManager && await registration.pushManager.getSubscription();
      if (subscription) await subscription.unsubscribe();
    }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && $('settings-dialog').open && !pushBusy) refreshPushStatus(); });
  setInterval(() => { if (!document.hidden) refreshQueue().catch(() => {}); }, 2500);
})();
