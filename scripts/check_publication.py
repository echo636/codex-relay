"""Inspect exact Git blobs for forbidden runtime files, secrets and private paths.

Optional --deny-file contains local-only private identifiers, one per line. Never
commit that file. Findings print only filename/category, never matching values.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PATTERNS={
    'private-key':rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'api-credential':rb'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b',
    'personal-home-path':rb'/(?:home|Users)/[a-zA-Z0-9_.-]+/',
    'private-ip':rb'(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])',
    'url-credential':rb'https?://[^\s/"\x27:@]+:[^\s/"\x27@]+@',
}
BLOCKED_PARTS={'.venv','.browsers','__pycache__','test-results','.state','state','backups','node_modules'}
BLOCKED_NAMES={'access-token','auth.json','config.toml','.env','webpush-private.pem'}
BLOCKED_SUFFIXES={'.sqlite','.sqlite3','.db','.pem','.key','.log','.pyc','.jsonl'}


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--staged',action='store_true');mode.add_argument('--tracked',action='store_true')
    parser.add_argument('--deny-file',type=Path)
    args=parser.parse_args()
    denied=[] if not args.deny_file else [line.encode() for line in args.deny_file.read_text().splitlines() if line.strip()]
    findings=[];count=0
    # The index, including mode bits, is the content of the next commit.
    records=git('ls-files','--stage','-z').split(b'\0') if args.staged else git('ls-tree','-rz','HEAD').split(b'\0')
    for record in records:
        if not record:continue
        meta,rawname=record.split(b'\t',1);name=rawname.decode();path=Path(name);bits=meta.split();count+=1
        mode=bits[0];oid=bits[1] if args.staged else bits[2]
        if mode not in (b'100644',b'100755'):
            findings.append((name,'symlink-or-unexpected-mode'));continue
        if any(part in BLOCKED_PARTS for part in path.parts) or path.name in BLOCKED_NAMES or path.suffix in BLOCKED_SUFFIXES or (path.name.startswith('.env.') and path.name!='.env.example'):
            findings.append((name,'runtime-or-private-file'))
        data=git('cat-file','blob',oid.decode())
        for kind,pattern in PATTERNS.items():
            if re.search(pattern,data):findings.append((name,kind))
        if any(value.lower() in data.lower() or value.lower() in rawname.lower() for value in denied):
            findings.append((name,'local-private-identifier'))
    for name,kind in findings:print(f'{name}: {kind}')
    print(f'Checked {count} Git blobs; {len(findings)} finding(s).')
    return bool(findings)


if __name__=='__main__':sys.exit(main())
