# Kairos 1분봉 프로젝트 로직 리마인드

작성일: 2026-05-06

## 현재 큰 구조

Kairos는 1분봉 시장 데이터를 수집하고, 그 데이터로 TP/SL/TIMEOUT 분리 모델을 학습한 뒤, 실거래 엔진에서 같은 피처와 같은 진입 점수식을 사용해 주문을 실행하는 구조다.

핵심 흐름은 다음 네 단계다.

1. `collector`가 Binance 시장 데이터를 DB에 저장한다.
2. `common/ml/scalping_1m_dataset.py`가 DB의 1분봉 데이터를 학습/검증용 피처와 라벨로 변환한다.
3. `trainer/train_scalping_1m_model.py`가 TP, SL, TIMEOUT LightGBM 모델 3개를 학습한다.
4. `trader/trading_engine.py`가 실시간 1분봉 close 이벤트마다 같은 피처 생성/점수 계산을 수행하고 주문한다.

웹 도구는 결과 확인과 수동 백테스트 실행 보조 역할이다. 이번 데드 코드 정리 범위에서는 `web` 폴더를 제외했다.

## 설정의 중심

전략 설정은 `common/config/strategy_config.py`의 `StrategyConfig`가 기준이다.

현재 1분봉 기본 실험값은 다음과 같다.

```text
SCALPING_1M_MODEL_NAME = v1_tp35_sl20_h60
SCALPING_1M_TARGET_TP = 0.035
SCALPING_1M_STOP_LOSS = 0.02
SCALPING_1M_HORIZON_MINUTES = 60
LIMIT_PER_SYMBOL = 47520
SCALPING_1M_PUMP_MIN_CHANGE_24H = 0.20
SCALPING_1M_REQUIRE_PUMP_BASE_CONDITION = True
SCALPING_1M_CANDIDATE_TOP_N = 15
SCALPING_1M_ENTRY_SCORE_THRESHOLD = 0.30
```

중요한 점은 모델명만 바뀌는 것이 아니라, `Scalping1MConfig`와 `build_scalping_1m_config()`가 이 값을 읽어 학습/검증/실거래 기본값을 만든다는 것이다. 그래서 모델명을 바꿔 재학습할 때는 config의 모델명, TP/SL/horizon 의미, 실제 저장되는 모델 파일명이 같이 맞아야 한다.

## 데이터 수집

시장 데이터 수집의 중심은 `collector/collector_main.py`의 `CollectorService`다.

주요 역할은 다음과 같다.

- Binance REST로 15분봉 기준 데이터와 OI/funding 보정 데이터를 가져온다.
- Binance websocket으로 1분봉 kline close 이벤트를 받는다.
- `candles_1m`을 공식 1분봉 원본으로 저장한다.
- 필요 시 `candles_1m` 기반으로 `candles_5m`, `candles_15m`를 합성한다.
- ZMQ로 실거래 엔진에 시장 데이터와 심볼 목록을 전달한다.

DB 저장 계층은 `common/database/manager.py`의 `DBManager`가 담당한다. 상위 코드는 SQL을 직접 흩뿌리지 않고, `save_candles`, `save_candles_1m`, `save_funding_rates`, `get_candles_1m` 같은 목적별 메서드를 호출한다.

## 학습 데이터 생성

`common/ml/scalping_1m_dataset.py`의 `Scalping1MDatasetBuilder`가 1분봉 학습 데이터셋을 만든다.

주요 책임은 다음과 같다.

- `candles_1m`에서 심볼별 최근 캔들을 읽는다.
- TP/SL 선터치 기준으로 라벨을 만든다.
- 1분봉 가격/거래량/꼬리/변동성 피처를 만든다.
- BTC 상대강도, 5분/15분 합성 문맥, 지지/저항, wedge support 계열 피처를 만든다.
- 24시간 상승률이 +20% 이상인 row만 학습 후보로 남긴다.
- 최근 blind 구간을 학습에서 제외하고 train/validation을 나눈다.
- TP, SL, TIMEOUT 각각의 target 모드에 맞춰 모델 입력 컬럼을 정리한다.

여기서 중요한 점은 실거래 엔진도 같은 builder를 사용한다는 것이다. 즉 학습 때 만든 피처와 실시간 추론 피처가 갈라지지 않도록 같은 코드 경로를 탄다.

## 모델 학습

학습 엔트리는 `trainer/train_scalping_1m_model.py`다.

