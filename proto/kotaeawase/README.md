# こたえあわせ（仮）ソース一式

身内限定・一回性レガシー・3人用クイズカードゲーム。GM=マッチ、プレイヤー=からあげ/てかさ/逆廻。

## ファイル
- `engine.js` … 純粋状態機械（createGame/reduce/judge*/balanceDeal/viewFor）。UI・通信から独立。
- `net.js` … オンライン通信層（MockDB＝RTDB模倣 / RoomHost=GM権威 / RoomClient / hydrateState）。DOM非依存。
- `ui_template.html` … UI本体。`<!-- __ENGINE__/__NET__/__DUMMY__/__REAL__ -->` を差し替えて単一HTML化。
- `dummy_data.js` … 検証用ダミーデッキ（window.DUMMY_DECKS）。
- `real_decks.js` … 本番デッキ（window.REAL_DECKS。評価xlsxから選定・rev2）。
- `cards_full.json` … 482枚の全評価抽出（base/mod表記保持）。デッキ再選定の元データ。
- `build.py` … 結合ビルド。`python3 build.py こたえあわせ_v0.7.html`
- テスト: `test.js`(エンジン) / `test_view.js`(秘匿不変条件) / `test_net.js`(結合) / `test_force.js`(未提出スキップ) / `test_resume_strip.js`(hostStateローカル化・空値ストリップ回帰・GM復帰) / `test_static.js`(ソーステキストの静的検証: alert/confirm撲滅・版ガード・ルール到達性等)
  - 実行: `node test.js` 等。ブラウザ検証は `smoke*.js`（Chromium executablePath要指定）。

## ビルド & テスト
```
python3 build.py こたえあわせ_v0.7.html
node test.js && node test_view.js && node test_net.js && node test_force.js && node test_resume_strip.js && node test_static.js
```

## v0.7での変更（知見突合による修正一括）
- `hostState`（答え全部）はRTDBに書かず、GM端末のlocalStorage相当のstoreにのみ保存する（`net.js`の`RoomHost`）。
- GM復帰導線（ロール選択画面から「GMとして部屋に復帰」）・presence再接続（`.info/connected`購読）・版不一致ガード（`meta.ver`）を追加。
- オンライン全画面からルール（❓ボタン）に到達可能。`alert()`/`confirm()`は全廃し、バナー通知と自前の確認モーダルに置換。
- 決着ラウンドの開示順を変更：札を選ぶ前に「マッチ（GM）基準であること」を開示（前半/後半は伏せたまま）。
- **`RULES_HTML`（§3-1の前半/後半定義・§3-3の決着ラウンド節）は design chat への差し戻し中。** §7完了条件の「核漏洩防止（序盤/終盤/マッチ/全体評価をRULES_HTMLに含めない）」と、§3-1/§3-3で指定された一字一句指定文が直接矛盾しているため、文言確定まで保留。詳細は報告書を参照。

## デッキ再選定
`cards_full.json` を入力に、割れ札(3人spread>=2.5)5〜6割＋前後半乖離札＋レベル分散で各セット24枚。
ストライク・防御は全キャラ除外。第2章はネクロバインダー（割れ札が最多）。
