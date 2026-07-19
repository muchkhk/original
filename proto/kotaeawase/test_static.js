/* 静的テスト: ui_template.html / net.js のソーステキストに対する不変条件を検証する。
 * ランタイム挙動ではなく「書かれているべきこと／書かれていてはいけないこと」の検査。
 * §3-1/§3-3（RULES_HTMLへの一字一句指定文の挿入）は design chat への差し戻し中のため、
 * ここでは §7 完了条件のうち §3 の一字一句一致チェックは含まない（文言確定後に追加する）。 */
'use strict';
const fs = require('fs');
const path = require('path');

let passed = 0, failed = 0;
function ok(c, m) { if (c) passed++; else { failed++; console.error('  FAIL:', m); } }

const uiSrc = fs.readFileSync(path.join(__dirname, 'ui_template.html'), 'utf-8');
const netSrc = fs.readFileSync(path.join(__dirname, 'net.js'), 'utf-8');

console.log('alert()/confirm()の撲滅（§5-5）:');
{
  const alertCount = (uiSrc.match(/\balert\(/g) || []).length;
  const confirmCount = (uiSrc.match(/\bconfirm\(/g) || []).length;
  ok(alertCount === 0, `alert( が0件であること (実:${alertCount})`);
  ok(confirmCount === 0, `confirm( が0件であること (実:${confirmCount})`);
}

console.log('版不一致ガード（§2-4）:');
{
  const verDefCount = (uiSrc.match(/const VER\s*=/g) || []).length;
  ok(verDefCount === 1, `VER の定義が1箇所だけであること (実:${verDefCount})`);
  ok(/ver:\s*VER/.test(uiSrc), 'createRoom() 呼び出しで meta.ver に VER を書いていること');
  ok(/ver:\s*ver\s*\|\|\s*null/.test(netSrc), 'net.js の createRoom() が meta.ver を保存すること');
  ok(/verNum\(/.test(uiSrc), '版比較ロジック(verNum)が存在すること');
  ok(/meta\.ver/.test(uiSrc), 'meta.ver を参照する比較分岐が存在すること');
  ok(/再読み込みしてください/.test(uiSrc), '「部屋の方が新しい」ケースのバナー文言が存在すること');
  ok(/部屋を作り直してください/.test(uiSrc), '「部屋の方が古い」ケースのバナー文言が存在すること');
}

console.log('全画面からルールに到達できること（§2-5）:');
{
  ok(/id="rulesbtn"/.test(uiSrc), 'ヘッダーに❓ボタン(#rulesbtn)が存在すること');
  const showRulesCallCount = (uiSrc.match(/UI\.showRules\(\)/g) || []).length;
  // ホットシート/ソロ用ヘッダー(frame())とオンライン用ヘッダー(Online.render())の双方に必要 → 最低2箇所
  ok(showRulesCallCount >= 2, `UI.showRules() の呼び出しがホットシート/オンライン双方のヘッダーに存在すること (実:${showRulesCallCount})`);
  ok(/function frame\(body\)[\s\S]{0,300}rulesbtn/.test(uiSrc), 'ホットシート/ソロのヘッダー(frame())に❓ボタンがあること');
  const onlineHeaderIdx = uiSrc.indexOf('  render() {\n    const app = document.getElementById');
  ok(onlineHeaderIdx >= 0 && uiSrc.slice(onlineHeaderIdx, onlineHeaderIdx + 1200).includes('rulesbtn'), 'オンラインのヘッダー(Online.render())に❓ボタンがあること');
}

console.log('決着ラウンド画面文言（§4・D-1）:');
{
  const hotseatIdx = uiSrc.indexOf('決着ラウンド — ${esc(p)} の選択');
  const hotseatWin = hotseatIdx >= 0 ? uiSrc.slice(hotseatIdx, hotseatIdx + 400) : '';
  ok(/マッチ/.test(hotseatWin) && /前半/.test(hotseatWin) && /後半/.test(hotseatWin),
    'ホットシート版 decisionPick 画面文言に「マッチ」「前半」「後半」を含むこと');

  const onlineIdx = uiSrc.indexOf('決着ラウンド — 最後の1枚を選ぶ');
  const onlineWin = onlineIdx >= 0 ? uiSrc.slice(onlineIdx, onlineIdx + 400) : '';
  ok(/マッチ/.test(onlineWin) && /前半/.test(onlineWin) && /後半/.test(onlineWin),
    'オンライン版 decisionPick 画面文言に「マッチ」「前半」「後半」を含むこと');
}

console.log('ルール説明の核漏洩防止（§7・RULES_HTML）:');
{
  const rulesMatch = /const RULES_HTML = `([\s\S]*?)`;/.exec(uiSrc);
  ok(!!rulesMatch, 'RULES_HTML 定数を検出できること');
  const rulesBody = rulesMatch ? rulesMatch[1] : '';
  for (const bad of ['序盤', '終盤', 'マッチ', '全体評価']) {
    ok(!rulesBody.includes(bad), `RULES_HTML に「${bad}」が含まれないこと`);
  }
  // 注: §3-1(前半/後半の定義・一字一句指定)・§3-3(決着ラウンド節)は
  // design chatへの差し戻し中のため、ここでは追加していない（文言確定後に別途検証を追加）。
}

console.log('hostState の DB書き込み撤去（§2-2）:');
{
  ok(!/updates\['hostState'\]/.test(netSrc) && !/updates\.hostState\s*=/.test(netSrc),
    'net.js に hostState への DB書き込みが存在しないこと');
  ok(/self\.store\.set\(storeKey/.test(netSrc), 'net.js が hostState をローカルstoreに保存していること');
  ok(!netSrc.includes('${base}/hostState'), 'loadOrCreate() がDBのhostStateパスを参照しないこと（storeベースに置換済み）');
}

console.log(`\n==== static結果: ${passed} passed, ${failed} failed ====`);
process.exit(failed ? 1 : 0);
