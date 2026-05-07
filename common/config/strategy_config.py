"""
StrategyConfig.py
=================
이 파일은 Kairos 프로젝트의 전략 관련 상수를 한곳에 모아둔 중앙 설정 파일입니다.

초급 개발자 기준으로 보면 이 파일은 "전략의 조정 손잡이 모음"입니다.
학습, 검증, 실거래 코드가 모두 같은 값을 공유해야 할 때
코드 곳곳에 숫자를 흩뿌리지 않고 여기서만 관리하도록 만든 것입니다.

여기에 모인 값들은 크게 다음 범주로 나뉩니다.
1. 데이터 로딩 범위와 라벨 기준
2. 리스크 관리 규칙
3. 피처 엔지니어링 파라미터
4. 실전 진입/차단 임계값
5. LightGBM 학습 파라미터

즉, 전략을 수정할 때는 보통 이 파일부터 먼저 보는 것이 맞습니다.
"""

from pathlib import Path

class StrategyConfig:
    """
    훈련, 검증, 실매매 전반에 걸쳐 공유되는 전략 및 모델 상수 집합입니다.

    이 클래스는 인스턴스를 만들어 쓰는 객체가 아니라,
    프로젝트 어디에서나 `StrategyConfig.SOME_VALUE` 형태로 참조하는 전역 설정 컨테이너입니다.
    """
    # --- 데이터 로딩 및 타겟 설정 ---
    TARGET_THRESHOLD = 0.05     # 익절 목표 (5%)
    LABEL_STOP_LOSS = 0.03      # 손절 임계값 (3%)
    FEE = 0.0005                # 거래 수수료 (0.05%)
    SLIPPAGE = 0.001            # 슬리피지 (0.1%)
    LIMIT_PER_SYMBOL = 47520    # 심볼당 최대 로드 캔들 수
    MIN_DATA_POINTS = 200       # 기술적 지표 계산을 위한 최소 데이터 건수
    CANDLE_LIMIT_LIVE = 250     # 실거래 엔진 메모리 버퍼 캔들 제한 (tail)
    TOP_COIN_COUNT = 15         # 실시간 감시 대상 상위 종목 수
    BLIND_TEST_DAYS = 3         # 최근 N일 데이터는 훈련에서 완전히 배제
    VAL_RATIO = 0.1             # 훈련 데이터 중 검증용 세트 비율
    QUOTE_ASSET = 'USDT'        # 주 거래 자산 (USDT 전용)
    
    # --- 리스크 관리 및 서킷 브레이커 ---
    DAILY_MDD_LIMIT = 0.30          # 고점 대비 30% 손실 시 일일 매매 중단
    MAX_SL_COUNT_FOR_BLACKLIST = 2  # 동일 종목 손절 누적 기준
    BLACKLIST_DURATION_HOURS = 2    # 동일 심볼 2연속 손절 후 진입 정지 시간 (2시간)
    GLOBAL_SL_COOLDOWN_MINUTES = 30  # 전체 계정 기준 SL 1회 후 신규 진입 정지 시간
    GLOBAL_SL_CIRCUIT_LOOKBACK_HOURS = 2  # 전체 SL 횟수 집계 구간
    GLOBAL_SL_CIRCUIT_THRESHOLD = 2  # 집계 구간 내 이 횟수 이상 SL이면 전체 진입 정지
    GLOBAL_SL_CIRCUIT_COOLDOWN_HOURS = 2  # 전체 SL 서킷 발동 후 신규 진입 정지 시간
    
    # 변동성 필터링 (죽은 구간 제외)
    MAX_SLOTS = 2               # 동시에 보유 가능한 최대 포지션 수 (슬롯)
    LEVERAGE = 5                # 레버리지 배수 (5배 적용)
    INITIAL_BALANCE = 100       # 시뮬레이션 시작 자금 (100 USDT)
    ENTRY_MARGIN_SAFETY_RATIO = 0.99  # 주문 가능 증거금 중 실제 진입에 사용할 비율
    
    # --- 모델 학습 (LightGBM) ---
    SCALPING_1M_MODEL_NAME = "v1_tp50_sl30_h120"
    SCALPING_1M_TARGET_TP = TARGET_THRESHOLD
    SCALPING_1M_STOP_LOSS = LABEL_STOP_LOSS
    SCALPING_1M_HORIZON_MINUTES = 120
    SCALPING_1M_MIN_DATA_POINTS = 300
    SCALPING_1M_SUPPORT_RESISTANCE_WINDOW = 120
    SCALPING_1M_SUPPORT_RESISTANCE_TOUCH_TOLERANCE = 0.003
    SCALPING_1M_SUPPORT_RESISTANCE_MIN_TOUCHES = 2
    SCALPING_1M_CANDIDATE_TOP_N = 15
    SCALPING_1M_SL_PENALTY_WEIGHT = 1.2
    SCALPING_1M_TIMEOUT_PENALTY = 0.0075
    SCALPING_1M_PUMP_MIN_CHANGE_24H = 0.20
    SCALPING_1M_PUMP_STRONG_CHANGE_24H = 0.40
    SCALPING_1M_PUMP_EXTREME_CHANGE_24H = 1.00
    SCALPING_1M_PUMP_MAX_AGE_HOURS = 72
    SCALPING_1M_MTF_LOOKBACK_DAYS = 3
    SCALPING_1M_MIN_TP_LIFT = -0.005
    SCALPING_1M_MAX_SL_LIFT = 0.02
    SCALPING_1M_MAX_TIMEOUT_LIFT = 0.30
    SCALPING_1M_MIN_TP_SL_LIFT_EDGE = 0.03
    SCALPING_1M_MIN_EXPECTED_PRICE_EDGE = 0.0010
    SCALPING_1M_REQUIRE_PUMP_BASE_CONDITION = True
    SCALPING_1M_MAX_TIMEOUT_PROB = 0.35
    SCALPING_1M_REQUIRE_MTF15_EMA_BULL = False
    SCALPING_1M_BLOCK_MTF5_TREND_TURN_UP = False
    SCALPING_1M_BLOCK_MTF5_TREND_CONTINUATION = False
    SCALPING_1M_REQUIRE_TP_ABOVE_SL = False
    SCALPING_1M_ENTRY_SCORE_THRESHOLD = 0.30
    SCALPING_1M_SL_RISK_WEIGHT = 0.7
    SCALPING_1M_TIMEOUT_RISK_WEIGHT = 0.003
    SCALPING_1M_DIAGNOSTIC_ENTRY_LIMIT = 500
    SCALPING_1M_MODEL_TARGET_OVERRIDES = {
        "tp35_sl20": {"target_tp": 0.035, "stop_loss": 0.02, "horizon_h60": 60, "horizon_h120": 120},
        "tp50_sl30": {"target_tp": 0.05, "stop_loss": 0.03, "horizon_h60": 60, "horizon_h120": 120},
        "v3_h120": {"target_tp": 0.05, "stop_loss": 0.03, "horizon_minutes": 120},
        "v3_h60": {"target_tp": 0.05, "stop_loss": 0.03, "horizon_minutes": 60},
        "v4_pump20_h120": {"target_tp": 0.05, "stop_loss": 0.03, "horizon_minutes": 120},
    }
    NUM_BOOST_ROUND = 5000      # 최대 부스팅 라운드 (학습률 0.01 대응을 위해 상향)
    EARLY_STOPPING_ROUNDS = 100 # 조기 종료 기준
    EVAL_THRESHOLD = 0.2      # V7 Full-Feature 모델용 (5% 펌핑 기준 0.55+)
    MARKETDATA_ZMQ_ENDPOINT = "tcp://127.0.0.1:5555"
    MARKETDATA_SYMBOLS_RPC_ENDPOINT = "tcp://127.0.0.1:5556"
    REST_SYNC_DELAY_SECONDS = 30
    POSITION_REST_SYNC_INTERVAL_SECONDS = 2  # 유저 스트림 누락 대비 거래소 포지션 REST 동기화 주기
    ACCOUNT_UPDATE_SYNC_DELAY_SECONDS = 0.5  # 계정 업데이트 후 주문 체결 이벤트를 기다리는 짧은 동기화 지연
    MISSING_POSITION_SYNC_EXIT_GRACE_SECONDS = 3.0  # 포지션 없음 감지 후 체결/손익 이벤트를 기다리는 시간
    FUNDING_RATE_CACHE_SECONDS = 1800  # 실시간 추론 중 funding rate REST 조회 캐시 유지 시간
    ENGINE_REST_MARKET_ENRICH_ENABLED = False  # 엔진 배치 직전 OI/funding REST 보정 사용 여부
    CLOSE_EVENT_BATCH_INTERVAL_SECONDS = 1
    CLOSE_EVENT_MAX_WAIT_SECONDS = 5
    COLLECTOR_REST_INTERVAL = "15m"
    COLLECTOR_PRIMARY_INTERVAL = "1m"
    COLLECTOR_INTRABAR_INTERVAL = "1m"
    COLLECTOR_WS_SUBSCRIBE_INTRABAR = False  # 1분봉 단일 웹소켓만 구독하므로 추가 intrabar 구독은 사용하지 않습니다.
    COLLECTOR_1M_REST_BACKFILL_MINUTES = 30
    COLLECTOR_WS_MIN_ACTIVE_RATIO = 0.8
    COLLECTOR_WS_RECONNECT_CONSECUTIVE_BATCHES = 2
    COLLECTOR_WS_RECONNECT_COOLDOWN_SECONDS = 300
    COLLECTOR_WS_FINALIZED_RETENTION_SECONDS = 300
    COLLECTOR_WS_MESSAGE_QUEUE_SIZE = 20000
    COLLECTOR_WS_QUEUE_BACKLOG_WARN_THRESHOLD = 5000
    COLLECTOR_WS_RECV_IDLE_TIMEOUT_SECONDS = 90
    COLLECTOR_WS_STREAM_CHUNK_SIZE = 100  # 한 multiplex 웹소켓에 묶을 최대 stream 수
    COLLECTOR_WS_USE_RAW_MARKET_STREAM = True  # python-binance 래퍼 대신 raw futures websocket 사용 여부
    COLLECTOR_WS_RAW_BASE_URL = "wss://fstream.binance.com/market/stream?streams="
    COLLECTOR_WS_RAW_MAX_QUEUE = 4096
    COLLECTOR_SYNTHESIZE_TIMEFRAMES_ENABLED = True  # collector에서 candles_1m 기반 5m/15m 합성 적재를 자동 실행합니다.
    COLLECTOR_SYNTH_5M_DELAY_SECONDS = 10  # 5분 경계 후 1분봉 close 저장을 기다리는 시간
    COLLECTOR_SYNTH_15M_DELAY_SECONDS = 240  # 15분 REST/OI 저장 완료 후 합성을 시작하기 위한 지연
    COLLECTOR_SYNTH_EMPTY_TABLE_LOOKBACK_HOURS = 24  # 합성 테이블이 비어 있을 때 자동 bootstrap할 최대 범위
    
    LGBM_PARAMS = {
        'objective': 'binary',
        'metric': ['auc', 'binary_logloss'],
        'boosting_type': 'gbdt',
        'num_leaves': 31,
        'learning_rate': 0.03,
        'feature_fraction': 0.7,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'min_data_in_leaf': 50,
        'lambda_l1': 1.0,
        'lambda_l2': 1.0,
        'verbose': -1,
        'seed': 42
    }
    
    # 모델 저장소 (프로젝트 루트 기준)
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    MODEL_DIR = BASE_DIR / "common" / "models"
