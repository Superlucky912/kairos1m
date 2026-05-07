## 기본 1분봉 모델명 TP5/SL3 재학습 대상으로 변경

- 요청: 기존 1분봉 v8 모델을 TP 5%, SL 3% 기준으로 재학습할 수 있도록 기본 모델명을 변경.
- 변경 이유:
  - `build_scalping_1m_config()`는 모델명에 `tp50_sl30`이 포함되면 `target_tp=0.05`, `stop_loss=0.03`, `horizon_minutes=60`으로 설정한다.
  - 실거래 TP/SL 설정값인 `StrategyConfig.TARGET_THRESHOLD=0.05`, `LABEL_STOP_LOSS=0.03`과 학습 라벨 기준을 맞추기 위해 기본 모델명을 `tp50_sl30_h60` 계열로 변경했다.
- 변경 파일:
  - `common/ml/scalping_1m_dataset.py`
  - `common/config/strategy_config.py`
  - `web/api/app/main.py`
  - `web/api/app/services/scalping_backtest_service.py`
  - `web/ui/index.html`
  - `web/ui/assets/app.js`
- 변경 내용:
  - 기본 모델명:
    - `scalping_1m_lgbm_v8_wedge_support_breakout_tp35_sl20_h60`
    - `scalping_1m_lgbm_v8_wedge_support_breakout_tp50_sl30_h60`
  - `StrategyConfig`의 TP/SL 주석을 실제 값에 맞춰 5%, 3%로 정정했다.
- 실행하지 않은 작업:
  - 모델 학습 스크립트는 사용자가 직접 실행하는 원칙에 따라 실행하지 않았다.
  - 실거래 엔진도 실행하지 않았다.

================================================================================

## StrategyConfig 1분봉 설정 중앙화 및 미사용 상수 정리

- 요청:
  - `common/config/strategy_config.py`의 모든 상수를 확인하고, 사용하지 않는 상수는 삭제.
  - 1분봉 학습/검증/실거래/웹 백테스트에서 쓰는 설정값을 `StrategyConfig`로 모아 일관성을 복구.
- 변경 이유:
  - `SCALPING_1M_MODEL_NAME`은 TP5/SL3 모델명인데, 일부 1분봉 기본 설정이 과거 TP3.5/SL2 값을 직접 들고 있을 수 있었다.
  - 학습, 검증, 실거래, 웹 API의 기본 모델명/threshold/top N/TP/SL/horizon 설정 출처를 하나로 맞춰야 재학습과 검증 결과를 같은 기준으로 비교할 수 있다.
- 주요 변경 파일:
  - `common/config/strategy_config.py`
  - `common/ml/scalping_1m_dataset.py`
  - `common/ml/scalping_1m_scoring.py`
  - `trainer/train_scalping_1m_model.py`
  - `trainer/validate_scalping_1m.py`
  - `trader/trading_engine.py`
  - `web/api/app/main.py`
  - `web/api/app/services/scalping_backtest_service.py`
  - `web/ui/index.html`
  - `web/ui/assets/app.js`
- 변경 내용:
  - 1분봉 모델명, TP, SL, horizon, 지지/저항 파라미터, 펌핑 조건, entry gate 조건, scoring 가중치, 진단 entry 제한을 `StrategyConfig.SCALPING_1M_*`로 중앙화했다.
  - `Scalping1MConfig`와 `build_scalping_1m_config()`가 모듈 레벨 상수가 아니라 `StrategyConfig`를 기본값으로 사용하도록 정리했다.
  - `Scalping1MDatasetBuilder`의 기본 config 생성 경로를 `Scalping1MConfig()` 직접 생성에서 `build_scalping_1m_config()`로 바꿨다.
  - 학습/검증 CLI 기본 모델명과 limit이 `StrategyConfig.SCALPING_1M_MODEL_NAME`, `StrategyConfig.LIMIT_PER_SYMBOL`을 따르도록 맞췄다.
  - 실거래 엔진의 1분봉 config 생성도 `build_scalping_1m_config()`로 통일했다.
  - 웹 API에 `/api/scalping-1m/defaults`를 추가해 정적 UI가 Python config 기본값을 받아오도록 했다.
  - 웹 UI의 모델명/threshold/candidate top N 하드코딩 기본값을 제거하고, 빈 값이면 API 기본값이 적용되도록 했다.
  - `trainer/train_scalping_1m_model.py`, `trainer/validate_scalping_1m.py`의 오래된 Horizon 120분 설명을 `StrategyConfig` 기준 설명으로 정정했다.