학습은 하나의 모델이 아니라 3개 모델로 분리된다.

- `{model_name}_tp.pkl`: horizon 안에 TP가 먼저 닿을 확률
- `{model_name}_sl.pkl`: horizon 안에 SL이 먼저 닿을 확률
- `{model_name}_timeout.pkl`: horizon 안에 TP/SL 모두 미도달할 확률

LightGBM 파라미터는 `StrategyConfig.LGBM_PARAMS`를 기본으로 하되, 1분봉 학습용 보정값을 추가한다. 학습 결과는 `common/models`에 저장되고, feature importance 문서도 함께 생성된다.

현재 기본 학습 명령은 다음 형태다.

```powershell
.\venv\Scripts\python.exe -m trainer.train_scalping_1m_model --model v1_tp35_sl20_h60 --limit 47520
```

## 검증 백테스트

검증 엔트리는 `trainer/validate_scalping_1m.py`다.

검증은 실매매 엔진을 켜지 않고 DB의 1분봉 데이터만으로 blind 구간을 시뮬레이션한다.

핵심 절차는 다음과 같다.

1. 학습 때와 같은 config로 검증 데이터셋을 만든다.
2. TP/SL/TIMEOUT 모델 3개를 로드한다.
3. 각 row에 `p_tp`, `p_sl`, `p_timeout`을 붙인다.
4. `common/ml/scalping_1m_scoring.py`의 공통 scoring/gate를 적용한다.
5. 슬롯, 손절 블랙리스트, 수수료, 슬리피지, 레버리지, MDD를 반영해 거래를 시뮬레이션한다.
6. 결과 markdown과 trades json을 `common/logs/backtest`에 저장한다.

현재 기본 검증 명령은 다음 형태다.

```powershell
.\venv\Scripts\python.exe -m trainer.validate_scalping_1m --model v1_tp35_sl20_h60 --limit 47520 --candidate-top-n 15
```

## 진입 점수와 게이트

실거래와 백테스트가 공유하는 진입 판단은 `common/ml/scalping_1m_scoring.py`에 있다.

핵심 개념은 단순히 `p_tp`가 높은 종목을 사는 것이 아니라, TP 확률의 상대 우위와 SL/TIMEOUT 위험을 같이 보는 것이다.

대략적인 점수 구조는 다음과 같다.

```text
entry_score =
  tp_lift * TP
  - max(sl_lift, 0) * SL * SL_RISK_WEIGHT
  - max(timeout_lift, 0) * TIMEOUT_RISK_WEIGHT
```

그리고 hard gate는 다음 조건들을 같이 본다.

- TP lift가 너무 낮지 않은가
- SL lift가 너무 높지 않은가
- TIMEOUT lift가 너무 높지 않은가
- TP lift와 SL lift 차이가 충분한가
- 기대 가격 edge가 양수인가
- 펌핑 age, 거래량 가속, MTF 구조 조건을 만족하는가
- 설정에 따라 `p_tp > p_sl`을 강제할 수 있는가

현재는 이 판단식이 검증과 실거래에서 같은 모듈을 사용한다.

## 실거래 엔진

실거래 시작점은 `main.py`이고, 실제 중심은 `trader/trading_engine.py`의 `KairosEngine`이다.

엔진 시작 시 주요 초기화는 다음과 같다.

- `BinanceClient` 생성
- `DBManager` 생성
- `OrderManager` 생성
- `Scalping1MDatasetBuilder` 생성
- ZMQ subscriber/client 연결
- TP/SL/TIMEOUT 모델 3개 로드
- DB 리스크 테이블 초기화
- 현재 포지션과 보호 주문 상태 동기화

실시간 루프의 핵심 흐름은 다음과 같다.

1. collector에서 ZMQ로 1분봉 close 이벤트를 받는다.
2. 심볼별 candle buffer를 업데이트한다.
3. close event batch manager가 직전 마감봉 기준으로 batch inference를 예약한다.
4. 엔진은 후보 심볼과 BTCUSDT 문맥을 모아 1분봉 피처를 생성한다.
5. TP/SL/TIMEOUT 모델 확률을 계산한다.
6. 공통 scoring/gate를 적용한다.
7. MDD, 전체 SL circuit, 심볼별 SL blacklist, 슬롯 수를 점검한다.
8. 통과한 후보를 랭킹 순으로 시장가 진입한다.
9. 체결 가격 기준으로 TP/SL 보호 주문을 생성한다.
10. user stream과 REST sync로 체결/청산/누락 상태를 보정한다.

