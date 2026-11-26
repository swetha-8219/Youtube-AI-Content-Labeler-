"""Pluggable adapters for checks that need a third-party service.

Both are OFF until configured. When off, they return value=None ("Not checked").

SynthID: Google offers a public detector website, but no confirmed public API.
  If you get API access, set SYNTHID_API_URL (+ SYNTHID_API_KEY) to an endpoint that
  accepts {"imageUrl": ...} and returns {"detected": bool}.

Classifier: any AI-media classifier behind CLASSIFIER_URL (+ CLASSIFIER_API_KEY),
  accepting {"imageUrl": ...} and returning {"score": 0..1}. See vendors/ for proxies.

SAFETY: we check YouTube's AUTO-GENERATED frames (start / middle / end of the video),
never the custom thumbnail. Creators often use AI art for thumbnails on real videos;
checking hqdefault.jpg would badge real footage as AI.
  SynthID counts as detected only if >= 2 of 3 frames agree.
  The classifier score is the MEDIAN of the frames that scored.
"""
import json
import os
import statistics
import urllib.request

from . import result

FRAMES = ("1", "2", "3")  # start, middle, end


def frame_urls(video_id, frame):
    """Higher-resolution frame first (hq1.jpg, 480x360), then the documented 120x90 frame (1.jpg)."""
    return [f"https://i.ytimg.com/vi/{video_id}/hq{frame}.jpg",
            f"https://i.ytimg.com/vi/{video_id}/{frame}.jpg"]


def _post(url, key, body):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})},
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def _per_frame(video_id, url, key, post, parse):
    """Run the vendor on each frame; try the hq frame, fall back to the small one."""
    out = []
    for f in FRAMES:
        for img in frame_urls(video_id, f):
            try:
                out.append(parse(post(url, key, {"imageUrl": img})))
                break
            except Exception:
                continue
    return out


def _disabled():
    # Kill switch for estimates (amber badges). Provenance checks stay on.
    return os.environ.get("DISABLE_ESTIMATES") == "1"


def synthid(video_id, post=_post):
    url = os.environ.get("SYNTHID_API_URL")
    if not url:
        return result(status="not_configured", detail="No SynthID API configured")
    hits = _per_frame(video_id, url, os.environ.get("SYNTHID_API_KEY"), post, lambda d: bool(d["detected"]))
    if len(hits) < 2:
        return result(status="error", detail=f"Only {len(hits)} of 3 frames could be checked")
    detected = sum(hits) >= 2
    return result(status="ok", value=detected, detail=f"Watermark in {sum(hits)} of {len(hits)} video frames")


HIVE_URL = "https://api.thehive.ai/api/v3/hive/ai-generated-and-deepfake-content-detection"


def _check_score(s):
    s = float(s)
    if not 0 <= s <= 1:
        raise ValueError("score out of range")
    return s


def parse_hive(d):
    """Hive v3: output[i].classes = [{class, value}, ...]; take ai_generated (max over frames)."""
    vals = [c["value"] for o in d["output"] for c in o["classes"] if c["class"] == "ai_generated"]
    if not vals:
        raise ValueError("no ai_generated class in Hive response")
    return _check_score(max(vals))


def _hive_post(post):
    # Hive's body shape differs from our generic {"imageUrl"}; translate here.
    def p(url, key, body):
        return post(url, key, {"input": [{"media_url": body["imageUrl"]}]})
    return p


def classifier(video_id, post=_post):
    if _disabled():
        return result(status="not_configured", detail="Estimates disabled (kill switch)")
    provider = os.environ.get("CLASSIFIER_PROVIDER", "generic").lower()
    if provider == "hive":
        key = os.environ.get("HIVE_API_KEY")
        if not key:
            return result(status="not_configured", detail="Set HIVE_API_KEY")
        url, call, parse = os.environ.get("HIVE_API_URL", HIVE_URL), _hive_post(post), parse_hive
    else:
        url, key = os.environ.get("CLASSIFIER_URL"), os.environ.get("CLASSIFIER_API_KEY")
        if not url:
            return result(status="not_configured", detail="No classifier configured")
        call, parse = post, (lambda d: _check_score(d["score"]))

    scores = _per_frame(video_id, url, key, call, parse)
    if len(scores) < 2:
        return result(status="error", detail=f"Only {len(scores)} of 3 frames could be scored")
    return result(status="ok", value=round(statistics.median(scores), 3),
                  detail=f"Median of {len(scores)} video frames")
