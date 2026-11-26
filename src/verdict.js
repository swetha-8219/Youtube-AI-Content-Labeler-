// Verdict engine: turns signals into one of three verdicts.
// There is deliberately no "Real" verdict: absence of signals proves nothing.
// Any signal that is null/undefined was NOT checked and is shown as "Not checked".
(function (root) {
  const THRESHOLDS = {
    classifierLikely: 0.8,     // classifier alone
    classifierWithCrowd: 0.6,  // classifier + crowd
    crowdMin: 25               // reports needed to support a 0.6+ score
  };

  const NOT_CHECKED = "Not checked";

  /**
   * @param {object} s
   * @param {boolean} s.youtubeLabel      YouTube's AI label found on the page
   * @param {boolean|null} s.youtubeApi   containsSyntheticMedia from the YouTube Data API
   * @param {boolean|null} s.c2paAI       C2PA manifest declares AI generation
   * @param {boolean|null} s.synthid      SynthID watermark detected
   * @param {number|null} s.classifier    0..1 likelihood from an AI-media classifier
   * @param {number|null} s.crowdReports  "This is AI" reports from users
   */
  function computeVerdict(s) {
    const ytHit = !!s.youtubeLabel || s.youtubeApi === true;
    const ytResult = s.youtubeLabel ? "Found on page"
      : s.youtubeApi === true ? "Disclosed (YouTube API)"
      : s.youtubeApi === false ? "Not disclosed"
      : "Not found on page";
    const c = typeof s.classifier === "number" ? s.classifier : null;
    const crowd = typeof s.crowdReports === "number" ? s.crowdReports : null;

    const evidence = [
      { name: "YouTube AI label", result: ytResult, hit: ytHit, kind: "proof" },
      { name: "C2PA content credentials",
        result: s.c2paAI === true ? "Says AI-generated" : s.c2paAI === false ? "No AI declared" : NOT_CHECKED,
        hit: s.c2paAI === true, kind: "proof" },
      { name: "SynthID watermark",
        result: s.synthid === true ? "Detected" : s.synthid === false ? "Not detected" : NOT_CHECKED,
        hit: s.synthid === true, kind: "proof" },
      { name: "AI classifier",
        result: c == null ? NOT_CHECKED : Math.round(c * 100) + "% likely AI",
        hit: c != null && c >= THRESHOLDS.classifierWithCrowd, kind: "estimate" },
      { name: "Crowd reports",
        result: crowd == null ? NOT_CHECKED : crowd + " reports",
        hit: crowd != null && crowd >= THRESHOLDS.crowdMin, kind: "estimate" }
    ];

    if (ytHit || s.c2paAI === true || s.synthid === true) {
      return { level: "confirmed", label: "AI-generated", evidence,
        why: "A provenance signal confirms this video was made or altered with AI." };
    }
    if (c != null && (c >= THRESHOLDS.classifierLikely ||
        (c >= THRESHOLDS.classifierWithCrowd && (crowd || 0) >= THRESHOLDS.crowdMin))) {
      return { level: "likely", label: "Likely AI · " + Math.round(c * 100) + "%", evidence,
        why: "No watermark found, but our classifier estimates this is AI. This is an estimate, not proof." };
    }
    return { level: "unverified", label: "Unverified", evidence,
      why: "No AI signals found. This does not mean the video is real." };
  }

  root.AILabel = Object.assign(root.AILabel || {}, { computeVerdict, THRESHOLDS });
})(typeof window !== "undefined" ? window : globalThis);
