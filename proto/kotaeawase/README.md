# こたえあわせ（仮）ソース一式

身内限定・一回性レガシー・3人用クイズカードゲーム。GM=マッチ、プレイヤー=からあげ/てかさ/逆廻。

## ファイル
- `engine.js` … 純粋状態機械（createGame/reduce/judge*/balanceDeal/viewFor）。UI・通信から独立。
- `net.js` … オンライン通信層（MockDB＝RTDB模倣 / RoomHost=GM権威 / RoomClient / hydrateState）。DOM非依存。
- `ui_template.html` … UI本体。`<!-- __ENGINE__/__NET__/__DUMMY__/__REAL__ -->` を差し替えて単一HTML化。
- `dummy_data.js` … 検証用ダミーデッキ（window.DUMMY_DECKS）。
- `real_decks.js` … 本番デッキ（window.REAL_DECKS。評価xlsxから選定・rev2）。
- `cards_full.json` … 482枚の全評価抽出（base/mod表記保持）。デッキ再選定の元データ。
- `build.py` … 結合ビルド。公開先はリポジトリ直下 `kotaeawase.html`（ASCII固定名・版番号はファイル名に入れない）。
  `python3 build.py ../../kotaeawase.html`（`proto/kotaeawase/`から実行する場合）
- テスト: `test.js`(エンジン) / `test_view.js`(秘匿不変条件・§14-9故意注入の存在証明を含む) / `test_net.js`(結合) / `test_force.js`(未提出スキップ) / `test_resume_strip.js`(hostStateローカル化・空値ストリップ回帰・GM復帰) / `test_static.js`(ソーステキストの静的検証: alert/confirm撲滅・版ガード・ルール到達性・核漏洩防止等)
  - 実行: `node test.js` 等。ブラウザ検証は `smoke*.js`（Chromium executablePath要指定。未同梱）。

## ビルド & テスト
```
python3 build.py ../../kotaeawase.html
node test.js && node test_view.js && node test_net.js && node test_force.js && node test_resume_strip.js && node test_static.js
```
**「テストした成果物＝出荷する成果物」**を保つため、`build.py`はリポジトリ直下の`kotaeawase.html`へ直接出力する（`proto/kotaeawase/`配下に別名の複製は置かない）。公開URLは `https://muchkhk.github.io/original/kotaeawase.html?v=0.7` のように、`?v=`クエリでキャッシュ対策する運用（実装技術知見§4.5-1）。

## v0.7での変更（知見突合による修正一括・v4/v5指示書対応）
- `hostState`（答え全部）はRTDBに書かず、GM端末のlocalStorage相当のstoreにのみ保存する（`net.js`の`RoomHost`）。
- GM復帰導線（ロール選択画面から「GMとして部屋に復帰」）・presence再接続（`.info/connected`購読）・版不一致ガード（`meta.ver`）を追加。
- オンライン全画面からルール（❓ボタン）に到達可能。`alert()`/`confirm()`は全廃し、バナー通知と自前の確認モーダルに置換。
- 決着ラウンドの開示順を変更：札を選ぶ前に「マッチ（GM）基準であること」を開示（前半/後半は伏せたまま）。
- ルール説明に用語集タブを追加。「前半」「後半」の意味・「序盤」「終盤」等の語はルール説明・用語集のどこにも書かない（核は画面上でのみ開示）。
- ネタばらし画面限定で前半/後半の点数を併記（`viewFor`の秘匿範囲を明示的に1段拡張。`sanitizeResult`/`sanitizeDecision`の`entries`に`scoreZen`/`scoreKou`を追加）。
- 秘匿不変条件は場所ベースの構造チェック（知見§14-9の存在証明義務対応）。判定確定済みentries以外に点数らしき値が1つでもあれば、キー名や値に関わらず検出する。

## デッキ再選定
`cards_full.json` を入力に、割れ札(3人spread>=2.5)5〜6割＋前後半乖離札＋レベル分散で各セット24枚。
ストライク・防御は全キャラ除外。第2章はネクロバインダー（割れ札が最多）。
