# Kairos Trading Concept

이 문서는 Kairos의 현재 구현을 기준으로 정리한 운영/설계 문서다.
기존 문서에는 목표 아키텍처와 이상적인 분리 구조가 섞여 있었고, 실제 코드/DB 상태와 차이가 있었다.
이번 개정본은 먼저 "지금 실제로 어떻게 동작하는가"를 명확히 적고, 이후 재설계 포인트를 분리해서 다룬다.

---

## 1. Current Reality

현재 Kairos는 완전한 분산 컨테이너 아키텍처라기보다, 단일 런타임 엔진 안에 다음 책임이 함께 들어간 구조에 가깝다.

1. Binance WebSocket 수신
2. 초기 버퍼 로딩과 REST 보정
3. 실시간 feature 계산
4. 모델 추론과 메타 필터 적용
5. 주문 실행과 TP/SL 관리
6. DB 동기화와 운영 상태 저장

즉, 현재 시스템의 핵심 운영 단위는 `trader/trading_engine.py`의 `KairosEngine`이다.

---

## 2. Current DB Structure

현재 운영 DB의 public schema에는 다음 5개 테이블이 있다.

1. `candles`
2. `funding_rates`
3. `trades`
4. `system_state`
5. `sl_history`

실제 운영 데이터 기준 대략적인 규모는 다음과 같다.

- `candles`: 2,576,172 rows
- `funding_rates`: 87,131 rows
- `trades`: 24 rows
- `system_state`: 2 rows
- `sl_history`: 0 rows

또한 `candles`, `funding_rates`는 Timescale hypertable이다.

---

## 3. Table Definitions

### 3.1 `candles`

역할:
실매매와 학습의 공통 원본 시계열 저장소. 사실상 가장 중요한 source of truth다.

Primary Key:
`(timestamp, symbol)`

컬럼:

| Column | Type | Nullable | Meaning |
| --- | --- | --- | --- |
| `timestamp` | `timestamptz` | NO | 15분 봉 시각 |
| `symbol` | `varchar` | NO | 심볼 |
| `open` | `numeric` | NO | 시가 |
| `high` | `numeric` | NO | 고가 |
| `low` | `numeric` | NO | 저가 |
| `close` | `numeric` | NO | 종가 |
| `volume` | `numeric` | NO | 거래량 |
| `quote_volume` | `numeric` | NO | quote 기준 거래대금 |
| `open_interest` | `numeric` | YES | OI |
| `trades_count` | `integer` | YES | 체결 건수 |
| `taker_buy_base` | `numeric` | YES | taker buy base |
| `taker_buy_quote` | `numeric` | YES | taker buy quote |

특징:

- 시계열 원본 테이블이다.
- feature는 저장하지 않는다.
- 현재 1분봉 학습/검증은 `Scalping1MDatasetBuilder`가 `candles_1m`을 기반으로 feature를 계산한다.
- 최근 확인 기준 554개 심볼이 들어 있다.

현재 한계:

- `numeric` 위주라 계산용으로는 안전하지만, 대용량 분석 시 속도/저장 비용을 다시 검토할 수 있다.
- funding이 별도 테이블이기 때문에 로딩 시 `merge_asof`가 필요하다.

### 3.2 `funding_rates`

역할:
펀딩비 원본 저장소.

Primary Key:
`(timestamp, symbol)`

컬럼:

| Column | Type | Nullable | Meaning |
| --- | --- | --- | --- |
| `timestamp` | `timestamptz` | NO | funding 시각 |
| `symbol` | `varchar` | NO | 심볼 |
| `funding_rate` | `numeric` | NO | funding rate |

특징:

- `candles`와 직접 join된 구조는 아니다.
- 1분봉 스캘핑 경로에서는 `candles_15m` 합성 테이블의 문맥 피처가 필요할 때만 backward merge로 결합된다.
- 최근 데이터가 `candles`보다 덜 최신일 수 있다.

현재 한계:

- 실매매에서는 websocket 경로에서 funding을 직접 보정하고, 학습에서는 DB에서 merge한다.
- 즉 funding 데이터의 최신성과 결합 방식이 경로별로 완전히 동일하다고 보긴 어렵다.

### 3.3 `trades`

역할:
실제 진입/청산 결과와 진입 시점 snapshot을 저장하는 운영 로그 테이블.

Primary Key:
`id`

컬럼:

