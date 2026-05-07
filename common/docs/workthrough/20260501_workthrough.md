# 2026-05-01 작업 일지

================================================================================

## 1분봉 TP 라벨 판정 horizon 120분 전환

### 요청
- 사용자가 "TP 120분으로 늘려봐"라고 요청했다.
- 코드 확인 결과 TP 가격폭은 `SCALPING_1M_TP = 0.05`, SL 가격폭은 `SCALPING_1M_SL = 0.03`이고, TP/SL 선터치 판정 시간은 `SCALPING_1M_HORIZON = 45`로 관리되고 있었다.
- 따라서 요청은 TP 가격폭 변경이 아니라 TP/SL 라벨 및 백테스트 제한시간 horizon을 45분에서 120분으로 늘리는 작업으로 해석했다.

### 변경
- `common/ml/scalping_1m_dataset.py`
  - 기본 모델명을 `scalping_1m_lgbm_v3_h120`으로 변경했다.
  - `SCALPING_1M_HORIZON`을 `45`에서 `120`으로 변경했다.
- `common/config/strategy_config.py`
  - `StrategyConfig.MODEL_NAME`을 `scalping_1m_lgbm_v3_h120`으로 변경했다.
- `trainer/train_scalping_1m_model.py`
  - 학습 엔트리 설명 문구를 Horizon 120분 기준으로 수정했다.
- `trainer/validate_scalping_1m.py`
  - 검증 엔트리 설명 문구를 Horizon 120분 기준으로 수정했다.
- `web/api/app/services/scalping_backtest_service.py`, `web/api/app/main.py`, `web/ui/index.html`, `web/ui/assets/app.js`
  - 웹 백테스트 기본 모델명을 `scalping_1m_lgbm_v3_h120`으로 맞췄다.

### 안전상 판단
- 기존 `scalping_1m_lgbm_v2` 산출물은 45분 horizon 라벨로 학습된 모델이다.
- horizon을 120분으로 바꾼 뒤에도 같은 모델명을 유지하면 실거래/웹이 이전 45분 모델을 그대로 로드할 수 있어, 기본 모델명을 새 이름으로 올렸다.
- 이 변경 후에는 120분 기준 모델을 새로 학습해야 정상 백테스트가 가능하다.

### 검증
- `rg "Horizon 45|45분|SCALPING_1M_HORIZON = 45|scalping_1m_lgbm_v2" common\ml common\config trainer web\api web\ui`
  - 코드 경로 기준 잔존 기본값 없음.
- `.\venv\Scripts\python.exe -c "from common.ml.scalping_1m_dataset import Scalping1MConfig; c=Scalping1MConfig(); print(c.model_name, c.horizon_minutes, c.target_tp, c.stop_loss)"`
  - `scalping_1m_lgbm_v3_h120 120 0.05 0.03`
- `.\venv\Scripts\python.exe -c "from web.api.app.main import app; print(app.title)"`
  - `Kairos Web Backtest`
- `.\venv\Scripts\python.exe -c "from trainer.validate_scalping_1m import validate_scalping_1m; from trainer.train_scalping_1m_model import train_scalping_1m_model; print('trainer imports ok')"`
  - `trainer imports ok`

================================================================================

## 120분 horizon 모델 진입 0건 문제 수정

### 증상
- `python -m trainer.validate_scalping_1m` 실행 결과 `scalping_1m_lgbm_v3_h120` 백테스트가 `Entries=0`으로 종료됐다.

### 원인 확인
- 리포트 필터 통계에서 `rows_after_active_volatility=15257` 이후 `rows_after_probability_gate=0`으로 전부 잘렸다.
- 학습 로그 기준 120분 horizon 데이터셋 분포는 `TP=14.16%`, `SL=27.64%`, `Timeout=58.20%`였다.
- 120분 모델의 blind 기준 확률 baseline은 `p_tp=0.2637`, `p_sl=0.4663`, `p_timeout=0.3261`이었다.
- 기존 하드 게이트는 `p_tp > p_sl`을 요구했다.
- 하지만 TP 폭은 5%, SL 폭은 3%이므로 120분 체계에서는 단순 확률 비교보다 `p_tp * TP - p_sl * SL` 기대폭 비교가 맞다.

