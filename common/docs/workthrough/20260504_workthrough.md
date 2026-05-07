================================================================================

# 2026-05-04 작업 기록 - 1분봉 데이터셋 메모리 에러 완화

## 배경

`trainer.validate_scalping_1m` 실행 중 `common/ml/scalping_1m_dataset.py`의
`add_cross_symbol_features()` 반환부에서 다음 메모리 에러가 발생했다.

- 위치: `result.replace([np.inf, -np.inf], 0).fillna(0)`
- 원인: 1,100만 row 이상 대용량 DataFrame 전체에 `replace()`를 수행하면서 pandas가
  여러 컬럼 블록에 대한 거대한 bool mask를 한 번에 생성했다.
- 에러: `Unable to allocate 1.25 GiB for an array with shape (122, 11038609)`

## 변경 내용

- `Scalping1MDatasetBuilder._sanitize_numeric_values()`를 추가했다.
  - 문자열/시간 컬럼에는 불필요한 `replace()`를 수행하지 않는다.
  - 숫자/불리언 컬럼만 컬럼 단위로 `inf`, `-inf`, `NaN`을 0으로 정리한다.
  - 전체 DataFrame 단위 mask 생성을 피해서 순간 메모리 사용량을 낮춘다.
- `load_dataset()`, `add_symbol_features()`, `add_mtf_features()`,
  `build_timeframe_feature_frame()`, `add_cross_symbol_features()`,
  `prepare_features()`의 대용량 `replace([np.inf, -np.inf], 0).fillna(0)` 경로를
  공통 helper로 교체했다.
- `add_cross_symbol_features()`에서 BTC 상대강도/랭크 컬럼을 하나씩 삽입하지 않고
  `pd.concat()`으로 feature block을 한 번에 붙이도록 변경했다.
- `apply_scalping_rank_score()`는 기존 `scalping_rank_score`가 있으면 먼저 제거한 뒤
  새 점수를 한 번에 붙이도록 변경했다.
  - `filter_top_gainer_targets()`와 `add_cross_symbol_features()` 양쪽에서 호출돼도
    중복 컬럼이 생기지 않게 했다.

## 검증

- `python -m py_compile common/ml/scalping_1m_dataset.py` 통과.
- 3개 심볼, `limit_per_symbol=400` 스모크 데이터셋 생성 통과.
  - rows=680
  - cols=390
  - duplicate_columns=0
  - performance_warnings=0
  - numeric inf 없음
  - numeric NaN 없음

## 남은 주의점

- 전체 555개 심볼, 20,000 limit 검증은 실행 시간이 길고 DB 부하가 있으므로 별도 실행이 필요하다.
- 이번 변경은 피처 계산식/라벨/랭킹 점수의 의미를 바꾸지 않고, 대용량 정리 방식과 컬럼 삽입 방식만 변경했다.

================================================================================

# 2026-05-04 작업 기록 - TP 5%, SL 3% 실험 모델명 분기 추가

## 배경

사용자가 현재 v8 구조에서 `TP=5%`, `SL=3%` 조건도 비교할 필요가 있다고 판단했다.
기존 CLI는 `--tp`, `--sl` 인자를 받지 않고 모델명에 따라 `build_scalping_1m_config()`가
라벨 설정을 결정한다.

## 변경 내용

- `common/ml/scalping_1m_dataset.py`의 `build_scalping_1m_config()`에
  `tp50_sl30` 모델명 분기를 추가했다.
- 모델명에 `tp50_sl30`이 포함되면 다음 설정을 사용한다.
  - `target_tp = 0.05`
  - `stop_loss = 0.03`
  - `horizon_minutes = 120` if `h120` 포함, 아니면 `60`
- 기존 기본 v8 모델명 `tp35_sl20_h60`은 그대로 유지했다.
  - 실거래 기본값과 기존 비교 기준을 덮어쓰지 않기 위한 조치다.

