"""Actionable storage and writer-conflict errors without changing task ownership."""
import errno


def model_error_text(error):
    if not error:
        return None
    if not isinstance(error, dict):
        return str(error)[:3000]
    message = error.get('message') or 'Model request failed'
    detail = error.get('additionalDetails') or ''
    if 'client not allowed' in (message + detail).lower():
        return 'The model provider rejected this Codex client (HTTP 403 / Client not allowed). Check provider client restrictions and the native client identity.' + (' ' + message if message.startswith('Reconnecting') else '')
    return (message + ('\n' + detail if detail and detail not in message else ''))[:3000]


def writer_conflict(error):
    return any(value in str(error).lower() for value in ('live local writer', 'active writer', 'writer lock'))


def storage_error(error):
    text = str(error).lower()
    return getattr(error, 'errno', None) in (errno.EDQUOT, errno.ENOSPC, errno.EIO) or any(
        value in text for value in ('quota exceeded', 'disk i/o error', 'database or disk is full', 'no space left on device', 'os error 122'))


def explain_error(error):
    text = str(error)
    if 'quota exceeded' in text.lower() or 'os error 122' in text.lower() or getattr(error, 'errno', None) == errno.EDQUOT:
        return 'The server account disk quota is exhausted, so Codex cannot read or write conversations. Free account storage; a quota can be exceeded even when the disk has free space.'
    if storage_error(error):
        return 'Conversation database I/O failed. Check account quota, free space and filesystem health. The gateway will retry with backoff.'
    if writer_conflict(error):
        return 'A Codex process still holds this conversation writer lock. This does not prove a task is still running; the lock may be left after a failure. The gateway did not interrupt that process. Continue from a saved-history fork, or let the owner release the lock normally before reconnecting.'
    return text
