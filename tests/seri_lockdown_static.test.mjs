import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";

const seri = fs.readFileSync("seri.html", "utf8");
const index = fs.readFileSync("index.html", "utf8");
const rules = JSON.parse(fs.readFileSync("firebase/firebase_rules.json", "utf8"));
const seriConfig = JSON.parse(fs.readFileSync("firebase.seri.json", "utf8"));
const witchConfig = JSON.parse(fs.readFileSync("firebase.json", "utf8"));

test("seri.htmlは静的なメンテナンス表示だけを提供する", () => {
  assert.match(seri, /セキュリティ構造の更新のため一時停止しています/);
  assert.match(seri, /旧ルームの作成・参加・観戦機能は利用できません/);
  assert.doesNotMatch(seri, /<script\b/i);
  assert.doesNotMatch(seri, /firebase/i);
  assert.doesNotMatch(seri, /rooms\//i);
  assert.doesNotMatch(seri, /ルーム作成|参加する|観戦する/);
});

test("seri.htmlは通信とスクリプトをCSPでも禁止する", () => {
  assert.match(seri, /Content-Security-Policy/);
  assert.match(seri, /default-src 'none'/);
  assert.doesNotMatch(seri, /connect-src/);
});

test("ゲーム一覧の影の競りカードはリンク無効で更新中表示になる", () => {
  const start = index.indexOf("<!-- ===== 影の競り ===== -->");
  const end = index.indexOf("<!-- ===== 魔女の調合 ===== -->");
  const card = index.slice(start, end);
  assert.match(card, /<article class="game disabled" aria-disabled="true">/);
  assert.match(card, /セキュリティ更新中/);
  assert.doesNotMatch(card, /href="seri\.html/);
  assert.doesNotMatch(card, /▶ 遊ぶ/);
  assert.doesNotMatch(index, /4文字または6文字/);
});

test("魔女の調合への導線は維持する", () => {
  assert.match(index, /<a class="game" href="witch\.html\?v=1\.3">/);
});

test("旧roomsは全read/writeを拒否する", () => {
  assert.equal(rules.rules[".read"], false);
  assert.equal(rules.rules[".write"], false);
  assert.deepEqual(rules.rules.rooms, { ".read": false, ".write": false });
});

test("影の競り用deploy設定は魔女の調合用設定から分離される", () => {
  assert.equal(seriConfig.database.rules, "firebase/firebase_rules.json");
  assert.equal(witchConfig.database.rules, "firebase/witch_rules.json");
});