| Column | Type | Nullable | Meaning |
| --- | --- | --- | --- |
| `id` | `integer` | NO | trade row id |
| `symbol` | `text` | NO | 진입 심볼 |
| `entry_time` | `timestamptz` | NO | 진입 시각 |
| `exit_time` | `timestamptz` | YES | 청산 시각 |
| `entry_price` | `float8` | YES | 진입가 |
| `exit_price` | `float8` | YES | 청산가 |
| `qty` | `float8` | YES | 수량 |
| `leverage` | `int4` | YES | 레버리지 |
| `pnl` | `float8` | YES | 실현 손익 |
| `roe` | `float8` | YES | 수익률 |
| `exit_reason` | `text` | YES | TP/SL/SYNC_EXIT 등 |
| `entry_prob` | `float8` | YES | 진입 시 base 확률 |
| `features` | `jsonb` | YES | 진입 시점 snapshot |
| `status` | `text` | YES | `OPEN` / `CLOSED` |

특징:

- 이 테이블은 "학습용 메인 feature store"가 아니다.
- 현재는 실거래 로그와 진입 시점 컨텍스트를 남기는 용도다.
- `features`에는 진입 row 전체 또는 복구 시점 row가 JSONB로 들어간다.
- 최근 수정으로 `pred_proba`, `base_pred_proba`, `meta_pred_proba`도 같이 저장될 수 있다.

중요한 해석:

- 원본 시장 데이터는 `candles` / `funding_rates`에 있다.
- `trades.features`는 정규화된 재학습 테이블이 아니라 분석용 snapshot에 가깝다.
- 따라서 이 JSONB를 바로 재학습 파이프라인의 공식 입력 계약으로 쓰기에는 위험하다.

현재 한계:

- feature schema version이 없다.
- 어떤 모델 버전 기준 feature인지 명시 컬럼이 없다.
- inference용 feature와 research snapshot feature가 분리돼 있지 않다.
- base/meta threshold, model version, candidate filtering stage 같은 운영 메타데이터도 구조화돼 있지 않다.

### 3.4 `system_state`

역할:
엔진 전역 상태를 key-value 형태로 저장하는 간단한 운영 상태 테이블.

Primary Key:
`key`

컬럼:

| Column | Type | Nullable | Meaning |
| --- | --- | --- | --- |
| `key` | `text` | NO | 상태 키 |
| `val` | `float8` | YES | 숫자 상태값 |
| `val_text` | `text` | YES | 문자열 상태값 |
| `updated_at` | `timestamptz` | YES | 갱신 시각 |

현재 용도:

- `peak_balance`
- `last_reset_date`

특징:

- 전역 런타임 상태를 빠르게 복원하기 위한 최소 저장소다.
- 스키마 유연성은 높지만, 타입 안정성은 약하다.

### 3.5 `sl_history`

역할:
심볼별 손절 누적과 블랙리스트 판단을 위한 상태 테이블.

Primary Key:
`symbol`

컬럼:

| Column | Type | Nullable | Meaning |
| --- | --- | --- | --- |
| `symbol` | `text` | NO | 심볼 |
| `sl_count` | `int4` | YES | 누적 손절 횟수 |
| `last_sl_at` | `timestamptz` | YES | 마지막 손절 시각 |

특징:

- risk control 목적의 운영 보조 테이블이다.
- 현재는 비어 있지만, 코드에서는 블랙리스트 판단에 사용한다.

---

## 4. Current Data Flow

### 4.1 Market Data Flow

1. Binance에서 캔들/OI/funding 관련 데이터를 받는다.
2. `candles`에 OHLCV + OI + taker 정보를 저장한다.
3. `funding_rates`에는 funding만 별도 저장한다.
4. 학습 시에는 `candles`와 `funding_rates`를 다시 합쳐서 사용한다.
5. 실매매 시에는 메모리 buffer 중심으로 돌고, 일부 REST 보정이 들어간다.

### 4.2 Training Flow

1. `Scalping1MDatasetBuilder`가 `candles_1m`을 읽는다.
2. target label을 만든다.
3. `Scalping1MDatasetBuilder`가 1분봉 피처와 `candles_5m`/`candles_15m` 문맥 피처를 생성한다.
4. `prepare_features()`에서 `DROP_COLS`를 제거하고 학습 입력 X를 만든다.
5. base model과 meta model을 학습한다.

### 4.3 Live Trading Flow

1. 엔진이 메모리 candle buffer를 유지한다.
2. 마감 시점마다 현재 모델 버전 기준 feature를 생성한다.
3. base model 추론을 한다.
4. meta model이 있으면 `base_pred_proba`를 추가해 meta 추론을 한다.
5. threshold와 risk rule을 통과하면 주문한다.
6. 주문 시점 row를 `trades.features`에 저장한다.

