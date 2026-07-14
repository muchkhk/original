import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const html = fs.readFileSync("witch.html", "utf8");
const packageJson = JSON.parse(fs.readFileSync("package.json", "utf8"));

test("全インラインscriptにJavaScript構文エラーがない", () => {
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map(match => match[1])
    .filter(source => source.trim());
  assert.ok(scripts.length >= 3);
  scripts.forEach((source, index) => new vm.Script(source, { filename: `witch-inline-${index + 1}.js` }));
});

test("オンラインDBはroomsV2と6文字コードだけを使う", () => {
  assert.match(html, /function roomPath\(p\)\{ return "roomsV2\/"/);
  assert.match(html, /for\(let i=0;i<6;i\+\+\)/);
  assert.match(html, /maxlength="6"/);
  assert.doesNotMatch(html, /_db\.ref\("rooms\//);
});

test("通常購読と観戦購読はprivateRecipes全体を含めない", () => {
  const playerSub = html.slice(html.indexOf("function subscribeRoom()"), html.indexOf("// 生存フラグ"));
  const spectatorSub = html.slice(html.indexOf("function subscribeSpectatorRoom()"), html.indexOf("function resumeSpectatorRoom("));
  assert.doesNotMatch(playerSub, /roomPath\("privateRecipes/);
  assert.doesNotMatch(spectatorSub, /roomPath\("privateRecipes/);
  assert.match(playerSub, /roomPath\("reveal"\)/);
  assert.match(spectatorSub, /roomPath\("reveal"\)/);
});

test("オンライン採点公開値にtotalsやfiredを保存しない", () => {
  const resolve = html.slice(html.indexOf("async function resolveScore()"), html.indexOf("function onGameUpdate()"));
  assert.match(resolve, /cur\.roundEval=\{roundScore:rs,omni,rational:rat,pct,cutPct\}/);
  assert.doesNotMatch(resolve, /cur\.roundEval=\{[^}]*totals/);
  assert.doesNotMatch(resolve, /cur\.history\.push\(\{[^;]*fired/);
});

test("採点と終了公開はホスト固定で再接続待ちを表示する", () => {
  assert.match(html, /return !!\(myUid&&ST\.meta&&ST\.meta\.host===myUid\)/);
  assert.match(html, /進行役の再接続を待っています/);
  assert.match(html, /game\.phase==="end"&&amResolver\(\)/);
});

test("Emulator接続はlocalhost系かつ明示指定に限定され、初期操作より前に行われる", () => {
  const init = html.slice(html.indexOf("const _emulatorMode"), html.indexOf("async function withRetry"));
  assert.match(init, /get\("emulator"\)==="1"/);
  assert.match(init, /location\.hostname==="localhost" \|\| location\.hostname==="127\.0\.0\.1"/);
  assert.ok(init.indexOf("_auth.useEmulator") < init.indexOf("_auth.setPersistence"));
  assert.ok(init.indexOf("_db.useEmulator") < html.indexOf('_db.ref(".info/serverTimeOffset")'));
  assert.match(html, /if\(emulatorBadge\)emulatorBadge\.hidden=!_emulatorMode/);
  assert.match(html, /<div id="emulatorBadge"[^>]*hidden>LOCAL EMULATOR<\/div>/);
  const projectId = html.match(/projectId:\s*"([^"]+)"/)[1];
  assert.match(packageJson.scripts["emulators:witch"], new RegExp(`--project ${projectId}$`));
});

test("匿名認証はLOCAL永続化と初期状態確定後、必要な場合だけ実行する", () => {
  const auth = html.slice(html.indexOf("async function waitForAuthSettled"), html.indexOf("function genCode"));
  const ensure = auth.slice(auth.indexOf("async function ensureAuth"));
  assert.match(html, /setPersistence\(firebase\.auth\.Auth\.Persistence\.LOCAL\)/);
  assert.ok(auth.indexOf("await _authPersistenceReady") < auth.indexOf("_auth.onAuthStateChanged"));
  assert.ok(ensure.indexOf("await waitForAuthSettled()") < ensure.indexOf("if(_auth.currentUser)"));
  assert.ok(ensure.indexOf("if(_auth.currentUser)") < ensure.indexOf("_auth.signInAnonymously()"));
});

test("Firebaseの即時renderより前に描画状態を初期化する", () => {
  const connectedListener = html.indexOf('_db.ref(".info/connected").on');
  for (const declaration of ["let SOLO=", 'let _inJoin=""', "let localCuts=[]"]) {
    assert.ok(html.indexOf(declaration) >= 0, `${declaration} が見つからない`);
    assert.ok(html.indexOf(declaration) < connectedListener, `${declaration} は接続コールバックより前に必要`);
  }
});

test("開始済みルームは同一UIDの席だけを再利用し、新規UIDを拒否する", () => {
  const join = html.slice(html.indexOf("async function joinRoom()"), html.indexOf("async function claimEmptySeat()"));
  assert.match(join, /const existingSeat=findSeatByUid\(state\.seats,myUid\)/);
  assert.ok(join.indexOf("existingSeat!==null") < join.indexOf('state.meta.phase!=="lobby"'));
  assert.match(join, /resumePlayerRoom\(code,state,existingSeat\); return/);
  assert.match(join, /新しいUIDでは途中参加できません/);
  const resume = html.slice(html.indexOf("function resumePlayerRoom("), html.indexOf("function subscribeSpectatorRoom()"));
  assert.match(resume, /ST\.mySeat=seat/);
  assert.match(resume, /ST\.isHost=state\.meta\.host===myUid/);
  assert.match(resume, /subscribeRoom\(\)/);
  assert.doesNotMatch(resume, /claimEmptySeat|claimSeat/);
});

test("保存済みルーム復帰はauth.uidを席から照合し、一時エラーでは保存を消さない", () => {
  const finder = html.slice(html.indexOf("function findSeatByUid("), html.indexOf("async function readRoomState("));
  assert.match(finder, /data\.uid===uid/);
  const restore = html.slice(html.indexOf("async function restoreSavedRoom()"), html.indexOf("async function createRoom()"));
  assert.match(restore, /const seat=findSeatByUid\(state\.seats,myUid\)/);
  assert.match(restore, /if\(seat===null\)\{ forgetRoom\(\)/);
  const catchBody = restore.slice(restore.indexOf("}catch(e){"));
  assert.doesNotMatch(catchBody, /forgetRoom\(\)/);
  const remembered = html.slice(html.indexOf("function rememberRoom("), html.indexOf("function forgetRoom("));
  assert.doesNotMatch(remembered, /uid/i);
});

test("ホストのawait_score復帰は既存transactionのphaseガードで一度だけ採点する", () => {
  const resume = html.slice(html.indexOf("function resumeAwaitingHostScore()"), html.indexOf("async function publishReveal()"));
  assert.match(resume, /phase==="await_score"&&amResolver\(\)/);
  assert.match(resume, /setTimeout\(resolveScore,120\)/);
  const resolve = html.slice(html.indexOf("async function resolveScore()"), html.indexOf("function onGameUpdate()"));
  assert.match(resolve, /transaction\(cur=>/);
  assert.match(resolve, /cur\.phase!=="await_score"\|\|cur\.round!==g\.round/);
});

test("観戦復帰は共有情報だけを再購読しprivateRecipesを購読しない", () => {
  const spectator = html.slice(html.indexOf("function subscribeSpectatorRoom()"), html.indexOf("async function restoreSavedRoom()"));
  assert.match(spectator, /roomPath\("meta"\)/);
  assert.match(spectator, /roomPath\("game"\)/);
  assert.match(spectator, /roomPath\("reveal"\)/);
  assert.doesNotMatch(spectator, /privateRecipes/);
  assert.match(spectator, /ST\.myRecipes=null/);
});