### 변경
- `common/ml/scalping_1m_scoring.py`
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL = False`로 변경했다.
  - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE = 0.0015`를 추가했다.
  - `apply_entry_gate_with_risk()`를 추가해 `p_tp * TP - p_sl * SL >= 0.0015` 조건을 하드 게이트에 넣었다.
  - 기본 `SCALPING_1M_ENTRY_SCORE_THRESHOLD`를 `0.30`으로 올렸다.
- `trainer/validate_scalping_1m.py`
  - 백테스트가 `apply_entry_gate_with_risk()`를 사용하도록 변경했다.
  - 리포트 Hard Gate 설명에 기대폭 조건을 표시하도록 변경했다.
- `trader/trading_engine.py`
  - 실매매 후보 필터도 동일한 `apply_entry_gate_with_risk()`를 사용하도록 변경했다.
- `web/api/app/services/scalping_backtest_service.py`, `web/api/app/main.py`, `web/ui/index.html`
  - 웹/API 기본 threshold를 `0.30`으로 맞췄다.

### 검증
- 진단 후보 500건에 새 게이트를 적용했을 때 96건이 통과했다.
- threshold `-0.002` 전체 검증:
  - ON: `Entries=39`, `TP=7`, `SL=15`, `Timeout=17`, `Final=88.12`, `MDD=38.13%`
  - 0건 문제는 해결됐지만 수익성이 부족했다.
- threshold `0.30` 전체 검증:
  - OFF: `Entries=40`, `TP=11`, `SL=15`, `Timeout=14`, `Final=129.64`, `MDD=43.95%`
  - ON: `Entries=39`, `TP=11`, `SL=16`, `Timeout=12`, `Final=128.81`, `MDD=44.31%`
- import 및 기본값 확인:
  - `SCALPING_1M_ENTRY_SCORE_THRESHOLD=0.3`
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL=False`
  - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE=0.0015`
  - `ScalpingBacktestPayload().threshold=0.3`

### 남은 리스크
- 수익률은 개선됐지만 MDD가 44% 수준이라 실거래 투입 기준으로는 아직 위험하다.
- 다음 단계는 후보 수를 줄이거나, 글로벌 SL/동시 슬롯/쿨다운/진입 시간대 필터를 강화해 MDD를 먼저 낮추는 것이다.

================================================================================

## 120분 horizon 모델 피처 기반 추가 필터 적용

### 요청
- 사용자가 "특징을 맞추던가 필터를 더 걸던가"라고 요청했다.
- 기존 threshold 0.30 결과는 `Entries=39`, `TP=11`, `SL=16`, `Timeout=12`, `Final=128.81`, `MDD=44.31%`로 수익은 났지만 손실 구간이 너무 컸다.

### 분석
- 최신 거래 39건과 리포트에 저장된 진단 후보 500건의 `entry_features`를 파싱해 TP/SL/TIMEOUT별 피처 차이를 비교했다.
- 모델 확률 자체는 TP/SL을 강하게 구분하지 못했다.
  - 실제 거래 39건에서 `p_tp`, `p_sl`, `entry_rank_score` 평균은 TP와 SL 사이에 큰 차이가 없었다.
  - 학습 로그 기준 TP 모델 validation AUC는 약 `0.6209`, SL 모델 validation AUC는 약 `0.6149`로 분리력이 강하지 않았다.
- 반면 피처 기반 조건에서는 다음 방향이 상대적으로 일관됐다.
  - `p_timeout <= 0.35`
  - `pump_episode_age_hours <= 8`
  - `volume_accel_5m >= -0.15`
  - `mtf_15m_ema_bull_stack == 1`
  - `mtf_5m_trend_turn_up == 0`
  - `mtf_5m_trend_continuation_long == 0`
- 새 필터를 적용하기 전 진단 후보 500건 중 현재 게이트 통과 후보는 96건, 라벨은 `TP=33`, `SL=36`, `TIMEOUT=27`이었다.
- 새 필터를 사후 적용하면 진단 후보 통과는 38건, 라벨은 `TP=20`, `SL=11`, `TIMEOUT=7`로 개선됐다.
- 기존 실제 거래 39건에 사후 적용하면 10건만 통과했고 라벨은 `TP=4`, `SL=1`, `TIMEOUT=5`였다.

