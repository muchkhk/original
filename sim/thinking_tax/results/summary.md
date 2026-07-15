# 思考課金ポーカー v0-A モンテカルロ検証 結果サマリ

- 実行日時(BASE_SEED基準): 20260715
- 総ペア数: 36 (8戦略の重複組合せ, 対角含む)
- ペアあたりゲーム数: 2000
- 総ゲーム数: 72000
- 実行時間: 49.3秒
- 無限ループガード発動回数: 0

## 1. 勝率マトリクス (行 vs 列, 引き分け0.5勝)

| strategy | S1_NeverPeek | S2_AlwaysPeek | S3_MarginalPeek | S4_BluffReveal | S5_HonestValue | S6_ShortStackPush | S7_SignalReader | S8_SignalBlind |
|---|---|---|---|---|---|---|---|---|
| S1_NeverPeek | 0.506 | 0.694 | 0.512 | 0.590 | 0.753 | 0.502 | 0.500 | 0.504 |
| S2_AlwaysPeek | 0.306 | 0.496 | 0.305 | 0.411 | 0.400 | 0.287 | 0.312 | 0.315 |
| S3_MarginalPeek | 0.488 | 0.695 | 0.492 | 0.589 | 0.723 | 0.477 | 0.504 | 0.494 |
| S4_BluffReveal | 0.410 | 0.589 | 0.411 | 0.496 | 0.597 | 0.397 | 0.398 | 0.396 |
| S5_HonestValue | 0.247 | 0.601 | 0.277 | 0.403 | 0.506 | 0.262 | 0.274 | 0.268 |
| S6_ShortStackPush | 0.498 | 0.713 | 0.523 | 0.603 | 0.738 | 0.504 | 0.513 | 0.535 |
| S7_SignalReader | 0.500 | 0.688 | 0.496 | 0.602 | 0.726 | 0.487 | 0.496 | 0.511 |
| S8_SignalBlind | 0.496 | 0.685 | 0.506 | 0.604 | 0.732 | 0.465 | 0.489 | 0.497 |

## 2. 対フィールド平均勝率

| strategy | field_avg_winrate |
|---|---|
| S6_ShortStackPush | 0.5784 |
| S1_NeverPeek | 0.5701 |
| S7_SignalReader | 0.5631 |
| S8_SignalBlind | 0.5593 |
| S3_MarginalPeek | 0.5576 |
| S4_BluffReveal | 0.4617 |
| S5_HonestValue | 0.3547 |
| S2_AlwaysPeek | 0.3541 |

## 3. 覗き発動率 x ポットサイズ帯

| strategy | 2-4 | 5-8 | 9-14 | 15+ |
|---|---|---|---|---|
| S1_NeverPeek | 0.000 | 0.000 | 0.000 | 0.000 |
| S2_AlwaysPeek | 0.334 | 1.000 | 1.000 | 0.995 |
| S3_MarginalPeek | 0.000 | 0.007 | 0.007 | 0.005 |
| S4_BluffReveal | 0.000 | 0.000 | 0.000 | 0.000 |
| S5_HonestValue | 0.000 | 0.000 | 0.000 | 0.000 |
| S6_ShortStackPush | 0.000 | 0.016 | 0.018 | 0.013 |
| S7_SignalReader | 0.000 | 0.007 | 0.007 | 0.006 |
| S8_SignalBlind | 0.000 | 0.006 | 0.007 | 0.006 |

## 4. 覗きの判断転換率 (S2/S3/S7, 参考としてS8も併記)

| strategy | flip_count | peek_count | flip_rate |
|---|---|---|---|
| S2_AlwaysPeek | 31119 | 132141 | 0.2355 |
| S3_MarginalPeek | 353 | 720 | 0.4903 |
| S7_SignalReader | 390 | 780 | 0.5000 |
| S8_SignalBlind | 339 | 727 | 0.4663 |

## 5. スパイラル指標 (2:1劣勢からの逆転率)

- 全体平均: 0.2185 (n=71907)

戦略別 (劣勢側だった場合の最終勝率):

| strategy | eventual_winrate | n_events |
|---|---|---|
| S1_NeverPeek | 0.2727 | 8400 |
| S2_AlwaysPeek | 0.0974 | 9983 |
| S3_MarginalPeek | 0.2562 | 8439 |
| S4_BluffReveal | 0.2170 | 9535 |
| S5_HonestValue | 0.1459 | 10451 |
| S6_ShortStackPush | 0.2777 | 8246 |
| S7_SignalReader | 0.2643 | 8436 |
| S8_SignalBlind | 0.2581 | 8417 |

(ペア別の詳細は spiral_by_pair.csv を参照)

## 6. ゲーム長分布

- 全体平均ラウンド数: 16.26
- 最短: 2, 最長: 30
- 30ラウンド到達(タイムアウト)率: 0.1779

(ヒストグラム全体は game_length_histogram.csv, ペア別平均は game_length_by_pair.csv を参照)

## 7. オールイン統計

| strategy | allin_count | decision_count | allin_rate | allin_round_winrate | avg_pool_share_at_allin |
|---|---|---|---|---|---|
| S1_NeverPeek | 22619 | 463930 | 0.0488 | 0.5426 | 0.0422 |
| S2_AlwaysPeek | 33692 | 311024 | 0.1083 | 0.6100 | 0.0546 |
| S3_MarginalPeek | 22777 | 463577 | 0.0491 | 0.5332 | 0.0421 |
| S4_BluffReveal | 26579 | 433534 | 0.0613 | 0.5062 | 0.0418 |
| S5_HonestValue | 23977 | 514354 | 0.0466 | 0.4458 | 0.0425 |
| S6_ShortStackPush | 37831 | 435885 | 0.0868 | 0.5852 | 0.0969 |
| S7_SignalReader | 23011 | 462965 | 0.0497 | 0.5384 | 0.0417 |
| S8_SignalBlind | 23186 | 462057 | 0.0502 | 0.5330 | 0.0418 |
