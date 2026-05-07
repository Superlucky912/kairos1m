# 2026-05-02 작업 기록

## v6 방향성 피처 추가 실험

### 작업 배경
- 사용자가 기존 피처가 TP/SL을 구분하기에 두루뭉술하다고 지적했고, 방향성 피처를 적용하라고 요청했다.
- v5(`TP 3.5% / SL 2% / horizon 60분`)는 라벨 이벤트와 피처 사용량은 늘었지만, SL 비중이 커져 현재 게이트 기준 진입 0건이었다.
- 이번 작업은 TP/SL 숫자는 유지하고, 현재 봉까지 알 수 있는 과거 기반 방향성 피처만 추가해 `scalping_1m_lgbm_v6_directional_tp35_sl20_h60`으로 분리했다.

### 코드 변경
- `common/ml/scalping_1m_dataset.py`
  - 기본 모델명을 `scalping_1m_lgbm_v6_directional_tp35_sl20_h60`으로 변경했다.
  - 다음 1분봉 방향성 피처를 추가했다.
    - `taker_buy_ratio_3m`
    - `taker_buy_ratio_5m`
    - `buy_pressure_persistence_3m`
    - `buy_pressure_persistence_5m`
    - `buy_pressure_decay_5m`
    - `volume_price_alignment_5m`
    - `impulse_3m`
    - `impulse_5m`
    - `impulse_follow_through_3m`
    - `impulse_follow_through_5m`
    - `impulse_failure_5m`
    - `breakout_close_20m`
    - `breakout_wick_fail_20m`
    - `breakout_hold_3m`
    - `breakout_hold_5m`
    - `breakout_reclaim_5m`
    - `breakout_failure_pressure_5m`
    - `breakout_quality_follow_5m`
    - `close_near_recent_high_5m`
    - `low_reclaim_strength_5m`
    - `sl_sweep_reclaim_5m`
    - `directional_continuation_score`
  - 후보 압축용 `scalping_rank_score`에 방향성 보너스와 실패 돌파 패널티를 반영했다.
- `common/config/strategy_config.py`
  - `MODEL_NAME` 기본값을 `scalping_1m_lgbm_v6_directional_tp35_sl20_h60`으로 변경했다.
- `web/api/app/main.py`, `web/api/app/services/scalping_backtest_service.py`, `web/ui/index.html`, `web/ui/assets/app.js`
  - web 기본 모델명을 `scalping_1m_lgbm_v6_directional_tp35_sl20_h60`으로 맞췄다.

### 정적/스모크 확인
- 설정 확인:
  - `scalping_1m_lgbm_v6_directional_tp35_sl20_h60 60 0.035 0.02`
  - `StrategyConfig.MODEL_NAME = scalping_1m_lgbm_v6_directional_tp35_sl20_h60`
- 작은 샘플에서 새 피처 생성과 `prepare_features()` 실행을 확인했다.
- 새 피처는 현재 봉과 과거 봉만 사용하도록 계산했다.

### 학습 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.train_scalping_1m_model --model scalping_1m_lgbm_v6_directional_tp35_sl20_h60 --limit 20000`
- 데이터셋:
  - rows: `712,576`
  - cols: `367`
  - Feature: `336`
- 라벨 분포:
  - `TP=97,432 (13.67%)`
  - `SL=208,243 (29.22%)`
  - `Timeout=406,901 (57.10%)`
- 모델 검증:
  - TP: Best Iteration `13`, Valid AUC `0.6237`
  - SL: Best Iteration `52`, Valid AUC `0.6165`
  - TIMEOUT: Best Iteration `176`, Valid AUC `0.8183`

### 백테스트 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model scalping_1m_lgbm_v6_directional_tp35_sl20_h60 --limit 20000 --candidate-top-n 15`
- 결과:
  - OFF: `Entries=0`, `TP=0`, `SL=0`, `Timeout=0`, `Final=100.00`, `MDD=0.00%`
  - ON: `Entries=0`, `TP=0`, `SL=0`, `Timeout=0`, `Final=100.00`, `MDD=0.00%`
