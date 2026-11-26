"""SQLite storage: verdict cache (by video ID) and crowd reports.

Privacy: we never store a user identity. Clients send a random install ID,
which is hashed with a server salt before it touches the database.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time

CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", 24 * 3600))
REPORTS_PER_CLIENT_PER_HOUR = int(os.environ.get("REPORTS_PER_CLIENT_PER_HOUR", 30))
SALT = os.environ.get("REPORT_SALT", "change-me-in-production")

_lock = threading.Lock()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cache (
                video_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                checked_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
                video_id TEXT NOT NULL,
                client_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (video_id, client_hash)
            );
            CREATE INDEX IF NOT EXISTS reports_by_client ON reports(client_hash, created_at);
            -- "This label looks wrong". Keeps the video ID (a public video, not a user)
            -- so reviewers can turn confirmed mistakes into eval cases.
            CREATE TABLE IF NOT EXISTS feedback (
                video_id TEXT NOT NULL,
                client_hash TEXT NOT NULL,
                level TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (video_id, client_hash)
            );
            -- Aggregate monitoring counters: no video IDs, no users.
            CREATE TABLE IF NOT EXISTS counters (
                day TEXT NOT NULL,
                name TEXT NOT NULL,
                n INTEGER NOT NULL,
                PRIMARY KEY (day, name)
            );
            """
        )

    # ---- cache ----
    def get_cached(self, video_id):
        with _lock:
            row = self.db.execute(
                "SELECT payload, checked_at FROM cache WHERE video_id = ?", (video_id,)
            ).fetchone()
        if not row or time.time() - row[1] > CACHE_TTL_SECONDS:
            return None
        return json.loads(row[0])

    def put_cached(self, video_id, payload):
        with _lock:
            self.db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (video_id, json.dumps(payload), time.time()),
            )
            self.db.commit()

    # ---- monitoring ----
    def bump(self, name, n=1):
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with _lock:
            self.db.execute(
                "INSERT INTO counters VALUES (?, ?, ?) ON CONFLICT(day, name) DO UPDATE SET n = n + ?",
                (day, name, n, n),
            )
            self.db.commit()

    def stats(self, days=14):
        with _lock:
            rows = self.db.execute(
                "SELECT day, name, n FROM counters ORDER BY day DESC, name LIMIT ?", (days * 20,)
            ).fetchall()
            pending = self.db.execute(
                "SELECT video_id, level, COUNT(*) c FROM feedback GROUP BY video_id, level ORDER BY c DESC LIMIT 50"
            ).fetchall()
        out = {}
        for day, name, n in rows:
            out.setdefault(day, {})[name] = n
        return {"daily": out, "wrongLabelQueue": [{"videoId": v, "level": l, "count": c} for v, l, c in pending]}

    # ---- "label looks wrong" ----
    def add_feedback(self, video_id, client_id, level):
        h = self._hash(client_id)
        with _lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO feedback VALUES (?, ?, ?, ?)", (video_id, h, level, time.time())
            )
            self.db.commit()
        return "added" if cur.rowcount else "duplicate"

    # ---- crowd reports ----
    @staticmethod
    def _hash(client_id):
        return hashlib.sha256((SALT + client_id).encode()).hexdigest()

    def report_count(self, video_id):
        with _lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM reports WHERE video_id = ?", (video_id,)
            ).fetchone()[0]

    def add_report(self, video_id, client_id):
        """Returns (status, count). status: 'added' | 'duplicate' | 'rate_limited'."""
        h = self._hash(client_id)
        hour_ago = time.time() - 3600
        with _lock:
            recent = self.db.execute(
                "SELECT COUNT(*) FROM reports WHERE client_hash = ? AND created_at > ?",
                (h, hour_ago),
            ).fetchone()[0]
            if recent >= REPORTS_PER_CLIENT_PER_HOUR:
                status = "rate_limited"
            else:
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO reports VALUES (?, ?, ?)", (video_id, h, time.time())
                )
                status = "added" if cur.rowcount else "duplicate"
                self.db.commit()
            count = self.db.execute(
                "SELECT COUNT(*) FROM reports WHERE video_id = ?", (video_id,)
            ).fetchone()[0]
        return status, count
