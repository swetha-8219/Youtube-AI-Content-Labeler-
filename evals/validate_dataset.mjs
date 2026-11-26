#!/usr/bin/env node
// Validates community-labeled video files and optionally merges them into one dataset.
//
//   node evals/validate_dataset.mjs                         # check evals/community/*.csv
//   node evals/validate_dataset.mjs --merge evals/dataset.csv   # check, then write merged file
//
// Exits 1 on any error, so CI blocks bad submissions.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const dir = path.join(here, "community");
const REQUIRED = ["video_id", "truth", "group", "youtube_label", "evidence_url"];
const GROUPS = new Set(["english-pro", "non-english", "phone-lowlight", "animation-cgi", "music-filtered", "news", "shorts", "other"]);
const ID = /^[A-Za-z0-9_-]{11}$/;

const args = process.argv.slice(2);
const mergeTo = args.includes("--merge") ? args[args.indexOf("--merge") + 1] : null;

const files = fs.existsSync(dir) ? fs.readdirSync(dir).filter(f => f.endsWith(".csv")).sort() : [];
const errors = [];
const seen = new Map();
const rows = [];

for (const f of files) {
  const lines = fs.readFileSync(path.join(dir, f), "utf8").trim().split(/\r?\n/);
  const head = lines[0].split(",").map(s => s.trim());
  const missing = REQUIRED.filter(c => !head.includes(c));
  if (missing.length) { errors.push(`${f}: missing columns ${missing.join(", ")}`); continue; }
  lines.slice(1).forEach((line, i) => {
    if (!line.trim()) return;
    const where = `${f}:${i + 2}`;
    const cells = line.split(",").map(s => s.trim());
    if (cells.length !== head.length) { errors.push(`${where}: expected ${head.length} columns, got ${cells.length} (no commas inside fields)`); return; }
    const r = Object.fromEntries(head.map((h, j) => [h, cells[j]]));
    const before = errors.length;
    if (!ID.test(r.video_id)) errors.push(`${where}: video_id must be the 11-character YouTube ID`);
    if (!["ai", "real"].includes(r.truth)) errors.push(`${where}: truth must be ai or real`);
    if (!GROUPS.has(r.group)) errors.push(`${where}: group must be one of ${[...GROUPS].join(", ")}`);
    if (!["0", "1"].includes(r.youtube_label)) errors.push(`${where}: youtube_label must be 0 or 1`);
    if (!/^https:\/\/\S+$/.test(r.evidence_url)) errors.push(`${where}: evidence_url must be an https link showing how you know`);
    if (errors.length > before) return; // invalid rows never count toward progress
    if (seen.has(r.video_id)) {
      const prev = seen.get(r.video_id);
      if (prev.truth !== r.truth) errors.push(`${where}: ${r.video_id} conflicts with ${prev.where} (${prev.truth} vs ${r.truth}); needs maintainer review`);
      return; // duplicate; keep the first
    }
    seen.set(r.video_id, { truth: r.truth, where });
    rows.push(r);
  });
}

const count = (k, v) => rows.filter(r => r[k] === v).length;
console.log(`${files.length} file(s), ${rows.length} unique videos: ${count("truth", "ai")} AI, ${count("truth", "real")} real`);
for (const g of GROUPS) {
  const n = count("group", g);
  if (n) console.log(`  ${g.padEnd(16)} ${String(n).padStart(4)}  (AI ${rows.filter(r => r.group === g && r.truth === "ai").length}, real ${rows.filter(r => r.group === g && r.truth === "real").length})`);
}
if (rows.length < 500) console.log(`Progress toward beta goal: ${rows.length}/500`);

if (errors.length) {
  console.error(`\n${errors.length} problem(s):\n  ` + errors.join("\n  "));
  process.exit(1);
}
if (mergeTo) {
  fs.writeFileSync(mergeTo, "video_id,truth,group,youtube_label\n" +
    rows.map(r => [r.video_id, r.truth, r.group, r.youtube_label].join(",")).join("\n") + "\n");
  console.log(`Wrote ${mergeTo}`);
}