- 삭제한 미사용 상수:
  - 구형 학습/피처/동적 threshold 계열: `SL_PENALTY_WEIGHT`, `TOP_GAINERS_WEIGHT`, `TARGET_COLUMN`, `SESSIONS`, `DROP_COLS`, `MFI_PERIOD`, `LOOKBACK_WINDOWS`, `EMA_FAST`, `EMA_MID`, `EMA_SLOW`, `SLOPE_PERIOD`, `RSI_PERIOD`, `ADX_PERIOD`, `CCI_PERIOD`, `EMA_PERIOD`, `BB_PERIOD`, `BB_STD`, `MACD_FAST`, `MACD_SLOW`, `MACD_SIGNAL`, `MARKET_BREADTH_EMA`, `VOLATILITY_LOOKBACK`, `REL_VOLUME_WINDOW`, `VOL_SPIKE_WINDOW`, `VOL_SPIKE_THRESHOLD`, `OI_CHANGE_WINDOW`, `BTC_LOOKBACK_WINDOW`, `MIN_NATR_THRESHOLD`, `DYNAMIC_THRESHOLD_ENABLED`, `BREADTH_HURDLE_BASE`, `BREADTH_PENALTY_STEP`
  - 참조가 남지 않은 별칭/collector/feature 설정: `MODEL_NAME`, `COLLECTOR_OI_REFRESH_SECONDS`, `COLLECTOR_OI_REFRESH_CONCURRENCY`, `FEATURE_PARALLEL_ENABLED`, `FEATURE_PARALLEL_WORKERS`
- 남긴 상수:
  - `PREDICTION_HORIZON`, `SL_COOLDOWN`, `TARGET_THRESHOLD`, `LABEL_STOP_LOSS`는 `common/ml/trading_rules.py`에서 참조가 남아 있어 삭제하지 않았다.
  - `common/ml/trading_rules.py`의 해당 함수들이 현재 호출되는지는 별도 죽은 코드 판단이 필요하므로, 이번 변경에서는 상수 삭제만 강행하지 않았다.
- 검증:
  - `Scalping1MConfig()`와 `build_scalping_1m_config()` 모두 모델명 `scalping_1m_lgbm_v8_wedge_support_breakout_tp50_sl30_h60`, TP `0.05`, SL `0.03`, horizon `60`, limit `47520`으로 생성됨을 확인했다.
  - `ScalpingBacktestPayload()`와 `/api/scalping-1m/defaults` 기본값이 같은 모델명, threshold `0.3`, candidate top N `15`를 반환함을 확인했다.
  - `py_compile`로 수정한 Python 파일 문법 검사를 통과했다.
  - 미사용 `StrategyConfig` 상수 재검색 결과가 비어 있음을 확인했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진 실행은 하지 않았다.

================================================================================

## web 제외 데드 코드 삭제 및 프로젝트 로직 리마인드 문서 작성

- 요청:
  - `web` 폴더를 제외한 나머지 소스에서 데드 코드를 삭제.
  - 데드 코드 전용 상수도 함께 삭제.
  - 프로젝트 전체 로직을 markdown 문서로 다시 정리.
- 사전 확인:
  - `web` 제외 Python 파일의 함수/클래스/모듈 참조 관계를 검색했다.
  - 직접 실행 엔트리인 `collector/*_main.py`, `trainer/*`, `validate_*`, `main.py`는 import가 없어도 삭제 후보에서 제외했다.
  - 실거래 보호 주문 경로에서 사용 중인 `TradingRules.get_tp_sl_prices`, `TradingRules.get_exit_order_side`는 유지했다.
- 삭제한 파일:
  - `common/utils/helpers.py`