## 검증

- `python -m py_compile common/ml/scalping_1m_dataset.py` 통과.
- 설정 분기 확인:
  - `scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60` -> TP 3.5%, SL 2%, horizon 60
  - `scalping_1m_lgbm_v8_wedge_support_breakout_tp50_sl30_h60` -> TP 5%, SL 3%, horizon 60
  - `scalping_1m_lgbm_v8_wedge_support_breakout_tp50_sl30_h120` -> TP 5%, SL 3%, horizon 120

================================================================================

# 2026-05-04 작업 기록 - edge 없는 v1~v7 모델 산출물 폐기

## 배경

사용자가 v1~v7 실험이 양의 기대값(edge)을 만들지 못했다고 판단했고,
실제 모델 산출물 폐기를 승인했다.

## 확인한 근거

- `common/logs/backtest`의 v1~v7 리포트와 JSON을 확인했다.
- 현재 기본 실거래 모델 참조는 다음 v8 계열이다.
  - `StrategyConfig.MODEL_NAME = scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60`
  - `SCALPING_1M_MODEL_NAME = scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60`
- v1~v7 모델 파일은 기본 로드 경로가 아니므로 삭제해도 현재 기본 런타임 모델 로드는 끊기지 않는다.

## 삭제 내용

- `common/models` 내부의 v1~v7 1분봉 모델 산출물만 삭제했다.
- 삭제 패턴:
  - `scalping_1m_lgbm_v[1-7]*.pkl`
  - `feature_importance_scalping_1m_lgbm_v[1-7]*.md`
- 삭제 전 모든 대상 경로가 `C:\dev\kairos_1m\common\models` 내부인지 확인했다.
- 삭제된 파일 수: 46개

## 보존 내용

- 백테스트 리포트는 증거 기록으로 보존했다.
  - `common/logs/backtest/scalping_1m_results_scalping_1m_lgbm_v*`
- 현재 기준 v8 산출물은 삭제하지 않았다.
  - `scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60_tp/sl/timeout.pkl`
  - `feature_importance_scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60_tp/sl/timeout.md`

## 검증

- v1~v7 삭제 대상 재검색 결과: 남은 파일 없음.
- v8 모델/feature importance 파일 6개가 남아 있음을 확인했다.
- 기본 모델명 참조가 v8로 유지됨을 확인했다.

================================================================================

# 2026-05-04 작업 기록 - 1분봉 학습 속도 병목 완화

## 배경

사용자가 전체 555개 심볼, limit 20000 학습이 예전보다 느리다고 보고했다.
실행 중인 프로세스를 확인한 결과 학습 프로세스는 멈춘 것이 아니라 CPU와 메모리를 계속 사용 중이었다.

- 실행 중 프로세스 예시: PID 2448
- private memory 약 8GB
- training log는 `데이터셋 생성 시작` 이후 `top15/강한 펌핑 필터` 로그까지 도달하지 못한 상태였다.

## 원인 판단

- 현재 실행은 기본 모델명으로 돌아가 `TP=3.5%, SL=2%` 설정이었다.
  - 새 실험명 `scalping_1m_lgbm_v1_tp50_sl30_h60`을 지정하지 않은 실행으로 보인다.
- 코드상 `add_cross_symbol_features()`가 `top15/강한 펌핑` 필터 전에 전체 1,100만 row 규모 데이터에
  `scalping_rank_score`와 숫자 정리를 수행하고 있었다.
- `scalping_rank_score`는 후보 필터 후 `add_pump_episode_features()`에서 다시 계산되므로,
  필터 전 전체 row에 계산하는 것은 중복 작업이었다.

## 변경 내용

