"""Presentation-only filtering. Never archive, delete, resume, or stop a thread."""
import json


def visibility_fields(row):
    preference = row.get('list_visibility') or 'auto'
    source = row.get('source')
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except (ValueError, TypeError):
            pass
    title = (row.get('title') or '').strip()
    reason = None
    if isinstance(source, dict) and any(key.lower().replace('_', '') == 'subagent' for key in source):
        reason = 'subagent'
    elif source == 'exec':
        reason = 'automation'
    elif title.casefold() in ('', 'New conversation', 'new chat', 'new conversation') and not row.get('has_content'):
        reason = 'empty'
    if preference == 'show':
        reason = None
    elif preference == 'hide':
        reason = 'manual'
    return {'list_visibility': preference, 'list_hidden': reason is not None, 'list_hidden_reason': reason}
