# Local browser dependencies

These browser distributions are copied from the pinned npm tarballs recorded in
`packages.json`. Each tarball was checked against its npm SHA-512 integrity before
copying the distribution files. Original licenses are stored in each directory.
No CDN requests or Node build step are needed in production.

- markdown-it 15.0.2: `dist/browser/markdown-it.umd.min.js`, renamed to `markdown-it.min.js` (MIT).
- KaTeX 0.19.0: `dist/katex.min.js`, `dist/katex.min.css`, and `dist/fonts/*.{woff2,woff,ttf}` (MIT).
- highlight.js CDN assets 11.12.0: `highlight.min.js`, the common-language browser bundle (BSD-3-Clause).
- Mermaid 12.2.0: `dist/mermaid.min.js`, the standalone browser bundle, loaded on demand in the diagram sandbox (MIT).

When updating, verify tarball integrity, preserve license files, update the
server asset allowlist and service-worker shell version, and run the browser
suite for math, security, streaming, code, and phone layout regressions.

Run `python scripts/vendor_assets.py --verify` from the repository root to check
local hashes, or `--refresh` to reproduce the files from the pinned tarballs.
Mermaid bundled notices are also retained as THIRD_PARTY_NOTICES.txt.
