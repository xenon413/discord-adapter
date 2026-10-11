from threading import Lock


class PCMBuffer:
    """Thread-safe, bounded 48 kHz stereo signed-16-bit PCM; oldest audio is discarded."""

    def __init__(self, max_bytes: int = 384000, max_users: int = 16) -> None:
        if max_bytes < 4 or max_bytes % 4 or max_users < 1:
            raise ValueError("Buffer sizes must be positive and aligned to stereo PCM frames")
        self.max_bytes = max_bytes
        self.max_users = max_users
        self._buffers: dict[int, bytearray] = {}
        self._lock = Lock()
        self.closed = False
        self.dropped_bytes = 0

    def write(self, user_id: int, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            if self.closed:
                return
            if len(pcm) % 4 or (
                user_id not in self._buffers and len(self._buffers) >= self.max_users
            ):
                self.dropped_bytes += len(pcm)
                return
            buffer = self._buffers.setdefault(user_id, bytearray())
            overflow = max(0, len(buffer) + len(pcm) - self.max_bytes)
            self.dropped_bytes += overflow
            if len(pcm) >= self.max_bytes:
                buffer[:] = pcm[-self.max_bytes :]
            else:
                if overflow:
                    del buffer[:overflow]
                buffer.extend(pcm)

    def take(self, user_id: int) -> bytes:
        with self._lock:
            return bytes(self._buffers.pop(user_id, b""))

    def sizes(self) -> dict[str, int]:
        with self._lock:
            return {str(user_id): len(data) for user_id, data in self._buffers.items()}

    def close(self) -> None:
        with self._lock:
            self.closed = True
            self._buffers.clear()