- 필터 통계:
  - `rows_total=64,796`
  - `rows_after_active_volatility=15,276`
  - `rows_after_probability_gate=0`
- baseline:
  - `p_tp=0.2603`
  - `p_sl=0.4993`
  - `p_timeout=0.2949`

### 진단 후보 분석
- diagnostic 후보 500건:
  - `SL=275`
  - `TP=135`
  - `TIMEOUT=90`
- `p_tp` 최대값: `0.3125`
- `p_sl` 최소값: `0.4023`
- `sl_lift <= 0` 통과 후보: `60/500`
- `edge = p_tp * 0.035 - p_sl * 0.02` 최대값: `0.00212`
- 조건별 통과 수:
  - `sl_lift <= 0`: `60/500`
  - `edge >= 0.0015`: `8/500`
  - 누적 조건에서 `volume_accel_5m >= -0.15`까지 통과: `7/500`
  - 이후 `mtf_15m_ema_bull_stack == 1`에서 `0/500`
- edge를 넘은 8개 후보의 실제 라벨:
  - `TP=2`
  - `SL=5`
  - `TIMEOUT=1`

### 판단
- 방향성 피처를 추가해도 TP/SL 분리력은 개선되지 않았다.
- 일부 새 피처는 SL 모델 중요도에 들어갔지만, TP 모델의 상위 중요도는 여전히 `extreme_range_width_120m`, `mtf_15m_range_pct`, `range_width_120m`, `volatility_15m`처럼 변동성/레인지 계열이 지배했다.
- 현재 문제는 단순히 피처 수가 부족한 것이 아니라, 후보군 자체가 SL이 더 많이 나는 구간을 많이 포함한다는 점이다.
- v6도 현재 게이트 기준 진입 0건이므로 실전 기본값으로 쓰기 어렵다.

================================================================================

## 2026-05-02 v7 지지/돌파 병행 후보 구조 실험

### 작업 배경
- 사용자 요청:
  - 지지 근처 매수만 보지 말고 돌파 매매도 함께 보도록 후보 구조를 개선한다.
  - 기존 펌핑/돌파 추격형 피처만으로는 TP/SL 구분이 약하므로, 지지선 매수와 돌파 매수를 별도 구조로 반영한다.
- 안전 판단:
  - 실거래 실행 파일은 구동하지 않았다.
  - 기존 중복 진입 방지, 포지션 동기화, TP/SL 복구 로직은 건드리지 않았다.
  - 변경은 학습/검증용 1분봉 피처, 후보 점수, 모델 기본명 동기화 범위로 제한했다.

### 변경 파일
- `common/ml/scalping_1m_dataset.py`
  - 기본 모델명을 `scalping_1m_lgbm_v7_support_breakout_tp35_sl20_h60`로 변경했다.
  - TP/SL/Horizon은 `3.5% / 2.0% / 60분` 조건을 유지했다.
  - 후보 압축용 `scalping_rank_score`에 지지 매수 보너스를 강화했다.
  - 돌파 보너스는 유지하되 실패 돌파/윗꼬리 패널티가 반영되도록 했다.
- `common/ml/scalping_1m_scoring.py`
  - `build_entry_structure_bonus()`를 추가해 지지 셋업과 돌파 셋업을 entry rank score에 반영했다.
  - `build_entry_structure_masks()`를 추가해 `support_setup`, `breakout_setup`을 분리했다.
  - probability gate에 구조 게이트를 포함했다.
  - 최종 후보에 `entry_setup = support/breakout` 값을 남기도록 했다.
  - 지지 반응 구간도 active volatility mask를 통과할 수 있도록 완화했다.
- `common/config/strategy_config.py`
  - 기본 모델명을 v7로 변경했다.
