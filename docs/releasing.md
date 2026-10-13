# Release process

1. Update the single version in `codex_remote/__init__.py` and CHANGELOG.md.
2. Run unit, browser, language, asset and installed-wheel checks in a clean environment.
3. Run native fixtures against each claimed compatible Codex version. Record which
   checks actually ran; do not turn protocol mock results into compatibility claims.
4. Review the exact staged files using `check_publication.py --staged` and a secret
   scanner. A local `--deny-file` can add organization/project identifiers without
   putting those identifiers in the repository. Check commit author metadata too.
5. Build sdist/wheel using the pinned development dependencies. Inspect their contents;
   no runtime data, screenshots, native binaries or credentials may be included.
6. Publish the reviewed commit/tag and artifacts only after CI succeeds. Fresh machine,
   real phone and public HTTPS/push checks should be explicitly recorded when performed.

Do not deploy this release into a running personal gateway automatically. Upgrades
follow the backup/stop/verify procedure in operations.md. A source release need not
restart any existing server or native Codex daemon.
