# 思考課金ポーカー v0-B(蘇生版) モンテカルロ検証 結果サマリ

- 実行日時(BASE_SEED基準): 20260715
- 総ペア数: 45 (9戦略の重複組合せ, 対角含む)
- ペアあたりゲーム数: 2000
- 総ゲーム数: 90000
- 実行時間: 54.1秒
- 無限ループガード発動回数: 0

## 1. 勝率マトリクス (行 vs 列, 引き分け0.5勝)

| strategy | S1_NeverPeek | S2_AlwaysPeek | S3_MarginalPeek | S4_BluffReveal | S5_HonestValue | S6_ShortStackPush | S7_SignalReader | S8_SignalBlind | S9_PotAwareBluff |
|---|---|---|---|---|---|---|---|---|---|
| S1_NeverPeek | 0.523 | 0.640 | 0.495 | 0.577 | 0.637 | 0.493 | 0.489 | 0.501 | 0.689 |
| S2_AlwaysPeek | 0.360 | 0.496 | 0.384 | 0.447 | 0.461 | 0.365 | 0.384 | 0.375 | 0.551 |
| S3_MarginalPeek | 0.505 | 0.617 | 0.500 | 0.576 | 0.615 | 0.470 | 0.510 | 0.487 | 0.705 |
| S4_BluffReveal | 0.423 | 0.553 | 0.424 | 0.486 | 0.533 | 0.415 | 0.410 | 0.417 | 0.605 |
| S5_HonestValue | 0.363 | 0.539 | 0.385 | 0.467 | 0.511 | 0.363 | 0.381 | 0.344 | 0.638 |
| S6_ShortStackPush | 0.507 | 0.635 | 0.530 | 0.585 | 0.637 | 0.487 | 0.510 | 0.527 | 0.716 |
| S7_SignalReader | 0.510 | 0.616 | 0.490 | 0.590 | 0.619 | 0.490 | 0.504 | 0.484 | 0.707 |
| S8_SignalBlind | 0.499 | 0.625 | 0.513 | 0.583 | 0.656 | 0.473 | 0.516 | 0.506 | 0.715 |
| S9_PotAwareBluff | 0.311 | 0.449 | 0.295 | 0.395 | 0.362 | 0.284 | 0.293 | 0.285 | 0.513 |

## 2. 対フィールド平均勝率

| strategy | field_avg_winrate |
|---|---|
| S6_ShortStackPush | 0.5701 |
| S8_SignalBlind | 0.5651 |
| S1_NeverPeek | 0.5605 |
| S7_SignalReader | 0.5566 |
| S3_MarginalPeek | 0.5538 |
| S4_BluffReveal | 0.4743 |
| S5_HonestValue | 0.4435 |
| S2_AlwaysPeek | 0.4247 |
| S9_PotAwareBluff | 0.3542 |

## 3. 覗き発動率 x ポットサイズ帯 (n=覗き実行総数)

| strategy | n | 2-4 | 5-8 | 9-14 | 15+ |
|---|---|---|---|---|---|
| S1_NeverPeek | 0 | 0.000 | 0.000 | 0.000 | 0.000 |
| S2_AlwaysPeek | 135633 | 0.215 | 0.529 | 0.683 | 0.997 |
| S3_MarginalPeek | 13404 | 0.000 | 0.000 | 0.066 | 0.118 |
| S4_BluffReveal | 0 | 0.000 | 0.000 | 0.000 | 0.000 |
| S5_HonestValue | 0 | 0.000 | 0.000 | 0.000 | 0.000 |
| S6_ShortStackPush | 11240 | 0.000 | 0.000 | 0.057 | 0.097 |
| S7_SignalReader | 12965 | 0.000 | 0.000 | 0.066 | 0.112 |
| S8_SignalBlind | 13071 | 0.000 | 0.000 | 0.065 | 0.114 |
| S9_PotAwareBluff | 0 | 0.000 | 0.000 | 0.000 | 0.000 |