### 변경
- `common/ml/scalping_1m_scoring.py`
  - 공통 하드 게이트 상수를 추가했다.
    - `SCALPING_1M_MAX_TIMEOUT_PROB = 0.35`
    - `SCALPING_1M_MAX_PUMP_AGE_HOURS = 8.0`
    - `SCALPING_1M_MIN_VOLUME_ACCEL_5M = -0.15`
    - `SCALPING_1M_REQUIRE_MTF15_EMA_BULL = True`
    - `SCALPING_1M_BLOCK_MTF5_TREND_TURN_UP = True`
    - `SCALPING_1M_BLOCK_MTF5_TREND_CONTINUATION = True`
  - `apply_entry_gate_with_risk()`에 위 조건을 추가했다.
- `trainer/validate_scalping_1m.py`
  - 리포트의 Hard Gate 설명에 새 필터 조건이 표시되도록 변경했다.

### 검증
- import 확인:
  - `SCALPING_1M_MAX_TIMEOUT_PROB=0.35`
  - `SCALPING_1M_MAX_PUMP_AGE_HOURS=8.0`
  - `SCALPING_1M_MIN_VOLUME_ACCEL_5M=-0.15`
  - `SCALPING_1M_REQUIRE_MTF15_EMA_BULL=True`
  - `web.api.app.main` import 정상
- 전체 백테스트:
  - 명령: `.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model scalping_1m_lgbm_v3_h120 --limit 20000 --candidate-top-n 15`
  - OFF: `Entries=17`, `TP=6`, `SL=3`, `Timeout=8`, `Final=185.52`, `MDD=7.88%`
  - ON: `Entries=17`, `TP=6`, `SL=3`, `Timeout=8`, `Final=185.52`, `MDD=7.88%`
- 이전 결과 대비:
  - 진입 수: `39 -> 17`
  - SL: `16 -> 3`
  - Final: `128.81 -> 185.52`
  - MDD: `44.31% -> 7.88%`

### 판단
- 이번 개선은 모델 재학습이 아니라 실매매/백테스트 공통 하드 필터 개선이다.
- TP/SL 모델 자체의 분리력이 아직 강하지 않으므로, 다음 개선은 라벨/샘플링/피처를 다시 잡아 모델 AUC를 올리는 방향이 필요하다.

================================================================================

## change_24h 20% 이상 펌핑 구간 전용 학습 실험

### 요청
- 사용자가 현재 모델이 펌핑 구간을 학습하는 것이라면 전체 1분봉 중 `change_24h >= 20%`인 캔들만 학습하는 것이 맞지 않겠냐고 제안했다.
- 코드 확인 결과 기존 학습 필터는 `top15 OR change_24h >= 40%`였고, `change_24h >= 20%`는 `pump_base_condition` 피처 계산에만 쓰이고 있었다.

### 변경
- `common/ml/scalping_1m_dataset.py`
  - 기본 모델명을 `scalping_1m_lgbm_v4_pump20_h120`로 변경했다.
  - 학습 후보를 `change_24h >= 20% AND (top15 OR change_24h >= 40%)`로 제한했다.
  - 최종 학습 row도 `pump_base_condition == 1`을 요구하도록 변경했다.
- `common/ml/scalping_1m_scoring.py`
  - 실거래/백테스트 공통 게이트에 `pump_base_condition == 1` 조건을 추가했다.
- `common/config/strategy_config.py`, web API/UI 기본 모델명
  - 기본 모델명을 `scalping_1m_lgbm_v4_pump20_h120`로 맞췄다.

### 학습 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.train_scalping_1m_model --model scalping_1m_lgbm_v4_pump20_h120 --limit 20000`
- 데이터셋:
  - 필터 전 후보 row: `11,004,606`
  - 필터 후 학습 row: `156,593`
  - 기존 v3 학습 row `710,716`보다 크게 줄었다.