- `web/api/app/main.py`, `web/api/app/services/scalping_backtest_service.py`, `web/ui/index.html`, `web/ui/assets/app.js`
  - web 기본 백테스트 모델명을 v7로 동기화했다.

### 학습 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.train_scalping_1m_model --model scalping_1m_lgbm_v7_support_breakout_tp35_sl20_h60 --limit 20000`
- 데이터셋:
  - rows: `712,576`
  - cols: `367`
  - Feature: `336`
- 라벨 분포:
  - `TP=97,432 (13.67%)`
  - `SL=208,243 (29.22%)`
  - `Timeout=406,901 (57.10%)`
- 모델 검증:
  - TP: Best Iteration `13`, Valid AUC `0.6237`
  - SL: Best Iteration `52`, Valid AUC `0.6165`
  - TIMEOUT: Best Iteration `176`, Valid AUC `0.8183`

### 백테스트 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model scalping_1m_lgbm_v7_support_breakout_tp35_sl20_h60 --limit 20000 --candidate-top-n 15`
- 결과:
  - OFF: `Entries=20`, `TP=4`, `SL=10`, `Timeout=6`, `TP Rate=20.00%`, `Final=90.58`, `MDD=28.92%`
  - ON: `Entries=18`, `TP=3`, `SL=8`, `Timeout=7`, `TP Rate=16.67%`, `Final=92.64`, `MDD=21.21%`
- 필터 통계:
  - OFF:
    - `rows_total=64,796`
    - `rows_after_active_volatility=27,649`
    - `rows_after_probability_gate=74`
    - `rows_after_threshold=20`
    - `entries_opened=20`
  - ON:
    - `rows_total=64,796`
    - `rows_after_active_volatility=27,677`
    - `rows_after_probability_gate=76`
    - `rows_after_threshold=31`
    - `entries_opened=18`

### 구조별 진단
- 저장된 ON 거래 18건을 `entry_setup` 기준으로 확인했다.
- 실제 체결:
  - `support=18건`
  - `breakout=0건`
- support 거래 결과:
  - `TP=3`
  - `SL=8`
  - `Timeout=7`
  - 평균 ROE: 약 `-0.0054`
- 해석:
  - 돌파 셋업은 후보 구조에는 들어갔지만 최종 점수/게이트를 통과하지 못했다.
  - 현재 지지 셋업 정의는 진입을 만들 수는 있으나 SL을 충분히 줄이지 못했다.
  - `support_hold_120m`, `near_support_entry_120m`만으로는 실전적인 지지 반응을 충분히 구분하지 못한다.

### 판단
- 지지/돌파 구조를 분리하는 방향 자체는 맞지만, v7 결과는 실사용 기준으로 나쁘다.
- 현재 기준 최상 성과는 여전히 `scalping_1m_lgbm_v3_h120`이다.
- v7은 `Final=92.64`, `MDD=21.21%`라 기본 실거래 모델로 두면 위험하다.
- 다음 개선은 단순 완화가 아니라 다음 조건을 더 엄격히 분리해야 한다.
  - 지지 매수: 지지선 접촉 후 저점 회복, 하단 꼬리, 매도 압력 감소, 다음 봉 반등 확인.
  - 돌파 매수: 저항 돌파 후 종가 유지, 재돌파/리테스트 성공, 윗꼬리 실패 돌파 배제.
  - 후보 선택: support와 breakout을 같은 점수식에 섞지 말고 셋업별 threshold를 따로 둔다.

================================================================================

## 2026-05-02 v8 피벗 기반 웻지 지지/돌파 실험

### 작업 배경
- 사용자 요청:
  - 전전 고점과 전 고점, 전전 저점과 전 저점의 배열을 보고 웻지/추세를 판단한다.
  - 그 구조에서 지지선을 잡아 롱 진입을 해야 한다.
  - 지지 근처 매수뿐 아니라 돌파 매매도 함께 본다.