## 4. 覗きの判断転換率 (S2/S3/S7, 参考としてS8も併記)

| strategy | flip_count | peek_count | flip_rate |
|---|---|---|---|
| S2_AlwaysPeek | 29855 | 135633 | 0.2201 |
| S3_MarginalPeek | 6616 | 13404 | 0.4936 |
| S7_SignalReader | 6313 | 12965 | 0.4869 |
| S8_SignalBlind | 6482 | 13071 | 0.4959 |

## 5. スパイラル指標 (2:1劣勢からの逆転率)

- 全体平均: 0.2505 (n=90000)

戦略別 (劣勢側だった場合の最終勝率):

| strategy | eventual_winrate | n_events |
|---|---|---|
| S1_NeverPeek | 0.2885 | 9368 |
| S2_AlwaysPeek | 0.1812 | 10715 |
| S3_MarginalPeek | 0.2941 | 9425 |
| S4_BluffReveal | 0.2405 | 10462 |
| S5_HonestValue | 0.2333 | 11255 |
| S6_ShortStackPush | 0.3142 | 9229 |
| S7_SignalReader | 0.2861 | 9300 |
| S8_SignalBlind | 0.2965 | 9396 |
| S9_PotAwareBluff | 0.1508 | 10850 |

(ペア別の詳細は spiral_by_pair.csv を参照)

## 6. ゲーム長分布

- 全体平均ラウンド数: 12.79
- 最短: 2, 最長: 30
- 30ラウンド到達(タイムアウト)率: 0.0163

(ヒストグラム全体は game_length_histogram.csv, ペア別平均は game_length_by_pair.csv を参照)

## 7. オールイン統計

| strategy | allin_count | decision_count | allin_rate | allin_round_winrate | avg_pool_share_at_allin |
|---|---|---|---|---|---|
| S1_NeverPeek | 28184 | 391748 | 0.0719 | 0.5693 | 0.0590 |
| S2_AlwaysPeek | 30872 | 315722 | 0.0978 | 0.6110 | 0.0552 |
| S3_MarginalPeek | 27539 | 391255 | 0.0704 | 0.5929 | 0.0578 |
| S4_BluffReveal | 29939 | 373479 | 0.0802 | 0.5138 | 0.0555 |
| S5_HonestValue | 29432 | 405494 | 0.0726 | 0.5090 | 0.0596 |
| S6_ShortStackPush | 32609 | 388406 | 0.0840 | 0.6153 | 0.0718 |
| S7_SignalReader | 27018 | 389979 | 0.0693 | 0.5914 | 0.0573 |
| S8_SignalBlind | 27597 | 390767 | 0.0706 | 0.5947 | 0.0576 |
| S9_PotAwareBluff | 29866 | 354150 | 0.0843 | 0.4097 | 0.0567 |

## 8. 強制手率 (ラウンド帯別。合法な選択肢が実質{オールイン,フォールド}のみの決断の割合)

| round_band | forced_count | decision_count | forced_rate |
|---|---|---|---|
| R1-4 | 4591 | 1136672 | 0.0040 |
| R5-8 | 28353 | 1062941 | 0.0267 |
| R9-12 | 65822 | 664560 | 0.0990 |
| R13+ | 73649 | 536827 | 0.1372 |

## 9. 帯別ショーダウンポット分布

| round_band | n_showdowns | mean_pot | median_pot | max_pot |
|---|---|---|---|---|
| R1-4 | 224769 | 10.18 | 6.00 | 32 |
| R5-8 | 228639 | 11.70 | 8.00 | 34 |
| R9-12 | 162208 | 16.57 | 14.00 | 50 |
| R13+ | 148221 | 18.93 | 12.00 | 50 |

## 10. ゲーム決着様式の内訳

| mode | count | rate |
|---|---|---|
| bust | 88794 | 0.9866 |
| timeout_win | 1176 | 0.0131 |
| timeout_draw | 30 | 0.0003 |

(全90000ゲーム中の内訳。bust=プール0到達, timeout_win/draw=R30到達)