- 라벨 분포:
  - `TP=48,548 (31.00%)`
  - `SL=85,366 (54.51%)`
  - `Timeout=22,679 (14.48%)`
- 모델 검증:
  - TP: Best Iteration `1`, Valid AUC `0.5846`
  - SL: Best Iteration `2`, Valid AUC `0.5480`
  - TIMEOUT: Best Iteration `15`, Valid AUC `0.8005`

### 백테스트 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model scalping_1m_lgbm_v4_pump20_h120 --limit 20000 --candidate-top-n 15`
- 결과:
  - OFF: `Entries=0`, `TP=0`, `SL=0`, `Timeout=0`, `Final=100.00`
  - ON: `Entries=0`, `TP=0`, `SL=0`, `Timeout=0`, `Final=100.00`
- 필터 통계:
  - `rows_total=23,564`
  - `rows_after_active_volatility=5,830`
  - `rows_after_probability_gate=0`

### 원인 분석
- `v4` 모델의 blind baseline은 `p_tp=0.3189`, `p_sl=0.5466`, `p_timeout=0.1738`이었다.
- 진단 후보 500건 기준:
  - `edge = p_tp * 0.05 - p_sl * 0.03`의 최대값이 약 `0.0006`에 그쳤다.
  - 현재 실전 게이트의 `edge >= 0.0015`를 통과할 수 없었다.
  - `pump_episode_age_hours` 중앙값이 약 `214h`로 높아, 기존 `pump_age <= 8h` 필터와도 충돌했다.
- 피처 중요도:
  - TP 모델은 314개 피처 중 23개만 split에 쓰고 291개가 0이었다.
  - SL 모델은 314개 피처 중 32개만 split에 쓰고 282개가 0이었다.
  - TIMEOUT 모델은 314개 피처 중 78개가 split에 쓰였다.

### 판단
- `change_24h >= 20%`만 강제하면 "더 좋은 펌핑 학습"이 되는 것이 아니라, 현재 데이터에서는 SL 선터치가 훨씬 지배적인 구간으로 바뀐다.
- TP/SL 모델은 조기 종료가 너무 빠르고 AUC도 낮아 실매매 후보로 쓰기 어렵다.
- 이 실험은 `20% 이상 펌핑 구간 내부에서는 TP/SL 분리가 더 어려워진다`는 결론에 가깝다.
- `v4`는 새 산출물로 보존하되, 현재 게이트 기준에서는 진입 0건이므로 실전 기본값으로 계속 쓸지 재검토가 필요하다.

================================================================================

## 1분봉 단타 horizon 60분 전환 실험

### 작업 배경
- 사용자가 기존 120분 horizon을 60분으로 낮춰보라고 요청했다.
- 직전 `v4_pump20_h120` 실험은 `change_24h >= 20%` 강제 조건 때문에 SL 비중이 커지고 백테스트 진입이 0건이었다.
- 따라서 60분 실험은 v4 펌핑 20% 강제가 아니라, 기존에 가장 나았던 v3 후보군 기준으로 되돌려 별도 모델명 `scalping_1m_lgbm_v3_h60`으로 분리했다.

### 코드 변경
- `common/ml/scalping_1m_dataset.py`
  - 기본 모델명을 `scalping_1m_lgbm_v3_h60`으로 변경했다.
  - `SCALPING_1M_HORIZON`을 `120`에서 `60`으로 변경했다.
  - 후보 필터를 `change_24h >= 20% AND (top15 OR strong 40%)`에서 `top15 OR strong_change_24h >= 40%`로 복구했다.
  - 최종 학습 row에서 `pump_base_condition == 1` 강제를 제거했다.
- `common/ml/scalping_1m_scoring.py`
  - `SCALPING_1M_REQUIRE_PUMP_BASE_CONDITION = False`로 변경해 v3 계열 게이트와 맞췄다.
- `common/config/strategy_config.py`
  - `StrategyConfig.MODEL_NAME` 기본값을 `scalping_1m_lgbm_v3_h60`으로 변경했다.
- `web/api/app/main.py`, `web/api/app/services/scalping_backtest_service.py`, `web/ui/index.html`, `web/ui/assets/app.js`
  - web 백테스트 기본 모델명을 `scalping_1m_lgbm_v3_h60`으로 맞췄다.

