"""YouTube Data API v3: read status.containsSyntheticMedia.

This is YouTube's "altered or synthetic content" disclosure flag, exposed in the API
since Oct 2024. Requires YOUTUBE_API_KEY (free; 1 quota unit per videos.list call).

Note: verify with a real key that this field is returned for videos you don't own.
If it isn't, the extension still reads the on-page label, so nothing breaks.
"""
import json
import os
import urllib.parse
import urllib.request

from . import result

API = "https://www.googleapis.com/youtube/v3/videos"


def check(video_id, fetch=None):
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return result(status="not_configured", detail="Set YOUTUBE_API_KEY")
    url = API + "?" + urllib.parse.urlencode({"part": "status", "id": video_id, "key": key})
    try:
        data = fetch(url) if fetch else _get_json(url)
    except Exception as e:  # network, quota, bad key
        return result(status="error", detail=type(e).__name__)
    items = data.get("items") or []
    if not items:
        return result(status="error", detail="Video not found or private")
    flag = items[0].get("status", {}).get("containsSyntheticMedia")
    if flag is None:
        # Field absent: YouTube didn't return a disclosure for this video. Not a "no".
        return result(status="ok", value=None, detail="No disclosure field returned")
    return result(status="ok", value=bool(flag), detail="Creator disclosure via YouTube API")


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())