- 기존 v7 한계:
  - `support_hold_120m`, `near_support_entry_120m`는 120분 rolling quantile 기반의 수평 지지에 가까웠다.
  - 기울어진 지지선/저항선, 확정된 스윙 고점/저점 배열, 웻지 구조를 직접 반영하지 못했다.

### 변경 파일
- `common/ml/scalping_1m_dataset.py`
  - 기본 모델명을 `scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60`로 변경했다.
  - `add_wedge_pivot_features()`를 추가했다.
  - `pivot_span=3` 기준으로 확정된 과거 피벗만 사용한다.
    - 피벗 중심 이후 3개 봉이 지나야 확정되므로 현재 봉 이후 정보는 사용하지 않는다.
  - 신규 피처:
    - `prev_swing_high_120m`, `prev2_swing_high_120m`
    - `prev_swing_low_120m`, `prev2_swing_low_120m`
    - `pivot_lower_highs_120m`
    - `pivot_higher_lows_120m`
    - `pivot_lower_lows_120m`
    - `wedge_converging_120m`
    - `wedge_falling_120m`
    - `wedge_support_line_120m`, `wedge_resistance_line_120m`
    - `dist_to_wedge_support_120m`, `dist_to_wedge_resistance_120m`
    - `wedge_support_touch_120m`
    - `wedge_support_reclaim_120m`
    - `wedge_support_break_120m`
    - `wedge_resistance_breakout_120m`
    - `wedge_support_retest_quality_120m`
    - `wedge_breakout_quality_120m`
  - raw price line 계열은 metadata로 제외하고, 거리/기울기/구조 플래그 중심으로 모델 입력을 구성했다.
  - 후보 압축용 `scalping_rank_score`에 웻지 지지/돌파 보너스와 이탈 패널티를 반영했다.
- `common/ml/scalping_1m_scoring.py`
  - active volatility mask에 `wedge_support_reaction`을 추가했다.
  - entry structure bonus에 웻지 지지 리클레임, 터치, 저항 돌파, retest quality, breakout quality를 반영했다.
  - `build_entry_structure_masks()`에서 `wedge_support_setup`과 `wedge_resistance_breakout` 조건을 추가했다.
  - 최종 게이트에서 `wedge_support_break_120m` 이탈 후보를 제외했다.
- `common/config/strategy_config.py`
  - `StrategyConfig.MODEL_NAME` 기본값을 v8로 변경했다.
- `web/api/app/main.py`, `web/api/app/services/scalping_backtest_service.py`, `web/ui/index.html`, `web/ui/assets/app.js`
  - web 기본 백테스트 모델명을 v8로 동기화했다.

### 정적/스모크 확인
- `python -m py_compile`:
  - `common/ml/scalping_1m_dataset.py`
  - `common/ml/scalping_1m_scoring.py`
  - `common/config/strategy_config.py`
  - `web/api/app/main.py`
  - `web/api/app/services/scalping_backtest_service.py`
- venv import 확인:
  - `SCALPING_1M_MODEL_NAME = scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60`
  - `StrategyConfig.MODEL_NAME = scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60`
  - `from web.api.app.main import app; print(app.title)` 결과: `Kairos Web Backtest`

### 학습 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.train_scalping_1m_model --model scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60 --limit 20000`
- 데이터셋:
  - rows: `712,576`
  - cols: `390`
  - Feature: `353`
- 라벨 분포:
  - `TP=97,432 (13.67%)`
  - `SL=208,243 (29.22%)`
  - `Timeout=406,901 (57.10%)`
- 모델 검증:
  - TP: Best Iteration `13`, Valid AUC `0.6226`
  - SL: Best Iteration `53`, Valid AUC `0.6168`
  - TIMEOUT: Best Iteration `206`, Valid AUC `0.8183`
- 저장 모델:
  - `common/models/scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60_tp.pkl`
  - `common/models/scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60_sl.pkl`
  - `common/models/scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60_timeout.pkl`

