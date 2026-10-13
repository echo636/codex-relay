"""Web Push: opt-in device subscriptions; no transcript text in notification payloads."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import webpush, WebPushException
from codex_remote.server import APIError
from codex_remote.i18n import translate


class PushManager:
    def __init__(self, store, state, subject):
        self.store, self.subject = store, subject
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webpush")
        self.store.execute("CREATE TABLE IF NOT EXISTS push_subscriptions(id TEXT PRIMARY KEY,device TEXT,body TEXT)")
        columns = {row['name'] for row in self.store.rows('PRAGMA table_info(push_subscriptions)')}
        if 'language' not in columns:
            self.store.execute("ALTER TABLE push_subscriptions ADD COLUMN language TEXT DEFAULT 'en'")
        self.key_path = Path(state) / "webpush-private.pem"
        if not self.key_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            self.key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            self.key_path.chmod(0o600)
        key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.public_key = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def subscribe(self, device, subscription, language="en"):
        if not isinstance(subscription, dict):
            raise APIError(400, "Invalid push subscription.")
        endpoint = subscription.get("endpoint", "")
        if not isinstance(endpoint, str) or len(endpoint) > 4096:
            raise APIError(400, "Invalid push endpoint.")
        url = urlsplit(endpoint)
        allowed = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com", "web.push.apple.com", "notify.windows.com")
        host = url.hostname or ""
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443) or not any(host == h or host.endswith("." + h) for h in allowed):
            raise APIError(400, "Only HTTPS endpoints issued by supported browser push services are accepted.")
        keys = subscription.get("keys", {})
        try:
            for k, size in (("auth", 16), ("p256dh", 65)):
                value = keys[k]
                decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
                if len(decoded) != size:
                    raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise APIError(400, "Invalid push encryption parameters.")
        sid = hashlib.sha256(endpoint.encode()).hexdigest()
        self.store.execute("INSERT OR REPLACE INTO push_subscriptions(id,device,body,language) VALUES(?,?,?,?)", (sid, device, json.dumps(subscription), language if language in ("en", "zh-CN") else "en"))
        return {"ok": True}

    def unsubscribe(self, device):
        self.store.execute("DELETE FROM push_subscriptions WHERE device=?", (device,))

    def subscribed(self, device):
        return bool(self.store.rows("SELECT id FROM push_subscriptions WHERE device=? LIMIT 1", (device,)))

    def test(self, device):
        rows = self.store.rows("SELECT * FROM push_subscriptions WHERE device=? LIMIT 1", (device,))
        if not rows:
            raise APIError(409, "Enable notifications on this device first.")
        payload = json.dumps({"title": "Codex notification test", "body": "Receiving this notification confirms push delivery to this device."})
        result = self._send(rows[0], payload)
        if result == "expired":
            raise APIError(409, "Notification subscription expired. Enable notifications again.")
        if result != "sent":
            raise APIError(502, "Test notification failed. Check server connectivity to the push service and push configuration.")
        return {"accepted": True}

    def notify(self, tid, kind):
        payload = json.dumps({"title": "Codex needs your response" if kind == "approval" else "Codex turn finished",
                              "body": "Open your workspace for details.", "threadId": tid})
        for row in self.store.rows("SELECT * FROM push_subscriptions"):
            self.pool.submit(self._send, row, payload)

    def _send(self, row, payload):
        # Revoked browser sessions do not continue receiving notifications.
        if not self.store.rows("SELECT id FROM devices WHERE id=? AND expires>strftime('%s','now')", (row["device"],)):
            self.unsubscribe(row["device"])
            return "expired"
        try:
            value = json.loads(payload)
            for key in ('title', 'body'):
                value[key] = translate(value.get(key, ''), row.get('language', 'en'))
            payload = json.dumps(value, ensure_ascii=False)
            webpush(subscription_info=json.loads(row["body"]), data=payload, vapid_private_key=str(self.key_path),
                    vapid_claims={"sub": self.subject}, ttl=3600, timeout=10)
            return "sent"
        except WebPushException as error:
            if error.response is not None and error.response.status_code in (404, 410):
                self.store.execute("DELETE FROM push_subscriptions WHERE id=?", (row["id"],))
                return "expired"
        except Exception:
            pass  # Notifications must never interrupt agent execution.
        return "failed"

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