- `add_cross_symbol_features()`에서 필터 전 전체 row 대상 `apply_scalping_rank_score()` 호출을 제거했다.
- `add_cross_symbol_features()`에서 필터 전 전체 row 대상 `_sanitize_numeric_values()` 호출을 제거했다.
- `load_dataset()`에 심볼 피처 생성 진행률 로그를 추가했다.
  - 50심볼마다 또는 마지막 심볼에서 진행 상황을 기록한다.
- 이 변경은 후보 필터 후 랭킹 점수 계산을 유지하므로 최종 학습 후보의 `scalping_rank_score` 의미를 바꾸지 않는다.

## 검증

- `python -m py_compile common/ml/scalping_1m_dataset.py` 통과.
- 3개 심볼, `limit_per_symbol=400`, 모델명 `scalping_1m_lgbm_v1_tp50_sl30_h60` 스모크 데이터셋 생성 통과.
  - TP=5%, SL=3%, horizon=60 확인
  - rows=680
  - cols=390
  - duplicate_columns=0
  - performance_warnings=0
  - `scalping_rank_score` 존재
  - numeric NaN=0

## 주의점

- 이미 실행 중인 학습 프로세스는 시작 시점의 코드를 메모리에 올려 사용하므로 이번 개선이 적용되지 않는다.
- 개선을 적용하려면 사용자가 현재 학습을 중단한 뒤 새 명령으로 다시 실행해야 한다.

================================================================================

# 2026-05-04 작업 기록 - 대용량 cross-symbol concat 메모리 에러 수정

## 배경

전체 555개 심볼, limit 20000 검증 중 다음 메모리 에러가 다시 발생했다.

- 에러 위치: `add_cross_symbol_features()`와 `apply_scalping_rank_score()`의 `pd.concat(...).copy()`
- 에러: `Unable to allocate 5.22 GiB for an array with shape (127, 11038912) and data type float32`

## 원인

- 이전 PerformanceWarning 개선 과정에서 컬럼 삽입을 줄이기 위해 `pd.concat(...).copy()`를 사용했다.
- 작은 DataFrame에서는 경고가 없어졌지만, 1,100만 row 전체 DataFrame에서는 pandas가 전체 float32 블록을
  consolidate하면서 5GB 이상 임시 배열을 만들었다.
- 즉, 경고를 없애려다 대용량 학습에서는 더 큰 메모리 피크를 만든 것이다.

## 변경 내용

- 대용량 전체 DataFrame 대상 `pd.concat(...).copy()` 경로를 제거했다.
- `add_cross_symbol_features()`는 컬럼 단위 대입으로 변경해 전체 127개 컬럼 블록 복사를 피했다.
- `apply_scalping_rank_score()`도 `scalping_rank_score` 1개 컬럼을 직접 대입하도록 변경했다.
- 학습 데이터셋 생성 경로에서는 cross-symbol rank를 전체 row에서 계산하되,
  `top15` 또는 `strong_change_24h` 후보를 먼저 필터링한 뒤 후보 row에만 BTC 상대강도/rank 컬럼을 붙이도록 변경했다.
  - 전체 row에 불필요한 cross-symbol feature block을 붙이지 않는다.
  - `scalping_rank_score`는 후보 필터 후 `add_pump_episode_features()`에서 계산된다.

## 검증

- `python -m py_compile common/ml/scalping_1m_dataset.py` 통과.
- 3개 심볼, `limit_per_symbol=400`, 모델명 `scalping_1m_lgbm_v1_tp50_sl30_h60` 스모크 데이터셋 생성 통과.
  - TP=5%, SL=3%, horizon=60 확인
  - rows=680
  - cols=390
  - duplicate_columns=0
  - performance_warnings=0
  - `scalping_rank_score` 존재
  - numeric NaN=0

## 재실행 주의

- 실패한 기존 프로세스/명령은 기본 모델명으로 실행되어 `TP=3.5%, SL=2%` 설정이었다.
- 새 5%/3% 실험은 반드시 `--model scalping_1m_lgbm_v1_tp50_sl30_h60`을 지정해야 한다.
