# 2026-05-07 작업 기록

================================================================================

## 웹 entry_features 문자 단위 표시 수정

- 요청:
  - 웹의 `[진입 시점 전체 피처]`가 `0: {`, `1: '`, `10: p`처럼 문자 인덱스 형태로 표시되는 문제 확인 및 수정.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
  - `web/api/app/services/scalping_backtest_service.py`
  - `web/ui/assets/app.js`
- 원인:
  - 백테스트 JSON 저장 시 `entry_features` dict가 `_json_safe_value()`에서 문자열로 변환됐다.
  - 웹 UI는 이 문자열에 `Object.entries()`를 적용해 문자열의 각 문자를 key/value처럼 표시했다.
- 변경 내용:
  - `_json_safe_value()`가 dict/list/tuple을 재귀적으로 JSON-safe 값으로 변환하도록 변경해 신규 리포트에서는 `entry_features`가 객체로 저장되게 했다.
  - web API 정규화 단계에서 구형 리포트의 Python dict 문자열을 `ast.literal_eval()`로 복구하도록 추가했다.
  - UI에서도 `entry_features`가 문자열이면 raw fallback으로 처리해 문자 단위 분해가 다시 발생하지 않게 했다.
- 검증:
  - `trainer/validate_scalping_1m.py`, `web/api/app/services/scalping_backtest_service.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
  - synthetic 거래 DataFrame 직렬화 결과 `entry_features`가 dict로 유지되는 것을 확인했다.
  - 기존 `scalping_1m_results_v1_tp50_sl30_h120_dual.trades.json`의 문자열 `entry_features`가 API 로딩 단계에서 dict로 복구되는 것을 확인했다.
- 실행하지 않은 작업:
  - 웹 서버, 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## 진입 gate에서 pump_age와 volume_accel 차단 제거

- 요청:
  - `pump_age <= 8h` 조건 제거.
  - `volume_accel_5m` 하한 조건 제거.
- 변경 파일:
  - `common/config/strategy_config.py`
  - `common/ml/scalping_1m_scoring.py`
  - `trainer/validate_scalping_1m.py`
- 변경 내용:
  - `StrategyConfig.SCALPING_1M_MAX_ENTRY_PUMP_AGE_HOURS` 상수를 삭제했다.
  - `StrategyConfig.SCALPING_1M_MIN_VOLUME_ACCEL_5M` 상수를 삭제했다.
  - `apply_entry_gate_with_risk()`의 `pump_episode_age_hours <= ...` 조건을 제거했다.
  - `apply_entry_gate_with_risk()`의 `volume_accel_5m >= ...` 조건을 제거했다.
  - breakout structure gate 내부의 `volume_accel_5m >= -0.20` 조건도 제거했다.
  - Blind 검증 리포트의 Hard Gate 출력에서 두 조건을 제거했다.
- 남겨둔 것:
  - `pump_episode_age_hours`, `volume_accel_5m` 피처 자체는 모델 입력과 진단용으로 계속 남겼다.
  - 제거한 것은 매수를 차단하는 hard gate 조건이다.
- 검증:
  - 관련 상수와 gate 출력 참조가 코드에서 제거됐음을 확인했다.
  - `common/config/strategy_config.py`, `common/ml/scalping_1m_scoring.py`, `trainer/validate_scalping_1m.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
  - synthetic 후보로 `pump_episode_age_hours=36h`, `volume_accel_5m=-9.0`이어도 다른 조건이 맞으면 gate를 통과하는 것을 확인했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## 백테스트 리포트 파일명 단축

- 요청:
  - 백테스트 Markdown 파일명을 `scalping_1m_results_v1_tp35_sl20_h60_dual.md` 같은 긴 형태가 아니라 `v1_tp35_sl20_h60_dual.md` 형태로 모델명에 맞춰 단축.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
  - `web/api/app/services/scalping_backtest_service.py`
- 변경 내용:
  - 신규 Blind 검증 리포트 저장 경로를 `scalping_1m_results_{model_name}_dual.md`에서 `{model_name}_dual.md`로 변경했다.
  - 웹 API의 리포트 목록/조회 허용 패턴을 보강해 신규 짧은 파일명과 기존 긴 파일명을 모두 읽을 수 있게 했다.
  - 기존 생성 파일 중 현재 사용 중인 `v1` 리포트와 거래 JSON을 짧은 이름으로 이동했다.
    - `scalping_1m_results_v1_tp35_sl20_h60_dual.md` -> `v1_tp35_sl20_h60_dual.md`
    - `scalping_1m_results_v1_tp35_sl20_h60_dual.trades.json` -> `v1_tp35_sl20_h60_dual.trades.json`
    - `scalping_1m_results_v1_tp50_sl30_h120_dual.md` -> `v1_tp50_sl30_h120_dual.md`
    - `scalping_1m_results_v1_tp50_sl30_h120_dual.trades.json` -> `v1_tp50_sl30_h120_dual.trades.json`
