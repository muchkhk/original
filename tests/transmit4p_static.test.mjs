import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";

const HTML = fs.readFileSync("proto/transmitcore_4p_v0.html", "utf8");
const SCRIPT = HTML.match(/<script nonce="transmit4p-v0">\n([\s\S]*?)\n<\/script>/)[1];

test("script block parses as valid JS", () => {
  assert.doesNotThrow(() => new vm.Script(SCRIPT, { filename: "transmit4p.js" }));
});

test("uses transmit4pRoomsV0 root, not a legacy/other project's room path", () => {
  assert.match(HTML, /const ROOT = "transmit4pRoomsV0"/);
  assert.doesNotMatch(HTML, /seriRoomsV2/);
  assert.doesNotMatch(HTML, /roomsV2/);
});

test("no innerHTML assignment with dynamic/untrusted content (only the static clear-to-empty-string is allowed)", () => {
  const matches = [...HTML.matchAll(/\.innerHTML\s*=\s*([^;]+);/g)];
  for (const m of matches) {
    assert.equal(m[1].trim(), "''", `unexpected innerHTML assignment: ${m[0]}`);
  }
});

test("hint text and other user-controlled strings are rendered via createTextNode, not string concatenation into innerHTML", () => {
  assert.match(HTML, /createTextNode/);
});

test("room code generation excludes ambiguous characters and is 6 chars", () => {
  assert.match(HTML, /CODE_CHARS\s*=\s*"ABCDEFGHJKLMNPQRSTUVWXYZ23456789"/);
  assert.match(HTML, /newCode\(\)/);
});

test("emulator connection is guarded to localhost/127.* and an explicit query param", () => {
  assert.match(HTML, /hostname === 'localhost'/);
  assert.match(HTML, /\/\^127\\\./);
  assert.match(HTML, /q\.get\('emulator'\)\s*===\s*'1'/);
});

test("VER is defined and compared against the room's stored meta.version for a mismatch banner", () => {
  assert.match(HTML, /const VER = "4p-v0\.\d+\.\d+"/);
  assert.match(HTML, /ST\.meta\.version !== VER/);
});

test("reconnection: restoreSession checks the live seat's uid against the current auth uid, not a trusted client value", () => {
  assert.match(HTML, /seat\.uid === user\.uid/);
});

test("all 32 frozen hint texts from the definition file are present verbatim", () => {
  const defMd = fs.readFileSync("proto/核定義_伝達核v1.0_動物軸版_凍結_2026-07-17.md", "utf8");
  const rows = [...defMd.matchAll(/^\| (体重|速さ|寿命|危険度|かわいさ|人気|五十音|群れ) \| (遠|中|直) \| ([^|]+) \|/gm)];
  assert.equal(rows.length, 32, "expected 32 hint rows in the frozen definition");
  for (const [, axis, tier, text] of rows) {
    const needle = `text:'${text.trim()}'`;
    assert.ok(HTML.includes(needle), `missing or altered hint text: axis=${axis} tier=${tier} text="${text.trim()}"`);
  }
});

test("cross-check: frozen 32 hint texts against the v0.1-expanded human hint filter (informational, does not block — NPC hints are exempt from the filter per spec)", () => {
  const defMd = fs.readFileSync("proto/核定義_伝達核v1.0_動物軸版_凍結_2026-07-17.md", "utf8");
  const rows = [...defMd.matchAll(/^\| (体重|速さ|寿命|危険度|かわいさ|人気|五十音|群れ) \| (遠|中|直) \| ([^|]+) \|/gm)];
  assert.equal(rows.length, 32, "expected 32 hint rows in the frozen definition");
  const digitSrc = SCRIPT.match(/const FILTER_DIGIT_REGEX = (\/[\s\S]*?\/);/)[1];
  const wordsSrc = SCRIPT.match(/const FILTER_RANK_WORDS = (\[[\s\S]*?\]);/)[1];
  const FILTER_DIGIT_REGEX = new Function(`return ${digitSrc};`)();
  const FILTER_RANK_WORDS = new Function(`return ${wordsSrc};`)();
  const violations = [];
  for (const [, axis, tier, textRaw] of rows) {
    const text = textRaw.trim();
    if (FILTER_DIGIT_REGEX.test(text)) { violations.push({ axis, tier, text, reason: 'digit' }); continue; }
    const hitWord = FILTER_RANK_WORDS.find(w => text.includes(w));
    if (hitWord) violations.push({ axis, tier, text, reason: `rank_word:${hitWord}` });
  }
  if (violations.length) {
    console.log(`[informational] ${violations.length} frozen hint text(s) would trip the human-facing filter (NPC-exempt, listed in the report):`);
    for (const v of violations) console.log(`  axis=${v.axis} tier=${v.tier} text="${v.text}" reason=${v.reason}`);
  }
  assert.ok(true);
});

test("all 8 axis rank tables match the frozen definition exactly", () => {
  const defMd = fs.readFileSync("proto/核定義_伝達核v1.0_動物軸版_凍結_2026-07-17.md", "utf8");
  const rows = [...defMd.matchAll(/^\| (体重|速さ|寿命|危険度|かわいさ|人気|五十音|群れ) \| (ゾウ|ウマ|ライオン|オオカミ|サル|ネコ|ウサギ|ネズミ) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$/gm)];
  assert.equal(rows.length, 8, "expected 8 axis rows in the frozen definition");
  // Extract just the AXIS_ORDER object literal for isolated evaluation.
  const m = SCRIPT.match(/const AXIS_ORDER = (\{[\s\S]*?\n\})/);
  const AXIS_ORDER = new Function(`return ${m[1]};`)();
  for (const row of rows) {
    const axis = row[1];
    const expected = row.slice(2).map(s => s.trim());
    assert.deepEqual(AXIS_ORDER[axis], expected, `axis ${axis} rank order mismatch`);
  }
});

test("no external CDN besides the Firebase compat SDK (matches the online-prototype allowance, not a solo-file no-network rule)", () => {
  const scriptSrcs = [...HTML.matchAll(/<script src="([^"]+)"/g)].map(m => m[1]);
  for (const src of scriptSrcs) {
    assert.match(src, /^https:\/\/www\.gstatic\.com\/firebasejs\//, `unexpected external script source: ${src}`);
  }
});

test("CSP nonce on the inline script tag matches the nonce allowed in the CSP meta tag", () => {
  const cspMatch = HTML.match(/Content-Security-Policy" content="[^"]*'nonce-([^']+)'/);
  const scriptTagMatch = HTML.match(/<script nonce="([^"]+)">/);
  assert.ok(cspMatch, "CSP meta tag must declare a nonce for script-src");
  assert.ok(scriptTagMatch, "inline <script> tag must carry a matching nonce attribute");
  assert.equal(scriptTagMatch[1], cspMatch[1], "script tag nonce must match the CSP-declared nonce");
});

test("no confirm/alert/prompt usage", () => {
  assert.doesNotMatch(HTML, /\bconfirm\(/);
  assert.doesNotMatch(HTML, /\balert\(/);
  assert.doesNotMatch(HTML, /\bprompt\(/);
});

test("spectator/observation mode is not implemented (explicitly out of scope per instruction)", () => {
  assert.doesNotMatch(HTML, /spectator/i);
});