### 백테스트 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60 --limit 20000 --candidate-top-n 15`
- 결과:
  - OFF: `Entries=23`, `TP=6`, `SL=7`, `Timeout=10`, `TP Rate=26.09%`, `Final=127.17`, `MDD=23.34%`
  - ON: `Entries=20`, `TP=5`, `SL=5`, `Timeout=10`, `TP Rate=25.00%`, `Final=131.05`, `MDD=14.38%`
- 필터 통계:
  - OFF:
    - `rows_total=64,796`
    - `rows_after_candidate_rank=63,176`
    - `rows_after_active_volatility=38,736`
    - `rows_after_probability_gate=66`
    - `rows_after_threshold=23`
    - `entries_opened=23`
  - ON:
    - `rows_total=64,796`
    - `rows_after_candidate_rank=63,176`
    - `rows_after_active_volatility=38,736`
    - `rows_after_probability_gate=66`
    - `rows_after_threshold=36`
    - `entries_opened=20`
- 리포트:
  - `common/logs/backtest/scalping_1m_results_scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60_dual.md`
  - `common/logs/backtest/scalping_1m_results_scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60_dual.trades.json`

### 구조별 진단
- ON 거래 20건 기준:
  - `support=14건`
    - `TP=3`
    - `SL=4`
    - `Timeout=7`
    - 평균 ROE: 약 `+0.0198`
  - `breakout=6건`
    - `TP=2`
    - `SL=1`
    - `Timeout=3`
    - 평균 ROE: 약 `+0.0544`
- v7 대비 변화:
  - v7은 ON 기준 `Entries=18`, `TP=3`, `SL=8`, `Timeout=7`, `Final=92.64`, `MDD=21.21%`
  - v8은 ON 기준 `Entries=20`, `TP=5`, `SL=5`, `Timeout=10`, `Final=131.05`, `MDD=14.38%`
  - 돌파 셋업이 실제 체결에 포함되기 시작했다.

### 판단
- 피벗 기반 웻지 구조를 후보 압축/게이트에 넣은 것은 v7보다 명확히 개선됐다.
- 다만 v8의 TP 모델 AUC는 v7보다 약간 낮고, feature importance에서 웻지 플래그 대부분은 직접 중요 피처로 쓰이지 않았다.
- 따라서 현재 성과 개선은 모델이 웻지를 강하게 학습했다기보다, 후보 압축과 하드 게이트가 진입 후보를 더 잘 골라낸 결과로 보는 것이 맞다.
- v3_h120의 `Final=185.52`, `MDD=7.88%`보다는 아직 낮으므로 v8을 최종 실전 기본값으로 확정하기에는 이르다.
- 다음 개선 포인트:
  - support와 breakout을 같은 threshold로 섞지 않고 별도 threshold를 둔다.
  - wedge support 후보는 지지선 리클레임 이후 다음 1~2봉 확인 조건을 별도 실험한다.
  - breakout 후보는 저항선 돌파 후 retest 성공 조건과 윗꼬리 실패 돌파 배제를 더 강하게 둔다.

================================================================================

## 2026-05-02 candles_1m 최신성 확인 및 v3_h120 리테스트

### 작업 배경
- 사용자 요청:
  - 현재 collector로 수집된 1분봉이 지금 시간만큼 있는지 확인한다.
  - `scalping_1m_lgbm_v3_h120`을 현재 DB 기준으로 다시 테스트한다.

### DB 최신성 확인
- 확인 시각:
  - KST: `2026-05-02 06:09`
  - UTC: `2026-05-01 21:09`
- `candles_1m` 범위:
  - min timestamp: `2026-02-10 09:00:00+00:00`
  - max timestamp: `2026-04-30 01:41:00+00:00`
  - max timestamp KST: `2026-04-30 10:41`
- 현재 시각 대비 지연:
  - 약 `156,496초`
  - 약 `43.5시간`
