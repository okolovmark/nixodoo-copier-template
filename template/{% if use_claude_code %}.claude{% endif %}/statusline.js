#!/usr/bin/env node
"use strict";

// Claude Code statusline. Reads the session JSON on stdin, prints one line.
//
// Two things that used to break, and how they're handled here:
//   1. The 5h/7d usage segment vanished once a minute. Cause: the cache was
//      only *read* while fresh. Fix: always read it for display; freshness
//      only decides whether to re-fetch (serve-stale-while-revalidate).
//   2. The whole line sometimes didn't render. Cause: the refresh kept Node's
//      event loop alive, blocking the statusline on curl. Fix: the refresh is
//      fully detached (spawn + unref) with a hard --max-time, so Node exits
//      immediately and the line is always printed from cache.

const fs = require("fs");
const path = require("path");
const os = require("os");
const { execFileSync, spawn } = require("child_process");

const CACHE_FILE = path.join(os.tmpdir(), "claude-usage-cache.json");
const LOCK_FILE = CACHE_FILE + ".lock";
const REFRESH_AFTER_MS = 60_000; // re-fetch usage when the cache is older than this
const CURL_TIMEOUT_S = 10; // hard ceiling so a hung request can't stall anything
const USAGE_URL = "https://api.anthropic.com/api/oauth/usage";