## 주문과 보호 로직

주문 실행은 `trader/trader_order_manager.py`의 `OrderManager`가 담당한다.

중요한 안전 장치는 다음과 같다.

- 진입 전 레버리지와 마진 타입 설정
- exchange precision과 lot size 기준 수량 정규화
- 진입 시장가 주문 후 실제 체결가/수량 확인
- TP/SL 조건부 주문 생성
- 기존 TP/SL 주문 탐지와 누락 복구
- 포지션 수동/동기화 청산 처리

TP/SL 가격과 청산 주문 방향 계산은 `common/ml/trading_rules.py`에 남아 있다. 현재 이 파일은 실제로 쓰는 `normalize_position_side`, `get_exit_order_side`, `get_tp_sl_prices`만 유지한다.

## 리스크 관리

실거래 리스크 관리는 여러 층으로 나뉜다.

- `MAX_SLOTS`: 동시에 열 수 있는 포지션 수 제한
- `DAILY_MDD_LIMIT`: 일일 최대 낙폭 제한
- `MAX_SL_COUNT_FOR_BLACKLIST`: 같은 심볼 연속 SL 후 블랙리스트
- `GLOBAL_SL_COOLDOWN_MINUTES`: 전체 계정 기준 SL 직후 신규 진입 정지
- `GLOBAL_SL_CIRCUIT_LOOKBACK_HOURS`, `GLOBAL_SL_CIRCUIT_THRESHOLD`: 최근 구간 전체 SL 횟수 기반 circuit
- `ENTRY_MARGIN_SAFETY_RATIO`: 주문 가능 증거금 중 실제 진입 사용 비율 제한

과거 중복 진입/멀티 프로세스 문제 때문에, 이 프로젝트에서는 성능보다 중복 진입 방지와 포지션 상태 동기화가 우선이다.

## 이번 데드 코드 정리 결과

이번에 `web` 제외 소스에서 삭제한 것은 실제 참조가 없는 코드만이다.

삭제한 항목은 다음과 같다.

- `common/utils/helpers.py`
- `common/utils/time_utils.py`의 `get_now_kst`, `format_kst`
- `common/ml/trading_rules.py`의 `is_in_sl_cooldown`, `calculate_trade_metrics`, `check_exit_conditions`
- `common/config/strategy_config.py`의 `SL_COOLDOWN`, `PREDICTION_HORIZON`
- `common/database/manager.py`의 `get_symbol_sl_info`, `save_candles_5m`, `save_candles_15m`, `get_funding_rates`, `get_all_symbols_data`
- `common/binance/binance_client.py`의 listen key 수동 래퍼와 PAPI TP/SL 래퍼
- `common/binance/websocket_manager.py`의 `_drain_dispatch_queue`
- `trader/trading_engine.py`의 `_update_buffer_from_df`

삭제하지 않은 것은 다음과 같다.

- `collector/*_main.py`, `trainer/*`, `validate_*`: 직접 실행 엔트리라 import가 없어도 삭제하지 않았다.
- `TradingRules.get_tp_sl_prices`, `TradingRules.get_exit_order_side`: 실거래 보호 주문 경로에서 사용 중이라 유지했다.
- DB 테이블 생성 로직: 현재 직접 호출되지 않는 저장 메서드가 있어도 테이블은 collector/학습/실거래 전체 데이터 계약이라 유지했다.

## 현재 작업할 때 봐야 할 파일

설정 변경:

- `common/config/strategy_config.py`

데이터 수집:

- `collector/collector_main.py`
- `collector/ws_collector.py`
- `collector/rest_collector.py`
- `collector/synthesize_timeframes_main.py`

데이터셋/피처:

- `common/ml/scalping_1m_dataset.py`
- `common/ml/scalping_1m_scoring.py`

학습/검증:

- `trainer/train_scalping_1m_model.py`
- `trainer/validate_scalping_1m.py`

실거래:

- `main.py`
- `trader/trading_engine.py`
- `trader/trader_order_manager.py`
- `trader/close_event_batch_manager.py`

공통 인프라:

- `common/database/manager.py`
- `common/binance/binance_client.py`
- `common/binance/websocket_manager.py`
- `common/ipc/zmq_pubsub.py`