- 삭제한 미사용 함수/메서드:
  - `common/utils/time_utils.py`
    - `get_now_kst`
    - `format_kst`
  - `common/ml/trading_rules.py`
    - `is_in_sl_cooldown`
    - `calculate_trade_metrics`
    - `check_exit_conditions`
  - `common/database/manager.py`
    - `get_symbol_sl_info`
    - `save_candles_5m`
    - `save_candles_15m`
    - `get_funding_rates`
    - `get_all_symbols_data`
  - `common/binance/binance_client.py`
    - `get_futures_listen_key`
    - `keep_alive_futures_listen_key`
    - `close_futures_listen_key`
    - `futures_post_algo_tp_sl_papi`
  - `common/binance/websocket_manager.py`
    - `_drain_dispatch_queue`
  - `trader/trading_engine.py`
    - `_update_buffer_from_df`
- 삭제한 설정 상수:
  - `StrategyConfig.SL_COOLDOWN`
  - `StrategyConfig.PREDICTION_HORIZON`
- 문서 작성:
  - `common/docs/answer/20260506_project_logic_reminder.md`
  - 데이터 수집, DB, 데이터셋/피처, 학습, 검증 백테스트, 실거래 엔진, 주문/보호 로직, 리스크 관리, 이번 데드 코드 삭제 범위를 정리했다.
- 검증:
  - 삭제한 이름이 `web` 제외 소스에서 더 이상 참조되지 않음을 확인했다.
  - 수정 Python 파일 `py_compile` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - collector, 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## 기본 1분봉 모델명 단축

- 요청:
  - 앞으로 모델명을 너무 길게 쓰지 않고 `v1_tp50_sl30_h60` 정도의 짧은 이름으로 사용.
  - 직접 실행 스크립트는 보존.
- 변경 파일:
  - `common/config/strategy_config.py`
  - `common/docs/answer/20260506_project_logic_reminder.md`
- 변경 내용:
  - `StrategyConfig.SCALPING_1M_MODEL_NAME`을 `v1_tp50_sl30_h60`로 변경했다.
  - 프로젝트 로직 리마인드 문서의 기본 모델명과 학습/검증 명령 예시를 같은 이름으로 갱신했다.
- 검증:
  - `Scalping1MConfig()`와 `build_scalping_1m_config()`가 `v1_tp50_sl30_h60`, TP `0.05`, SL `0.03`, horizon `60`으로 생성됨을 확인했다.
  - `common/config/strategy_config.py`, `common/ml/scalping_1m_dataset.py` 문법 검사를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## Blind 백테스트 청산 가격 추적 버그 수정

- 요청/상황:
  - Blind 검증 로그에서 진입 수가 너무 적고, 120분 horizon인데 며칠 뒤 청산되는 거래가 관측됐다.
  - 더 많이 진입하고 매매하려면 먼저 백테스트가 열린 포지션을 정확히 닫아야 한다.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
- 원인:
  - 기존 백테스트는 진입 후보로 필터링된 `eval_df` row만 순회하며 포지션 청산을 확인했다.
  - 학습/검증 데이터셋은 `pump_base_condition == 1` row만 남기므로, 진입 후 해당 심볼이 후보 조건에서 빠지면 다음 가격 row가 없어 포지션이 계속 열린 상태로 남을 수 있었다.
  - 이 상태에서는 `MAX_SLOTS`가 막혀 이후 후보를 살 수 없고, `horizon`도 실제 분 단위가 아니라 후보 timestamp index 차이로 계산됐다.
- 변경 내용:
  - 검증 시 청산용 원본 1분봉 가격 흐름을 `candles_1m`에서 별도 조회하도록 `_load_exit_price_frame()`을 추가했다.
  - `simulate_scalping_trades()`가 진입 후보는 기존 `eval_df`로 판단하되, 청산은 원본 `price_df` 기준으로 TP/SL/Timeout을 추적하도록 변경했다.
  - Timeout 판단을 후보 index 차이가 아니라 실제 `current_ts - entry_time` 분 단위로 계산하도록 변경했다.
  - 필터 통계에 `blocked_full_slots`, `open_positions_remaining`, `exit_price_rows`를 추가했다.
