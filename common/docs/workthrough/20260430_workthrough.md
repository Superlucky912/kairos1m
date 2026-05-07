# 2026-04-30 작업 일지

================================================================================

## 1분봉 전환 및 web 백테스트 연결 강화

### 수행 내용

- collector의 실시간 웹소켓 기준을 1분봉으로 전환하기 위해 `COLLECTOR_REST_INTERVAL=15m`, `COLLECTOR_PRIMARY_INTERVAL=1m`로 역할을 분리했다.
- REST 수집기는 기존 `candles` 테이블에 15분봉과 OI를 저장하도록 유지했다.
- 웹소켓 collector는 마감된 1분봉 kline을 `candles_1m`에 저장하고, 같은 메시지를 ZMQ로 trader에 전달하도록 변경했다.
- 웹소켓 심볼 교체 예약 기준을 다음 15분 경계에서 다음 1분 경계 이후로 변경했다.
- trader는 기존 15분봉 `FeatureSelector`/글로벌 모델 경로 대신 `Scalping1MDatasetBuilder`와 `scalping_1m_lgbm_v2_tp/sl/timeout` 3모델을 사용하도록 변경했다.
- 실매매 후보 필터와 진입 점수 계산을 백테스트와 공유하기 위해 `common/ml/scalping_1m_scoring.py`를 추가했다.
- web 백테스트 JSON 계약에 `threshold`, `candidate_top_n`, `data_interval`을 추가하고, replay 응답에도 `data_interval=1m`을 명시했다.
- 15분 글로벌 학습/검증 파일과 모델 산출물을 삭제했다.

### 삭제한 15분 전용 자산

- `trainer/train_global_model.py`
- `trainer/validate_blind.py`
- `common/ml/data_loader.py`
- `common/ml/feature_selector.py`
- `common/ml/feature_engineer.py`
- `common/utils/compare_live_backtest_trade.py`
- `common/utils/diagnose_preds.py`
- `common/models/global_pumping_model_v*.pkl`
- `common/models/global_pumping_model_v*_meta.pkl`
- `common/models/global_pumping_model_v*_meta_config.json`
- `common/models/feature_importance_v*.md`

### 검증 결과

- UTF-8 기준으로 관련 파일을 읽고 수정했다.
- 기본 Python에는 일부 의존성이 없어 web/trader import 검증이 실패했으나, 프로젝트 `venv` 기준으로 재검증했다.
- `venv\Scripts\python.exe` 기준 `web.api.app.main` import 성공.
- `venv\Scripts\python.exe` 기준 `trader.trading_engine`, `collector.ws_collector` import 성공.
- `rg` 기준 코드 경로에서 `FeatureSelector`, `DataLoader`, `global_pumping_model`, `train_global_model`, `validate_blind` 잔존 참조가 제거된 것을 확인했다.

### 주의 사항

- 현재 git 상태에는 이번 작업 전부터 존재한 것으로 보이는 문서/로그 대량 삭제가 함께 표시된다.
- 이번 작업에서는 해당 기존 삭제 상태를 되돌리거나 정리하지 않았다.

================================================================================

## Web 백테스트 요약 표기 보완

### 수행 내용

- web 요약 카드의 `TP / SL` 표기가 결과 건수를 오해하게 만들 수 있어 `TP Count`, `SL Count`, `Timeout`으로 분리했다.
- 최신 리포트 기준 실제 요약은 `Entries=5`, `TP=1`, `SL=2`, `Timeout=2`이며, `TP + SL = 3`은 전체 성공 건수가 아니라 TIMEOUT을 제외한 방향성 청산 건수다.

### 결과

- UI에서 TP 건수와 SL 건수, TIMEOUT 건수가 각각 독립적으로 표시된다.
