# 1분봉 단타 Blind 검증 결과 - v1_tp50_sl30_h120

- **실행 시각**: 2026-05-07 12:32:17 (KST)
- **시간 기준**: DB 저장/모델 계산은 UTC, 리포트와 웹 표시 시간은 KST(Asia/Seoul)
- **Blind 표시 구간**: 2026-05-04T10:28:00+09:00 ~ 2026-05-07T10:27:00+09:00 (KST)
- **TP/SL/Horizon**: 5.0% / 3.0% / 120분
- **Entry Rank Score Threshold**: 0.3000
- **Entry Score**: `tp_lift * TP - max(sl_lift, 0) * SL * 0.70 - max(timeout_lift, 0) * 0.0030`
- **Entry Rank Score**: `tp_lift * 3.0 + p_tp - max(sl_lift, 0) * 0.8 - max(timeout_lift, 0) * 0.25`
- **Probability Baseline**: `p_tp=0.3159`, `p_sl=0.5248`, `p_timeout=0.2489`
- **Hard Gate**: `tp_lift >= -0.01`, `sl_lift <= 0.02`, `timeout_lift <= 0.30`, `tp_lift - sl_lift >= 0.03`, `p_tp * TP - p_sl * SL >= 0.0010`, `pump_base_condition = True`, `p_timeout <= 0.35`, `mtf15_ema_bull = False`, `block_mtf5_turn_up = False`, `block_mtf5_trend_continuation = False`, `p_tp > p_sl = False`
- **Risk Gate**: UTC 일자별 고점 대비 MDD `>= 30.00%` 도달 시 신규 진입 차단
- **후보 압축**: timestamp별 scalping_rank_score 상위 15개

## 손절 제한 OFF/ON 비교
| 모드 | 진입 | TP | SL | Timeout | TP율 | 최종 잔고 | 수익률 | MDD |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| OFF | 6 | 1 | 2 | 3 | 16.67% | 107.15 | +7.15% | 15.67% |
| ON | 6 | 1 | 2 | 3 | 16.67% | 107.15 | +7.15% | 15.67% |

## 일자별 진입/손익
손익은 각 거래의 진입일(KST) 기준으로 집계합니다.

### 손절 제한 OFF
|   일차 | 날짜(KST)    |   진입 |   TP |   SL |   Timeout |   손익(USDT) | 수익률(초기자금대비)   | 평균 ROE   |
|-----:|:-----------|-----:|-----:|-----:|----------:|-----------:|:--------------|:---------|
|    1 | 2026-05-04 |    0 |    0 |    0 |         0 |     0      | +0.00%        | +0.00%   |
|    2 | 2026-05-05 |    0 |    0 |    0 |         0 |     0      | +0.00%        | +0.00%   |
|    3 | 2026-05-06 |    5 |    1 |    1 |         3 |    16.3083 | +16.31%       | +6.62%   |
|    4 | 2026-05-07 |    1 |    0 |    1 |         0 |    -9.1593 | -9.16%        | -15.75%  |

### 손절 제한 ON
|   일차 | 날짜(KST)    |   진입 |   TP |   SL |   Timeout |   손익(USDT) | 수익률(초기자금대비)   | 평균 ROE   |
|-----:|:-----------|-----:|-----:|-----:|----------:|-----------:|:--------------|:---------|
|    1 | 2026-05-04 |    0 |    0 |    0 |         0 |     0      | +0.00%        | +0.00%   |
|    2 | 2026-05-05 |    0 |    0 |    0 |         0 |     0      | +0.00%        | +0.00%   |
|    3 | 2026-05-06 |    5 |    1 |    1 |         3 |    16.3083 | +16.31%       | +6.62%   |
|    4 | 2026-05-07 |    1 |    0 |    1 |         0 |    -9.1593 | -9.16%        | -15.75%  |