- 검증:
  - 새 짧은 리포트 파일과 JSON 파일이 존재함을 확인했다.
  - 기존 `scalping_1m_results_v1_tp35_sl20_h60_dual.md`가 더 이상 남아 있지 않음을 확인했다.
  - `trainer/validate_scalping_1m.py`, `web/api/app/services/scalping_backtest_service.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.

================================================================================

## 수정/보완 기록 - 거래 복기 차트 이동/호버 기능

- 보완 사유:
  - 거래 복기 차트 이동/호버 기능 기록이 기존 파일의 중간 위치에도 남아 있어, append-only 원칙에 맞춰 동일 작업의 최신 요약을 파일 하단에 다시 명시한다.
- 최종 변경 파일:
  - `web/api/app/services/scalping_backtest_service.py`
  - `web/api/app/main.py`
  - `web/ui/index.html`
  - `web/ui/assets/app.js`
  - `web/ui/assets/styles.css`
- 최종 변경 내용:
  - 거래 복기 기본 padding을 180분으로 확대했다.
  - replay API가 `padding_minutes` query parameter를 받도록 했다.
  - 거래 차트에 120개 캔들 window, 좌우 이동 slider, hover tooltip을 추가했다.
  - ENTRY 캔들 hover 시 모델 판단값과 핵심 진입 피처 요약을 표시하도록 했다.
  - CSS/JS asset cache 버전을 `20260507-trade-window1`로 갱신했다.
- 검증:
  - `python -m py_compile web/api/app/main.py web/api/app/services/scalping_backtest_service.py` 통과.
  - `node --check web/ui/assets/app.js` 통과.
- 실행하지 않은 작업:
  - 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.

================================================================================

## 수정/보완 기록 - 거래 복기 차트 이동/호버 기능

- 보완 사유:
  - 거래 복기 차트 이동/호버 기능 기록이 기존 파일의 중간 위치에도 남아 있어, append-only 원칙에 맞춰 동일 작업의 최신 요약을 파일 하단에 다시 명시한다.
- 최종 변경 파일:
  - `web/api/app/services/scalping_backtest_service.py`
  - `web/api/app/main.py`
  - `web/ui/index.html`
  - `web/ui/assets/app.js`
  - `web/ui/assets/styles.css`
- 최종 변경 내용:
  - 거래 복기 기본 padding을 180분으로 확대했다.
  - replay API가 `padding_minutes` query parameter를 받도록 했다.
  - 거래 차트에 120개 캔들 window, 좌우 이동 slider, hover tooltip을 추가했다.
  - ENTRY 캔들 hover 시 모델 판단값과 핵심 진입 피처 요약을 표시하도록 했다.
  - CSS/JS asset cache 버전을 `20260507-trade-window1`로 갱신했다.
- 검증:
  - `python -m py_compile web/api/app/main.py web/api/app/services/scalping_backtest_service.py` 통과.
  - `node --check web/ui/assets/app.js` 통과.
- 실행하지 않은 작업:
  - 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.

================================================================================

## 거래 복기 차트 이동/호버 기능 추가

- 요청:
  - 웹의 거래 상세 `candleChart`도 `label-chart-canvas large`처럼 더 많은 캔들을 보여주고, 좌우로 이동하며, hover tooltip으로 진입 근거를 볼 수 있게 변경.
  - 거래 복기 기본 padding을 180분으로 확대.
- 변경 파일:
  - `web/api/app/services/scalping_backtest_service.py`
  - `web/api/app/main.py`
  - `web/ui/index.html`
  - `web/ui/assets/app.js`
  - `web/ui/assets/styles.css`
- 변경 내용:
  - 거래 복기 API의 기본 `padding_minutes`를 30분에서 180분으로 확대했다.
  - `/api/scalping-1m/reports/{report_name}/trades/{trade_index}/replay`에서 query parameter `padding_minutes`를 받을 수 있게 했다.
  - 웹 거래 차트에 tooltip 레이어와 range slider를 추가했다.
  - 거래 차트는 전체 캔들을 한 번에 찌그러뜨리지 않고 120개 캔들 window로 보여주며, slider로 좌우 이동한다.
  - 거래 선택 시 ENTRY 근처가 먼저 보이도록 초기 window 위치를 계산한다.
  - hover tooltip에 KST 시간, OHLC, 캔들 수익률, 변동폭, 몸통/꼬리 비율, 거래량을 표시한다.
  - ENTRY 캔들 hover 시 모델 판단값과 주요 진입 피처 요약을 추가로 표시한다.
  - 창 크기 변경 시 기존 차트를 지우는 대신 현재 거래 복기 차트를 다시 렌더링하도록 했다.
  - CSS/JS 캐시 버전을 갱신했다.
- 검증:
  - `python -m py_compile web/api/app/main.py web/api/app/services/scalping_backtest_service.py` 통과.
  - `node --check web/ui/assets/app.js` 통과.
- 실행하지 않은 작업:
  - 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.

================================================================================

## 표시/리포트 시간 기준 KST 명시

- 요청:
  - DB 저장과 내부 계산은 UTC를 유지하되, 사용자가 보는 표시/리포트 기준 시간은 KST로 변환하고 `AI_RULES.md`에도 명시.
- 변경 파일:
  - `AI_RULES.md`
  - `trainer/validate_scalping_1m.py`
  - `web/api/app/services/scalping_backtest_service.py`
  - `web/ui/assets/app.js`
  - `web/ui/index.html`
- 변경 내용:
  - `AI_RULES.md`에 시간 기준 정책을 추가했다.
    - DB 저장 및 내부 계산 timestamp는 UTC 유지.
    - 웹 UI, Markdown 리포트, 거래 상세, 툴팁, 요약 출력은 KST 기준으로 표시.
  - 백테스트 거래 JSON 저장 시 `entry_time_kst`, `exit_time_kst`, `generated_at_kst`, `display_timezone`, `storage_timezone`을 포함하도록 했다.
  - Markdown 리포트 상단에 시간 기준 설명과 Blind 표시 구간(KST)을 추가했다.
  - 웹 API가 구형 JSON처럼 `entry_time_kst`가 없는 거래 row를 읽더라도 UTC 원본을 KST ISO 문자열로 변환하도록 했다.
  - 웹 거래 목록/상세/차트 메타와 리포트 수정 시각 표시에는 `(KST)`를 붙이고, 짧은 시간 포맷도 `Asia/Seoul`로 고정했다.
  - 웹 JS 캐시 버전을 갱신했다.
- 검증:
  - `trainer/validate_scalping_1m.py`, `web/api/app/services/scalping_backtest_service.py` 문법 검사를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.

================================================================================

## CANDIDATE 미진입 사유 기록 추가

- 요청:
  - 웹 차트의 `CANDIDATE`는 그대로 두되, 왜 실제 `ENTRY`가 되지 않았는지 확인할 수 있게 개선.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
  - `web/ui/assets/app.js`
  - `web/ui/index.html`
- 변경 내용:
  - 백테스트 시뮬레이션 과정에서 실제 진입 파이프라인을 따라가며 `diagnostic_entries`를 생성하도록 변경했다.
  - 각 `CANDIDATE`에 아래 필드를 추가했다.
    - `entry_decision`
    - `miss_reason`
    - `blocked_by`
    - `candidate_stage`
    - `candidate_rank`
    - `active_slots`
    - `max_slots`
    - `active_positions`
    - `would_pass_active_volatility`
    - `would_pass_entry_gate`
    - `would_pass_cooldown`
  - 기록되는 대표 미진입 사유를 추가했다.
    - `DAILY_MDD_BLOCK`
    - `SLOT_FULL`
    - `ENTRY_GATE_FAIL`
    - `HELD_SYMBOL`
    - `COOLDOWN_BLOCK`
    - `RANK_NOT_SELECTED`
  - 웹 tooltip에서 `미진입 사유`, `차단 근거`, `판단 단계`, `슬롯`을 표시하도록 했다.
  - 같은 분에 `ENTRY`와 `CANDIDATE`가 겹치면 `ENTRY`가 우선 표시되도록 했다.
  - 브라우저 캐시 회피를 위해 `app.js` asset 버전을 `20260507-candidate-reason1`로 갱신했다.
- 주의:
  - 기존 리포트 JSON에는 새 필드가 없으므로, 이 개선은 다음 Blind 검증 실행 결과부터 웹에서 확인할 수 있다.
- 검증:
  - `trainer/validate_scalping_1m.py`, `web/api/app/services/scalping_backtest_service.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.

