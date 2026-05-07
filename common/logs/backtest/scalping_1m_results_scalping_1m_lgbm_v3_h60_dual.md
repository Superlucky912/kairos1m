# 1분봉 단타 Blind 검증 결과 - scalping_1m_lgbm_v3_h60

- **실행 시각**: 2026-05-01 04:42:06 (KST)
- **TP/SL/Horizon**: 5.0% / 3.0% / 60분
- **Entry Rank Score Threshold**: 0.3000
- **Entry Score**: `tp_lift * TP - max(sl_lift, 0) * SL * 0.70 - max(timeout_lift, 0) * 0.0030`
- **Entry Rank Score**: `tp_lift * 3.0 + p_tp - max(sl_lift, 0) * 0.8 - max(timeout_lift, 0) * 0.25`
- **Probability Baseline**: `p_tp=0.1874`, `p_sl=0.3709`, `p_timeout=0.5883`
- **Hard Gate**: `tp_lift >= -0.01`, `sl_lift <= 0.00`, `timeout_lift <= 0.30`, `tp_lift - sl_lift >= 0.00`, `p_tp * TP - p_sl * SL >= 0.0015`, `pump_base_condition = False`, `p_timeout <= 0.35`, `pump_age <= 8.0h`, `volume_accel_5m >= -0.15`, `mtf15_ema_bull = True`, `block_mtf5_turn_up = True`, `block_mtf5_trend_continuation = True`, `p_tp > p_sl = False`
- **후보 압축**: timestamp별 scalping_rank_score 상위 15개

## 손절 제한 OFF/ON 비교
| 모드 | 진입 | TP | SL | Timeout | TP율 | 최종 잔고 | 수익률 | MDD |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| OFF | 1 | 0 | 0 | 1 | 0.00% | 101.26 | +1.26% | 0.00% |
| ON | 1 | 0 | 0 | 1 | 0.00% | 101.26 | +1.26% | 0.00% |

## 필터 통계
- **OFF**: {'timestamps': 4320, 'rows_total': 64796, 'rows_after_candidate_rank': 64796, 'rows_after_active_volatility': 15276, 'rows_after_probability_gate': 1, 'rows_after_threshold': 1, 'entries_opened': 1, 'cooldown_blocked': 0}
- **ON**: {'timestamps': 4320, 'rows_total': 64796, 'rows_after_candidate_rank': 64796, 'rows_after_active_volatility': 15276, 'rows_after_probability_gate': 1, 'rows_after_threshold': 1, 'entries_opened': 1, 'cooldown_blocked': 0}
- **DIAGNOSTIC 후보 저장**: 500건

## 최근 거래 50건
| entry_time_kst            | exit_time_kst             | symbol      | reason   |   pnl_roe |   entry_price |   exit_price |     p_tp |     p_sl |   p_timeout |   tp_lift |     sl_lift |   timeout_lift |   entry_score |   entry_rank_score |   rank_at_entry |   balance |   drawdown |
|:--------------------------|:--------------------------|:------------|:---------|----------:|--------------:|-------------:|---------:|---------:|------------:|----------:|------------:|---------------:|--------------:|-------------------:|----------------:|----------:|-----------:|
| 2026-04-30 06:01:00+09:00 | 2026-04-30 07:01:00+09:00 | ZEREBROUSDT | TIMEOUT  | 0.0252361 |      0.026118 |     0.026289 | 0.251008 | 0.366579 |    0.341991 | 0.0636172 | -0.00429858 |      -0.246316 |    0.00318086 |            0.44186 |               1 |   101.262 |          0 |