## 필터 통계
- **OFF**: {'timestamps': 4320, 'rows_total': 40808, 'rows_after_candidate_rank': 37223, 'rows_after_active_volatility': 23771, 'rows_after_probability_gate': 22, 'rows_after_threshold': 6, 'entries_opened': 6, 'blocked_full_slots': 0, 'open_positions_remaining': 0, 'exit_price_rows': 243131, 'cooldown_blocked': 0, 'daily_mdd_limit': 0.3, 'max_daily_mdd': 0.1566991704578485, 'daily_mdd_blocked': 0, 'daily_mdd_block_events': 0}
- **ON**: {'timestamps': 4320, 'rows_total': 40808, 'rows_after_candidate_rank': 37223, 'rows_after_active_volatility': 23771, 'rows_after_probability_gate': 22, 'rows_after_threshold': 6, 'entries_opened': 6, 'blocked_full_slots': 0, 'open_positions_remaining': 0, 'exit_price_rows': 243131, 'cooldown_blocked': 0, 'daily_mdd_limit': 0.3, 'max_daily_mdd': 0.1566991704578485, 'daily_mdd_blocked': 0, 'daily_mdd_block_events': 0}
- **DIAGNOSTIC 후보 저장**: 500건
- **CANDIDATE 미진입 사유**: `miss_reason`과 `blocked_by`는 차트 hover tooltip에서 확인합니다. `ENTRY_GATE_FAIL`, `HELD_SYMBOL`, `SLOT_FULL`, `COOLDOWN_BLOCK`, `DAILY_MDD_BLOCK`, `RANK_NOT_SELECTED` 등이 기록됩니다.

## 최근 거래 50건 (KST)
| entry_time_kst            | exit_time_kst             | symbol    | reason   |   pnl_roe |   pnl_amount |   entry_price |   exit_price |     p_tp |     p_sl |   p_timeout |    tp_lift |    sl_lift |   timeout_lift |   entry_score |   entry_rank_score |   rank_at_entry |   balance |   drawdown |
|:--------------------------|:--------------------------|:----------|:---------|----------:|-------------:|--------------:|-------------:|---------:|---------:|------------:|-----------:|-----------:|---------------:|--------------:|-------------------:|----------------:|----------:|-----------:|
| 2026-05-06 09:53:00+09:00 | 2026-05-06 11:53:00+09:00 | ZECUSDT   | TIMEOUT  |  0.090522 |     4.5261   |     515.7     |   525.81     | 0.318154 | 0.49474  |    0.270191 | 0.00222103 | -0.0300935 |     0.0212505  |   4.73e-05    |           0.828007 |               1 |   104.526 |  0         |
| 2026-05-06 13:11:00+09:00 | 2026-05-06 15:11:00+09:00 | ZECUSDT   | TIMEOUT  |  0.168245 |     8.79302  |     525.76    |   544.24     | 0.320076 | 0.483183 |    0.314427 | 0.00414227 | -0.0416506 |     0.0654865  |   1.0654e-05  |           1.0755   |               1 |   113.319 |  0         |
| 2026-05-06 15:44:00+09:00 | 2026-05-06 16:03:00+09:00 | ZECUSDT   | TP       |  0.2425   |    13.7399   |     542.09    |   569.195    | 0.320076 | 0.495961 |    0.254466 | 0.00414227 | -0.0288718 |     0.00552569 |   0.000190536 |           0.759021 |               1 |   127.059 |  0         |
| 2026-05-06 19:32:00+09:00 | 2026-05-06 20:15:00+09:00 | SKYAIUSDT | SL       | -0.1575   |   -10.0059   |       0.78306 |     0.759568 | 0.326337 | 0.499119 |    0.252956 | 0.0104034  | -0.0257147 |     0.00401534 |   0.000508123 |           0.842982 |               1 |   117.053 |  0.07875   |
| 2026-05-06 21:49:00+09:00 | 2026-05-06 23:49:00+09:00 | ZECUSDT   | TIMEOUT  | -0.012727 |    -0.744871 |     573.94    |   573.34     | 0.317649 | 0.49474  |    0.32927  | 0.00171564 | -0.0300935 |     0.0803297  |  -0.000155207 |           0.927609 |               1 |   116.308 |  0.0846124 |
| 2026-05-07 00:19:00+09:00 | 2026-05-07 01:36:00+09:00 | ZECUSDT   | SL       | -0.1575   |    -9.15928  |     581.88    |   564.424    | 0.320076 | 0.497613 |    0.300593 | 0.00414227 | -0.0272205 |     0.0516522  |   5.21569e-05 |           0.795976 |               1 |   107.149 |  0.156699  |