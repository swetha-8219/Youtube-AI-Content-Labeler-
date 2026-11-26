"""AI Label Layer verifier backend. Python 3.10+ standard library only.

Run:   python3 server.py            (listens on http://localhost:8787)
Env:   YOUTUBE_API_KEY, SYNTHID_API_URL, CLASSIFIER_URL, PORT, DB_PATH  (see README)

Endpoints
  GET  /health                      which checks are configured
  GET  /verify?videoId=ID           signals for a YouTube video (cached 24h by video ID)
  POST /report  {videoId, clientId} crowd "this is AI" report
  POST /check-file  (raw body, Content-Type: video/mp4 etc.)  C2PA check on an uploaded file
  POST /feedback {videoId, clientId, level}  "this label looks wrong"
  GET  /stats  (header X-Stats-Token)         daily counters + wrong-label review queue

Safety env: DISABLE_ESTIMATES=1 (kill switch for amber badges), TRUST_PROXY=1 (behind a proxy),
            LIMIT_VERIFY_PER_IP_HOUR, LIMIT_REPORTS_PER_IP_HOUR, LIMIT_FILES_PER_IP_HOUR, STATS_TOKEN
"""
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signals import adapters, c2pa_check, youtube_api  # noqa: E402
from ratelimit import Limiter, env_int  # noqa: E402
from store import Store  # noqa: E402

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", 50)) * 1024 * 1024
FILE_TYPES = {"video/mp4", "video/quicktime", "image/jpeg", "image/png", "image/webp", "audio/mpeg", "audio/mp4"}

store = Store(os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "ail.db")))
pool = ThreadPoolExecutor(max_workers=8)
LEVELS = {"confirmed", "likely", "unverified"}
limit_verify = Limiter(env_int("LIMIT_VERIFY_PER_IP_HOUR", 300))    # uncached checks only
limit_reports = Limiter(env_int("LIMIT_REPORTS_PER_IP_HOUR", 60))   # reports + feedback, across all client IDs
limit_files = Limiter(env_int("LIMIT_FILES_PER_IP_HOUR", 10))


class RateLimited(Exception):
    pass


def verify(video_id, ip="local"):
    cached = store.get_cached(video_id)
    store.bump("verify.cache_hit" if cached else "verify.cache_miss")
    if not cached and not limit_verify.allow(ip):
        store.bump("limit.verify")
        raise RateLimited()
    if cached:
        signals, checked_at, was_cached = cached["signals"], cached["checkedAt"], True
    else:
        futures = {
            "youtubeApi": pool.submit(youtube_api.check, video_id),
            "synthid": pool.submit(adapters.synthid, video_id),
            "classifier": pool.submit(adapters.classifier, video_id),
        }
        signals = {k: f.result() for k, f in futures.items()}
        for k, sig in signals.items():
            store.bump(f"signal.{k}.{sig['status']}")
            if sig["value"] is True or (k == "classifier" and (sig["value"] or 0) >= 0.8):
                store.bump(f"signal.{k}.positive")
        checked_at, was_cached = time.time(), False
        # Don't cache failures, so a transient error doesn't stick for 24h.
        if not any(s["status"] == "error" for s in signals.values()):
            store.put_cached(video_id, {"signals": signals, "checkedAt": checked_at})
    # Crowd count is always live.
    signals = dict(signals, crowd={"value": store.report_count(video_id), "status": "ok", "detail": ""})
    return {"videoId": video_id, "signals": signals, "checkedAt": checked_at, "cached": was_cached}


def health():
    return {
        "ok": True,
        "checks": {
            "youtubeApi": bool(os.environ.get("YOUTUBE_API_KEY")),
            "synthid": bool(os.environ.get("SYNTHID_API_URL")),
            "classifier": bool(os.environ.get("CLASSIFIER_URL")),
            "c2paFiles": _has_c2pa(),
            "crowd": True,
        },
        "estimatesDisabled": os.environ.get("DISABLE_ESTIMATES") == "1",
    }


def _has_c2pa():
    try:
        import c2pa  # noqa: F401
        return True
    except ImportError:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "AILabelLayer/0.2"

    def _ip(self):
        if os.environ.get("TRUST_PROXY") == "1":
            xff = self.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[0].strip()
        return self.client_address[0]

    def _json_body(self, max_len=1024):
        length = int(self.headers.get("Content-Length") or 0)
        if length > max_len:
            raise ValueError("too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _cors(self):
        origin = self.headers.get("Origin", "")
        allowed = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
        if origin.startswith("chrome-extension://") or origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(200, health())
        if u.path == "/verify":
            vid = (parse_qs(u.query).get("videoId") or [""])[0]
            if not VIDEO_ID.match(vid):
                return self._send(400, {"error": "videoId must be an 11-character YouTube ID"})
            try:
                return self._send(200, verify(vid, self._ip()))
            except RateLimited:
                return self._send(429, {"error": "rate limited"})
        if u.path == "/stats":
            token = os.environ.get("STATS_TOKEN")
            if not token or self.headers.get("X-Stats-Token") != token:
                return self._send(403, {"error": "set STATS_TOKEN and send X-Stats-Token"})
            return self._send(200, store.stats())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if u.path in ("/report", "/feedback"):
            try:
                body = self._json_body()
            except ValueError:  # includes JSONDecodeError
                return self._send(400, {"error": "invalid or oversized JSON"})
            vid, cid = str(body.get("videoId", "")), str(body.get("clientId", ""))
            if not VIDEO_ID.match(vid) or not CLIENT_ID.match(cid):
                return self._send(400, {"error": "videoId and clientId required"})
            if not limit_reports.allow(self._ip()):
                store.bump("limit.reports")
                return self._send(429, {"status": "rate_limited"})
            if u.path == "/feedback":
                level = str(body.get("level", ""))
                if level not in LEVELS:
                    return self._send(400, {"error": f"level must be one of {sorted(LEVELS)}"})
                status = store.add_feedback(vid, cid, level)
                if status == "added":
                    store.bump(f"feedback.wrong.{level}")
                return self._send(200, {"status": status})
            status, count = store.add_report(vid, cid)
            if status == "added":
                store.bump("report.added")
            return self._send(429 if status == "rate_limited" else 200, {"status": status, "count": count})
        if u.path == "/check-file":
            mime = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if mime not in FILE_TYPES:
                return self._send(415, {"error": f"Unsupported type. Use one of: {sorted(FILE_TYPES)}"})
            if not 0 < length <= MAX_UPLOAD:
                return self._send(413, {"error": "file missing or too large"})
            if not limit_files.allow(self._ip()):
                return self._send(429, {"error": "rate limited"})
            with tempfile.NamedTemporaryFile(delete=True) as tmp:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    tmp.write(chunk)
                    remaining -= len(chunk)
                tmp.flush()
                res = c2pa_check.check_file(tmp.name, mime)
            return self._send(200, {"c2pa": res})
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        # Log method + path only; never query strings with video IDs tied to IPs.
        sys.stderr.write("%s %s\n" % (self.command, urlparse(self.path).path))


def main():
    port = int(os.environ.get("PORT", 8787))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"AI Label Layer backend on http://{host}:{port}  checks={health()['checks']}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
