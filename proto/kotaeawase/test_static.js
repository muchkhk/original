/* 静的テスト: ui_template.html / net.js のソーステキストに対する不変条件を検証する。
 * ランタイム挙動ではなく「書かれているべきこと／書かれていてはいけないこと」の検査。
 * v4指示書（§3-1/§3-3/§5-3の文言確定・§3-4新規追加）に対応。 */
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

console.log('ルール説明の核漏洩防止（§3-1/§3-3・RULES_HTML/GLOSSARY_HTML）:');
{
  const rulesMatch = /const RULES_HTML = `([\s\S]*?)`;/.exec(uiSrc);
  ok(!!rulesMatch, 'RULES_HTML 定数を検出できること');
  const rulesBody = rulesMatch ? rulesMatch[1] : '';
  const glossMatch = /const GLOSSARY_HTML = `([\s\S]*?)`;/.exec(uiSrc);
  ok(!!glossMatch, 'GLOSSARY_HTML 定数を検出できること');
  const glossBody = glossMatch ? glossMatch[1] : '';
  for (const bad of ['序盤', '終盤', 'スレイ・ザ・スパイア']) {
    ok(!rulesBody.includes(bad), `RULES_HTML に「${bad}」が含まれないこと`);
    ok(!glossBody.includes(bad), `GLOSSARY_HTML に「${bad}」が含まれないこと`);
  }
  // 「マッチ」はRULES_HTMLのみ禁止（§3-3: 決着の判定者はルール説明で明かさない。
  // 用語集・画面文言では既存どおり許容範囲＝GLOSSARY_HTMLには元々登場していない）
  ok(!rulesBody.includes('マッチ'), 'RULES_HTML に「マッチ」が含まれないこと');
  ok(!glossBody.includes('マッチ'), 'GLOSSARY_HTML に「マッチ」が含まれないこと');

  // §3-1: 一字一句指定文がRULES_HTMLに完全一致で含まれること
  const sec31Exact = '「前半」「後半」とは？\n札の強さは、2種類記録されている。GMがラウンドごとに「前半で判定」「後半で判定」のどちらかを宣言し、宣言された側の強さで勝負する。同じ札でも、前半と後半で強さが違うことがある。\nこの2つが何を指しているのかは、あえて伏せてある。\n遊びながら気づいてほしい。';
  ok(rulesBody.includes(sec31Exact), 'RULES_HTML に §3-1 の定義文が一字一句含まれること');
}

console.log('ネタばらし画面の前半/後半併記（§3-4）:');
{
  ok(/function halfPairHtml\(/.test(uiSrc), 'halfPairHtml() が定義されていること');
  ok(/scoreZen/.test(uiSrc) && /scoreKou/.test(uiSrc), 'halfPairHtml() が scoreZen/scoreKou を参照すること');
  const hotseatTL = /showTimeline\(\) \{[\s\S]*?\n  \},/.exec(uiSrc);
  const onlineTL = /showTimelineV\(\) \{[\s\S]*?\n  \},/.exec(uiSrc);
  ok(!!hotseatTL && /halfPairHtml\(/.test(hotseatTL[0]), 'ホットシート showTimeline() が halfPairHtml()（scoreZen/scoreKou併記）を呼ぶこと');
  ok(!!onlineTL && /halfPairHtml\(/.test(onlineTL[0]), 'オンライン showTimelineV() が halfPairHtml()（scoreZen/scoreKou併記）を呼ぶこと');

  // 非対象: roundEnd/finalのカードタイル生成・resultBoard・renderFinalV には現れないこと
  const noLeakTargets = [
    ['function renderRoundEnd\\(\\)[\\s\\S]*?\\n\\}', 'renderRoundEnd()'],
    ['function renderFinal\\(\\)[\\s\\S]*?\\n\\}', 'renderFinal()'],
    ['resultBoard\\(r\\)[\\s\\S]*?\\n  \\},', 'resultBoard()'],
    ['renderFinalV\\(v\\)[\\s\\S]*?\\n  \\},', 'renderFinalV()'],
  ];
  for (const [pat, label] of noLeakTargets) {
    const m = new RegExp(pat).exec(uiSrc);
    ok(!!m, `${label} を検出できること`);
    ok(!m || (!/scoreZen/.test(m[0]) && !/scoreKou/.test(m[0]) && !/halfPairHtml\(/.test(m[0])), `${label} に scoreZen/scoreKou/halfPairHtml() が現れないこと`);
  }
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
