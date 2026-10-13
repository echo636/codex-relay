"""Install the built wheel into a clean target and exercise its assets and fixture RPC."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
wheels=sorted((ROOT/'dist').glob('*.whl'))
if len(wheels)!=1:raise SystemExit('Build exactly one wheel first.')
with tempfile.TemporaryDirectory(prefix='codex-remote-wheel-') as directory:
    target=Path(directory)/'installed'
    subprocess.run([sys.executable,'-m','pip','install','--no-deps','--no-compile','--target',str(target),str(wheels[0])],check=True)
    code='''
import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import codex_remote
from codex_remote.server import STATIC,VENDOR_FILES
from codex_remote.features import RemoteGateway
assert Path(codex_remote.__file__).is_relative_to(Path(sys.argv[1]))
for asset in ['index.html','i18n.js','locales/zh-CN.json','locales/zh-CN.js',*[p.lstrip('/') for p in VENDOR_FILES]]:
    assert (STATIC/asset).is_file(),asset
root=Path(sys.argv[2]);(root/'state').mkdir();(root/'workspace').mkdir()
g=RemoteGateway(root/'state',[root/'workspace'],[sys.executable,sys.argv[3]],background=False)
try:
    assert g.new_thread(str(root/'workspace'),'Wheel fixture')['id']
finally:
    g.close();g.store.close()
print('PASS: installed wheel imports, packaged assets and local protocol fixture.')
'''
    subprocess.run([sys.executable,'-I','-c',code,str(target),directory,str(ROOT/'tests/fake_codex.py')],cwd=directory,check=True)
