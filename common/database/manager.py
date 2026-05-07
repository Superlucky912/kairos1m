"""
DBManager.py
============
이 파일은 프로젝트의 모든 영속성 저장을 담당하는 데이터베이스 계층입니다.

초급 개발자 기준으로 보면 이 클래스는 "DB와 직접 대화하는 창구"입니다.
상위 로직은 SQL을 매번 직접 작성하지 않고,
캔들 저장, 트레이드 기록, 리스크 상태 조회 같은 목적별 메서드를 통해 DB를 사용합니다.

핵심 역할은 다음과 같습니다.
1. PostgreSQL / TimescaleDB 연결 관리
2. 캔들, 펀딩비, 트레이드 같은 핵심 데이터 저장
3. 엔진 상태와 손절 이력 같은 운영 상태 조회/갱신
4. 실거래와 학습 코드가 공통으로 쓰는 조회 인터페이스 제공

즉, 이 파일은 단순 CRUD 모음이 아니라
"프로젝트가 무엇을 어디에 저장하는가"를 일관되게 관리하는 영속성 계층입니다.
"""

from __future__ import annotations

import json
import uuid
import warnings
from datetime import datetime
from typing import Any

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from common.config.base_config import Config
from common.utils.logger import logger

# pandas.read_sql 관련 DBAPI2 경고 억제
warnings.filterwarnings('ignore', message='.*pandas only supports SQLAlchemy.*')