================================================================================

## CANDIDATE 미진입 사유 기록 추가

- 요청:
  - 웹 차트의 `CANDIDATE`는 그대로 두되, 왜 실제 `ENTRY`가 되지 않았는지 확인할 수 있게 개선.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
  - `web/ui/assets/app.js`
  - `web/ui/index.html`
- 변경 내용:
  - 백테스트 시뮬레이션 과정에서 실제 진입 파이프라인을 따라가며 `diagnostic_entries`를 생성하도록 변경했다.
  - 각 `CANDIDATE`에 아래 필드를 추가했다.
    - `entry_decision`
    - `miss_reason`
    - `blocked_by`
    - `candidate_stage`
    - `candidate_rank`
    - `active_slots`
    - `max_slots`
    - `active_positions`
    - `would_pass_active_volatility`
    - `would_pass_entry_gate`
    - `would_pass_cooldown`
  - 기록되는 대표 미진입 사유를 추가했다.
    - `DAILY_MDD_BLOCK`
    - `SLOT_FULL`
    - `ENTRY_GATE_FAIL`
    - `HELD_SYMBOL`
    - `COOLDOWN_BLOCK`
    - `RANK_NOT_SELECTED`
  - 웹 tooltip에서 `미진입 사유`, `차단 근거`, `판단 단계`, `슬롯`을 표시하도록 했다.
  - 같은 분에 `ENTRY`와 `CANDIDATE`가 겹치면 `ENTRY`가 우선 표시되도록 했다.
  - 브라우저 캐시 회피를 위해 `app.js` asset 버전을 `20260507-candidate-reason1`로 갱신했다.
