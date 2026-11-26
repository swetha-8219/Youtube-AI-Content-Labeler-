# Help us build the 500-video test set

AI Label Layer puts a badge on YouTube videos: **AI-generated**, **Likely AI**, or **Unverified**. Before anyone relies on those badges, we have to prove they're accurate, and fair across different kinds of videos. That needs a test set of **500+ public YouTube videos where the right answer is known for certain**.

You don't need to code. Ten well-sourced videos is a real contribution.

## The one rule: evidence, not guesses

Every video needs a link that shows how you know the answer. "It looks AI" is not evidence, and neither is another detector's score. If we label from guesses, we end up testing our guesses instead of the tool.

| Label | Good evidence |
|---|---|
| `ai` | The creator says which AI tool made it (in the description, a pinned comment or a post). It's an official sample from an AI lab (Veo, Sora, Runway…). YouTube shows "Altered or synthetic content". It's a known AI channel that states so. |
| `real` | The original upload from a news outlet, broadcaster or agency. The creator's own behind-the-scenes or raw footage. Videos uploaded before 2022, before realistic AI video existed. |

Not sure? Leave it out. Precision matters more than volume.

## What we need most

We need a balance of **AI and real in every group**. The tool must not unfairly flag certain kinds of real videos.

| Group | What it means | Why it matters |
|---|---|---|
| `english-pro` | Professionally produced, English | Baseline |
| `non-english` | Any language other than English | Detectors are often trained mostly on English content |
| `phone-lowlight` | Phone footage, night, grainy, heavily compressed | **The biggest false-alarm risk**: grain can look "synthetic" |
| `animation-cgi` | Animation, CGI, video games, VFX | Not AI, but easy to mistake for it |
| `music-filtered` | Music videos and heavy filters or color grading | Same risk as above |
| `news` | News and documentary footage | Highest stakes if mislabeled |
| `shorts` | YouTube Shorts | Different format and compression |
| `other` | Anything else | |

The target is at least 30 AI and 30 real videos per group. Run `node evals/validate_dataset.mjs` to see the current counts.

## How to contribute

**Option A: no coding.** Open an issue using **"Contribute labeled videos"** (Issues → New issue). Paste one line per video. A maintainer will add them.

**Option B: pull request.** Add a file `evals/community/<your-github-name>.csv` in this format:

```
video_id,truth,group,youtube_label,evidence_url
abcDEF12345,ai,shorts,1,https://example.com/creator-says-made-with-veo
```

- `video_id`: the 11 characters after `watch?v=` (or after `/shorts/`)
- `youtube_label`: `1` if YouTube shows "Altered or synthetic content" on the video, `0` if not
- No commas inside any field

Then run `node evals/validate_dataset.mjs`. CI runs the same check on your PR.

## Review

- A maintainer checks each evidence link before merge.
- If two contributors disagree about a video, it's held back until it's resolved.
- Mistakes found later are fixed, not hidden.

## Please don't

- Submit private, unlisted or deleted videos.
- Label someone's video `ai` to discredit them. Submissions made in bad faith will be removed, and the contributor blocked.
- Include personal information in notes or evidence.

## Credit

Contributors are listed in the release notes of the version their videos helped validate.
