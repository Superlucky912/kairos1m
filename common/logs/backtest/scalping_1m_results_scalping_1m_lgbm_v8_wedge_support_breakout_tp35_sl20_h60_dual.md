# 1분봉 단타 Blind 검증 결과 - scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60

- **실행 시각**: 2026-05-06 10:59:09 (KST)
- **TP/SL/Horizon**: 3.5% / 2.0% / 60분
- **Entry Rank Score Threshold**: 0.3000
- **Entry Score**: `tp_lift * TP - max(sl_lift, 0) * SL * 0.70 - max(timeout_lift, 0) * 0.0030`
- **Entry Rank Score**: `tp_lift * 3.0 + p_tp - max(sl_lift, 0) * 0.8 - max(timeout_lift, 0) * 0.25`
- **Probability Baseline**: `p_tp=0.2393`, `p_sl=0.4925`, `p_timeout=0.2335`
- **Hard Gate**: `tp_lift >= -0.01`, `sl_lift <= 0.05`, `timeout_lift <= 0.30`, `tp_lift - sl_lift >= 0.00`, `p_tp * TP - p_sl * SL >= 0.0005`, `pump_base_condition = False`, `p_timeout <= 0.35`, `pump_age <= 8.0h`, `volume_accel_5m >= -0.15`, `mtf15_ema_bull = False`, `block_mtf5_turn_up = False`, `block_mtf5_trend_continuation = False`, `p_tp > p_sl = False`
- **후보 압축**: timestamp별 scalping_rank_score 상위 15개

## 손절 제한 OFF/ON 비교
| 모드 | 진입 | TP | SL | Timeout | TP율 | 최종 잔고 | 수익률 | MDD |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| OFF | 6 | 1 | 1 | 4 | 16.67% | 118.13 | +18.13% | 5.11% |
| ON | 6 | 1 | 1 | 4 | 16.67% | 118.13 | +18.13% | 5.11% |

## 필터 통계
- **OFF**: {'timestamps': 2357, 'rows_total': 34399, 'rows_after_candidate_rank': 34324, 'rows_after_active_volatility': 21670, 'rows_after_probability_gate': 19, 'rows_after_threshold': 6, 'entries_opened': 6, 'cooldown_blocked': 0}
- **ON**: {'timestamps': 2357, 'rows_total': 34399, 'rows_after_candidate_rank': 34324, 'rows_after_active_volatility': 21670, 'rows_after_probability_gate': 19, 'rows_after_threshold': 6, 'entries_opened': 6, 'cooldown_blocked': 0}
- **DIAGNOSTIC 후보 저장**: 500건

## 최근 거래 50건
| entry_time_kst            | exit_time_kst             | symbol       | reason   |    pnl_roe |   entry_price |   exit_price |     p_tp |     p_sl |   p_timeout |      tp_lift |    sl_lift |   timeout_lift |   entry_score |   entry_rank_score |   rank_at_entry |   balance |   drawdown |
|:--------------------------|:--------------------------|:-------------|:---------|-----------:|--------------:|-------------:|---------:|---------:|------------:|-------------:|-----------:|---------------:|--------------:|-------------------:|----------------:|----------:|-----------:|
| 2026-05-03 21:40:00+09:00 | 2026-05-03 22:40:00+09:00 | BRUSDT       | TIMEOUT  |  0.0287049 |      0.18782  |     0.18918  | 0.261268 | 0.415304 |    0.192429 |  0.0219718   | -0.0771484 |     -0.0411042 |   0.000769012 |           0.914557 |               1 |   101.435 |   0        |
| 2026-05-04 08:35:00+09:00 | 2026-05-04 09:35:00+09:00 | REZUSDT      | TIMEOUT  |  0.0324767 |      0.005128 |     0.005169 | 0.254763 | 0.386379 |    0.317467 |  0.0154669   | -0.106073  |      0.0839339 |   0.000289539 |           0.782134 |               1 |   103.082 |   0        |
| 2026-05-06 04:20:00+09:00 | 2026-05-06 05:20:00+09:00 | GWEIUSDT     | TIMEOUT  |  0.117947  |      0.12595  |     0.12911  | 0.269568 | 0.445549 |    0.142096 |  0.0302718   | -0.0469026 |     -0.0914375 |   0.00105951  |           1.05249  |               1 |   109.161 |   0        |
| 2026-05-06 05:34:00+09:00 | 2026-05-06 06:22:00+09:00 | GWEIUSDT     | TP       |  0.1675    |      0.12814  |     0.132625 | 0.239149 | 0.369794 |    0.306848 | -0.000147469 | -0.122658  |      0.0733143 |  -0.000225104 |           1.23027  |               1 |   118.304 |   0        |
| 2026-05-06 07:11:00+09:00 | 2026-05-06 08:11:00+09:00 | 1000LUNCUSDT | TIMEOUT  |  0.104584  |      0.11197  |     0.11448  | 0.259427 | 0.418999 |    0.321865 |  0.0201308   | -0.0734526 |      0.0883317 |   0.000439583 |           0.736716 |               1 |   124.49  |   0        |
| 2026-05-06 08:05:00+09:00 | 2026-05-06 08:24:00+09:00 | GWEIUSDT     | SL       | -0.1075    |      0.12966  |     0.127067 | 0.248821 | 0.361001 |    0.28821  |  0.00952451  | -0.131451  |      0.054677  |   0.000169327 |           0.62294  |               1 |   118.131 |   0.051079 |