- 주의:
  - 기존 리포트 JSON에는 새 필드가 없으므로, 이 개선은 다음 Blind 검증 실행 결과부터 웹에서 확인할 수 있다.
- 검증:
  - `trainer/validate_scalping_1m.py`, `web/api/app/services/scalping_backtest_service.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.

================================================================================

## SL 방어 게이트 변경 기록 위치 보완

- 보완 사유:
  - 이번 작업의 핵심 내용은 `common/config/strategy_config.py`의 SL 방어 게이트 강화다.
  - 작업 기록 누적 원칙에 맞춰 파일 하단에도 동일 변경 요약을 명확히 남긴다.
- 최종 적용값:
  - `SCALPING_1M_MAX_SL_LIFT = 0.02`
  - `SCALPING_1M_MIN_TP_SL_LIFT_EDGE = 0.03`
  - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE = 0.0010`
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL = True`
- 쿨다운 상태:
  - Blind 검증 ON 모드에는 동일 심볼 연속 SL 블랙리스트가 적용되어 있다.
  - 실거래 엔진에는 전역 SL 쿨다운/서킷브레이커가 존재한다.
  - 현재 Blind 검증 시뮬레이터에는 전역 SL 쿨다운/서킷브레이커가 아직 적용되어 있지 않다.

================================================================================

## 웹 Top 15 라벨 TP/SL 문구 설정값 연동

- 요청:
  - Top 15 라벨 맵 tooltip과 설명에 남아 있던 `TP 5%`, `SL -3%` 하드코딩 문구 수정.
- 변경 파일:
  - `web/ui/assets/app.js`
  - `web/ui/index.html`
  - `web/api/app/services/scalping_backtest_service.py`
- 변경 내용:
  - Top 15 라벨 API 응답의 `target_tp`, `stop_loss`, `horizon_minutes`를 UI 상태에 저장하도록 했다.
  - 캔들 hover tooltip의 TP/SL 설명이 현재 설정값을 사용하도록 변경했다.
    - 예: `v1_tp35_sl20_h60` 기준 `+3.5%`, `-2.0%`, `60분`으로 표시.
  - Top 15 라벨 섹션 설명 문구도 고정 숫자 대신 현재 설정 TP/SL 기준이라고 표현했다.
  - 브라우저 캐시 회피를 위해 `app.js` asset 버전을 `20260507-label-config1`로 갱신했다.
  - API 서비스 docstring의 고정 TP/SL 숫자도 현재 설정 기준으로 수정했다.
- 검증:
  - 관련 파일에서 `TP 5%`, `+5%`, `SL -3%`, `-3%` 하드코딩 문구가 제거됐음을 확인했다.
  - `web/api/app/services/scalping_backtest_service.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 웹 서버는 실행하지 않았다.

================================================================================

## TP 우위 강제 게이트 완화

- 요청:
  - SL 방어 게이트 적용 후 `v1_tp35_sl20_h60` 진입이 2건까지 줄어든 상태라, 진입 수 회복을 위해 가장 강한 차단 조건을 완화.