---

## 5. What The Current DB Is Good At

- 원본 시계열 보관
- 학습 데이터 재생성
- 실제 체결 로그 보관
- 진입 시점 snapshot 복기
- 엔진 전역 상태 저장

즉, 현재 DB는 "운영 DB + 원본 데이터 저장소" 역할은 꽤 잘 수행하고 있다.

---

## 6. What The Current DB Is Not Yet

현재 DB는 아직 다음 역할까지는 못 한다.

### 6.1 Dedicated Feature Store

지금은 feature를 테이블로 정규화해 저장하지 않는다.
재학습은 항상 원본 데이터에서 다시 feature를 계산한다.

의미:

- 장점: feature 생성 로직만 맞으면 재현성이 좋다.
- 단점: 당시 실제 inference input을 정확히 장기 보존하는 구조는 아니다.

### 6.2 Research Snapshot Schema

`trades.features`는 JSONB snapshot이지만, 연구용 계약이 명확하지 않다.

빠진 것들:

- `feature_schema_version`
- `model_name`
- `model_feature_version`
- `snapshot_feature_version`
- `meta_threshold`
- `eval_threshold`
- candidate filtering stage 정보

### 6.3 Execution Audit Schema

현재 `trades` 하나에 entry/exit가 같이 들어간다.
하지만 주문 단위 audit trail은 없다.

예:

- signal generated
- order requested
- order acknowledged
- partial fill
- protective order placed
- protective order missing / repaired

이런 실행 이벤트 로그 테이블은 아직 없다.

---

## 7. Current Design Gaps

현재 가장 큰 설계 부채는 다음과 같다.

### 7.1 Inference Feature Set vs Snapshot Feature Set

가장 중요하다.

실매매 진입 판단에 쓰는 feature 세트와,
거래 후 분석/재학습에 참고할 snapshot feature 세트가 분리돼야 한다.

하지만 현재는 이 경계가 불명확하다.

원래 의도:

- inference: 현재 운영 모델에 맞는 최소/정확 feature
- snapshot: 더 넓은 연구용 feature

현재 현실:

- 두 책임이 코드상 섞여 있었다.
- 실제로 `v1`이 실매매 추론 경로에 섞여 들어간 적이 있었다.

### 7.2 Runtime State vs Analytical State

`system_state`, `sl_history`는 운영 상태다.
반면 `trades.features`는 분석 상태에 가깝다.

이 둘이 DB 안에서 혼재하지만,
문서 차원에서 명확히 구분되어 있지 않았다.

### 7.3 Model Lineage

현재 DB만 봐서는 각 거래가

- 어떤 base model
- 어떤 meta model
- 어떤 threshold
- 어떤 feature version

으로 실행되었는지 완전하게 복원하기 어렵다.

---

## 8. Recommended DB Direction

재설계 시 DB는 다음 3계층으로 나누는 것이 좋다.

### 8.1 Raw Market Layer

- `candles`
- `funding_rates`

이 계층은 지금처럼 유지 가능하다.

### 8.2 Runtime Trading Layer

- `trades`
- `trade_orders`
- `trade_events`
- `system_state`
- `sl_history`

실제 주문/체결/복구/보호주문 감사 로그까지 다루는 계층이다.

### 8.3 Research / Replay Layer

- `trade_feature_snapshots`
- `model_runs`
- `signal_decisions`

이 계층은 "나중에 왜 진입했는지, 무엇을 다시 학습할지"를 다루는 분석 계층이다.

---

## 9. Immediate Documentation Conclusion

현재 DB는 다음처럼 이해하면 가장 정확하다.

- `candles`, `funding_rates`: 학습과 실매매의 원본 데이터 저장소
- `trades`: 실제 거래 결과와 진입 당시 스냅샷 로그
- `system_state`, `sl_history`: 운영 상태 저장소

그리고 아직 없는 것은 다음이다.

- 정식 feature store
- inference/snapshot 분리 스키마
- 주문/체결 이벤트 audit schema
- model lineage 복원용 메타데이터 구조

---

## 10. Next Step

이 문서 다음 단계에서는 아래를 순서대로 정리한다.

1. 현재 코드 기준 컴포넌트 책임 분리
2. inference feature와 snapshot feature의 명세 분리
3. DB 신규 테이블 초안 정의
4. `AI_trading_concept.md`를 목표 구조 기준으로 다시 확장
