"""Run: python3 -m unittest -v test_backend   (from the backend folder)

Starts the real server on a free port, plus a fake SynthID/classifier service,
and exercises every endpoint over HTTP.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeVendor(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        FakeVendor.calls += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["imageUrl"].startswith("https://i.ytimg.com/vi/")
        assert "default" not in body["imageUrl"], "must never check the custom thumbnail"
        out = {"detected": "AIAIAIAIAI1" in body["imageUrl"]} if self.path == "/synthid" else {"score": 0.91}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class BackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vport = free_port()
        cls.vendor = ThreadingHTTPServer(("127.0.0.1", vport), FakeVendor)
        threading.Thread(target=cls.vendor.serve_forever, daemon=True).start()
        os.environ.update({
            "SYNTHID_API_URL": f"http://127.0.0.1:{vport}/synthid",
            "CLASSIFIER_URL": f"http://127.0.0.1:{vport}/classify",
            "DB_PATH": tempfile.mktemp(suffix=".db"),
            "REPORTS_PER_CLIENT_PER_HOUR": "3",
            "LIMIT_REPORTS_PER_IP_HOUR": "8",
            "STATS_TOKEN": "s3cret",
        })
        os.environ.pop("YOUTUBE_API_KEY", None)
        import server  # import after env is set
        cls.server_mod = server
        port = free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.vendor.shutdown()

    def setUp(self):
        # All test traffic comes from 127.0.0.1; give each test a fresh IP budget.
        for lim in (self.server_mod.limit_reports, self.server_mod.limit_verify, self.server_mod.limit_files):
            lim.hits.clear()

    def req(self, path, data=None, ctype="application/json"):
        body = json.dumps(data).encode() if isinstance(data, dict) else data
        r = urllib.request.Request(self.base + path, data=body, headers={"Content-Type": ctype} if body is not None else {})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_health(self):
        code, body = self.req("/health")
        self.assertEqual(code, 200)
        self.assertFalse(body["checks"]["youtubeApi"])
        self.assertTrue(body["checks"]["synthid"])

    def test_verify_runs_checks_then_caches(self):
        before = FakeVendor.calls
        code, body = self.req("/verify?videoId=AIAIAIAIAI1")
        self.assertEqual(code, 200)
        s = body["signals"]
        self.assertTrue(s["synthid"]["value"])
        self.assertEqual(s["classifier"]["value"], 0.91)
        self.assertIsNone(s["youtubeApi"]["value"])
        self.assertEqual(s["youtubeApi"]["status"], "not_configured")
        self.assertFalse(body["cached"])
        self.assertEqual(FakeVendor.calls - before, 6)  # 3 frames x 2 vendors
        code, body = self.req("/verify?videoId=AIAIAIAIAI1")
        self.assertTrue(body["cached"])
        self.assertEqual(FakeVendor.calls - before, 6, "cached call must not hit vendors")

    def test_verify_rejects_bad_ids(self):
        for bad in ["", "short", "../../etc/passwd", "AAAAAAAAAAA%27"]:
            code, _ = self.req("/verify?videoId=" + bad)
            self.assertEqual(code, 400, bad)

    def test_reports_dedupe_and_rate_limit(self):
        code, b = self.req("/report", {"videoId": "RRRRRRRRRR1", "clientId": "client-aaaa-1"})
        self.assertEqual((code, b["status"], b["count"]), (200, "added", 1))
        code, b = self.req("/report", {"videoId": "RRRRRRRRRR1", "clientId": "client-aaaa-1"})
        self.assertEqual((b["status"], b["count"]), ("duplicate", 1))
        self.req("/report", {"videoId": "RRRRRRRRRR2", "clientId": "client-aaaa-1"})
        self.req("/report", {"videoId": "RRRRRRRRRR3", "clientId": "client-aaaa-1"})
        code, b = self.req("/report", {"videoId": "RRRRRRRRRR4", "clientId": "client-aaaa-1"})
        self.assertEqual((code, b["status"]), (429, "rate_limited"))
        _, v = self.req("/verify?videoId=RRRRRRRRRR1")
        self.assertEqual(v["signals"]["crowd"]["value"], 1)

    def test_feedback_and_stats(self):
        code, b = self.req("/feedback", {"videoId": "FFFFFFFFFF1", "clientId": "client-fb-0001", "level": "likely"})
        self.assertEqual((code, b["status"]), (200, "added"))
        code, b = self.req("/feedback", {"videoId": "FFFFFFFFFF1", "clientId": "client-fb-0001", "level": "likely"})
        self.assertEqual(b["status"], "duplicate")
        code, _ = self.req("/feedback", {"videoId": "FFFFFFFFFF1", "clientId": "client-fb-0002", "level": "real"})
        self.assertEqual(code, 400, "no 'real' level exists")
        code, _ = self.req("/stats")
        self.assertEqual(code, 403)
        r = urllib.request.Request(self.base + "/stats", headers={"X-Stats-Token": "s3cret"})
        stats = json.loads(urllib.request.urlopen(r).read())
        self.assertIn({"videoId": "FFFFFFFFFF1", "level": "likely", "count": 1}, stats["wrongLabelQueue"])
        today = next(iter(stats["daily"].values()))
        self.assertGreaterEqual(today.get("feedback.wrong.likely", 0), 1)

    def test_ip_limit_beats_fresh_client_ids(self):
        # Brigading: each request uses a brand-new client ID; the IP limit must still stop it.
        codes = [self.req("/report", {"videoId": "BBBBBBBBBB1", "clientId": f"fresh-client-{i:04d}"})[0] for i in range(12)]
        self.assertIn(429, codes)
        _, v = self.req("/verify?videoId=BBBBBBBBBB1")
        self.assertLess(v["signals"]["crowd"]["value"], 12)

    def test_report_validation(self):
        code, _ = self.req("/report", {"videoId": "RRRRRRRRRR1"})
        self.assertEqual(code, 400)
        code, _ = self.req("/report", b"not json")
        self.assertEqual(code, 400)

    def test_check_file_without_c2pa_library(self):
        code, b = self.req("/check-file", b"\x00" * 100, ctype="video/mp4")
        self.assertEqual(code, 200)
        self.assertIn(b["c2pa"]["status"], ("not_configured", "ok"))
        code, _ = self.req("/check-file", b"x", ctype="application/x-evil")
        self.assertEqual(code, 415)


class C2PAInterpretTest(unittest.TestCase):
    def store(self, dst):
        return {"active_manifest": "m1", "manifests": {"m1": {
            "signature_info": {"issuer": "Example AI Co"},
            "assertions": [{"label": "c2pa.actions.v2", "data": {"actions": [
                {"action": "c2pa.created", "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/" + dst}]}}]}}}

    def test_ai_generated(self):
        from signals.c2pa_check import interpret_manifest_store
        r = interpret_manifest_store(self.store("trainedAlgorithmicMedia"))
        self.assertTrue(r["value"])
        self.assertIn("Example AI Co", r["detail"])

    def test_camera_is_not_a_real_badge(self):
        from signals.c2pa_check import interpret_manifest_store
        r = interpret_manifest_store(self.store("digitalCapture"))
        self.assertFalse(r["value"])  # "no AI declared", never "real"


class FrameAdapterTest(unittest.TestCase):
    def setUp(self):
        os.environ["SYNTHID_API_URL"] = "http://x/synthid"
        os.environ["CLASSIFIER_URL"] = "http://x/classify"
        os.environ.pop("DISABLE_ESTIMATES", None)

    def test_synthid_needs_two_of_three_frames(self):
        from signals import adapters
        seen = []
        def post(url, key, body):
            seen.append(body["imageUrl"])
            return {"detected": body["imageUrl"].endswith("hq2.jpg")}  # only the middle frame
        r = adapters.synthid("VVVVVVVVVV1", post=post)
        self.assertFalse(r["value"], "1 of 3 frames is not enough for a red badge")
        self.assertTrue(all("/hq" in u and "default" not in u for u in seen))

    def test_falls_back_to_small_frames(self):
        from signals import adapters
        def post(url, key, body):
            if "/hq" in body["imageUrl"]:
                raise IOError("404")
            return {"score": 0.5}
        r = adapters.classifier("VVVVVVVVVV1", post=post)
        self.assertEqual(r["value"], 0.5)

    def test_classifier_uses_median(self):
        from signals import adapters
        scores = iter([0.95, 0.10, 0.20])  # one outlier frame must not drive the badge
        r = adapters.classifier("VVVVVVVVVV1", post=lambda u, k, b: {"score": next(scores)})
        self.assertEqual(r["value"], 0.2)

    def test_kill_switch(self):
        from signals import adapters
        os.environ["DISABLE_ESTIMATES"] = "1"
        try:
            r = adapters.classifier("VVVVVVVVVV1", post=lambda u, k, b: {"score": 0.99})
            self.assertIsNone(r["value"])
        finally:
            os.environ.pop("DISABLE_ESTIMATES")

    def test_bad_vendor_score_rejected(self):
        from signals import adapters
        r = adapters.classifier("VVVVVVVVVV1", post=lambda u, k, b: {"score": 7})
        self.assertEqual(r["status"], "error")
        self.assertIsNone(r["value"])


class HiveAdapterTest(unittest.TestCase):
    # Structure from Hive's v3 docs: output[i].classes = [{class, value}].
    def hive_resp(self, ai):
        return {"task_id": "t", "model": "m", "output": [{
            "extra": [{"name": "frame_index", "value": 0}, {"name": "timestamp", "value": 0.0}],
            "classes": [{"class": "not_ai_generated", "value": round(1 - ai, 3)},
                        {"class": "ai_generated", "value": ai},
                        {"class": "none", "value": 0.9}, {"class": "deepfake", "value": 0.01}]}]}

    def setUp(self):
        os.environ.update({"CLASSIFIER_PROVIDER": "hive", "HIVE_API_KEY": "k"})

    def tearDown(self):
        for k in ("CLASSIFIER_PROVIDER", "HIVE_API_KEY"):
            os.environ.pop(k, None)

    def test_hive_request_and_parse(self):
        from signals import adapters
        sent = []
        scores = iter([0.97, 0.93, 0.12])
        def post(url, key, body):
            sent.append((url, key, body))
            return self.hive_resp(next(scores))
        r = adapters.classifier("HHHHHHHHHH1", post=post)
        self.assertEqual(r["value"], 0.93)  # median of frames
        url, key, body = sent[0]
        self.assertIn("api.thehive.ai/api/v3/hive/ai-generated-and-deepfake-content-detection", url)
        self.assertEqual(key, "k")
        self.assertEqual(list(body), ["input"])
        self.assertTrue(body["input"][0]["media_url"].endswith("/hq1.jpg"))

    def test_hive_missing_key(self):
        from signals import adapters
        os.environ.pop("HIVE_API_KEY")
        self.assertEqual(adapters.classifier("HHHHHHHHHH1")["status"], "not_configured")

    def test_hive_malformed_response(self):
        from signals import adapters
        r = adapters.classifier("HHHHHHHHHH1", post=lambda u, k, b: {"output": []})
        self.assertEqual(r["status"], "error")


class YouTubeApiTest(unittest.TestCase):
    def test_reads_flag(self):
        from signals import youtube_api
        os.environ["YOUTUBE_API_KEY"] = "test"
        try:
            r = youtube_api.check("x" * 11, fetch=lambda u: {"items": [{"status": {"containsSyntheticMedia": True}}]})
            self.assertTrue(r["value"])
            r = youtube_api.check("x" * 11, fetch=lambda u: {"items": [{"status": {}}]})
            self.assertIsNone(r["value"])  # absent field is not a "no"
        finally:
            os.environ.pop("YOUTUBE_API_KEY")


if __name__ == "__main__":
    unittest.main()