- 변경 파일:
  - `common/config/strategy_config.py`
- 변경 내용:
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL`을 `True`에서 `False`로 되돌렸다.
  - 아래 SL 방어 게이트는 유지했다.
    - `SCALPING_1M_MAX_SL_LIFT = 0.02`
    - `SCALPING_1M_MIN_TP_SL_LIFT_EDGE = 0.03`
    - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE = 0.0010`
- 이유:
  - 현재 모델의 baseline이 `p_sl > p_tp` 구조라 `p_tp > p_sl` 강제 조건이 후보 대부분을 제거했다.
  - SL lift/TP-SL lift edge/기대값 방어는 유지하면서, 절대 확률 비교만 풀어 중간 수준의 진입 수를 확인하기 위한 조정이다.
- 검증:
  - `StrategyConfig` import로 최종 값이 `0.02`, `0.03`, `0.001`, `False`로 로드됨을 확인했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## TP 우위 강제 게이트 완화

- 요청:
  - SL 방어 게이트 적용 후 `v1_tp35_sl20_h60` 진입이 2건까지 줄어든 상태라, 진입 수 회복을 위해 가장 강한 차단 조건을 완화.
- 변경 파일:
  - `common/config/strategy_config.py`
- 변경 내용:
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL`을 `True`에서 `False`로 되돌렸다.
  - 아래 SL 방어 게이트는 유지했다.
    - `SCALPING_1M_MAX_SL_LIFT = 0.02`
    - `SCALPING_1M_MIN_TP_SL_LIFT_EDGE = 0.03`
    - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE = 0.0010`
- 이유:
  - 현재 모델의 baseline이 `p_sl > p_tp` 구조라 `p_tp > p_sl` 강제 조건이 후보 대부분을 제거했다.
  - SL lift/TP-SL lift edge/기대값 방어는 유지하면서, 절대 확률 비교만 풀어 중간 수준의 진입 수를 확인하기 위한 조정이다.
- 검증:
  - `StrategyConfig` import로 최종 값이 `0.02`, `0.03`, `0.001`, `False`로 로드됨을 확인했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## Blind 검증 일일 MDD 신규 진입 차단 적용

- 요청:
  - 실매매와 동일하게 당일 최대 손실 제한을 Blind 검증에도 반영.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
- 변경 내용:
  - 실거래 엔진과 동일한 UTC 일자 기준으로 일일 리스크 날짜 키를 계산하는 `_utc_day_key()`를 추가했다.
  - `simulate_scalping_trades()`에서 UTC 일자가 바뀔 때 `daily_peak_balance`를 현재 잔고로 리셋하도록 했다.
  - 청산 손익 반영 후 일일 고점 잔고를 갱신하고, 신규 진입 직전에 당일 MDD를 계산하도록 했다.
  - 당일 MDD가 `StrategyConfig.DAILY_MDD_LIMIT` 이상이면 기존 포지션은 유지하되 신규 진입 후보 처리를 차단하도록 했다.
  - 필터 통계에 `daily_mdd_limit`, `max_daily_mdd`, `daily_mdd_blocked`, `daily_mdd_block_events`를 추가했다.
  - Blind 검증 리포트 설정 설명에 `Risk Gate` 항목을 추가했다.
- 검증:
  - `trainer/validate_scalping_1m.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 못한 확인:
  - 현재 쉘 기본 Python에서 `pandas` 모듈을 찾지 못해 import 기반 smoke test는 실행하지 못했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## Blind 검증 일일 MDD 신규 진입 차단 적용

- 요청:
  - 실매매와 동일하게 당일 최대 손실 제한을 Blind 검증에도 반영.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
- 변경 내용:
  - 실거래 엔진과 동일한 UTC 일자 기준으로 일일 리스크 날짜 키를 계산하는 `_utc_day_key()`를 추가했다.
  - `simulate_scalping_trades()`에서 UTC 일자가 바뀔 때 `daily_peak_balance`를 현재 잔고로 리셋하도록 했다.
  - 청산 손익 반영 후 일일 고점 잔고를 갱신하고, 신규 진입 직전에 당일 MDD를 계산하도록 했다.
  - 당일 MDD가 `StrategyConfig.DAILY_MDD_LIMIT` 이상이면 기존 포지션은 유지하되 신규 진입 후보 처리를 차단하도록 했다.
  - 필터 통계에 `daily_mdd_limit`, `max_daily_mdd`, `daily_mdd_blocked`, `daily_mdd_block_events`를 추가했다.
  - Blind 검증 리포트 설정 설명에 `Risk Gate` 항목을 추가했다.
- 검증:
  - `trainer/validate_scalping_1m.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 못한 확인:
  - 현재 쉘 기본 Python에서 `pandas` 모듈을 찾지 못해 import 기반 smoke test는 실행하지 못했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## SL 방어 게이트 변경 기록 위치 보완

