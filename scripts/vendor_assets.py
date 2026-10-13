"""Verify browser assets, or refresh them from integrity-pinned npm tarballs."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]/'codex_remote/static/vendor'
SPECS={
    'markdown-it':('markdown-it',{'package/dist/browser/markdown-it.umd.min.js':'markdown-it.min.js','package/LICENSE':'LICENSE'}),
    'katex':('katex',{'package/dist/katex.min.js':'katex.min.js','package/dist/katex.min.css':'katex.min.css','package/LICENSE':'LICENSE'}),
    '@highlightjs/cdn-assets':('highlight',{'package/highlight.min.js':'highlight.min.js','package/LICENSE':'LICENSE'}),
    'mermaid':('mermaid',{'package/dist/mermaid.min.js':'mermaid.min.js','package/LICENSE':'LICENSE'}),
}


def refresh():
    for package in json.loads((ROOT/'packages.json').read_text()):
        with urllib.request.urlopen(package['url'],timeout=60) as response:raw=response.read()
        expected=package['integrity'].removeprefix('sha512-')
        if base64.b64encode(hashlib.sha512(raw).digest()).decode()!=expected:raise ValueError('Tarball integrity mismatch: '+package['package'])
        folder,mapping=SPECS[package['package']];mapping=dict(mapping)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            if folder=='katex':
                for member in archive.getnames():
                    if re.fullmatch(r'package/dist/fonts/[A-Za-z0-9_-]+\.(woff2?|ttf)',member):mapping[member]=member.removeprefix('package/dist/')
            for member,target in mapping.items():
                data=archive.extractfile(member).read();path=ROOT/folder/target;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    bundle=(ROOT/'mermaid/mermaid.min.js').read_text()
    notices=re.search(r'/\*! Bundled license information:[\s\S]*?\*/',bundle)
    if not notices:raise ValueError('Upstream Mermaid license notices are missing.')
    (ROOT/'mermaid/THIRD_PARTY_NOTICES.txt').write_text(notices.group()+'\n')
    assets={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder,_ in SPECS.values() for p in sorted((ROOT/folder).rglob('*')) if p.is_file()}
    (ROOT/'assets.sha256.json').write_text(json.dumps(assets,indent=2,sort_keys=True)+'\n')


def verify():
    assets=json.loads((ROOT/'assets.sha256.json').read_text())
    actual={str(p.relative_to(ROOT)) for folder,_ in SPECS.values() for p in (ROOT/folder).rglob('*') if p.is_file()}
    if actual!=set(assets):raise ValueError('Vendored asset list differs from its manifest.')
    for name,digest in assets.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:raise ValueError('Asset integrity mismatch: '+name)
    print(f'Verified {len(assets)} pinned browser assets and notices.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--verify',action='store_true');group.add_argument('--refresh',action='store_true');args=parser.parse_args()
    if args.refresh:refresh()
    verify()
