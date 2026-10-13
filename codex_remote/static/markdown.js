'use strict';
(() => {
  const md = window.markdownit({html: false, linkify: true, breaks: true});
  const escape = md.utils.escapeHtml;
  const mathCache = new Map(), codeCache = new Map();
  function remember(cache, key, value, limit) {
    if (cache.size >= limit) cache.delete(cache.keys().next().value);
    cache.set(key, value); return value;
  }
  function safeLink(value) {
    try { return ['http:', 'https:', 'mailto:'].includes(new URL(value, location.href).protocol); }
    catch (_) { return false; }
  }
  md.validateLink = safeLink;
  md.renderer.rules.link_open = (tokens, index, options, env, self) => {
    tokens[index].attrSet('target', '_blank');
    tokens[index].attrSet('rel', 'noopener noreferrer');
    return self.renderToken(tokens, index, options);
  };
  // External images stay explicit links: rendering a reply must not contact a
  // third party. Uploaded images can use our authenticated, same-origin route.
  md.renderer.rules.image = (tokens, index) => {
    const token = tokens[index], src = token.attrGet('src') || '';
    const alt = md.renderer.renderInlineAsText(token.children || [], md.options, {});
    if (!safeLink(src)) return escape(alt);
    const url = new URL(src, location.href);
    if (url.origin === location.origin && /^\/api\/uploads\/[a-f0-9]{32}$/.test(url.pathname) && !url.search) {
      return '<img class="md-image" loading="lazy" src="' + escape(url.href) + '" alt="' + escape(alt) + '">';
    }
    return '<a class="md-image-link" href="' + escape(url.href) + I18n.t("\" target=\"_blank\" rel=\"noopener noreferrer\">View image · ") + escape(alt || I18n.t("Original image")) + ' ↗</a>';
  };
  md.renderer.rules.table_open = () => I18n.t("<div class=\"table-scroll\" tabindex=\"0\" role=\"region\" aria-label=\"Table, scroll horizontally\"><table>\n");
  md.renderer.rules.table_close = () => '</table></div>\n';

  function codeHTML(source, language = '', label = '') {
    const lang = language.trim().split(/\s+/)[0].toLowerCase();
    let highlighted = escape(source);
    const key = lang + '\0' + source;
    // No language guessing on arbitrary logs; very large blocks stay selectable
    // plain code so one response cannot stall the phone with syntax analysis.
    if (source.length <= 50000 && window.hljs && hljs.getLanguage(lang)) {
      if (codeCache.has(key)) highlighted = codeCache.get(key);
      else {
        try { highlighted = remember(codeCache, key, hljs.highlight(source, {language: lang, ignoreIllegals: true}).value, 24); }
        catch (_) { /* Escaped source remains readable. */ }
      }
    }
    return '<div class="code-frame"><div class="code-toolbar"><span class="code-language">' + escape(label || lang || 'text') +
      I18n.t("</span><button type=\"button\" class=\"copy-code\" aria-label=\"Copy code\">Copy</button></div>") +
      '<pre class="code-block" tabindex="0"><code class="hljs">' + highlighted + '</code></pre></div>\n';
  }
  md.renderer.rules.fence = (tokens, index, options, env) => {
    const token = tokens[index], language = md.utils.unescapeAll(token.info);
    if (language.trim().split(/\s+/)[0].toLowerCase() !== 'mermaid') return codeHTML(token.content, language);
    const lastLine = (env.lines || [])[token.map[1] - 1] || '';
    const closed = new RegExp('^[\\s>]*' + token.markup[0] + '{' + token.markup.length + ',}\\s*$').test(lastLine);
    const complete = (env.complete || closed) && token.content.length <= 50000;
    const status = token.content.length > 50000 ? I18n.t("Diagram is too large. View the source.") : complete ? I18n.t("Rendering diagram…") : I18n.t("Generating diagram…");
    return '<section class="mermaid-block" data-complete="' + complete + '"><div class="mermaid-tools"><span>Mermaid</span></div>' +
      I18n.t("<div class=\"mermaid-preview\" tabindex=\"0\" role=\"region\" aria-label=\"Mermaid diagram\">") + status + '</div>' +
      '<details class="mermaid-source"' + (complete ? '' : ' open') + I18n.t("><summary>View Mermaid source</summary>") + codeHTML(token.content, 'mermaid') + '</details></section>\n';
  };
  md.renderer.rules.code_block = (tokens, index) => codeHTML(tokens[index].content);

  function escaped(source, position) {
    let slashes = 0;
    while (position > 0 && source[--position] === '\\') slashes++;
    return slashes % 2 === 1;
  }
  function closing(source, delimiter, start, dollar = false) {
    let position = start;
    while ((position = source.indexOf(delimiter, position)) !== -1) {
      if (!escaped(source, position) && (!dollar ||
          (!/\s/.test(source[position - 1]) && !/[\d$]/.test(source[position + 1] || '') && source[position - 1] !== '$'))) return position;
      position += delimiter.length;
    }
    return -1;
  }
  function formula(token, display) {
    const raw = token.markup + token.content + (token.meta.close || '');
    if (!token.meta.complete || token.content.length > 12000 || !window.katex) {
      return '<span class="math-source">' + escape(raw) + '</span>';
    }
    const key = (display ? 'D' : 'I') + token.markup + '\0' + token.content;
    if (mathCache.has(key)) return mathCache.get(key);
    let html;
    try {
      html = katex.renderToString(token.content, {
        displayMode: display, output: 'htmlAndMathml', throwOnError: true,
        trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 10,
      });
    } catch (_) { html = I18n.t("<span class=\"math-source\" title=\"Formula is incomplete or uses unsupported syntax\">") + escape(raw) + '</span>'; }
    return remember(mathCache, key, html, 128);
  }
  // Parse math before Markdown's backslash/emphasis rules. Code spans/fences use
  // the parser's normal precedence and never enter these rules.
  md.inline.ruler.before('escape', 'math', (state, silent) => {
    const start = state.pos, source = state.src;
    const open = source.startsWith('\\(', start) ? '\\(' : source.startsWith('\\[', start) ? '\\[' :
      source.startsWith('$$', start) ? '$$' : source[start] === '$' ? '$' : null;
    if (!open) return false;
    const dollar = open === '$', display = open === '$$' || open === '\\[';
    const close = open === '\\(' ? '\\)' : open === '\\[' ? '\\]' : open;
    if (dollar && (/\s/.test(source[start + 1] || ' ') || source[start - 1] === '$')) return false;
    let end = closing(source, close, start + open.length, dollar);
    if (dollar && (end < 0 || source.slice(start + 1, end).includes('\n'))) return false;
    const complete = end !== -1;
    if (!complete) end = source.length;
    if (!silent) {
      const token = state.push(display ? 'math_display_inline' : 'math_inline', '', 0);
      token.content = source.slice(start + open.length, end); token.markup = open;
      token.meta = {close: complete ? close : '', complete};
    }
    state.pos = complete ? end + close.length : end;
    return true;
  });
  md.block.ruler.before('fence', 'math_block', (state, startLine, endLine, silent) => {
    if (state.sCount[startLine] - state.blkIndent >= 4) return false;
    const start = state.bMarks[startLine] + state.tShift[startLine];
    const first = state.src.slice(start, state.eMarks[startLine]);
    const open = first.startsWith('$$') ? '$$' : first.startsWith('\\[') ? '\\[' : null;
    if (!open) return false;
    const close = open === '$$' ? '$$' : '\\]';
    let line = startLine, end = -1, content = first.slice(open.length), complete = false;
    for (;;) {
      end = closing(content, close, 0);
      if (end !== -1) {
        if (content.slice(end + close.length).trim()) return false;
        complete = true; break;
      }
      if (line + 1 >= endLine || state.sCount[line + 1] < state.blkIndent) break;
      line++;
      content += '\n' + state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
    }
    if (silent) return true;
    const token = state.push('math_block', 'div', 0);
    token.block = true; token.content = complete ? content.slice(0, end) : content;
    token.markup = open; token.meta = {close: complete ? close : '', complete}; token.map = [startLine, line + 1];
    state.line = line + 1;
    return true;
  }, {alt: ['paragraph', 'reference', 'blockquote', 'list']});
  md.renderer.rules.math_inline = (tokens, index) => '<span class="math-inline">' + formula(tokens[index], false) + '</span>';
  md.renderer.rules.math_display_inline = (tokens, index) => '<span class="math-block" tabindex="0">' + formula(tokens[index], true) + '</span>';
  md.renderer.rules.math_block = (tokens, index) => '<div class="math-block" tabindex="0">' + formula(tokens[index], true) + '</div>\n';
  md.core.ruler.after('inline', 'task_lists', state => {
    state.tokens.forEach((token, index, tokens) => {
      if (token.type !== 'inline' || !tokens[index - 2] || tokens[index - 2].type !== 'list_item_open') return;
      const first = token.children[0], match = first && first.type === 'text' && /^\[([ xX])\]\s+/.exec(first.content);
      if (!match) return;
      tokens[index - 2].attrJoin('class', 'task-list-item');
      first.content = first.content.slice(match[0].length);
      const check = new state.Token('task_checkbox', '', 0); check.meta = {checked: match[1] !== ' '};
      token.children.unshift(check);
    });
  });
  md.renderer.rules.task_checkbox = (tokens, index) => tokens[index].meta.checked ?
    I18n.t("<span class=\"task-check checked\" role=\"img\" aria-label=\"Completed\">✓</span> ") :
    I18n.t("<span class=\"task-check\" role=\"img\" aria-label=\"Incomplete\"></span> ");

  async function copyCode(event) {
    const button = event.target.closest('.copy-code');
    if (!button || !event.currentTarget.contains(button)) return;
    const code = button.closest('.code-frame').querySelector('code').textContent;
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(code);
      else {
        // The campus HTTP entry is not a secure context; support its copy action.
        const previous = document.activeElement, field = document.createElement('textarea');
        field.className = 'copy-helper'; field.value = code; field.readOnly = true;
        document.body.append(field);
        let copied;
        try { field.select(); copied = document.execCommand('copy'); }
        finally { field.remove(); if (previous) previous.focus({preventScroll: true}); }
        if (!copied) throw new Error('copy unavailable');
      }
      button.textContent = I18n.t("Copied");
    } catch (_) { button.textContent = I18n.t("Select to copy"); }
    setTimeout(() => { button.textContent = I18n.t("Copy"); }, 2000);
  }
  function bindCopy(parent) {
    parent.removeEventListener('click', copyCode); parent.addEventListener('click', copyCode);
  }
  function markdown(parent, text, complete = true) {
    parent.classList.add('markdown');
    try { parent.innerHTML = md.render(String(text), {complete, lines: String(text).split('\n')}); }
    catch (_) { parent.textContent = text; }
    bindCopy(parent);
    if (window.diagramRenderer) window.diagramRenderer.render(parent);
  }
  function code(parent, text, language, label) {
    parent.innerHTML = codeHTML(String(text), language, label); bindCopy(parent);
  }
  window.messageRenderer = {markdown, code, clear() { mathCache.clear(); codeCache.clear(); if (window.diagramRenderer) window.diagramRenderer.clear(); }};
})();