- 보완 사유:
  - 이번 작업의 핵심 내용은 `common/config/strategy_config.py`의 SL 방어 게이트 강화다.
  - 작업 기록 누적 원칙에 맞춰 파일 하단에도 동일 변경 요약을 명확히 남긴다.
- 최종 적용값:
  - `SCALPING_1M_MAX_SL_LIFT = 0.02`
  - `SCALPING_1M_MIN_TP_SL_LIFT_EDGE = 0.03`
  - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE = 0.0010`
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL = True`
- 쿨다운 상태:
  - Blind 검증 ON 모드에는 동일 심볼 연속 SL 블랙리스트가 적용되어 있다.
  - 실거래 엔진에는 전역 SL 쿨다운/서킷브레이커가 존재한다.
  - 현재 Blind 검증 시뮬레이터에는 전역 SL 쿨다운/서킷브레이커가 아직 적용되어 있지 않다.

================================================================================

## 1분봉 SL 방어 게이트 1차 강화

- 요청:
  - `v1_tp35_sl20_h60` Blind 검증에서 SL 비율과 MDD가 과도하게 커진 상태라, SL을 줄이기 위한 1순위 방어 방안을 모두 적용.
- 변경 파일:
  - `common/config/strategy_config.py`
- 변경 내용:
  - `SCALPING_1M_MAX_SL_LIFT`를 `0.05`에서 `0.02`로 낮췄다.
    - 모델 기준 SL 확률 상승폭이 큰 후보를 더 강하게 차단하기 위한 변경이다.
  - `SCALPING_1M_MIN_TP_SL_LIFT_EDGE`를 `0.0`에서 `0.03`으로 높였다.
    - TP lift가 SL lift보다 충분히 우위에 있을 때만 진입시키기 위한 변경이다.
  - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE`를 `0.0005`에서 `0.0010`으로 높였다.
    - TP/SL 가격폭을 반영한 기대값이 얇은 진입을 줄이기 위한 변경이다.
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL`을 `False`에서 `True`로 변경했다.
    - 모델이 SL 확률을 TP 확률보다 높게 보는 후보를 진입 금지하기 위한 변경이다.
- 쿨다운 확인:
  - `trainer/validate_scalping_1m.py`의 ON 검증에는 동일 심볼 연속 SL 블랙리스트가 이미 적용되어 있다.
  - `trader/trading_engine.py`에는 전역 SL 쿨다운과 전역 SL 서킷브레이커가 존재한다.
  - 다만 전역 SL 쿨다운/서킷브레이커는 현재 Blind 검증 시뮬레이터에는 반영되어 있지 않다.
- 검증:
  - `StrategyConfig` import로 네 설정값이 각각 `0.02`, `0.03`, `0.001`, `True`로 로드됨을 확인했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## TP3.5 SL2 h60 기본 모델 전환

- 요청:
  - 진입점은 유지하되 TP/SL이 불만족스러워, TP 3.5%, SL 2%, horizon 60분 모델을 확인할 수 있게 기본 설정 변경.
- 변경 파일:
  - `common/config/strategy_config.py`
  - `common/docs/answer/20260506_project_logic_reminder.md`
- 변경 내용:
  - `TARGET_THRESHOLD`를 `0.035`로 변경했다.
  - `LABEL_STOP_LOSS`를 `0.02`로 변경했다.
  - `SCALPING_1M_MODEL_NAME`을 `v1_tp35_sl20_h60`으로 변경했다.
  - `SCALPING_1M_HORIZON_MINUTES`를 `60`으로 변경했다.
  - `SCALPING_1M_MODEL_TARGET_OVERRIDES`에 `tp35_sl20` 계열을 추가해 명시 모델명에도 TP/SL/h60/h120 해석이 일관되게 동작하도록 했다.
  - 프로젝트 리마인드 문서의 현재 기본값과 학습/검증 명령 예시를 `v1_tp35_sl20_h60` 기준으로 갱신했다.