### 정적 확인
- 명령:
  - `.\venv\Scripts\python.exe -c "from common.ml.scalping_1m_dataset import Scalping1MConfig; from common.ml.scalping_1m_scoring import SCALPING_1M_REQUIRE_PUMP_BASE_CONDITION; from web.api.app.main import ScalpingBacktestPayload; c=Scalping1MConfig(); print(c.model_name, c.horizon_minutes, c.target_tp, c.stop_loss); print(SCALPING_1M_REQUIRE_PUMP_BASE_CONDITION); print(ScalpingBacktestPayload().model)"`
- 결과:
  - `scalping_1m_lgbm_v3_h60 60 0.05 0.03`
  - `False`
  - `scalping_1m_lgbm_v3_h60`

### 학습 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.train_scalping_1m_model --model scalping_1m_lgbm_v3_h60 --limit 20000`
- 데이터셋:
  - 필터 전 row: `11,037,906`
  - 필터 후 row: `712,576`
  - Train/Val/Blind: `583,002 / 64,778 / 64,796`
- 라벨 분포:
  - `TP=65,931 (9.25%)`
  - `SL=141,839 (19.91%)`
  - `Timeout=504,806 (70.84%)`
- 모델 검증:
  - TP: Best Iteration `10`, Valid AUC `0.7113`
  - SL: Best Iteration `30`, Valid AUC `0.6841`
  - TIMEOUT: Best Iteration `170`, Valid AUC `0.8227`
- 산출물:
  - `common/models/scalping_1m_lgbm_v3_h60_tp.pkl`
  - `common/models/scalping_1m_lgbm_v3_h60_sl.pkl`
  - `common/models/scalping_1m_lgbm_v3_h60_timeout.pkl`
  - `common/models/feature_importance_scalping_1m_lgbm_v3_h60_tp.md`
  - `common/models/feature_importance_scalping_1m_lgbm_v3_h60_sl.md`
  - `common/models/feature_importance_scalping_1m_lgbm_v3_h60_timeout.md`

### 백테스트 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model scalping_1m_lgbm_v3_h60 --limit 20000 --candidate-top-n 15`
- 결과:
  - OFF: `Entries=1`, `TP=0`, `SL=0`, `Timeout=1`, `Final=101.26`, `MDD=0.00%`
  - ON: `Entries=1`, `TP=0`, `SL=0`, `Timeout=1`, `Final=101.26`, `MDD=0.00%`
- 필터 통계:
  - `rows_total=64,796`
  - `rows_after_active_volatility=15,276`
  - `rows_after_probability_gate=1`
  - `rows_after_threshold=1`

### 판단
- 60분 horizon은 라벨 관점에서 SL 비중을 크게 낮추고 Timeout 비중을 크게 키웠다.
- TP/SL/TIMEOUT AUC는 120분 v3 대비 전반적으로 좋아졌다.
- 다만 현재 실전 게이트가 `p_timeout <= 0.35`, 기대값 edge, 8시간 이내 펌핑 나이 등을 동시에 요구하기 때문에 실제 진입은 1건에 그쳤다.
- 따라서 `h60`은 모델 분리력은 더 좋아졌지만, 현재 게이트를 그대로 쓰면 너무 보수적이다.

================================================================================

## v5 TP 3.5% / SL 2% / horizon 60분 실험

### 작업 배경
- 사용자가 `TP 3.5`, `SL 2`처럼 더 극단적인 라벨 폭을 적용하면 피처 특징이 더 살아날 수 있는지 확인하고자 했다.
- 실험명은 `scalping_1m_lgbm_v5_tp35_sl20_h60`으로 분리했다.

### 코드 변경
- `common/ml/scalping_1m_dataset.py`
  - `SCALPING_1M_MODEL_NAME = "scalping_1m_lgbm_v5_tp35_sl20_h60"`
  - `SCALPING_1M_TP = 0.035`
  - `SCALPING_1M_SL = 0.02`
  - `SCALPING_1M_HORIZON = 60` 유지
