'use strict';
// Runs in an opaque-origin sandbox. It has no access to the conversation DOM,
// cookies, storage or API, and its CSP prohibits outgoing network requests.
(() => {
  const stage = document.getElementById('stage');
  let sequence = Promise.resolve();
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== parent || !data || data.type !== 'mermaid-render' || typeof data.source !== 'string') return;
    sequence = sequence.then(async () => {
      try {
        if (data.source.length > 50000) throw new Error("Diagram is too large. View the source.");
        mermaid.initialize({startOnLoad: false, securityLevel: 'strict', theme: data.theme === 'dark' ? 'dark' : 'default',
          fontFamily: 'Arial, sans-serif', htmlLabels: false, flowchart: {htmlLabels: false},
          suppressErrorRendering: true, maxTextSize: 50000, maxEdges: 500, logLevel: 5,
          secure: ['secure', 'securityLevel', 'startOnLoad', 'maxTextSize', 'maxEdges', 'suppressErrorRendering', 'htmlLabels', 'theme', 'themeCSS']});
        const result = await mermaid.render('diagram-' + data.id, data.source, stage);
        const xml = new DOMParser().parseFromString(result.svg, 'image/svg+xml');
        const svg = xml.documentElement, box = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
        if (svg.localName !== 'svg') throw new Error("Invalid diagram output.");
        // Explicit dimensions make the standalone image sharp and zoomable.
        if (box.length === 4 && box.every(Number.isFinite) && box[2] > 0 && box[3] > 0) {
          svg.setAttribute('width', String(Math.ceil(box[2]))); svg.setAttribute('height', String(Math.ceil(box[3])));
        }
        svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        parent.postMessage({type: 'mermaid-result', id: data.id, svg: new XMLSerializer().serializeToString(svg)}, '*');
      } catch (_) {
        parent.postMessage({type: 'mermaid-result', id: data.id, error: "Unable to render the diagram. Expand the source to check Mermaid syntax."}, '*');
      } finally { stage.replaceChildren(); }
    });
  });
  parent.postMessage({type: 'mermaid-ready'}, '*');
})();