- 검증:
  - `common/config/strategy_config.py`, `common/ml/scalping_1m_dataset.py`, `trainer/train_scalping_1m_model.py`, `trainer/validate_scalping_1m.py`, web API 파일 문법 검사를 통과했다.
  - `build_scalping_1m_config()`가 `model=v1_tp35_sl20_h60`, TP `0.035`, SL `0.02`, horizon `60`으로 생성됨을 확인했다.
  - web API 기본값도 `model=v1_tp35_sl20_h60`으로 반환됨을 확인했다.
  - `common/models`에 동일한 짧은 모델명 산출물이 아직 없음을 확인했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## SL 방어 게이트 변경 기록 위치 보완

- 보완 사유:
  - 이번 작업의 핵심 내용은 `common/config/strategy_config.py`의 SL 방어 게이트 강화다.
  - 작업 기록 누적 원칙에 맞춰 파일 하단에도 동일 변경 요약을 명확히 남긴다.
- 최종 적용값:
  - `SCALPING_1M_MAX_SL_LIFT = 0.02`
  - `SCALPING_1M_MIN_TP_SL_LIFT_EDGE = 0.03`
  - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE = 0.0010`
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL = True`
- 쿨다운 상태:
  - Blind 검증 ON 모드에는 동일 심볼 연속 SL 블랙리스트가 적용되어 있다.
  - 실거래 엔진에는 전역 SL 쿨다운/서킷브레이커가 존재한다.
  - 현재 Blind 검증 시뮬레이터에는 전역 SL 쿨다운/서킷브레이커가 아직 적용되어 있지 않다.

================================================================================

## Blind 검증 일일 MDD 신규 진입 차단 적용

- 요청:
  - 실매매와 동일하게 당일 최대 손실 제한을 Blind 검증에도 반영.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
- 변경 내용:
  - 실거래 엔진과 동일한 UTC 일자 기준으로 일일 리스크 날짜 키를 계산하는 `_utc_day_key()`를 추가했다.
  - `simulate_scalping_trades()`에서 UTC 일자가 바뀔 때 `daily_peak_balance`를 현재 잔고로 리셋하도록 했다.
  - 청산 손익 반영 후 일일 고점 잔고를 갱신하고, 신규 진입 직전에 당일 MDD를 계산하도록 했다.
  - 당일 MDD가 `StrategyConfig.DAILY_MDD_LIMIT` 이상이면 기존 포지션은 유지하되 신규 진입 후보 처리를 차단하도록 했다.
  - 필터 통계에 `daily_mdd_limit`, `max_daily_mdd`, `daily_mdd_blocked`, `daily_mdd_block_events`를 추가했다.
  - Blind 검증 리포트 설정 설명에 `Risk Gate` 항목을 추가했다.
- 검증:
  - `trainer/validate_scalping_1m.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 못한 확인:
  - 현재 쉘 기본 Python에서 `pandas` 모듈을 찾지 못해 import 기반 smoke test는 실행하지 못했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## 웹 Top 15 라벨 TP/SL 문구 설정값 연동

- 요청:
  - Top 15 라벨 맵 tooltip과 설명에 남아 있던 `TP 5%`, `SL -3%` 하드코딩 문구 수정.
- 변경 파일:
  - `web/ui/assets/app.js`
  - `web/ui/index.html`
  - `web/api/app/services/scalping_backtest_service.py`
- 변경 내용:
  - Top 15 라벨 API 응답의 `target_tp`, `stop_loss`, `horizon_minutes`를 UI 상태에 저장하도록 했다.
  - 캔들 hover tooltip의 TP/SL 설명이 현재 설정값을 사용하도록 변경했다.
    - 예: `v1_tp35_sl20_h60` 기준 `+3.5%`, `-2.0%`, `60분`으로 표시.
  - Top 15 라벨 섹션 설명 문구도 고정 숫자 대신 현재 설정 TP/SL 기준이라고 표현했다.
  - 브라우저 캐시 회피를 위해 `app.js` asset 버전을 `20260507-label-config1`로 갱신했다.
  - API 서비스 docstring의 고정 TP/SL 숫자도 현재 설정 기준으로 수정했다.
- 검증:
  - 관련 파일에서 `TP 5%`, `+5%`, `SL -3%`, `-3%` 하드코딩 문구가 제거됐음을 확인했다.
  - `web/api/app/services/scalping_backtest_service.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 웹 서버는 실행하지 않았다.

================================================================================

## TP 우위 강제 게이트 완화

- 요청:
  - SL 방어 게이트 적용 후 `v1_tp35_sl20_h60` 진입이 2건까지 줄어든 상태라, 진입 수 회복을 위해 가장 강한 차단 조건을 완화.