- 검증:
  - `trainer/validate_scalping_1m.py` 문법 검사를 통과했다.
  - `git diff --check -- trainer/validate_scalping_1m.py`를 통과했다.
  - synthetic 데이터로 후보 row가 진입 시점 1개뿐이어도 원본 1분봉 가격으로 120분 뒤 TIMEOUT 청산되는 것을 확인했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## collector_main days 옵션 1분봉 백필 연결

- 요청:
  - `python -m collector.collector_main --days 2` 실행 시 15분봉만 채우지 말고 1분봉도 함께 REST 백필되도록 변경.
- 변경 파일:
  - `collector/collector_main.py`
- 원인:
  - `RestCollector`에는 `intrabar_1m_candles` 인자가 이미 있었지만, `collector_main`의 startup bootstrap 호출에서 이 값을 넘기지 않아 `candles_1m` REST 백필이 비활성화돼 있었다.
  - 예약 REST 수집에서도 `COLLECTOR_1M_REST_BACKFILL_MINUTES` 설정값이 있었지만 호출부에 연결되지 않아 최근 1분봉 보강이 동작하지 않았다.
- 변경 내용:
  - startup bootstrap에서 `bootstrap_days * 24 * 60`개 1분봉을 함께 수집하도록 `intrabar_1m_candles`를 전달했다.
  - 예약 REST 수집에서도 `self.rest_collector.scheduled_intrabar_candles`를 전달해 최근 1분봉 보강을 수행하도록 연결했다.
  - bootstrap 로그에 1분봉 백필 캔들 수를 표시하도록 변경했다.
- 검증:
  - `collector/collector_main.py` 문법 검사를 통과했다.
  - `git diff --check -- collector/collector_main.py`를 통과했다.
- 실행하지 않은 작업:
  - collector, 백필, 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## MTF 누락 피처 fragmented DataFrame 경고 제거

- 요청:
  - `common/ml/scalping_1m_dataset.py:837`의 `result[f"{prefix}_{column}"] = 0.0` 경로에서 `PerformanceWarning: DataFrame is highly fragmented`가 반복 출력되는 문제 제거.
- 변경 파일:
  - `common/ml/scalping_1m_dataset.py`
- 원인:
  - 상위 timeframe 데이터가 없을 때 `_fill_missing_mtf_columns()`가 수십 개 MTF 컬럼을 루프에서 하나씩 삽입했다.
  - 이미 피처 컬럼이 많은 DataFrame에 반복 삽입이 들어가면서 pandas block fragmentation 경고가 발생했다.
- 변경 내용:
  - 누락 MTF 컬럼 목록을 먼저 만든 뒤, `pd.DataFrame(0.0, columns=...)`으로 한 번에 생성하도록 변경했다.
  - 기존 컬럼과는 `pd.concat(axis=1)`으로 한 번에 결합하도록 변경했다.
- 검증:
  - `common/ml/scalping_1m_dataset.py` 문법 검사를 통과했다.
  - `git diff --check -- common/ml/scalping_1m_dataset.py`를 통과했다.
  - 일부러 컬럼 150개짜리 DataFrame을 만든 뒤 `pd.errors.PerformanceWarning`을 에러로 승격한 상태에서 `_fill_missing_mtf_columns(..., "mtf_15m")`를 실행했고 경고 없이 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## Blind 검증 일자별 진입/손익 로그 추가

- 요청:
  - `validate_blind` 실행 시 1일차, 2일차, 3일차 각각 몇 번 진입했고 얼마 손익이 났는지 확인할 수 있게 변경.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
- 변경 내용:
  - 거래 청산 시 `pnl_amount`를 함께 저장하도록 변경했다.
  - Blind 구간의 KST 일자별 요약을 만드는 `_build_daily_trade_summary()`를 추가했다.
  - Markdown 리포트에 `## 일자별 진입/손익` 섹션을 추가했다.
    - 손절 제한 OFF/ON 각각 표시한다.
    - 일차, 날짜(KST), 진입 수, TP/SL/Timeout 수, 손익(USDT), 초기자금 대비 수익률, 평균 ROE를 표시한다.
  - JSON 결과에 손절 제한 ON 기준 `daily_summary`를 추가했다.
  - 콘솔 출력과 logger에는 손절 제한 ON 기준 일자별 요약을 출력하도록 추가했다.
