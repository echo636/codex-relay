# Third-party notices

Project-authored code is Apache-2.0. This does not replace licenses of bundled assets.
The native Codex CLI is a separate user-installed dependency and is not distributed here.

| Browser distribution | Version | License file |
| --- | --- | --- |
| markdown-it | 15.0.2 | codex_remote/static/vendor/markdown-it/LICENSE (MIT) |
| KaTeX, including its distributed fonts | 0.19.0 | codex_remote/static/vendor/katex/LICENSE (MIT) |
| highlight.js CDN assets | 11.12.0 | codex_remote/static/vendor/highlight/LICENSE (BSD-3-Clause) |
| Mermaid | 12.2.0 | codex_remote/static/vendor/mermaid/LICENSE (MIT) |

Mermaid's standalone bundle includes additional libraries. Its upstream bundled
license comments are retained in the JavaScript and copied verbatim into
`codex_remote/static/vendor/mermaid/THIRD_PARTY_NOTICES.txt`. These include DOMPurify's
Apache-2.0/MPL-2.0 notice and the MIT notices of other included components. Retain
those notices and the applicable licenses when redistributing the bundle.

Pinned upstream tarball URLs and SHA-512 integrity values are in vendor/packages.json.
The SHA-256 asset manifest detects local changes. `scripts/vendor_assets.py --refresh`
fetches those exact tarballs and regenerates assets/notices; it does not run npm scripts.
Review the resulting diff and browser tests before updating versions.

Python libraries are installed separately according to requirements-lock.txt. Their
licenses remain in their distributions. Installed artifacts must retain those notices;
this project does not claim ownership of dependencies or third-party trademarks.
