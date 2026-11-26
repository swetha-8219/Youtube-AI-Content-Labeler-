"""In-memory sliding-window rate limits keyed by a hashed client IP.

Client IDs are self-issued by the extension, so an abuser can mint unlimited ones.
The IP limit is the backstop. IPs are hashed with a per-process random salt and
never written to disk or logs.
"""
import hashlib
import os
import threading
import time
from collections import defaultdict, deque

_SALT = os.urandom(16)


def _key(ip):
    return hashlib.sha256(_SALT + ip.encode()).hexdigest()[:24]


class Limiter:
    def __init__(self, limit, window_seconds=3600):
        self.limit, self.window = limit, window_seconds
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, ip):
        if self.limit <= 0:
            return True
        k, now = _key(ip), time.time()
        with self.lock:
            q = self.hits[k]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > 100_000:  # bound memory
                self.hits.clear()
            return True


def env_int(name, default):
    return int(os.environ.get(name, default))
