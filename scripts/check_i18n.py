"""Validate the locale companion and preserve numbered format placeholders."""
import argparse
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true');args=parser.parse_args()
p=ROOT/'codex_remote/static/locales/zh-CN.json';catalog=json.loads(p.read_text())
for source,target in catalog.items():
    if sorted(re.findall(r'\{\d+\}',source))!=sorted(re.findall(r'\{\d+\}',target)):
        raise SystemExit('Translation placeholders differ: '+source)
output='globalThis.CODEX_REMOTE_LOCALES = '+json.dumps({'zh-CN':catalog},ensure_ascii=False)+';\n'
if args.write:p.with_suffix('.js').write_text(output)
if p.with_suffix('.js').read_text()!=output:raise SystemExit('Locale JS is stale; run with --write.')
print(f'Validated {len(catalog)} translation entries.')
