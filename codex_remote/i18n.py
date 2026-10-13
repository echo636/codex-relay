"""Translate gateway-owned responses only. Never translate conversation content."""
import json
from pathlib import Path

TRANSLATIONS = json.loads((Path(__file__).parent/'static/locales/zh-CN.json').read_text())


def locale(header=''):
    return 'zh-CN' if header.lower().split(',')[0].startswith('zh') else 'en'


def translate(message, language='en'):
    if language != 'zh-CN' or not isinstance(message, str):
        return message
    if message in TRANSLATIONS:
        return TRANSLATIONS[message]
    for source in sorted(TRANSLATIONS, key=len, reverse=True):
        if source.endswith(': ') and message.startswith(source):
            return TRANSLATIONS[source] + message[len(source):]
    return message