- 변경 파일:
  - `common/config/strategy_config.py`
- 변경 내용:
  - `SCALPING_1M_REQUIRE_TP_ABOVE_SL`을 `True`에서 `False`로 되돌렸다.
  - 아래 SL 방어 게이트는 유지했다.
    - `SCALPING_1M_MAX_SL_LIFT = 0.02`
    - `SCALPING_1M_MIN_TP_SL_LIFT_EDGE = 0.03`
    - `SCALPING_1M_MIN_EXPECTED_PRICE_EDGE = 0.0010`
- 이유:
  - 현재 모델의 baseline이 `p_sl > p_tp` 구조라 `p_tp > p_sl` 강제 조건이 후보 대부분을 제거했다.
  - SL lift/TP-SL lift edge/기대값 방어는 유지하면서, 절대 확률 비교만 풀어 중간 수준의 진입 수를 확인하기 위한 조정이다.
- 검증:
  - `StrategyConfig` import로 최종 값이 `0.02`, `0.03`, `0.001`, `False`로 로드됨을 확인했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진은 실행하지 않았다.

================================================================================

## 백테스트 리포트 파일명 단축

- 요청:
  - 백테스트 Markdown 파일명을 `scalping_1m_results_v1_tp35_sl20_h60_dual.md` 같은 긴 형태가 아니라 `v1_tp35_sl20_h60_dual.md` 형태로 모델명에 맞춰 단축.
- 변경 파일:
  - `trainer/validate_scalping_1m.py`
  - `web/api/app/services/scalping_backtest_service.py`
- 변경 내용:
  - 신규 Blind 검증 리포트 저장 경로를 `scalping_1m_results_{model_name}_dual.md`에서 `{model_name}_dual.md`로 변경했다.
  - 웹 API의 리포트 목록/조회 허용 패턴을 보강해 신규 짧은 파일명과 기존 긴 파일명을 모두 읽을 수 있게 했다.
  - 기존 생성 파일 중 현재 사용 중인 `v1` 리포트와 거래 JSON을 짧은 이름으로 이동했다.
    - `scalping_1m_results_v1_tp35_sl20_h60_dual.md` -> `v1_tp35_sl20_h60_dual.md`
    - `scalping_1m_results_v1_tp35_sl20_h60_dual.trades.json` -> `v1_tp35_sl20_h60_dual.trades.json`
    - `scalping_1m_results_v1_tp50_sl30_h120_dual.md` -> `v1_tp50_sl30_h120_dual.md`
    - `scalping_1m_results_v1_tp50_sl30_h120_dual.trades.json` -> `v1_tp50_sl30_h120_dual.trades.json`
- 검증:
  - 새 짧은 리포트 파일과 JSON 파일이 존재함을 확인했다.
  - 기존 `scalping_1m_results_v1_tp35_sl20_h60_dual.md`가 더 이상 남아 있지 않음을 확인했다.
  - `trainer/validate_scalping_1m.py`, `web/api/app/services/scalping_backtest_service.py` 문법 검사를 통과했다.
  - `git diff --check`를 통과했다.
- 실행하지 않은 작업:
  - 학습, 검증 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.

================================================================================

## 수정/보완 기록 - 거래 복기 차트 이동/호버 기능

- 보완 사유:
  - 거래 복기 차트 이동/호버 기능 기록이 기존 파일의 중간 위치에도 남아 있어, append-only 원칙에 맞춰 동일 작업의 최신 요약을 파일 하단에 다시 명시한다.
- 최종 변경 파일:
  - `web/api/app/services/scalping_backtest_service.py`
  - `web/api/app/main.py`
  - `web/ui/index.html`
  - `web/ui/assets/app.js`
  - `web/ui/assets/styles.css`
- 최종 변경 내용:
  - 거래 복기 기본 padding을 180분으로 확대했다.
  - replay API가 `padding_minutes` query parameter를 받도록 했다.
  - 거래 차트에 120개 캔들 window, 좌우 이동 slider, hover tooltip을 추가했다.
  - ENTRY 캔들 hover 시 모델 판단값과 핵심 진입 피처 요약을 표시하도록 했다.
  - CSS/JS asset cache 버전을 `20260507-trade-window1`로 갱신했다.
- 검증:
  - `python -m py_compile web/api/app/main.py web/api/app/services/scalping_backtest_service.py` 통과.
  - `node --check web/ui/assets/app.js` 통과.
- 실행하지 않은 작업:
  - 백테스트, 실거래 엔진, 웹 서버는 실행하지 않았다.
