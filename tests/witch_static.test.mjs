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

test("開始済みルームは同一UIDの席だけを再利用し、新規UIDは引き継ぎ申請フローへ入る", () => {
  const join = html.slice(html.indexOf("async function joinRoom()"), html.indexOf("async function claimEmptySeat()"));
  assert.match(join, /const existingSeat=findSeatByUid\(state\.seats,myUid\)/);
  assert.ok(join.indexOf("existingSeat!==null") < join.indexOf('state.meta.phase!=="lobby"'));
  assert.match(join, /resumePlayerRoom\(code,state,existingSeat\); return/);
  assert.match(join, /enterTakeoverFlow\(code,state\); return/);
  const resume = html.slice(html.indexOf("function resumePlayerRoom("), html.indexOf("function subscribeSpectatorRoom()"));
  assert.match(resume, /ST\.mySeat=seat/);
  assert.match(resume, /ST\.isHost=state\.meta\.host===myUid/);
  assert.match(resume, /subscribeRoom\(\)/);
  assert.doesNotMatch(resume, /claimEmptySeat|claimSeat/);
});

test("保存済みルーム復帰はauth.uidを席から照合し、一時エラーでは保存を消さない", () => {
  const finder = html.slice(html.indexOf("function findSeatByUid("), html.indexOf("async function readRoomState("));
  assert.match(finder, /data\.uid===uid/);
  const restore = html.slice(html.indexOf("async function restoreSavedRoom()"), html.indexOf("// ============ 席の引き継ぎ"));
  assert.match(restore, /const seat=findSeatByUid\(state\.seats,myUid\)/);
  assert.match(restore, /if\(seat===null\)\{/);
  assert.match(restore, /forgetRoom\(\);/);
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

test("P0-1: 保存済み部屋が見つからない場合も無言で終わらずshowErrorする", () => {
  const restore = html.slice(html.indexOf("async function restoreSavedRoom()"), html.indexOf("async function createRoom()"));
  assert.match(restore, /if\(!state\.exists\)\{ forgetRoom\(\); showError\(/);
});

test("P1-1: render()はscrollLeft/scrollTopを共通機構で保存・復元する", () => {
  const render = html.slice(html.indexOf("function captureScrollPositions"), html.indexOf("function homeHTML()"));
  assert.match(render, /function captureScrollPositions\(container\)\{/);
  assert.match(render, /function restoreScrollPositions\(container,map\)\{/);
  assert.match(render, /const _scroll=captureScrollPositions\(app\)/);
  assert.match(render, /const setApp=html=>\{ app\.innerHTML=html; restoreScrollPositions\(app,_scroll\); \}/);
  // 素材列・工房の記録・切り方プレビューの全コンテナにdata-scroll-idが付与されている
  const benchCount = (html.match(/class="bench" data-scroll-id="bench"/g) || []).length;
  assert.equal(benchCount, 4); // spectator / solo / nsolo / game
  const cutpreviewCount = (html.match(/data-scroll-id="cutpreview"/g) || []).length;
  assert.equal(cutpreviewCount, 3); // solo / nsolo / game
  assert.match(html, /data-scroll-id="mini-r\$\{h\.round\}"/);
  assert.match(html, /data-scroll-id="highlight-\$\{seat\}"/);
});

test("P2-1: 部屋コードのコピーはclipboard APIとフォールバックの両方を備え、失敗時はエラーを表示する", () => {
  assert.match(html, /function copyRoomCode\(\)\{/);
  assert.match(html, /navigator\.clipboard&&navigator\.clipboard\.writeText/);
  assert.match(html, /function fallbackCopyText\(text,done,fail\)\{/);
  assert.match(html, /document\.execCommand\("copy"\)/);
  const copyFn = html.slice(html.indexOf("function copyRoomCode()"), html.indexOf("function fallbackCopyText("));
  assert.match(copyFn, /showError\(/);
  assert.match(html, /onclick="copyRoomCode\(\)"/);
});

test("P2-2: 好みは使い切りでなく毎儀式判定されることが明記されている", () => {
  assert.match(html, /使い切りではなく、毎儀式、受け取った坩堝の中身で毎回判定される/);
  assert.match(html, /好みは使い切りではありません/);
});

test("P2-3: 採点結果に素材が次の儀式へ持ち越されない旨の一文がある", () => {
  const hintCount = (html.match(/この儀式で受け取った素材はここで魔力に変換され/g) || []).length;
  assert.ok(hintCount >= 4); // spectator / solo / nsolo / game
});

test("P2-4: 好みの型の説明文はすべて自分の坩堝であることを明記する（同居型は同じ坩堝にで代替）", () => {
  const recipeText = html.slice(html.indexOf("function recipeText(r)"), html.indexOf("function scoreRecipe(r,pile)"));
  assert.match(recipeText, /自分の坩堝の中で \$\{m\(r\.a\)\} を2つ以上、または/); // cnt2
  assert.match(recipeText, /自分の坩堝の中で \$\{m\(r\.a\)\} をちょうど1つ/); // solo
  assert.match(recipeText, /自分の坩堝に \$\{m\(r\.a\)\} と \$\{m\(r\.b\)\} を両方入れない/); // abs2
  assert.match(recipeText, /自分の坩堝に \$\{m\(r\.a\)\} を入れない/); // abs
});

test("P2-5: 遊び方に独立した用語集セクションがある", () => {
  const help = html.slice(html.indexOf("function helpHTML()"), html.indexOf("async function spectate()"));
  assert.match(help, /<h3>📚 用語集<\/h3>/);
  for (const term of ["坩堝", "儀式", "魔力", "調合師", "好み", "冴え", "秘伝"]) {
    assert.ok(help.includes(term), `用語集に${term}が見つからない`);
  }
});

test("P2-6/P2-7: 工房の記録と終了画面に儀式ごとの冴え%が表示される", () => {
  assert.match(html, /合計 <b style="color:\$\{h\.roundScore<0\?'var\(--bad\)':'var\(--good\)'\}">\$\{h\.roundScore>0\?'\+':''\}\$\{h\.roundScore\}<\/b>\$\{h\.pct!=null\?` ・ 冴え <b style="color:var\(--candle\)">\$\{h\.pct\}%<\/b>`:''\}/);
  assert.match(html, /儀式ごと：\$\{g\.history\.map\(h=>`\$\{h\.round\}:\$\{h\.roundScore>0\?'\+':''\}\$\{h\.roundScore\}\$\{h\.pct!=null\?`（冴え\$\{h\.pct\}%）`:''\}`\)\.join\("　"\)\}/);
});

test("v1.7-A: 席の引き継ぎフローが一通り配線されている", () => {
  assert.match(html, /function enterTakeoverFlow\(code,state\)\{/);
  assert.match(html, /function takeoverHTML\(\)\{/);
  assert.match(html, /if\(TAKEOVER\.active\)\{ setApp\(takeoverHTML\(\)\); return; \}/);
  // 申請：自UIDの子ノードへ書く
  const submit = html.slice(html.indexOf("async function submitTakeoverRequest("), html.indexOf("function cancelTakeoverRequest()"));
  assert.match(submit, /takeoverRoomPath\("takeoverRequests\/"\+myUid\)\)\.set\(\{seat,name,ts:now\(\),status:"pending",prevUid\}\)/);
  // ホスト承認：transactionでのレースガード（別経路で変わっていないか・元プレイヤーが復帰していないか）
  const approve = html.slice(html.indexOf("async function approveTakeover("), html.indexOf("// ============ 放置部屋のTTL掃除"));
  assert.match(approve, /if\(cur\.uid!==oldUid\)return;/);
  assert.match(approve, /if\(cur\.online!==false\)return;/);
  assert.match(approve, /await finishTakeoverMigration\(uid,oldUid\);/);
  assert.match(approve, /if\(!ST\.isHost\)return;/);
  const migrate = html.slice(html.indexOf("async function finishTakeoverMigration("), html.indexOf("async function approveTakeover("));
  assert.match(migrate, /updates\["privateRecipes\/"\+newUid\]=recSnap\.val\(\)/);
  assert.match(migrate, /updates\["privateRecipes\/"\+oldUid\]=null/);
  assert.match(migrate, /updates\["takeoverRequests\/"\+newUid\]=null/);
  const reject = html.slice(html.indexOf("async function rejectTakeover("), html.indexOf("async function finishTakeoverMigration("));
  assert.match(reject, /if\(!ST\.isHost\)return;/);
  assert.match(reject, /\.set\("rejected"\)/);
});

test("v1.7.1-A: 承認処理は中断状態（席は差し替え済み・好み未移行）から回復できる", () => {
  const submit = html.slice(html.indexOf("async function submitTakeoverRequest("), html.indexOf("function cancelTakeoverRequest()"));
  assert.match(submit, /const prevUid=\(TAKEOVER\.seats&&TAKEOVER\.seats\[seat\]&&TAKEOVER\.seats\[seat\]\.uid\)\|\|""/);
  const approve = html.slice(html.indexOf("async function approveTakeover("), html.indexOf("// ============ 放置部屋のTTL掃除"));
  assert.match(approve, /if\(seatData&&seatData\.uid===uid\)\{/);
  assert.match(approve, /if\(!req\.prevUid\)\{ showError/);
  assert.match(approve, /await finishTakeoverMigration\(uid,req\.prevUid\);/);
  assert.match(approve, /const oldUid=req\.prevUid\|\|\(seatData&&seatData\.uid\);/);
  const migrate = html.slice(html.indexOf("async function finishTakeoverMigration("), html.indexOf("async function approveTakeover("));
  assert.match(migrate, /if\(recSnap\.exists\(\)\)\{/); // 既に移行済みなら好み側は触らない＝冪等
});

test("v1.7-A: 旧UIDが引き継ぎ後に再接続した場合、実態に合わせた文言を表示する", () => {
  const restore = html.slice(html.indexOf("async function restoreSavedRoom()"), html.indexOf("// ============ 席の引き継ぎ"));
  assert.match(restore, /別の参加者に引き継がれました/);
  assert.match(restore, /saved\.seat!=null && state\.seats && state\.seats\[saved\.seat\] && state\.seats\[saved\.seat\]\.uid && state\.seats\[saved\.seat\]\.uid!==myUid/);
});

test("v1.7-A: ホストのみtakeoverRequestsを購読し、非rejectedの申請だけを承認/却下UIに出す", () => {
  const subscribe = html.slice(html.indexOf("function subscribeRoom()"), html.indexOf("// 生存フラグ"));
  assert.match(subscribe, /if\(ST\.isHost\)\{/);
  assert.match(subscribe, /roomPath\("takeoverRequests"\)/);
  const panel = html.slice(html.indexOf("function hostTakeoverPanel()"), html.indexOf("async function rejectTakeover("));
  assert.match(panel, /if\(!ST\.isHost\|\|!ST\.takeoverRequests\)return "";/);
  assert.match(panel, /req\.status!=="rejected"/);
});

test("v1.7-B: アプリ内ブラウザを保守的に検出し、ホーム/ロビーに警告を出す", () => {
  assert.match(html, /function isInAppBrowser\(\)\{/);
  assert.match(html, /\\bLine\\\/\|Instagram\|FBAN\|FBAV\|Twitter/);
  const home = html.slice(html.indexOf("function homeHTML()"), html.indexOf("function copyRoomCode()"));
  assert.match(home, /inAppBrowserWarningHTML\(\)/);
  const lobby = html.slice(html.indexOf("function lobbyHTML()"), html.indexOf("function gameHTML()"));
  assert.match(lobby, /inAppBrowserWarningHTML\(\)/);
});

test("v1.7-C: 好みプール一覧が通常18枚＋秘伝6種すべてを型ごとに掲載する", () => {
  assert.match(html, /function poolListHTML\(\)\{/);
  assert.match(html, /\{type:"pair",label:"🤝 同居"\},\{type:"cnt2",label:"🔢 個数"\},\{type:"solo",label:"1️⃣ 単体"\}/);
  assert.match(html, /\{type:"abs2",label:"⛔ 二種忌避"\},\{type:"abs",label:"🚫 忌避"\}/);
  assert.match(html, /bookList\(HARD,null\)/);
  const help = html.slice(html.indexOf("function helpHTML()"), html.indexOf("async function spectate()"));
  assert.match(help, /<h3>📜 好みの一覧<\/h3>/);
  assert.match(help, /\$\{poolListHTML\(\)\}/);
  assert.match(help, /<details>/);
});

test("v2.1-B: 称号ゲージはRANKSだけを唯一の基準として描画する（閾値のハードコード重複がない）", () => {
  assert.match(html, /const RANKS=\[/);
  assert.match(html, /function rankFor\(pct\)\{ return RANKS\.find\(r=>pct>=r\.min\); \}/);
  const gauge = html.slice(html.indexOf("function titleGaugeHTML("), html.indexOf("function endHTML()"));
  assert.match(gauge, /const bounds=RANKS\.slice\(\)\.reverse\(\)/);
  assert.match(gauge, /end-start/);
  assert.doesNotMatch(gauge, /9[028]|58|70/); // 閾値の数値がゲージ内に再ハードコードされていない
  const endFn = html.slice(html.indexOf("function endHTML()"), html.indexOf("// 定期再描画"));
  assert.match(endFn, /\$\{titleGaugeHTML\(keen\)\}/);
  const spectatorFn = html.slice(html.indexOf("function spectatorHTML()"), html.indexOf("function highlightPanel("));
  assert.match(spectatorFn, /\$\{titleGaugeHTML\(keen\)\}/);
});
