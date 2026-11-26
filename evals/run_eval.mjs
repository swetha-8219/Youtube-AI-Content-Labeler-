#!/usr/bin/env node
// Eval harness + CI gate for AI Label Layer.
//
// Scores a labeled set of videos with the SAME verdict rules the extension ships
// (src/verdict.js), checks the launch thresholds, sweeps classifier thresholds,
// and exits 1 if any threshold is missed.
//
// Usage:
//   node evals/run_eval.mjs --dataset evals/dataset.csv --backend http://localhost:8787
//   node evals/run_eval.mjs --dataset evals/dataset.csv --signals evals/signals.jsonl   (offline / CI)
//   add --save-signals out.jsonl to cache backend results for later offline runs
//
// dataset.csv columns: video_id,truth,group[,youtube_label]
//   truth: ai | real      group: free text (e.g. non-english, phone-lowlight, animation)
//   youtube_label: 1 if YouTube's on-page AI label is shown (annotators record this; optional)
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");

// Load the extension's verdict.js unchanged.
const ctx = { globalThis: {} };
ctx.window = ctx.globalThis;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(root, "src/verdict.js"), "utf8"), ctx);
const { computeVerdict, THRESHOLDS } = ctx.globalThis.AILabel;

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? acc.concat([[a.slice(2), all[i + 1]]]) : acc), [])
);
const limits = JSON.parse(fs.readFileSync(args.thresholds || path.join(here, "thresholds.json"), "utf8"));

function readCsv(file) {
  const [head, ...lines] = fs.readFileSync(file, "utf8").trim().split(/\r?\n/);
  const cols = head.split(",").map(s => s.trim());
  return lines.filter(Boolean).map(l => Object.fromEntries(l.split(",").map((v, i) => [cols[i], v.trim()])));
}

async function getSignals(rows) {
  if (args.signals) {
    const map = new Map(fs.readFileSync(args.signals, "utf8").trim().split("\n").map(l => {
      const o = JSON.parse(l); return [o.videoId, o.signals];
    }));
    return rows.map(r => map.get(r.video_id) || null);
  }
  if (!args.backend) throw new Error("Pass --backend URL or --signals file");
  const out = [];
  for (const r of rows) {
    const res = await fetch(`${args.backend.replace(/\/$/, "")}/verify?videoId=${encodeURIComponent(r.video_id)}`);
    out.push(res.ok ? (await res.json()).signals : null);
  }
  if (args["save-signals"]) {
    fs.writeFileSync(args["save-signals"], rows.map((r, i) => JSON.stringify({ videoId: r.video_id, signals: out[i] })).join("\n") + "\n");
  }
  return out;
}

function toInputs(row, s) {
  const v = k => (s && s[k] ? s[k].value : null);
  return {
    youtubeLabel: row.youtube_label === "1",
    youtubeApi: v("youtubeApi"), c2paAI: null, synthid: v("synthid"),
    classifier: v("classifier"), crowdReports: v("crowd")
  };
}

const ratio = (a, b) => (b ? a / b : null);
const pct = x => (x == null ? "  n/a " : (100 * x).toFixed(1).padStart(5) + "%");

function score(rows, inputs, override) {
  const T = override || THRESHOLDS;
  const saved = { ...THRESHOLDS };
  Object.assign(THRESHOLDS, T);
  const levels = inputs.map(i => computeVerdict(i).level);
  Object.assign(THRESHOLDS, saved);

  const c = { red: { ai: 0, real: 0 }, amber: { ai: 0, real: 0 }, grey: { ai: 0, real: 0 } };
  const byGroup = {};
  rows.forEach((r, i) => {
    const lv = levels[i] === "confirmed" ? "red" : levels[i] === "likely" ? "amber" : "grey";
    c[lv][r.truth]++;
    const g = (byGroup[r.group] ||= { real: 0, flaggedReal: 0, ai: 0, caughtAi: 0 });
    if (r.truth === "real") { g.real++; if (lv !== "grey") g.flaggedReal++; }
    else { g.ai++; if (lv !== "grey") g.caughtAi++; }
  });
  const nReal = c.red.real + c.amber.real + c.grey.real;
  const nAi = c.red.ai + c.amber.ai + c.grey.ai;
  const m = {
    red_precision: ratio(c.red.ai, c.red.ai + c.red.real),
    amber_precision: ratio(c.amber.ai, c.amber.ai + c.amber.real),
    amber_fpr_on_real: ratio(c.amber.real, nReal),
    red_fpr_on_real: ratio(c.red.real, nReal),
    catch_rate: ratio(c.red.ai + c.amber.ai, nAi)
  };
  const overallFpr = ratio(c.red.real + c.amber.real, nReal) || 0;
  const groups = Object.entries(byGroup).map(([g, v]) => ({
    group: g, n: v.real + v.ai, fpr: ratio(v.flaggedReal, v.real), catch: ratio(v.caughtAi, v.ai),
    fprRatio: overallFpr > 0 && v.real ? ratio(v.flaggedReal, v.real) / overallFpr : null
  }));
  return { counts: c, m, groups, nReal, nAi };
}