- `common/ml/scalping_1m_scoring.py`
  - fallback `apply_entry_gate()`의 기본 TP/SL을 `0.035 / 0.02`로 맞췄다.
- `common/config/strategy_config.py`
  - `TARGET_THRESHOLD = 0.035`
  - `LABEL_STOP_LOSS = 0.02`
  - `MODEL_NAME = "scalping_1m_lgbm_v5_tp35_sl20_h60"`
- web 기본 모델명도 `scalping_1m_lgbm_v5_tp35_sl20_h60`으로 맞췄다.

### 정적 확인
- 명령:
  - `.\venv\Scripts\python.exe -c "from common.ml.scalping_1m_dataset import Scalping1MConfig; from common.config.strategy_config import StrategyConfig; from web.api.app.main import ScalpingBacktestPayload; c=Scalping1MConfig(); print(c.model_name, c.horizon_minutes, c.target_tp, c.stop_loss); print(StrategyConfig.MODEL_NAME, StrategyConfig.TARGET_THRESHOLD, StrategyConfig.LABEL_STOP_LOSS); print(ScalpingBacktestPayload().model)"`
- 결과:
  - `scalping_1m_lgbm_v5_tp35_sl20_h60 60 0.035 0.02`
  - `scalping_1m_lgbm_v5_tp35_sl20_h60 0.035 0.02`
  - `scalping_1m_lgbm_v5_tp35_sl20_h60`

### 학습 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.train_scalping_1m_model --model scalping_1m_lgbm_v5_tp35_sl20_h60 --limit 20000`
- 데이터셋:
  - 필터 전 row: `11,037,906`
  - 필터 후 row: `712,576`
  - Train/Val/Blind: `583,002 / 64,778 / 64,796`
- 라벨 분포:
  - `TP=97,432 (13.67%)`
  - `SL=208,243 (29.22%)`
  - `Timeout=406,901 (57.10%)`
- 모델 검증:
  - TP: Best Iteration `13`, Valid AUC `0.6244`
  - SL: Best Iteration `51`, Valid AUC `0.6175`
  - TIMEOUT: Best Iteration `182`, Valid AUC `0.8173`
- 피처 사용량:
  - TP: 314개 중 nonzero `75`, zero `239`
  - SL: 314개 중 nonzero `103`, zero `211`
  - TIMEOUT: 314개 중 nonzero `161`, zero `153`

### 백테스트 결과
- 명령:
  - `.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model scalping_1m_lgbm_v5_tp35_sl20_h60 --limit 20000 --candidate-top-n 15`
- 결과:
  - OFF: `Entries=0`, `TP=0`, `SL=0`, `Timeout=0`, `Final=100.00`, `MDD=0.00%`
  - ON: `Entries=0`, `TP=0`, `SL=0`, `Timeout=0`, `Final=100.00`, `MDD=0.00%`
- 필터 통계:
  - `rows_total=64,796`
  - `rows_after_active_volatility=15,276`
  - `rows_after_probability_gate=0`
- baseline:
  - `p_tp=0.2613`
  - `p_sl=0.4977`
  - `p_timeout=0.2953`

### 진단 후보 분석
- diagnostic 후보 500건:
  - `SL=280`
  - `TP=123`
  - `TIMEOUT=97`
- `p_tp` 최대값: `0.3147`
- `p_sl` 최소값: `0.4522`
- `sl_lift <= 0` 통과 후보: `58/500`
- `edge = p_tp * 0.035 - p_sl * 0.02` 최대값: `0.00131`
- 현재 hard gate의 `edge >= 0.0015`를 통과하지 못했다.

### 판단
- 의도대로 라벨 이벤트는 늘고 피처 사용량도 늘었다.
- 그러나 TP보다 SL 이벤트가 더 많이 늘어, 모델이 진입 후보를 위험하게 본다.
- 현재 게이트 기준으로는 v5가 진입 0건이므로 실전 기본값으로 쓰기 어렵다.
- v5를 계속 살리려면 TP/SL 라벨 자체보다 `SL 위험이 낮은 눌림/재가속 후보`를 더 좁히거나, v5 전용 기대값 edge 기준을 재설계해야 한다.
