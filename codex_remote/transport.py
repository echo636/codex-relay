"""Connect to the same daemon as CLI / desktop, without owning its lifetime."""
import collections
import json
import threading
from websockets.sync.client import unix_connect
from codex_remote.server import CodexRPC, APIError


class SocketRPC(CodexRPC):
    def __init__(self, socket_path, on_message, on_exit):
        self.on_message, self.on_exit = on_message, on_exit
        self.lock = threading.Lock()
        self.pending, self.seq, self.closed = {}, 0, False
        self.received = 0
        self.stderr = collections.deque(maxlen=40)
        self.socket = unix_connect(socket_path, uri="ws://localhost", open_timeout=10,
                                   max_size=64 * 1024 * 1024, ping_interval=20, ping_timeout=20)
        self.reader = threading.Thread(target=self._read_socket, daemon=True)
        self.reader.start()

    def _write(self, message):
        try:
            self.socket.send(json.dumps(message, ensure_ascii=False))
        except Exception as error:
            raise OSError("Shared Codex socket disconnected") from error

    def _read_socket(self):
        try:
            for frame in self.socket:
                message = json.loads(frame)
                self.received += 1
                message["_transportSeq"] = self.received
                if "method" in message:
                    self.on_message(message)
                else:
                    with self.lock:
                        future = self.pending.get(message.get("id"))
                        if future and not future.done():
                            future.set_result(message)
        except Exception as error:
            self.stderr.append(type(error).__name__)
        finally:
            with self.lock:
                self.closed = True
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(APIError(503, "Disconnected from the Codex daemon. Reconnecting."))
            self.on_exit()

    def close(self):
        self.socket.close()
        if threading.current_thread() is not self.reader:
            self.reader.join(5)