const rows = readCsv(args.dataset || path.join(here, "dataset.csv"));
const bad = rows.filter(r => !["ai", "real"].includes(r.truth));
if (bad.length) { console.error(`truth must be ai|real; bad rows: ${bad.map(r => r.video_id).join(", ")}`); process.exit(2); }
const sigs = await getSignals(rows);
const missing = rows.filter((_, i) => !sigs[i]).length;
const inputs = rows.map((r, i) => toInputs(r, sigs[i]));
const res = score(rows, inputs);

console.log(`\nAI Label Layer eval: ${rows.length} videos (${res.nAi} AI, ${res.nReal} real)${missing ? `, ${missing} with no signals` : ""}\n`);
console.log("Verdict      AI   Real");
for (const [k, v] of Object.entries(res.counts)) console.log(`${k.padEnd(9)} ${String(v.ai).padStart(5)} ${String(v.real).padStart(6)}`);

const checks = [
  ["Red precision", res.m.red_precision, ">=", limits.red_precision_min],
  ["Amber false-positive rate on real", res.m.amber_fpr_on_real, "<=", limits.amber_fpr_on_real_max],
  ["Amber precision", res.m.amber_precision, ">=", limits.amber_precision_min],
  ["Catch rate (red+amber on AI)", res.m.catch_rate, ">=", limits.catch_rate_min]
];
let failed = 0;
console.log("\nLaunch gate");
for (const [name, val, op, lim] of checks) {
  // No predictions of a kind => metric is undefined; precision is vacuous, catch/FPR are not.
  const ok = val == null ? name.includes("precision") : op === ">=" ? val >= lim : val <= lim;
  if (!ok) failed++;
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name.padEnd(36)} ${pct(val)}  (need ${op} ${pct(lim).trim()})`);
}

console.log("\nBy group (false-positive rate on real videos, catch rate on AI videos)");
for (const g of res.groups.sort((a, b) => (b.fprRatio || 0) - (a.fprRatio || 0))) {
  const small = g.n < limits.min_videos_per_group;
  const ok = small || g.fprRatio == null || g.fprRatio <= limits.fairness_fpr_ratio_max;
  if (!ok) failed++;
  console.log(`  ${small ? "WARN" : ok ? "PASS" : "FAIL"}  ${g.group.padEnd(20)} n=${String(g.n).padStart(4)}  FPR ${pct(g.fpr)}  catch ${pct(g.catch)}` +
    (g.fprRatio != null ? `  ${g.fprRatio.toFixed(2)}x overall` : "") + (small ? `  (fewer than ${limits.min_videos_per_group} videos)` : ""));
}

console.log("\nClassifier threshold sweep (amber needs score >= t, or >= 0.75t with 25+ reports)");
console.log("     t   amber prec   amber FPR   catch");
for (const t of [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]) {
  const r = score(rows, inputs, { ...THRESHOLDS, classifierLikely: t, classifierWithCrowd: Math.round(t * 75) / 100 });
  const mark = Math.abs(t - THRESHOLDS.classifierLikely) < 1e-9 ? "  <- current" : "";
  console.log(`  ${t.toFixed(2)}   ${pct(r.m.amber_precision)}      ${pct(r.m.amber_fpr_on_real)}    ${pct(r.m.catch_rate)}${mark}`);
}

console.log(failed ? `\nGATE: FAIL (${failed} check${failed > 1 ? "s" : ""} missed)\n` : "\nGATE: PASS\n");
process.exit(failed ? 1 : 0);