class DBManager:
    """프로젝트 전반의 DB 연결과 저장/조회 작업을 묶는 데이터 접근 계층."""

    def __init__(self):
        """환경 설정에서 DB 접속 정보를 읽어 연결 객체의 초기 상태를 준비합니다."""
        self.host = Config.DB_HOST
        self.port = Config.DB_PORT
        self.db_name = Config.DB_NAME
        self.user = Config.DB_USER
        self.password = Config.DB_PASSWORD
        self.conn: Any | None = None
        logger.info(f"DBManager 초기화 ({self.host}:{self.port}/{self.db_name})")

    def _ensure_conn(self) -> Any:
        """연결이 닫혀 있으면 다시 열고, 이후 사용할 수 있는 DB 연결 객체를 보장합니다."""
        if self.conn is None or self.conn.closed:
            self.connect()
        if self.conn is None:
            raise RuntimeError("DB 연결을 확보하지 못했습니다.")
        return self.conn

    def connect(self) -> None:
        """PostgreSQL 데이터베이스 연결을 열고 autocommit 모드로 준비합니다."""
        if self.conn is not None and not self.conn.closed:
            return
        try:
            self.conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                dbname=self.db_name,
                user=self.user,
                password=self.password
            )
            self.conn.autocommit = True
            logger.info("DB 연결 성공")
        except Exception as e:
            logger.error(f"DB 연결 실패: {e}")
            raise

    def close(self) -> None:
        """DB 연결을 닫습니다."""
        conn = self.conn
        self.conn = None
        if conn:
            conn.close()
            logger.info("DB 연결 종료")

    def init_market_data_tables(self) -> None:
        """시장 데이터 저장 테이블을 보장합니다."""
        conn = self._ensure_conn()

        queries = [
            """
            CREATE TABLE IF NOT EXISTS candles (
                timestamp TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                open DOUBLE PRECISION,
                high DOUBLE PRECISION,
                low DOUBLE PRECISION,
                close DOUBLE PRECISION,
                volume DOUBLE PRECISION,
                quote_volume DOUBLE PRECISION,
                open_interest DOUBLE PRECISION,
                trades_count BIGINT,
                taker_buy_base DOUBLE PRECISION,
                taker_buy_quote DOUBLE PRECISION,
                PRIMARY KEY (timestamp, symbol)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS candles_1m (
                timestamp TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                open DOUBLE PRECISION,
                high DOUBLE PRECISION,
                low DOUBLE PRECISION,
                close DOUBLE PRECISION,
                volume DOUBLE PRECISION,
                quote_volume DOUBLE PRECISION,
                open_interest DOUBLE PRECISION,
                trades_count BIGINT,
                taker_buy_base DOUBLE PRECISION,
                taker_buy_quote DOUBLE PRECISION,
                PRIMARY KEY (timestamp, symbol)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS candles_5m (
                timestamp TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                open DOUBLE PRECISION NOT NULL,
                high DOUBLE PRECISION NOT NULL,
                low DOUBLE PRECISION NOT NULL,
                close DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
                quote_volume DOUBLE PRECISION NOT NULL,
                open_interest DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                trades_count BIGINT NOT NULL DEFAULT 0,
                taker_buy_base DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                taker_buy_quote DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (timestamp, symbol)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS candles_15m (
                timestamp TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                open DOUBLE PRECISION NOT NULL,
                high DOUBLE PRECISION NOT NULL,
                low DOUBLE PRECISION NOT NULL,
                close DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
                quote_volume DOUBLE PRECISION NOT NULL,
                open_interest DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                trades_count BIGINT NOT NULL DEFAULT 0,
                taker_buy_base DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                taker_buy_quote DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (timestamp, symbol)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS funding_rates (
                timestamp TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                funding_rate DOUBLE PRECISION,
                PRIMARY KEY (timestamp, symbol)
            );
            """,
        ]

        try:
            with conn.cursor() as cur:
                for query in queries:
                    cur.execute(query)
                for table_name in ("candles", "candles_1m", "candles_5m", "candles_15m"):
                    cur.execute(
                        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_symbol_ts "
                        f"ON {table_name} (symbol, timestamp DESC)"
                    )
                    cur.execute(
                        f"CREATE INDEX IF NOT EXISTS {table_name}_timestamp_idx "
                        f"ON {table_name} (timestamp DESC)"
                    )
                for table_name in ("candles_1m", "candles_5m", "candles_15m"):
                    cur.execute(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                    )
                    cur.execute(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                    )
            logger.info("시장 데이터 테이블 초기화 완료")
        except Exception as e:
            logger.error(f"시장 데이터 테이블 초기화 중 오류 발생: {e}")

    def _save_candle_frame(self, df: pd.DataFrame, table_name: str) -> None:
        """캔들 DataFrame을 지정한 테이블에 벌크 업서트 방식으로 저장합니다."""
        if df.empty:
            return
        if table_name not in {"candles", "candles_1m", "candles_5m", "candles_15m"}:
            raise ValueError(f"허용되지 않은 캔들 테이블 이름입니다: {table_name}")

        conn = self._ensure_conn()
        update_audit_sql = ", updated_at = NOW()" if table_name in {"candles_1m", "candles_5m", "candles_15m"} else ""
        query = f"""
            INSERT INTO {table_name} (
                timestamp, symbol, open, high, low, close, volume, quote_volume, open_interest, trades_count,
                taker_buy_base, taker_buy_quote
            ) VALUES %s
            ON CONFLICT (timestamp, symbol) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
                volume = EXCLUDED.volume, quote_volume = EXCLUDED.quote_volume, open_interest = EXCLUDED.open_interest,
                trades_count = EXCLUDED.trades_count, taker_buy_base = EXCLUDED.taker_buy_base, taker_buy_quote = EXCLUDED.taker_buy_quote
                {update_audit_sql}
        """
        required_cols = ['timestamp', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'quote_volume', 'open_interest', 'trades_count', 'taker_buy_base', 'taker_buy_quote']
        for col in required_cols:
            if col not in df.columns:
                if col == 'open_interest' and table_name == "candles_1m":
                    df[col] = 0.0
                else:
                    df[col] = 0.0 if col not in ('symbol', 'timestamp') else None
        data = [tuple(x) for x in df[required_cols].values]
        try:
            with conn.cursor() as cur:
                execute_values(cur, query, data)
            logger.debug(f"{table_name} 저장 완료: {len(data)}행")
        except Exception as e:
            conn.rollback()
            timestamp_min = df['timestamp'].min() if 'timestamp' in df.columns else 'N/A'
            timestamp_max = df['timestamp'].max() if 'timestamp' in df.columns else 'N/A'
            logger.error(
                f"{table_name} 저장 실패: rows={len(data)}, "
                f"timestamp={timestamp_min}~{timestamp_max}, error={e}"
            )

    def init_risk_tables(self) -> None:
        """
        실거래 운영에 필요한 보조 테이블을 초기화합니다.

        이 메서드는 트레이드 기록, 시스템 상태, 손절 누적 이력을 저장할 테이블을 보장합니다.
        엔진 시작 시 한 번 호출해 두면 이후 리스크 관리 로직이 안정적으로 동작할 수 있습니다.
        """
        conn = self._ensure_conn()

        queries = [
            # 1. 트레이드 기록 테이블 (JSONB 특징량 포함)
            """
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                entry_time TIMESTAMPTZ NOT NULL,
                exit_time TIMESTAMPTZ,
                entry_price DOUBLE PRECISION,
                exit_price DOUBLE PRECISION,
                qty DOUBLE PRECISION,
                leverage INTEGER,
                pnl DOUBLE PRECISION,
                roe DOUBLE PRECISION,
                exit_reason TEXT,
                entry_prob DOUBLE PRECISION,
                features JSONB,
                status TEXT DEFAULT 'OPEN',
                entry_source TEXT DEFAULT 'AI',
                training_excluded BOOLEAN DEFAULT FALSE
            );
            """,
            # 2. 엔진 시스템 상태 테이블 (Peak Balance, 리셋 날짜 등)
            """
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value DOUBLE PRECISION,
                val_text TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """,
            # 3. 손절 이력 테이블 (블랙리스트용)
            """
            CREATE TABLE IF NOT EXISTS sl_history (
                symbol TEXT PRIMARY KEY,
                sl_count INTEGER DEFAULT 0,
                last_sl_at TIMESTAMPTZ DEFAULT NOW()
            );
            """,
            # 4. 주문과 보호 주문의 핵심 실행 이벤트 감사 로그
            """
            CREATE TABLE IF NOT EXISTS trade_events (
                event_id TEXT PRIMARY KEY,
                trade_order_id TEXT NOT NULL,
                trade_id INTEGER,
                event_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                event_type TEXT NOT NULL,
                payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                order_id TEXT
            );
            """
        ]

        try:
            with conn.cursor() as cur:
                for q in queries:
                    cur.execute(q)
                cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_source TEXT DEFAULT 'AI'")
                cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS training_excluded BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE system_state ADD COLUMN IF NOT EXISTS value DOUBLE PRECISION")
                cur.execute("ALTER TABLE system_state ADD COLUMN IF NOT EXISTS val DOUBLE PRECISION")
                cur.execute("ALTER TABLE system_state ADD COLUMN IF NOT EXISTS val_text TEXT")
                cur.execute("ALTER TABLE system_state ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")
                cur.execute("UPDATE system_state SET value = COALESCE(val, value), val = COALESCE(val, value)")
                cur.execute("ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS trade_id INTEGER")
                cur.execute("ALTER TABLE trade_events ADD COLUMN IF NOT EXISTS order_id TEXT")
                cur.execute(
                    """
                    UPDATE trades
                    SET
                        entry_source = CASE
                            WHEN features->>'note' = 'Auto-synced from Binance (Sync Loop)' THEN 'MANUAL_SYNC'
                            WHEN entry_source IS NULL OR entry_source = '' THEN 'AI'
                            ELSE entry_source
                        END,
                        training_excluded = CASE
                            WHEN training_excluded THEN TRUE
                            WHEN exit_reason IN ('MANUAL_EXIT', 'SYNC_EXIT') THEN TRUE
                            WHEN features->>'note' = 'Auto-synced from Binance (Sync Loop)' THEN TRUE
                            ELSE FALSE
                        END
                    """
                )
            logger.info("🛡️ 리스크 관리 및 감사 테이블 초기화 완료")
        except Exception as e:
            logger.error(f"테이블 생성 중 오류 발생: {e}")

    def record_trade_event(
        self,
        symbol: str,
        event_type: str,
        detail: dict[str, Any] | None = None,
        trade_id: int | None = None,
        order_id: str | None = None,
    ) -> bool:
        """주문 요청, 체결, 보호 주문, 복구 같은 실행 이벤트를 감사 로그로 남깁니다."""
        conn = self._ensure_conn()
        query = """
            INSERT INTO trade_events (
                event_id, trade_order_id, trade_id, event_ts, event_type, payload_json, order_id
            )
            VALUES (%s, %s, %s, NOW(), %s, %s, %s);
        """
        payload = dict(detail or {})
        payload.setdefault("symbol", symbol)
        if order_id is not None:
            payload.setdefault("order_id", order_id)
        event_id = str(uuid.uuid4())
        trade_order_id = str(order_id or trade_id or f"{symbol}:{event_type}:{event_id}")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        event_id,
                        trade_order_id,
                        trade_id,
                        event_type,
                        json.dumps(payload, ensure_ascii=False),
                        order_id,
                    ),
                )
            return True
        except Exception as e:
            logger.error(f"거래 이벤트 감사 로그 저장 실패 ({symbol}, {event_type}): {e}")
            return False

    def save_trade_entry(
        self,
        symbol: str,
        entry_price: float,
        qty: float,
        leverage: int,
        entry_prob: float,
        features: dict[str, Any],
        entry_source: str = 'AI',
        training_excluded: bool = False,
    ) -> int | None:
        """새로 열린 포지션의 진입 정보를 DB에 기록하고 생성된 트레이드 ID를 반환합니다."""
        conn = self._ensure_conn()
        query = """
            INSERT INTO trades (
                symbol, entry_time, entry_price, qty, leverage, entry_prob, features, status, entry_source, training_excluded
            )
            VALUES (%s, NOW(), %s, %s, %s, %s, %s, 'OPEN', %s, %s)
            RETURNING id;
        """
        try:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        symbol,
                        entry_price,
                        qty,
                        leverage,
                        entry_prob,
                        json.dumps(features),
                        entry_source,
                        training_excluded,
                    ),
                )
                row = cur.fetchone()
                return int(row[0]) if row else None
        except Exception as e:
            logger.error(f"트레이드 진입 정보 저장 중 오류: {e}")
            return None

    def update_trade_exit(self, symbol: str, exit_price: float, pnl: float, roe: float, reason: str) -> bool:
        """가장 최근의 열린 트레이드를 찾아 청산 결과로 갱신합니다."""
        conn = self._ensure_conn()
        query = """
            UPDATE trades 
            SET
                exit_time = NOW(),
                exit_price = %s,
                pnl = %s,
                roe = %s,
                exit_reason = %s,
                status = 'CLOSED',
                training_excluded = training_excluded OR %s
            WHERE symbol = %s AND status = 'OPEN'
            AND id = (SELECT id FROM trades WHERE symbol = %s AND status = 'OPEN' ORDER BY entry_time DESC LIMIT 1);
        """
        try:
            exclude_from_training = reason in {'MANUAL_EXIT', 'SYNC_EXIT'}
            with conn.cursor() as cur:
                cur.execute(query, (exit_price, pnl, roe, reason, exclude_from_training, symbol, symbol))
                updated_rows = cur.rowcount
            if updated_rows > 0:
                logger.info(f"✅ [{symbol}] DB 트레이드 정산 완료 ({reason}, PnL: {pnl:.2f})")
                return True
            logger.info(f"ℹ️ [{symbol}] 정산할 OPEN 트레이드가 없어 DB 업데이트를 건너뜁니다. ({reason})")
            return False
        except Exception as e:
            logger.error(f"트레이드 청산 정보 업데이트 중 오류: {e}")
            return False

    def get_system_state(self, key: str, default: float = 0.0) -> float:
        """숫자형 운영 상태값을 조회하고 실패 시 지정된 기본값을 반환합니다."""
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute("SELECT COALESCE(value, val) FROM system_state WHERE key = %s", (key,))
                row = cur.fetchone()
                return row[0] if row and row[0] is not None else default
        except Exception as e:
            logger.warning(f"시스템 상태 조회 실패 ({key}): {e}")
            return default

    def get_latest_open_trade(self, symbol: str) -> dict[str, Any] | None:
        """특정 심볼의 최신 OPEN 트레이드를 조회해 TP/SL 복구 등에 사용합니다."""
        query = """
            SELECT id, entry_time, entry_price, qty, leverage
            FROM trades
            WHERE symbol = %s AND status = 'OPEN'
            ORDER BY entry_time DESC
            LIMIT 1
        """
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute(query, (symbol,))
                row = cur.fetchone()
                return {
                    'id': int(row[0]),
                    'entry_time': row[1],
                    'entry_price': float(row[2]),
                    'qty': float(row[3]),
                    'leverage': int(row[4]),
                } if row else None
        except Exception as e:
            logger.warning(f"OPEN 트레이드 조회 실패 ({symbol}): {e}")
            return None

    def get_open_trade_symbols(self) -> set[str]:
        """DB에 OPEN 상태로 남아 있는 트레이드 심볼 집합을 조회합니다."""
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute("SELECT DISTINCT symbol FROM trades WHERE status = 'OPEN'")
                return {str(row[0]) for row in cur.fetchall()}
        except Exception as e:
            logger.warning(f"OPEN 트레이드 심볼 목록 조회 실패: {e}")
            return set()

    def get_latest_open_trade_meta(self, symbol: str) -> dict[str, Any] | None:
        """최신 OPEN trade의 출처와 학습 제외 여부를 함께 조회합니다."""
        query = """
            SELECT entry_price, qty, entry_source, training_excluded
            FROM trades
            WHERE symbol = %s AND status = 'OPEN'
            ORDER BY entry_time DESC
            LIMIT 1
        """
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute(query, (symbol,))
                row = cur.fetchone()
                if row is None:
                    return None
                return {
                    'entry_price': float(row[0]),
                    'qty': float(row[1]),
                    'entry_source': str(row[2] or 'AI'),
                    'training_excluded': bool(row[3]),
                }
        except Exception as e:
            logger.warning(f"OPEN 트레이드 메타 조회 실패 ({symbol}): {e}")
            return None

    def set_system_state(self, key: str, val: float) -> None:
        """숫자형 운영 상태값을 upsert 방식으로 저장합니다."""
        query = """
            INSERT INTO system_state (key, value, val, updated_at) VALUES (%s, %s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, val = EXCLUDED.val, updated_at = NOW();
        """
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute(query, (key, val, val))
        except Exception as e:
            logger.error(f"시스템 상태 저장 실패 ({key}): {e}")

    def get_system_state_text(self, key: str, default: str = "") -> str:
        """문자열 운영 상태값을 조회하고 실패 시 지정된 기본값을 반환합니다."""
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute("SELECT val_text FROM system_state WHERE key = %s", (key,))
                row = cur.fetchone()
                return row[0] if row and row[0] is not None else default
        except Exception as e:
            logger.warning(f"문자열 시스템 상태 조회 실패 ({key}): {e}")
            return default

    def set_system_state_text(self, key: str, val: str) -> None:
        """문자열 운영 상태값을 upsert 방식으로 저장합니다."""
        query = """
            INSERT INTO system_state (key, val_text, updated_at) VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET val_text = EXCLUDED.val_text, updated_at = NOW();
        """
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute(query, (key, val))
        except Exception as e:
            logger.error(f"문자열 시스템 상태 저장 실패 ({key}): {e}")

    def get_symbols_sl_info(self, symbols: list[str]) -> dict[str, tuple[int, datetime | None]]:
        """여러 심볼의 손절 누적 정보를 한 번에 조회해 심볼별 맵으로 반환합니다."""
        if not symbols:
            return {}

        default_map: dict[str, tuple[int, datetime | None]] = {
            symbol: (0, None) for symbol in symbols
        }
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute(
                    "SELECT symbol, sl_count, last_sl_at FROM sl_history WHERE symbol = ANY(%s)",
                    (symbols,),
                )
                rows = cur.fetchall()
            for symbol, sl_count, last_sl_at in rows:
                default_map[str(symbol)] = (int(sl_count), last_sl_at)
            return default_map
        except Exception as e:
            logger.warning(f"복수 심볼 손절 이력 조회 실패: {e}")
            return default_map

    def get_recent_global_sl_info(self, lookback_hours: float) -> tuple[int, datetime | None]:
        """최근 지정 시간 안의 전체 SL 횟수와 가장 최근 SL 시각을 조회합니다."""
        query = """
            SELECT COUNT(*), MAX(exit_time)
            FROM trades
            WHERE exit_reason = 'SL'
              AND exit_time IS NOT NULL
              AND exit_time >= NOW() - (%s * INTERVAL '1 hour')
        """
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute(query, (lookback_hours,))
                row = cur.fetchone()
                if row is None:
                    return 0, None
                return int(row[0] or 0), row[1]
        except Exception as e:
            logger.warning(f"전체 SL 이력 조회 실패: {e}")
            return 0, None

    def record_symbol_sl(self, symbol: str) -> None:
        """심볼별 손절 횟수를 1회 누적하고 마지막 손절 시각을 갱신합니다."""
        query = """
            INSERT INTO sl_history (symbol, sl_count, last_sl_at) VALUES (%s, 1, NOW())
            ON CONFLICT (symbol) DO UPDATE SET sl_count = sl_history.sl_count + 1, last_sl_at = NOW();
        """
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute(query, (symbol,))
        except Exception as e:
            logger.error(f"손절 이력 저장 실패 ({symbol}): {e}")

    def clear_symbol_sl(self, symbol: str) -> None:
        """심볼의 손절 누적 횟수를 0으로 초기화합니다."""
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute("UPDATE sl_history SET sl_count = 0 WHERE symbol = %s", (symbol,))
        except Exception as e:
            logger.error(f"손절 이력 초기화 실패 ({symbol}): {e}")

    def save_candles(self, df: pd.DataFrame) -> None:
        """캔들 DataFrame을 벌크 업서트 방식으로 저장합니다."""
        self._save_candle_frame(df, "candles")

    def save_candles_1m(self, df: pd.DataFrame) -> None:
        """1분봉 DataFrame을 `candles_1m` 테이블에 저장합니다."""
        self._save_candle_frame(df, "candles_1m")

    def save_funding_rates(self, df: pd.DataFrame) -> None:
        """펀딩비 데이터를 DB에 Upsert 합니다."""
        if df.empty:
            return

        conn = self._ensure_conn()
        query = """
            INSERT INTO funding_rates (timestamp, symbol, funding_rate)
            VALUES %s ON CONFLICT (timestamp, symbol) DO UPDATE SET funding_rate = EXCLUDED.funding_rate
        """
        data = [tuple(x) for x in df[['timestamp', 'symbol', 'funding_rate']].values]
        try:
            with conn.cursor() as cur:
                execute_values(cur, query, data)
        except Exception as e:
            logger.error(f"펀딩비 저장 중 오류 발생: {e}")
            conn.rollback()

    def get_candles(self, symbol: str | None = None, limit: int | None = None) -> pd.DataFrame:
        """심볼과 행 수 제한 조건에 맞는 15분봉 캔들 데이터를 시간순으로 조회합니다."""
        params: list[Any] = []
        if limit:
            query = "SELECT * FROM (SELECT * FROM candles"
            if symbol:
                query += " WHERE symbol = %s"
                params.append(symbol)
            query += f" ORDER BY timestamp DESC LIMIT {limit}) AS sub ORDER BY timestamp ASC"
        else:
            query = "SELECT * FROM candles"
            if symbol:
                query += " WHERE symbol = %s"
                params.append(symbol)
            query += " ORDER BY timestamp ASC"
        try:
            return pd.read_sql(query, self._ensure_conn(), params=params)
        except Exception as e:
            logger.error(f"캔들 조회 실패 (symbol={symbol}, limit={limit}): {e}")
            return pd.DataFrame()

    def get_candles_1m(self, symbol: str | None = None, limit: int | None = None) -> pd.DataFrame:
        """심볼과 행 수 제한 조건에 맞는 1분봉 캔들 데이터를 시간순으로 조회합니다."""
        params: list[Any] = []
        if limit:
            query = "SELECT * FROM (SELECT * FROM candles_1m"
            if symbol:
                query += " WHERE symbol = %s"
                params.append(symbol)
            query += f" ORDER BY timestamp DESC LIMIT {limit}) AS sub ORDER BY timestamp ASC"
        else:
            query = "SELECT * FROM candles_1m"
            if symbol:
                query += " WHERE symbol = %s"
                params.append(symbol)
            query += " ORDER BY timestamp ASC"
        try:
            return pd.read_sql(query, self._ensure_conn(), params=params)
        except Exception as e:
            logger.error(f"1분봉 캔들 조회 실패 (symbol={symbol}, limit={limit}): {e}")
            return pd.DataFrame()

    def get_symbol_list(self) -> list[str]:
        """캔들 테이블에 존재하는 전체 심볼 목록을 조회합니다."""
        try:
            with self._ensure_conn().cursor() as cur:
                cur.execute("SELECT DISTINCT symbol FROM candles")
                return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"심볼 목록 조회 실패: {e}")
            return []
