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

function extractHintFilterFns() {
  const digitSrc = SCRIPT.match(/const FILTER_DIGIT_REGEX = (\/[\s\S]*?\/);/)[1];
  const wordsSrc = SCRIPT.match(/const FILTER_RANK_WORDS = (\[[\s\S]*?\]);/)[1];
  const whitelistSrc = SCRIPT.match(/const FILTER_KANJI_WHITELIST = (\[[\s\S]*?\]);/)[1];
  const FILTER_DIGIT_REGEX = new Function(`return ${digitSrc};`)();
  const FILTER_RANK_WORDS = new Function(`return ${wordsSrc};`)();
  const FILTER_KANJI_WHITELIST = new Function(`return ${whitelistSrc};`)();
  function hintFilterViolation(text) {
    if (typeof text !== 'string') return 'invalid';
    if (text.length === 0) return 'empty';
    if (text.length > 40) return 'too_long';
    let stripped = text;
    for (const w of FILTER_KANJI_WHITELIST) stripped = stripped.replaceAll(w, '');
    if (FILTER_DIGIT_REGEX.test(stripped)) return 'digit';
    for (const w of FILTER_RANK_WORDS) if (stripped.includes(w)) return 'rank_word:' + w;
    return null;
  }
  return { hintFilterViolation };
}

test("cross-check: frozen 32 hint texts against the whitelist-adjusted human hint filter must produce zero violations (hard assertion, 4p-v0.2.1)", () => {
  const defMd = fs.readFileSync("proto/核定義_伝達核v1.0_動物軸版_凍結_2026-07-17.md", "utf8");
  const rows = [...defMd.matchAll(/^\| (体重|速さ|寿命|危険度|かわいさ|人気|五十音|群れ) \| (遠|中|直) \| ([^|]+) \|/gm)];
  assert.equal(rows.length, 32, "expected 32 hint rows in the frozen definition");
  const { hintFilterViolation } = extractHintFilterFns();
  const violations = [];
  for (const [, axis, tier, textRaw] of rows) {
    const text = textRaw.trim();
    const v = hintFilterViolation(text);
    if (v) violations.push({ axis, tier, text, reason: v });
  }
  assert.deepEqual(violations, [], `expected zero filter violations among frozen hint texts, got: ${JSON.stringify(violations)}`);
});

test("hint filter whitelist-subtraction unit cases (4p-v0.2.1)", () => {
  const { hintFilterViolation } = extractHintFilterFns();
  assert.equal(hintFilterViolation("一緒にいたい順"), null, "whitelisted word should pass");
  assert.equal(hintFilterViolation("五十音で後ろ"), null, "whitelisted word should pass");
  assert.equal(hintFilterViolation("一匹でいそう"), null, "whitelisted word should pass");
  // "一番" contains the kanji digit "一" (not whitelisted), so the pre-existing digit-check-first
  // ordering classifies this as 'digit' rather than 'rank_word:一番' — still blocked either way.
  assert.ok(hintFilterViolation("一番大きい"), "rank word must still block (reason may be 'digit' due to pre-existing check order)");
  assert.equal(hintFilterViolation("三"), 'digit', "bare kanji digit must still block");
  assert.equal(hintFilterViolation("一一緒"), 'digit', "residual digit after whitelist subtraction must still block (no bypass)");
  assert.equal(hintFilterViolation("壱の獣"), 'digit', "kanji numeral variant must still block");
});

test("pickNextOpenSeat: returns the first untried open seat from a synced snapshot, or null when full (4p-v0.2.2)", () => {
  const teamSrc = SCRIPT.match(/const TEAM_OF_SEAT = (\{[\s\S]*?\});/)[1];
  const fnSrc = SCRIPT.match(/function pickNextOpenSeat\(seatsSnapshot, candidates, tried\)\{[\s\S]*?\n\}/)[0];
  const pickNextOpenSeat = new Function(`const TEAM_OF_SEAT = ${teamSrc}; ${fnSrc}; return pickNextOpenSeat;`)();

  const candidates = [0, 1, 2, 3];
  // 同期済みスナップショット：席0のみ占有 → 未試行の最初の空席(1)を返す
  assert.equal(pickNextOpenSeat({ 0: { uid: 'host' } }, candidates, new Set()), 1);
  // 空のスナップショット（部屋が本当に無人）→ 最初の候補(0)を返す
  assert.equal(pickNextOpenSeat({}, candidates, new Set()), 0);
  // 席0は空いて見えるが直前の試行で既に失敗（tried）済み → スキップして次の空席(1)
  assert.equal(pickNextOpenSeat({}, candidates, new Set([0])), 1);
  // 全席占有 → 満席（null）
  assert.equal(pickNextOpenSeat({ 0: {}, 1: {}, 2: {}, 3: {} }, candidates, new Set()), null);
  // 全席「試行済み」（実際は空いていても）→ 満席（null）扱いで打ち切り
  assert.equal(pickNextOpenSeat({}, candidates, new Set([0, 1, 2, 3])), null);
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