// --- ANSI helpers ---
const R = "\x1b[0m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED = "\x1b[31m";
const BLUE = "\x1b[34m";

const heat = (p) => (p >= 80 ? RED : p >= 50 ? YELLOW : GREEN);
const paint = (color, text) => `${color}${text}${R}`;

const fmtTokens = (t) =>
  t >= 1e6 ? (t / 1e6).toFixed(1) + "M" : t >= 1e3 ? Math.floor(t / 1e3) + "k" : String(t);

const fmtResetIn = (iso) => {
  if (!iso) return "";
  const ms = new Date(iso).getTime() - Date.now();
  if (!isFinite(ms) || ms <= 0) return "now";
  const d = Math.floor(ms / 86_400_000);
  const h = Math.floor((ms % 86_400_000) / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  if (d > 0) return `${d}d${h}h`;
  if (h > 0) return `${h}h${m}m`;
  return `${m}m`;
};

const fmtUsd = (n) => `$${n.toFixed(n >= 100 ? 0 : n >= 10 ? 1 : 2)}`;

// --- API-equivalent cost from transcripts ---
// j.cost.total_cost_usd is the plan/subscription figure and already rolls up
// subagents, but on a subscription it's a heavily reduced basis. To also show
// the "what this would cost on the API" number, we price raw token usage from
// the transcripts ourselves: the main session file PLUS every subagent file in
// <session>/subagents/ (subagents run as separate sessions, so their usage is
// only here). Published per-MTok rates; cache write = 1.25x (5m) / 2x (1h) of
// input, cache read = 0.1x. Opus 4.x 1M context is standard-priced (no premium).
const PRICES = {
  opus: { in: 5, out: 25, cw5: 6.25, cw1h: 10, cr: 0.5 },
  sonnet: { in: 3, out: 15, cw5: 3.75, cw1h: 6, cr: 0.3 },
  haiku: { in: 1, out: 5, cw5: 1.25, cw1h: 2, cr: 0.1 },
  fable: { in: 10, out: 50, cw5: 12.5, cw1h: 20, cr: 1.0 },
};
function rateFor(model = "") {
  const m = model.toLowerCase();
  if (m.includes("haiku")) return PRICES.haiku;
  if (m.includes("sonnet")) return PRICES.sonnet;
  if (m.includes("fable") || m.includes("mythos")) return PRICES.fable;
  return PRICES.opus; // opus-4.x and unknown default
}
function priceLine(o) {
  const u = o.message?.usage;
  if (o.type !== "assistant" || !u) return 0;
  const r = rateFor(o.message?.model);
  const cc = u.cache_creation || {};
  const cw5 = cc.ephemeral_5m_input_tokens ?? u.cache_creation_input_tokens ?? 0;
  const cw1h = cc.ephemeral_1h_input_tokens ?? 0;
  return (
    ((u.input_tokens ?? 0) * r.in +
      (u.output_tokens ?? 0) * r.out +
      cw5 * r.cw5 +
      cw1h * r.cw1h +
      (u.cache_read_input_tokens ?? 0) * r.cr) /
    1e6
  );
}

// Incrementally fold a JSONL transcript's cost into `state` ({off, cost}),
// reading only the bytes appended since last call. Transcripts are append-only.
function tailCost(file, state) {
  let fd;
  try {
    fd = fs.openSync(file, "r");
  } catch {
    return;
  }
  try {
    const size = fs.fstatSync(fd).size;
    if (size < state.off) {
      state.off = 0;
      state.cost = 0;
    } // file rotated/truncated — reparse
    if (size <= state.off) return;
    const buf = Buffer.allocUnsafe(size - state.off);
    fs.readSync(fd, buf, 0, buf.length, state.off);
    const text = buf.toString("utf8");
    const lastNl = text.lastIndexOf("\n");
    if (lastNl < 0) return; // no complete line yet
    const complete = text.slice(0, lastNl);
    for (const line of complete.split("\n")) {
      if (!line) continue;
      let o;
      try {
        o = JSON.parse(line);
      } catch {
        continue;
      }
      state.cost += priceLine(o);
    }
    state.off += Buffer.byteLength(complete, "utf8") + 1;
  } finally {
    fs.closeSync(fd);
  }
}

// Returns {main, sub, total} in USD at API list price, or null. Caches byte
// offsets + running cost per session in tmp so each render only parses new bytes.
function apiCost(j) {
  const tp = j.transcript_path;
  const sid = j.session_id;
  if (!tp || !sid) return null;
  const cacheFile = path.join(os.tmpdir(), `claude-cost-${sid}.json`);
  let c;
  try {
    c = JSON.parse(fs.readFileSync(cacheFile, "utf8"));
  } catch {
    c = {};
  }
  if (!c.main) c.main = { off: 0, cost: 0 };
  if (!c.subs) c.subs = {};

  tailCost(tp, c.main);

  const subDir = path.join(path.dirname(tp), path.basename(tp, ".jsonl"), "subagents");
  let sub = 0;
  try {
    for (const f of fs.readdirSync(subDir)) {
      if (!f.endsWith(".jsonl")) continue;
      const st = c.subs[f] || { off: 0, cost: 0 };
      tailCost(path.join(subDir, f), st);
      c.subs[f] = st;
      sub += st.cost;
    }
  } catch {}

  try {
    fs.writeFileSync(cacheFile, JSON.stringify(c));
  } catch {}
  return { main: c.main.cost, sub, total: c.main.cost + sub };
}

// --- main ---
let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (buf += d));
process.stdin.on("end", () => {
  let j = {};
  try {
    j = JSON.parse(buf);
  } catch {}
  try {
    process.stdout.write(render(j));
  } catch {
    process.stdout.write("");
  }
  maybeRefreshUsage();
});

function render(j) {
  const seg = [];

  // git branch (replaces the old long cwd path)
  const branch = gitRef(j.workspace?.current_dir || j.cwd);
  if (branch) seg.push(paint(BOLD + BLUE, branch));

  // model
  if (j.model?.display_name) seg.push(paint(DIM, j.model.display_name));

  // context window
  const ctx = j.context_window || {};
  const pct = Math.round(ctx.used_percentage ?? 0);
  const size = ctx.context_window_size ?? 200_000;
  const u = ctx.current_usage || {};
  const used =
    (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0);
  seg.push(`${paint(heat(pct), `Ctx ${pct}%`)} ${DIM}${fmtTokens(used)}/${fmtTokens(size)}${R}`);

  // usage limits — read even when stale, so the segment never blinks out
  const usage = readCache();
  if (usage) {
    const limit = (label, o) => {
      if (!o || o.utilization == null) return;
      const p = Math.round(o.utilization);
      seg.push(`${heat(p)}${label} ${p}%${R}${DIM}(${fmtResetIn(o.resets_at)})${R}`);
    };
    limit("5h", usage.five_hour);
    limit("7d", usage.seven_day);
    limit("Opus", usage.seven_day_opus); // only present when an Opus weekly cap is active

    // Model-scoped weekly caps (e.g. Fable) arrive only inside limits[], keyed
    // by model — there's no top-level seven_day_fable. Render each as used %
    // (same convention as the segments above; heat/red = little left), skipping
    // Opus when it already has its own dedicated line above to avoid a dupe.
    for (const l of usage.limits || []) {
      const name = l.scope?.model?.display_name;
      if (l.kind !== "weekly_scoped" || !name || l.percent == null) continue;
      if (name.toLowerCase() === "opus" && usage.seven_day_opus) continue;
      const p = Math.round(l.percent);
      seg.push(`${heat(p)}${name} ${p}%${R}${DIM}(${fmtResetIn(l.resets_at)})${R}`);
    }
  }

  // session cost — last segment. Shows the API-equivalent (pay-as-you-go price
  // of this session's tokens: main transcript + every subagent file), with the
  // subagent share in parens. The plan figure (j.cost.total_cost_usd) is
  // deliberately NOT shown — on a subscription it's notional, not money spent.
  let api = null;
  try {
    api = apiCost(j);
  } catch {}
  if (api && api.total > 0) {
    let txt = `api ${fmtUsd(api.total)}`;
    if (api.sub > 0) txt += ` (${fmtUsd(api.sub)} ag)`;
    seg.push(paint(DIM, txt));
  }

  return seg.join("  ");
}

function gitRef(dir) {
  if (!dir) return "";
  const git = (args) => {
    try {
      return execFileSync("git", ["-C", dir, ...args], {
        encoding: "utf8",
        timeout: 1000,
        stdio: ["ignore", "pipe", "ignore"],
      }).trim();
    } catch {
      return "";
    }
  };
  const branch = git(["branch", "--show-current"]);
  if (branch) return branch;
  const sha = git(["rev-parse", "--short", "HEAD"]); // detached HEAD
  return sha ? "@" + sha : "";
}

function readCache() {
  try {
    return JSON.parse(fs.readFileSync(CACHE_FILE, "utf8"));
  } catch {
    return null;
  }
}

function ageMs(file) {
  try {
    return Date.now() - fs.statSync(file).mtimeMs;
  } catch {
    return Infinity;
  }
}

function maybeRefreshUsage() {
  if (ageMs(CACHE_FILE) < REFRESH_AFTER_MS) return; // still fresh enough
  if (ageMs(LOCK_FILE) < (CURL_TIMEOUT_S + 5) * 1000) return; // a refresh is already in flight

  let token;
  try {
    const creds = JSON.parse(
      fs.readFileSync(path.join(os.homedir(), ".claude", ".credentials.json"), "utf8")
    );
    token = creds.claudeAiOauth?.accessToken;
  } catch {}
  if (!token) return;

  try {
    fs.writeFileSync(LOCK_FILE, ""); // stamp so concurrent renders don't stampede
  } catch {}

  // Detached pipeline: fetch -> validate JSON -> atomic rename. Token is passed
  // via env (not baked into the command string). stdio is ignored and the child
  // is unref'd so this process exits without waiting on curl.
  const tmp = `${CACHE_FILE}.${process.pid}.tmp`;
  const q = JSON.stringify;
  const script =
    `curl -sf --max-time ${CURL_TIMEOUT_S}` +
    ` -H "Authorization: Bearer $USAGE_TOKEN"` +
    ` -H "anthropic-beta: oauth-2025-04-20"` +
    ` ${q(USAGE_URL)} -o ${q(tmp)}` +
    ` && ${q(process.execPath)} -e 'JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"))' ${q(tmp)}` +
    ` && mv -f ${q(tmp)} ${q(CACHE_FILE)}` +
    ` || rm -f ${q(tmp)}`;

  try {
    const child = spawn("/bin/bash", ["-c", script], {
      detached: true,
      stdio: "ignore",
      env: { ...process.env, USAGE_TOKEN: token },
    });
    child.unref();
  } catch {}
}
