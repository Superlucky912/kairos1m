"""
synthesize_timeframes_main.py
=============================
`candles_1m` 원본 캔들에서 5분봉과 15분봉을 합성해 DB에 적재하는 실행 엔트리입니다.

이 스크립트는 기존 `candles` 테이블을 수정하지 않습니다.
5분봉은 OI를 쓰지 않으므로 `open_interest=0.0`으로 저장하고,
15분봉은 기존 `candles.open_interest`를 같은 `symbol + timestamp` 기준으로 복사합니다.

기본 실행은 dry-run이며, 실제 DB 적재는 `--apply`를 명시했을 때만 수행합니다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg2

from common.config.base_config import Config
from common.database.manager import DBManager
from common.utils.logger import setup_logger


logger = setup_logger("Kairos.SynthesizeTF", "collector.log")


@dataclass
class BatchResult:
    """한 배치 구간의 합성/적재 결과를 담는 값 객체입니다."""

    timeframe: str
    window_start: str
    window_end: str
    source_groups: int
    complete_groups: int
    incomplete_groups: int
    skipped_missing_oi: int
    upserted_rows: int


class TimeframeSynthesizer:
    """1분봉 원본을 5분봉/15분봉 합성 캔들로 변환해 DB에 반영합니다."""

    TIMEFRAME_SPEC = {
        "5m": {"seconds": 300, "minutes": 5, "table": "candles_5m", "copy_oi": False},
        "15m": {"seconds": 900, "minutes": 15, "table": "candles_15m", "copy_oi": True},
    }

    def __init__(self, apply: bool, batch_days: int, report_path: Path) -> None:
        """실행 옵션과 DB 연결 정보를 준비합니다."""
        self.apply = apply
        self.batch_days = max(1, batch_days)
        self.report_path = report_path
        self.conn = psycopg2.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            dbname=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
        )
        self.conn.autocommit = True

    def close(self) -> None:
        """DB 연결을 닫습니다."""
        self.conn.close()

    def ensure_tables(self) -> None:
        """합성 대상 테이블과 인덱스를 보장합니다."""
        db = DBManager()
        db.conn = self.conn
        db.init_market_data_tables()

    def load_source_bounds(self) -> tuple[datetime, datetime]:
        """`candles_1m`의 전체 수집 범위를 조회합니다."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM candles_1m")
            start_ts, end_ts = cur.fetchone()
        if start_ts is None or end_ts is None:
            raise RuntimeError("candles_1m 데이터가 없어 합성할 수 없습니다.")
        return start_ts, end_ts + timedelta(minutes=1)

    @staticmethod
    def parse_timestamp(value: str | None) -> datetime | None:
        """CLI timestamp 문자열을 UTC aware datetime으로 변환합니다."""
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def iter_windows(self, start_ts: datetime, end_ts: datetime) -> list[tuple[datetime, datetime]]:
        """전체 범위를 일 단위 배치 구간으로 나눕니다."""
        windows: list[tuple[datetime, datetime]] = []
        cursor = start_ts
        step = timedelta(days=self.batch_days)
        while cursor < end_ts:
            next_cursor = min(cursor + step, end_ts)
            windows.append((cursor, next_cursor))
            cursor = next_cursor
        return windows

    def run(
        self,
        timeframes: list[str],
        start_ts: datetime | None = None,
        end_ts: datetime | None = None,
    ) -> list[BatchResult]:
        """요청된 timeframe을 순서대로 합성합니다."""
        source_start, source_end = self.load_source_bounds()
        effective_start = start_ts or source_start
        effective_end = end_ts or source_end
        if effective_start >= effective_end:
            raise ValueError("합성 시작 시각은 종료 시각보다 빨라야 합니다.")

        self.ensure_tables()
        results: list[BatchResult] = []
        logger.info(
            "timeframe 합성 시작: apply=%s, range=%s~%s, timeframes=%s",
            self.apply,
            effective_start.isoformat(),
            effective_end.isoformat(),
            ",".join(timeframes),
        )

        for timeframe in timeframes:
            if timeframe not in self.TIMEFRAME_SPEC:
                raise ValueError(f"지원하지 않는 timeframe입니다: {timeframe}")
            for window_start, window_end in self.iter_windows(effective_start, effective_end):
                result = self.synthesize_window(timeframe, window_start, window_end)
                results.append(result)
                logger.info(
                    "[%s] %s~%s source=%s complete=%s incomplete=%s missing_oi=%s upserted=%s",
                    result.timeframe,
                    result.window_start,
                    result.window_end,
                    result.source_groups,
                    result.complete_groups,
                    result.incomplete_groups,
                    result.skipped_missing_oi,
                    result.upserted_rows,
                )

        self.write_report(results)
        return results

    def synthesize_window(self, timeframe: str, start_ts: datetime, end_ts: datetime) -> BatchResult:
        """단일 구간을 합성하고 dry-run 또는 upsert 결과를 반환합니다."""
        spec = self.TIMEFRAME_SPEC[timeframe]
        if spec["copy_oi"]:
            row = self._run_15m_query(start_ts, end_ts, apply=self.apply)
        else:
            row = self._run_5m_query(start_ts, end_ts, apply=self.apply)
        return BatchResult(
            timeframe=timeframe,
            window_start=start_ts.isoformat(),
            window_end=end_ts.isoformat(),
            source_groups=int(row["source_groups"]),
            complete_groups=int(row["complete_groups"]),
            incomplete_groups=int(row["incomplete_groups"]),
            skipped_missing_oi=int(row["skipped_missing_oi"]),
            upserted_rows=int(row["upserted_rows"]),
        )

    def _fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
        """SQL 실행 후 첫 row를 컬럼명 dict로 반환합니다."""
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            values = cur.fetchone()
        return dict(zip(columns, values, strict=True))

    def _run_5m_query(self, start_ts: datetime, end_ts: datetime, *, apply: bool) -> dict[str, Any]:
        """5분봉 합성 쿼리를 실행합니다."""
        if not apply:
            return self._fetch_one(self._dry_run_sql(bucket_seconds=300, expected_minutes=5), (start_ts, end_ts))
        return self._fetch_one(self._insert_5m_sql(), (start_ts, end_ts))

    def _run_15m_query(self, start_ts: datetime, end_ts: datetime, *, apply: bool) -> dict[str, Any]:
        """15분봉 합성 쿼리를 실행합니다."""
        if not apply:
            return self._fetch_one(self._dry_run_15m_sql(), (start_ts, end_ts))
        return self._fetch_one(self._insert_15m_sql(), (start_ts, end_ts))

    @staticmethod
    def _agg_cte(bucket_seconds: int) -> str:
        """1분봉을 지정 초 단위 bucket으로 묶는 공통 CTE를 반환합니다."""
        return f"""
            WITH agg AS (
                SELECT
                    to_timestamp(floor(extract(epoch FROM timestamp) / {bucket_seconds}) * {bucket_seconds}) AS timestamp,
                    symbol,
                    COUNT(*)::bigint AS minute_count,
                    (array_agg(open ORDER BY timestamp ASC))[1]::double precision AS open,
                    MAX(high)::double precision AS high,
                    MIN(low)::double precision AS low,
                    (array_agg(close ORDER BY timestamp DESC))[1]::double precision AS close,
                    SUM(volume)::double precision AS volume,
                    SUM(quote_volume)::double precision AS quote_volume,
                    SUM(trades_count)::bigint AS trades_count,
                    SUM(taker_buy_base)::double precision AS taker_buy_base,
                    SUM(taker_buy_quote)::double precision AS taker_buy_quote
                FROM candles_1m
                WHERE timestamp >= %s
                  AND timestamp < %s
                GROUP BY 1, symbol
            )
        """

    @classmethod
    def _dry_run_sql(cls, bucket_seconds: int, expected_minutes: int) -> str:
        """OI 복사가 필요 없는 timeframe의 dry-run 집계 SQL을 반환합니다."""
        return cls._agg_cte(bucket_seconds) + f"""
            SELECT
                COUNT(*)::bigint AS source_groups,
                COUNT(*) FILTER (WHERE minute_count = {expected_minutes})::bigint AS complete_groups,
                COUNT(*) FILTER (WHERE minute_count <> {expected_minutes})::bigint AS incomplete_groups,
                0::bigint AS skipped_missing_oi,
                0::bigint AS upserted_rows
            FROM agg
        """

    @classmethod
    def _dry_run_15m_sql(cls) -> str:
        """15분봉 OI 매칭까지 포함한 dry-run SQL을 반환합니다."""
        return cls._agg_cte(900) + """
            , complete AS (
                SELECT * FROM agg WHERE minute_count = 15
            )
            SELECT
                (SELECT COUNT(*) FROM agg)::bigint AS source_groups,
                (SELECT COUNT(*) FROM complete)::bigint AS complete_groups,
                (SELECT COUNT(*) FROM agg WHERE minute_count <> 15)::bigint AS incomplete_groups,
                COUNT(*) FILTER (WHERE c.open_interest IS NULL)::bigint AS skipped_missing_oi,
                0::bigint AS upserted_rows
            FROM complete a
            LEFT JOIN candles c
              ON c.symbol = a.symbol
             AND c.timestamp = a.timestamp
        """

    @classmethod
    def _insert_5m_sql(cls) -> str:
        """5분봉 upsert SQL을 반환합니다."""
        return cls._agg_cte(300) + """
            , complete AS (
                SELECT * FROM agg WHERE minute_count = 5
            ), inserted AS (
                INSERT INTO candles_5m (
                    timestamp, symbol, open, high, low, close, volume, quote_volume,
                    open_interest, trades_count, taker_buy_base, taker_buy_quote
                )
                SELECT
                    timestamp, symbol, open, high, low, close, volume, quote_volume,
                    0.0::double precision AS open_interest,
                    trades_count, taker_buy_base, taker_buy_quote
                FROM complete
                ON CONFLICT (timestamp, symbol) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    quote_volume = EXCLUDED.quote_volume,
                    open_interest = EXCLUDED.open_interest,
                    trades_count = EXCLUDED.trades_count,
                    taker_buy_base = EXCLUDED.taker_buy_base,
                    taker_buy_quote = EXCLUDED.taker_buy_quote,
                    updated_at = NOW()
                RETURNING 1
            )
            SELECT
                (SELECT COUNT(*) FROM agg)::bigint AS source_groups,
                (SELECT COUNT(*) FROM complete)::bigint AS complete_groups,
                (SELECT COUNT(*) FROM agg WHERE minute_count <> 5)::bigint AS incomplete_groups,
                0::bigint AS skipped_missing_oi,
                (SELECT COUNT(*) FROM inserted)::bigint AS upserted_rows
        """

    @classmethod
    def _insert_15m_sql(cls) -> str:
        """15분봉 upsert SQL을 반환합니다."""
        return cls._agg_cte(900) + """
            , complete AS (
                SELECT * FROM agg WHERE minute_count = 15
            ), ready AS (
                SELECT
                    a.timestamp,
                    a.symbol,
                    a.open,
                    a.high,
                    a.low,
                    a.close,
                    a.volume,
                    a.quote_volume,
                    c.open_interest::double precision AS open_interest,
                    a.trades_count,
                    a.taker_buy_base,
                    a.taker_buy_quote
                FROM complete a
                JOIN candles c
                  ON c.symbol = a.symbol
                 AND c.timestamp = a.timestamp
                 AND c.open_interest IS NOT NULL
            ), inserted AS (
                INSERT INTO candles_15m (
                    timestamp, symbol, open, high, low, close, volume, quote_volume,
                    open_interest, trades_count, taker_buy_base, taker_buy_quote
                )
                SELECT
                    timestamp, symbol, open, high, low, close, volume, quote_volume,
                    open_interest, trades_count, taker_buy_base, taker_buy_quote
                FROM ready
                ON CONFLICT (timestamp, symbol) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    quote_volume = EXCLUDED.quote_volume,
                    open_interest = EXCLUDED.open_interest,
                    trades_count = EXCLUDED.trades_count,
                    taker_buy_base = EXCLUDED.taker_buy_base,
                    taker_buy_quote = EXCLUDED.taker_buy_quote,
                    updated_at = NOW()
                RETURNING 1
            )
            SELECT
                (SELECT COUNT(*) FROM agg)::bigint AS source_groups,
                (SELECT COUNT(*) FROM complete)::bigint AS complete_groups,
                (SELECT COUNT(*) FROM agg WHERE minute_count <> 15)::bigint AS incomplete_groups,
                (
                    SELECT COUNT(*)
                    FROM complete a
                    LEFT JOIN candles c
                      ON c.symbol = a.symbol
                     AND c.timestamp = a.timestamp
                    WHERE c.open_interest IS NULL
                )::bigint AS skipped_missing_oi,
                (SELECT COUNT(*) FROM inserted)::bigint AS upserted_rows
        """

    def write_report(self, results: list[BatchResult]) -> None:
        """실행 결과를 JSON 리포트로 저장합니다."""
        report = {
            "apply": self.apply,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": self.summarize(results),
            "batches": [asdict(result) for result in results],
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("합성 리포트 저장 완료: %s", self.report_path)

    @staticmethod
    def summarize(results: list[BatchResult]) -> dict[str, dict[str, int]]:
        """timeframe별 누적 결과를 계산합니다."""
        summary: dict[str, dict[str, int]] = {}
        for result in results:
            bucket = summary.setdefault(
                result.timeframe,
                {
                    "source_groups": 0,
                    "complete_groups": 0,
                    "incomplete_groups": 0,
                    "skipped_missing_oi": 0,
                    "upserted_rows": 0,
                },
            )
            bucket["source_groups"] += result.source_groups
            bucket["complete_groups"] += result.complete_groups
            bucket["incomplete_groups"] += result.incomplete_groups
            bucket["skipped_missing_oi"] += result.skipped_missing_oi
            bucket["upserted_rows"] += result.upserted_rows
        return summary


def parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱합니다."""
    parser = argparse.ArgumentParser(description="candles_1m 기반 5m/15m 합성 적재")
    parser.add_argument("--apply", action="store_true", help="실제 DB에 upsert합니다. 없으면 dry-run입니다.")
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["5m", "15m"],
        choices=["5m", "15m"],
        help="합성할 timeframe 목록",
    )
    parser.add_argument("--start", default=None, help="합성 시작 시각(UTC ISO). 예: 2026-04-01T00:00:00Z")
    parser.add_argument("--end", default=None, help="합성 종료 시각 exclusive(UTC ISO). 예: 2026-05-01T00:00:00Z")
    parser.add_argument("--batch-days", type=int, default=1, help="한 번에 처리할 일 단위 배치 크기")
    parser.add_argument(
        "--report",
        default="common/logs/backtest/synthesize_timeframes_report.json",
        help="실행 결과 JSON 리포트 경로",
    )
    return parser.parse_args()


def main() -> None:
    """스크립트 실행 진입점입니다."""
    args = parse_args()
    synthesizer = TimeframeSynthesizer(
        apply=bool(args.apply),
        batch_days=int(args.batch_days),
        report_path=Path(args.report),
    )
    try:
        results = synthesizer.run(
            timeframes=list(args.timeframes),
            start_ts=TimeframeSynthesizer.parse_timestamp(args.start),
            end_ts=TimeframeSynthesizer.parse_timestamp(args.end),
        )
        summary = TimeframeSynthesizer.summarize(results)
        print(json.dumps({"apply": args.apply, "summary": summary}, ensure_ascii=False, indent=2))
    finally:
        synthesizer.close()


if __name__ == "__main__":
    main()