- 최근 5분 데이터:
  - distinct symbols: `0`
  - rows: `0`
- 판단:
  - 현재 `candles_1m`은 실시간으로 따라오지 못하고 있다.
  - collector가 멈췄거나 `candles_1m` 저장 경로가 동작하지 않는 상태로 봐야 한다.

### v3_h120 설정 복원 패치
- 문제:
  - 현재 전역 기본값은 v8 기준 `TP=3.5%`, `SL=2%`, `Horizon=60분`이다.
  - 기존 검증 함수는 모델명만 바꿔도 라벨/시뮬레이션 설정은 전역 기본값을 따라갔다.
  - 따라서 `--model scalping_1m_lgbm_v3_h120`만 실행하면 v3 모델을 v8 리스크 조건으로 검증할 수 있었다.
- 변경:
  - `common/ml/scalping_1m_dataset.py`
    - `build_scalping_1m_config()` 추가
    - `v3_h120` 모델명은 `TP=5%`, `SL=3%`, `Horizon=120분`으로 복원
    - `v3_h60` 모델명은 `TP=5%`, `SL=3%`, `Horizon=60분`으로 복원
    - `v4_pump20_h120` 모델명은 `TP=5%`, `SL=3%`, `Horizon=120분`으로 복원
  - `trainer/validate_scalping_1m.py`
    - 검증 config 생성을 `build_scalping_1m_config()`로 변경
  - `trainer/train_scalping_1m_model.py`
    - 학습 config 생성을 `build_scalping_1m_config()`로 변경
- 확인:
  - `scalping_1m_lgbm_v3_h120 0.05 0.03 120`
  - `python -m py_compile` 통과

