"""Run each browser regression in a fresh interpreter against local fixtures."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for name in ('browser_check', 'model_settings_check', 'scroll_check', 'clipboard_check', 'download_check', 'mermaid_check', 'mermaid_network_check', 'questions_check', 'ui_updates_check', 'i18n_check'):
    subprocess.run([sys.executable, str(ROOT/'tests'/f'{name}.py')], cwd=ROOT, check=True)
