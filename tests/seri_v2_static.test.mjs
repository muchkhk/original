import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import assert from "node:assert/strict";

const html = fs.readFileSync("seri.html", "utf8");
const inlineScripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);

test("seri v2のインラインJavaScript構文が正しい", () => {
  assert.ok(inlineScripts.length > 0);
  inlineScripts.forEach((code, i) => assert.doesNotThrow(() => new vm.Script(code, { filename: `seri-inline-${i}.js` })));
});

test("Firebase匿名認証とLOCAL永続化を使用する", () => {
  assert.match(html, /firebase-auth-compat\.js/);
  assert.match(html, /Auth\.Persistence\.LOCAL/);
  assert.match(html, /signInAnonymously\(\)/);
  assert.match(html, /auth\.uid|user\.uid/);
});

test("旧roomsではなくseriRoomsV2を使用する", () => {
  assert.match(html, /const ROOT="seriRoomsV2"/);
  assert.doesNotMatch(html, /["'`]rooms\//);
});

test("6文字のルームコードを使用する", () => {
  assert.match(html, /slice\(0,6\)/);
  assert.match(html, /code\.length!==6/);
  assert.match(html, /maxlength:6/);
});

test("存在しないmetaを読まずRulesで保護された新規ルームを作る", () => {
  const createRoom = html.match(/async function createRoom\(\)[\s\S]*?\n\nasync function joinRoom/);
  assert.ok(createRoom);
  assert.doesNotMatch(createRoom[0], /\/meta`\)\.get\(\)/);
  assert.match(createRoom[0], /\.set\(meta\)/);
  assert.match(createRoom[0], /PERMISSION_DENIED/);
});

test("参加時は占有済み席を飛ばし競合時だけ次の空席を試す", () => {
  const joinRoom = html.match(/async function joinRoom\(spectator=false\)[\s\S]*?\n\nfunction attachHostStreams/);
  assert.ok(joinRoom);
  assert.match(joinRoom[0], /if\(seats\[i\]\)continue/);
  assert.match(joinRoom[0], /transaction\(cur=>cur\|\|\{uid:user\.uid/);
  assert.match(joinRoom[0], /if\(e\?\.code!=="PERMISSION_DENIED"\)throw e/);
});

test("秘匿情報はUID別privateから読む", () => {
  assert.match(html, /private\/\$\{user\.uid\}/);
  assert.match(html, /private\/\$\{s\.uid\}/);
  assert.doesNotMatch(html, /listen\("private"[^\n]+ST\.spectator/);
});

test("一般プレイヤーはgameではなく本人席actionsへ書く", () => {
  assert.match(html, /actions\/\$\{ST\.seat\}/);
  assert.match(html, /processAction/);
  assert.match(html, /isHost\(\)/);
});

test("DB由来値をinnerHTMLへ渡さない", () => {
  assert.doesNotMatch(html, /innerHTML/);
  assert.match(html, /document\.createTextNode/);
  assert.match(html, /replaceChildren/);
});

test("同一匿名UIDのセッション復元経路がある", () => {
  assert.match(html, /restoreSession/);
  assert.match(html, /seat\.uid===user\.uid/);
  assert.match(html, /SESSION_KEY/);
});

test("観戦者は秘匿購読を作らない", () => {
  assert.match(html, /if\(!ST\.spectator&&ST\.seat/);
  assert.match(html, /観戦中：プレイ中の秘匿情報は表示されません/);
});

test("終了後だけreveal購読を開始する", () => {
  assert.match(html, /phase==="ended"&&!revealAttached/);
  assert.match(html, /listen\("reveal"/);
});

test("Emulator接続はlocalhostかつ明示パラメータ時だけ", () => {
  assert.match(html, /\^127\\\./);
  assert.match(html, /local&&q\.get\("emulator"\)==="1"/);
  assert.match(html, /useEmulator\("127\.0\.0\.1",9170\)/);
});

test("ブラウザ受入Emulatorは本番と同じboadgame2 namespaceを使う", () => {
  const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
  assert.match(pkg.scripts["emulators:seri-v2"], /--project boadgame2(?:\s|$)/);
});

test("series機能と自己申告UIDを廃止している", () => {
  assert.doesNotMatch(html, /ks_uid|function uid\(|series\//);
});

test("ホスト切断待ち表示と二重履歴防止がある", () => {
  assert.match(html, /進行役の再接続を待っています/);
  assert.match(html, /if\(existing\)return/);
  assert.match(html, /history\/\$\{g\.round\}/);
});