### v3_h120 리테스트
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model scalping_1m_lgbm_v3_h120 --limit 20000 --candidate-top-n 15`
- 데이터셋:
  - symbols: `555`
  - rows after target filter: `710,716`
  - cols: `390`
  - TP: `100,631 (14.16%)`
  - SL: `196,433 (27.64%)`
  - Timeout: `413,652 (58.20%)`
  - Train: `581,328`
  - Val: `64,592`
  - Blind: `64,796`
- 저장 모델과 현재 입력 피처 차이:
  - 검증 입력의 추가 피처 `39개`는 저장된 v3 모델 feature set에 맞춰 제외됐다.
- 결과:
  - OFF: `Entries=39`, `TP=9`, `SL=17`, `Timeout=13`, `TP Rate=23.08%`, `Final=86.67`, `MDD=50.09%`
  - ON: `Entries=35`, `TP=8`, `SL=15`, `Timeout=12`, `TP Rate=22.86%`, `Final=93.67`, `MDD=39.51%`
- 구조별 ON 진단:
  - `support=13건`
    - `TP=3`
    - `SL=5`
    - `Timeout=5`
    - 평균 ROE: 약 `+0.0151`
  - `breakout=22건`
    - `TP=5`
    - `SL=10`
    - `Timeout=7`
    - 평균 ROE: 약 `-0.0049`
- 리포트:
  - `common/logs/backtest/scalping_1m_results_scalping_1m_lgbm_v3_h120_dual.md`
  - `common/logs/backtest/scalping_1m_results_scalping_1m_lgbm_v3_h120_dual.trades.json`

### 판단
- 현재 DB는 최신 수집 상태가 아니므로 “현재 시장까지 포함한 v3 리테스트”는 아니다.
- 이번 결과는 `2026-04-30 10:41 KST`까지 수집된 `candles_1m` 기준이다.
- v3_h120은 현재 v8 웻지 공통 게이트와 결합했을 때 성과가 크게 나빠졌다.
- 과거 v3_h120 결과와 직접 비교하면 안 된다.
  - 과거 결과는 당시 scoring/gate 기준이었다.
  - 이번 결과는 현재 scoring/gate 기준이다.
- 실전 판단 전에는 먼저 collector의 `candles_1m` 최신 수집 문제를 해결해야 한다.

================================================================================

## 2026-05-02 collector 5m/15m 합성 스케줄 추가

### 작업 배경
- 사용자 요청:
  - `candles_1m` 기반 합성 적재를 수동 실행이 아니라 collector 스케줄로 붙인다.
  - 5분봉은 5분마다, 15분봉은 15분마다 실행한다.
- 기존 상태:
  - `collector/synthesize_timeframes_main.py`는 별도 CLI로 존재했다.
  - `collector/collector_main.py`의 collector 루프에는 합성 호출이 없었다.
  - collector는 웹소켓 1분봉 저장과 15분 REST 저장만 수행했다.

### 변경 파일
- `common/config/strategy_config.py`
  - `COLLECTOR_SYNTHESIZE_TIMEFRAMES_ENABLED = True`
  - `COLLECTOR_SYNTH_5M_DELAY_SECONDS = 10`
  - `COLLECTOR_SYNTH_15M_DELAY_SECONDS = 240`
  - `COLLECTOR_SYNTH_EMPTY_TABLE_LOOKBACK_HOURS = 24`
- `collector/collector_main.py`
  - `TimeframeSynthesizer`를 import했다.
  - collector 시작 시 `_run_timeframe_synthesis_loop()`를 별도 async task로 실행한다.
  - collector 종료 시 합성 task도 cancel/await 하도록 했다.
  - 5분봉 스케줄:
    - 다음 5분 경계 + `10초`에 실행
    - 목적: 직전 1분봉 close 저장 완료 대기
  - 15분봉 스케줄:
    - 다음 15분 경계 + `240초`에 실행
    - 목적: `candles` REST/OI 저장 완료 대기
  - 시작 직후 `5m`, `15m`을 한 번 즉시 실행해 밀린 합성을 따라잡도록 했다.
  - 합성은 `asyncio.to_thread()`로 분리해 collector event loop를 막지 않도록 했다.
  - 합성 실패는 logger error로 남기고 collector 수집 task에는 전파하지 않는다.

### 합성 범위 정책
- 각 실행마다 `candles_1m`의 `MAX(timestamp)`를 source end로 사용한다.
- 대상 테이블의 `MAX(timestamp)`부터 다시 합성해 마지막 bucket을 재-upsert한다.
- 대상 테이블이 비어 있으면 source 최신 시각 기준 최대 24시간만 자동 bootstrap한다.
- 불완전한 bucket은 기존 SQL대로 `minute_count` 조건에서 제외된다.

### 검증
- 정적 검증:
  - `.\venv\Scripts\python.exe -m py_compile collector\collector_main.py collector\synthesize_timeframes_main.py common\config\strategy_config.py`
- dry-run:
  - `.\venv\Scripts\python.exe -m collector.synthesize_timeframes_main --timeframes 5m --start 2026-05-01T21:30:00Z --end 2026-05-01T21:47:00Z --report common/logs/backtest/synthesize_timeframes_dryrun_check.json`
  - 결과:
    - `source_groups=16`
    - `complete_groups=0`
    - `incomplete_groups=16`
    - `upserted_rows=0`
- 스케줄 계산 확인:
  - 기준 시각 `2026-05-02 06:47:55 KST`
  - 5m 다음 실행: `2026-05-02 06:50:10 KST`
  - 15m 다음 실행: `2026-05-02 07:04:00 KST`

### 주의
- 현재 실행 중인 collector는 이미 떠 있는 프로세스이므로, 이번 코드 변경은 collector 재시작 후 반영된다.
- 최근 `candles_1m`은 웹소켓 대상 16개 심볼만 실시간 저장 중이다.
- `candles_5m`/`candles_15m`는 complete bucket만 적재하므로, 1분봉 gap이 있는 과거 구간은 별도 backfill 없이는 합성되지 않는다.