- 집계 기준:
  - 일차 구분은 Blind 데이터의 `timestamp`를 KST 날짜로 변환해 계산한다.
  - 손익은 각 거래의 진입일(KST) 기준으로 귀속한다.
- 검증:
  - `trainer/validate_scalping_1m.py` 문법 검사를 통과했다.
  - `git diff --check -- trainer/validate_scalping_1m.py`를 통과했다.
  - synthetic 거래 데이터로 1일차/2일차/3일차 집계가 진입 수와 손익을 올바르게 계산하는지 확인했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## 모델 피처 스키마 충돌 방지용 기본 모델명 갱신

- 요청/상황:
  - 24h +20% 펌핑 후보 방식으로 데이터셋 생성은 완료됐지만, 검증 단계에서 기존 저장 모델이 제거된 rank 피처를 요구하며 중단됐다.
  - 오류 피처:
    - `change_24h_rank`
    - `change_24h_rank_pct`
    - `quote_volume_24h_rank`
    - `quote_volume_24h_rank_pct`
- 원인:
  - 현재 피처셋에서는 위 rank 피처를 의도적으로 제거했다.
  - 그런데 `StrategyConfig.SCALPING_1M_MODEL_NAME`이 기존 저장 산출물 이름과 겹쳐 검증이 이전 `.pkl` 모델을 로드했다.
- 변경 파일:
  - `common/config/strategy_config.py`
  - `common/docs/answer/20260506_project_logic_reminder.md`
- 변경 내용:
  - 기본 모델명을 기존 산출물과 겹치지 않는 짧은 새 이름 `v2_tp50_sl30_h60`로 변경했다.
  - 프로젝트 로직 리마인드 문서의 기본 모델명, 학습 명령, 검증 명령 예시도 `v2_tp50_sl30_h60`로 갱신했다.
