# YouTube AI Content Labeler

**A browser extension that tells viewers whether a YouTube video is AI-generated, and shows the evidence.**

Many YouTube videos are now AI-generated, and viewers can't tell. People take false information from them as fact. YouTube's own AI label is easy to miss, and creators often don't disclose. Nobody can watermark someone else's upload, so this project puts a label on the **viewer's** side instead: a badge on every video's player.

| Badge | Meaning |
|---|---|
| 🔴 **AI-generated** | Proof found: YouTube's own AI label, a C2PA AI declaration, or a SynthID watermark |
| 🟠 **Likely AI · 88%** | An AI detector thinks so. **This is an estimate, not proof.** |
| ⚪ **Unverified** | No AI signals found. **This does not mean the video is real.** |

There is deliberately **no "Real" badge**. A missing watermark proves nothing, and a green check on a deepfake would do more harm than no badge at all.

Click any badge to see every check, what it found, and two buttons: **"I think this is AI"** and **"This label looks wrong"**.

> **Status: v0.3 prototype. Not ready for public use yet.** It works end to end. Accuracy is not proven until we finish the [500-video test set](#help-wanted-500-labeled-videos). See [Road to public beta](#road-to-public-beta).

**🙋 Help wanted:** we need people to label YouTube videos for testing. No coding required. [Jump to how you can help ↓](#help-wanted-500-labeled-videos)

---

## Quick start (about 5 minutes)

You need desktop Chrome, Python 3.10+, and optionally Node 18+ for the evals.

```bash
git clone https://github.com/swetha-8219/Youtube-AI-Content-Labeler-.git
cd Youtube-AI-Content-Labeler-

# 1. Start the backend (no installs needed)
cd backend
python3 server.py            # → http://localhost:8787
```

2. In Chrome, open `chrome://extensions` and turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose the repo folder (the one containing `manifest.json`).
4. Open any YouTube video. The badge appears in the top-left of the player.

With no API keys, two checks work: YouTube's on-page AI label and crowd reports. Every other check shows **"Not checked"**, never a false "no". To see a preview without installing, open `docs/demo.html` in a browser.

### Turn on more checks

| Check | How to enable | Notes |
|---|---|---|
| YouTube on-page label | Always on | Reads YouTube's "Altered or synthetic content" disclosure |
| YouTube API disclosure | `export YOUTUBE_API_KEY=...` | Free key from Google Cloud Console. Reads `status.containsSyntheticMedia` |
| AI detector (Hive) | `export CLASSIFIER_PROVIDER=hive HIVE_API_KEY=...` | Free tier is 100 requests a day. Each new video uses 3 requests (3 frames) |
| AI detector (other) | `export CLASSIFIER_URL=... CLASSIFIER_API_KEY=...` | Your endpoint takes `{"imageUrl"}` and returns `{"score": 0..1}` |
| SynthID watermark | `export SYNTHID_API_URL=... SYNTHID_API_KEY=...` | Google has a public detector website, but no confirmed public API yet |
| C2PA content credentials | `pip install c2pa-python` | **Uploaded files only** (`POST /check-file`). We never download YouTube videos, because that breaks YouTube's terms |
| Crowd reports | Always on | Limited per install and per network |

Check what's enabled with `curl localhost:8787/health`. Using a backend that isn't on localhost? Add its URL to `host_permissions` in `manifest.json`, then set it on the extension's **Options** page.

## How it works

```
YouTube page ──► content script ──► badge + details panel
                     │ video ID only
                     ▼
              background worker ──► backend /verify (24h cache)
                                       ├─ YouTube API disclosure
                                       ├─ SynthID      ┐ run on 3 auto-generated video frames,
                                       ├─ AI detector  ┘ never the custom thumbnail
                                       └─ crowd reports
```

- **Verdict rules** (`src/verdict.js`):
  - Any provenance signal gives 🔴.
  - A detector score ≥ 0.80, or ≥ 0.60 plus 25 or more reports, gives 🟠.
  - Anything else gives ⚪.
  - A model alone can never produce 🔴.
- **Frames, not thumbnails:** creators often put AI art on thumbnails of real videos, so the checks only look at YouTube's start, middle and end frames. SynthID needs 2 of 3 frames to agree. The detector uses the median score.
- **Fails safely:** if the backend is down, the badge falls back to YouTube's own label and says why.

## Safety

This project follows an [AI safety checklist across the development life cycle](https://github.com/swetha-8219/AI-Safety-skill-for-SDLC). It's rated **Tier 2**: a wrong label can hurt a real creator's reputation.

| Launch threshold (must pass before public beta) | Target |
|---|---|
| 🔴 badges that are correct | ≥ 99.5% |
| Real videos wrongly given 🟠 | ≤ 2% |
| 🟠 badges that are correct | ≥ 90% |
| AI videos that get 🔴 or 🟠 | ≥ 60% |
| Worst group's false-alarm rate vs. average | ≤ 1.5× |

Built-in controls:
- Kill switch for 🟠 badges: `DISABLE_ESTIMATES=1`.
- Per-IP rate limits (IPs are hashed and held in memory only).
- No video IDs in logs, and random install IDs stored hashed.
- Monitoring counters and a "label looks wrong" review queue (`/stats`, needs `STATS_TOKEN`).

## Tests and the CI safety gate

```bash
cd backend && python3 -m unittest -v test_backend        # 19 tests
node evals/validate_dataset.mjs                          # check community video labels
node evals/run_eval.mjs --dataset evals/dataset.csv --backend http://localhost:8787 --save-signals evals/signals.jsonl
```

`evals/run_eval.mjs` scores verdicts with the extension's own `verdict.js`. It checks every launch threshold, including each group's false-alarm rate, prints a detector-threshold sweep, and **fails the build** on any miss.

`.github/workflows/safety-gate.yml` runs all of this on every push. The files in `evals/fixtures/` are **synthetic** and only test the harness: the clean set must pass, and a set with planted bias against phone footage must fail.

## Help wanted: 500 labeled videos

**This is the single biggest thing blocking the public beta, and anyone can help.**

We need **500+ public YouTube videos where we know for sure** whether they're AI-generated or real, across many kinds of video. Without them, we can't prove the badges are accurate, or that they're fair to phone footage, non-English videos and animation.

**We especially need:**
- 📱 **Real** grainy, night or phone footage. This is our biggest false-alarm risk.
- 🌍 **Non-English** videos, both AI and real.
- 🎮 Animation, CGI and games. These are real, but easy to mistake for AI.
- 🤖 AI videos with **proof**: the creator names the tool, or it's an official sample from an AI lab.

**How to help:** open an issue with the **"Contribute labeled videos"** form and paste one line per video. No coding needed. Every video needs an evidence link: no guesses, and no detector scores.

👉 **[Read the contributor guide](CONTRIBUTING-VIDEOS.md)**, then **[submit videos](../../issues/new?template=label-videos.yml)**

Even 10 well-sourced videos help. Contributors are credited in the release notes.

## Road to public beta

| # | Step | Status | Who |
|---|---|---|---|
| 1 | Prototype, backend, safety review, eval harness, CI gate | ✅ Done (v0.3) | |
| 2 | **Collect 500+ labeled videos** (30+ AI and 30+ real per group) | 🟡 Open, [help wanted](#help-wanted-500-labeled-videos) | Community |
| 3 | Get a YouTube API key, and confirm `containsSyntheticMedia` is returned for other people's videos | ⬜ | Maintainer |
| 4 | Get a Hive API key, and confirm the `hq1/2/3.jpg` frame URLs work | ⬜ | Maintainer |
| 5 | Run the eval gate on real data, then choose the detector threshold (0.80 now; Hive recommends 0.9) | ⬜ Needs step 2 | Maintainer |
| 6 | Fix any group that fails the fairness check (≤ 1.5×) | ⬜ Needs step 5 | Maintainer + community |
| 7 | Deploy the backend over HTTPS (`TRUST_PROXY=1`, secret `REPORT_SALT`, `STATS_TOKEN`) | ⬜ | Maintainer |
| 8 | Publish a privacy policy: what's sent (video IDs only), what's kept, and for how long | ⬜ | Maintainer |
| 9 | Set up a creator appeal process with a named reviewer, and a weekly review of the "label looks wrong" queue | ⬜ | Maintainer |
| 10 | Test the kill switch, then run a 50-user closed beta and watch false-alarm reports | ⬜ | Maintainer + beta testers |
| 11 | Publish to the Chrome Web Store | ⬜ After 10 | Maintainer |

**Gate rule:** no public launch while any launch threshold fails, unless the owner signs a named exception.

## Repo layout

| Path | What |
|---|---|
| `manifest.json`, `src/` | Chrome extension (Manifest V3) |
| `src/verdict.js` | Verdict rules and thresholds |
| `backend/` | Python backend (standard library only) and its tests |
| `backend/signals/` | One module per check |
| `evals/` | Eval harness, thresholds, dataset validator and community labels |
| `docs/demo.html` | Clickable demo of the badges |
| `CONTRIBUTING-VIDEOS.md` | How to contribute labeled videos |

## Contributing code

Issues and PRs are welcome. Please keep every PR green on the safety gate, and add a test for any change to verdict rules or signals. A confirmed wrong label should become a permanent eval case.