- 검증:
  - `common/models`에 `v2_tp50_sl30_h60` 산출물이 아직 없음을 확인했다. 따라서 다음 학습은 새 모델 파일을 생성한다.
  - `StrategyConfig`, `build_scalping_1m_config()`, web API 기본 요청값이 모두 `v2_tp50_sl30_h60`을 반환함을 확인했다.
  - `common/config/strategy_config.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## pandas fragmented DataFrame 경고 제거

- 요청:
  - `common/ml/scalping_1m_dataset.py`에서 `PerformanceWarning: DataFrame is highly fragmented` 경고가 반복되어 로그가 지저분해지는 문제 제거.
- 원인:
  - 이미 컬럼이 많은 DataFrame에 `result["컬럼명"] = 값` 방식으로 펌핑 피처와 랭킹 점수를 추가하면서 pandas 내부 block이 잘게 쪼개졌다.
  - 특히 `pump_freshness_score`, `scalping_rank_score` 추가 지점에서 경고가 관측됐다.
- 변경 파일:
  - `common/ml/scalping_1m_dataset.py`
- 변경 내용:
  - `add_pump_episode_features()`에서 펌핑 관련 컬럼들을 개별 삽입하지 않고 `pump_features` DataFrame으로 한 번에 만든 뒤 `pd.concat(axis=1)`으로 붙이도록 변경했다.
  - `apply_scalping_rank_score()`에서 `scalping_rank_score`도 개별 삽입하지 않고 `pd.concat(axis=1)`으로 붙이도록 변경했다.
  - `add_cross_symbol_features()`의 BTC 상대강도 피처 추가도 같은 방식으로 정리해 동일 유형의 경고 가능성을 줄였다.
- 검증:
  - `common/ml/scalping_1m_dataset.py` 문법 검사를 통과했다.
  - `git diff --check -- common/ml/scalping_1m_dataset.py`를 통과했다.
  - 일부러 컬럼 150개짜리 DataFrame을 만든 뒤 `pd.errors.PerformanceWarning`을 에러로 승격한 상태에서 `add_pump_episode_features()`를 실행했고 경고 없이 통과했다.
  - 같은 조건에서 `add_cross_symbol_features(..., candidate_only=True)`도 경고 없이 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## 학습 후보를 24h +20% 펌핑 row로 전환

- 요청:
  - 학습에서 24h 상승률 top15 필터를 제거.
  - 반드시 24h +20% 이상인 row만 학습/검증/실거래 후보가 되도록 강제.
  - top15 rank 계산을 제거해 후보 필터와 메모리 부담을 줄임.
- 변경 파일:
  - `common/config/strategy_config.py`
  - `common/ml/scalping_1m_dataset.py`
  - `common/ml/scalping_1m_scoring.py`
  - `trainer/validate_scalping_1m.py`
  - `web/api/app/main.py`
  - `web/api/app/services/scalping_backtest_service.py`
  - `common/docs/answer/20260506_project_logic_reminder.md`
- 변경 내용:
  - `StrategyConfig.SCALPING_1M_MODEL_NAME`을 `v1_tp50_sl30_h60`로 맞췄다.
  - `SCALPING_1M_REQUIRE_PUMP_BASE_CONDITION = True`로 변경해 실거래/검증 gate에서 `pump_base_condition == 1`을 강제했다.
  - 기존 `SCALPING_1M_TOP_GAINER_COUNT`를 제거하고 후보 압축용 `SCALPING_1M_CANDIDATE_TOP_N`으로 정리했다.
  - 기존 24h 상승률 rank 기반 컬럼과 필터를 제거했다.
    - `change_24h_rank`
    - `change_24h_rank_pct`
    - `quote_volume_24h_rank`
    - `quote_volume_24h_rank_pct`
    - `is_top_gainer_target`
  - `Scalping1MDatasetBuilder.load_dataset()`에서 심볼별 전체 구간 기준으로 `pump_base_condition`과 펌핑 age를 먼저 계산한 뒤, BTCUSDT 외 심볼은 `pump_base_condition == 1` row만 `frames`에 쌓도록 바꿨다.
  - 최종 학습 후보 필터를 `filter_pump_targets()`로 명확히 바꿨다.
  - web API의 `candidate_top_n` 기본값도 새 `SCALPING_1M_CANDIDATE_TOP_N`을 보도록 변경했다.
- 검증:
  - 제거한 top-gainer rank 관련 이름이 `common/ml`, `common/config`, `trainer`, `trader`, `web`에서 더 이상 참조되지 않음을 확인했다.
  - 수정 Python 파일 `py_compile` 문법 검사를 통과했다.
  - 작은 synthetic DataFrame으로 `add_cross_symbol_features(..., candidate_only=True)`가 24h +20% 이상 row만 남기고 BTC 상대강도 피처를 붙이는 것을 확인했다.
  - web API 기본값이 `model=v1_tp50_sl30_h60`, `candidate_top_n=15`로 반환됨을 확인했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## Blind 검증 일자별 진입/손익 로그 추가 보완 기록

- 요청:
  - `validate_blind` 실행 시 1일차, 2일차, 3일차 각각 몇 번 진입했고 얼마 손익이 났는지 확인할 수 있게 변경.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
- 변경 내용:
  - 거래 청산 시 `pnl_amount`를 함께 저장하도록 변경했다.
  - Blind 구간의 KST 일자별 요약을 만드는 `_build_daily_trade_summary()`를 추가했다.
  - Markdown 리포트에 `## 일자별 진입/손익` 섹션을 추가했다.
  - JSON 결과에 손절 제한 ON 기준 `daily_summary`를 추가했다.
  - 콘솔 출력과 logger에는 손절 제한 ON 기준 일자별 요약을 출력하도록 추가했다.
- 집계 기준:
  - 일차 구분은 Blind 데이터의 `timestamp`를 KST 날짜로 변환해 계산한다.
  - 손익은 각 거래의 진입일(KST) 기준으로 귀속한다.
- 검증:
  - `trainer/validate_scalping_1m.py` 문법 검사를 통과했다.
  - `git diff --check -- trainer/validate_scalping_1m.py`를 통과했다.
  - synthetic 거래 데이터로 1일차/2일차/3일차 집계가 진입 수와 손익을 올바르게 계산하는지 확인했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.
