"""
backfill_gap_main.py
====================
이 파일은 DB에서 발견된 캔들 누락 구간만 REST API로 보정하는 one-shot 도구입니다.

기존 `backfill_30d_main.py`가 active 심볼 전체를 넓은 기간으로 다시 채우는 용도라면,
이 스크립트는 `candles`와 `candles_1m`의 실제 gap을 찾아 필요한 구간만 가져온다.
15분봉은 Binance OI 이력과 병합하고, funding rate는 gap 또는 지정 기간에 맞춰 별도 저장한다.
BTCUSDT의 15분 정렬 기준에 맞지 않는 row는 기본 dry-run으로 보여 주고,
`--delete-btc-offgrid --apply`를 함께 줄 때만 삭제한다.
"""

from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import Any, cast

import pandas as pd

from collector.backfill_30d_main import ApiCallRateLimiter
from common.binance.binance_client import BinanceClient
from common.config.base_config import Config
from common.config.strategy_config import StrategyConfig
from common.database.manager import DBManager
from common.utils.logger import setup_logger


logger = setup_logger(
    "Kairos.BackfillGap",
    Config.LOG_DIR / "collector" / "backfill_gap.log",
    rotation="daily",
)


@dataclass(frozen=True)
class GapRange:
    """DB에 이미 존재하는 양끝 row 사이의 누락 구간 정보를 표현합니다."""

    symbol: str
    gap_start: datetime
    gap_end: datetime
    missing_start: datetime
    missing_end: datetime
    missing_bars: int


class OIHistoryUnavailable(RuntimeError):
    """Binance에서 특정 심볼의 과거 OI 이력을 제공하지 않을 때 사용하는 예외입니다."""


class GapBackfillService:
    """DB gap 조회, REST 수집, DB upsert, 비정렬 row 삭제를 조합하는 서비스입니다."""

    def __init__(
        self,
        *,
        binance_client: BinanceClient | None,
        db_manager: DBManager,
        apply_changes: bool,
        max_calls_per_minute: int,
        logger: Logger,
    ) -> None:
        """Binance/DB 클라이언트와 dry-run 여부, REST rate limiter를 초기화합니다."""
        self.binance = binance_client
        self.db = db_manager
        self.apply_changes = apply_changes
        self.logger = logger
        self.rate_limiter = ApiCallRateLimiter(max_calls_per_minute, logger)
        self.oi_unavailable_symbols: set[str] = set()

    def _safe_api_call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """REST 호출에 전역 rate limit과 429/1003 cooldown 재시도를 적용합니다."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.rate_limiter.acquire()
                time.sleep(random.uniform(0.05, 0.15))
                return func(*args, **kwargs)
            except Exception as exc:
                if "1003" in str(exc) or "429" in str(exc):
                    wait_seconds = 65.0
                    self.logger.warning(
                        f"API 호출 제한 감지, cooldown 등록 ({attempt + 1}/{max_retries}, {wait_seconds:.0f}초)"
                    )
                    self.rate_limiter.register_cooldown(wait_seconds)
                    if attempt == max_retries - 1:
                        raise
                    continue

                if "-1130" in str(exc) or "startTime" in str(exc):
                    self.logger.warning(f"API 파라미터 거부 감지, 재시도 없이 fallback으로 넘깁니다: {exc}")
                    raise

                self.logger.error(f"API 호출 실패: {exc}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2)
        return None

    def _require_binance(self) -> BinanceClient:
        """REST backfill이 필요한 경로에서 Binance 클라이언트 존재를 보장합니다."""
        if self.binance is None:
            raise RuntimeError("REST backfill에는 Binance 클라이언트가 필요합니다.")
        return self.binance

    @staticmethod
    def _to_millis(ts: datetime) -> int:
        """timezone-aware datetime을 Binance REST용 millisecond timestamp로 변환합니다."""
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int(ts.timestamp() * 1000)

    @staticmethod
    def _parse_utc_datetime(value: str) -> datetime:
        """CLI 문자열을 UTC timezone-aware datetime으로 변환합니다."""
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _parse_symbol_list(symbols_arg: str) -> list[str]:
        """쉼표 구분 심볼 문자열을 대문자 중복 제거 리스트로 변환합니다."""
        symbols = [symbol.strip().upper() for symbol in symbols_arg.split(",") if symbol.strip()]
        return list(dict.fromkeys(symbols))

    @staticmethod
    def _filter_quote_asset_symbols(symbols: list[str]) -> list[str]:
        """전략 기준 quote asset에 맞는 심볼만 남깁니다."""
        quote_asset = StrategyConfig.QUOTE_ASSET.upper()
        return [symbol for symbol in symbols if symbol.upper().endswith(quote_asset)]

    @staticmethod
    def _klines_to_frame(
        all_klines: list[Any],
        symbol: str,
        *,
        include_open_interest: bool,
    ) -> pd.DataFrame:
        """Binance kline 응답을 DB 저장 컬럼을 가진 DataFrame으로 변환합니다."""
        if not all_klines:
            return pd.DataFrame()

        df_klines = pd.DataFrame(
            all_klines,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades_count",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )
        df_klines["timestamp"] = pd.to_datetime(df_klines["timestamp"], unit="ms", utc=True).dt.round("1s")
        df_klines["symbol"] = symbol
        df_klines["open_interest"] = 0.0
        result = df_klines[
            [
                "timestamp",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_volume",
                "open_interest",
                "trades_count",
                "taker_buy_base",
                "taker_buy_quote",
            ]
        ].copy()
        if include_open_interest:
            result = result.astype({"open_interest": float})
        return cast(pd.DataFrame, result)

    def _fetch_klines_range(
        self,
        *,
        symbol: str,
        interval: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> pd.DataFrame:
        """지정한 interval의 kline을 start/end 포함 범위로 반복 조회합니다."""
        if end_ts < start_ts:
            return pd.DataFrame()

        start_ms = self._to_millis(start_ts)
        end_ms = self._to_millis(end_ts)
        all_klines: list[Any] = []
        while start_ms <= end_ms:
            klines = self._safe_api_call(
                self._require_binance().get_klines,
                symbol,
                interval,
                limit=1000,
                startTime=start_ms,
                endTime=end_ms,
            )
            if not klines:
                break
            all_klines.extend(klines)
            last_open_ms = int(klines[-1][0])
            next_start_ms = last_open_ms + 1
            if next_start_ms <= start_ms:
                break
            start_ms = next_start_ms
            if len(klines) < 1000:
                break

        df = self._klines_to_frame(
            all_klines,
            symbol,
            include_open_interest=(interval == StrategyConfig.COLLECTOR_REST_INTERVAL),
        )
        if df.empty:
            return df
        start_pd = pd.Timestamp(start_ts)
        end_pd = pd.Timestamp(end_ts)
        return cast(pd.DataFrame, df[(df["timestamp"] >= start_pd) & (df["timestamp"] <= end_pd)].copy())

    def _fetch_open_interest_range(
        self,
        *,
        symbol: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> pd.DataFrame:
        """15분봉 구간에 맞는 OI 이력을 Binance REST에서 조회합니다."""
        if end_ts < start_ts:
            return pd.DataFrame()

        try:
            return self._fetch_open_interest_range_with_start_time(
                symbol=symbol,
                start_ts=start_ts,
                end_ts=end_ts,
            )
        except Exception as exc:
            if "-1130" not in str(exc) and "startTime" not in str(exc):
                self.logger.warning(f"[{symbol}] OI startTime 조회 실패, endTime fallback 시도: {exc}")
            return self._fetch_open_interest_range_by_end_time(
                symbol=symbol,
                start_ts=start_ts,
                end_ts=end_ts,
            )

    def _fetch_open_interest_range_with_start_time(
        self,
        *,
        symbol: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> pd.DataFrame:
        """startTime/endTime을 모두 써서 OI 이력을 조회합니다."""
        start_ms = self._to_millis(start_ts)
        end_ms = self._to_millis(end_ts)
        all_oi: list[Any] = []
        while start_ms <= end_ms:
            oi_data = self._safe_api_call(
                self._require_binance().get_open_interest_history,
                symbol,
                StrategyConfig.COLLECTOR_REST_INTERVAL,
                limit=500,
                startTime=start_ms,
                endTime=end_ms,
            )
            if not oi_data:
                break
            all_oi.extend(oi_data)
            last_ts = int(oi_data[-1]["timestamp"])
            next_start_ms = last_ts + 1
            if next_start_ms <= start_ms:
                break
            start_ms = next_start_ms
            if len(oi_data) < 500:
                break

        if not all_oi:
            return pd.DataFrame()

        df_oi = pd.DataFrame(all_oi)
        df_oi["timestamp"] = pd.to_datetime(df_oi["timestamp"], unit="ms", utc=True).dt.round("1s")
        df_oi = cast(pd.DataFrame, df_oi[["timestamp", "sumOpenInterest"]].copy())
        df_oi.columns = ["timestamp", "open_interest"]
        return cast(pd.DataFrame, df_oi.drop_duplicates(subset="timestamp"))

    def _fetch_open_interest_range_by_end_time(
        self,
        *,
        symbol: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> pd.DataFrame:
        """startTime이 거부되는 심볼을 위해 endTime 기준 역방향으로 OI를 조회합니다."""
        start_ms = self._to_millis(start_ts)
        current_end_ms = self._to_millis(end_ts)
        all_oi: list[Any] = []

        while current_end_ms >= start_ms:
            try:
                oi_data = self._safe_api_call(
                    self._require_binance().get_open_interest_history,
                    symbol,
                    StrategyConfig.COLLECTOR_REST_INTERVAL,
                    limit=500,
                    endTime=current_end_ms,
                )
            except Exception as exc:
                raise OIHistoryUnavailable(
                    f"{symbol} OI endTime fallback 실패: {exc}"
                ) from exc

            if not oi_data:
                break

            all_oi = list(oi_data) + all_oi
            oldest_ts = int(oi_data[0]["timestamp"])
            if oldest_ts <= start_ms or len(oi_data) < 500:
                break

            next_end_ms = oldest_ts - 1
            if next_end_ms >= current_end_ms:
                break
            current_end_ms = next_end_ms

        if not all_oi:
            raise OIHistoryUnavailable(f"{symbol} OI 이력 응답이 비어 있습니다.")

        df_oi = pd.DataFrame(all_oi)
        df_oi["timestamp"] = pd.to_datetime(df_oi["timestamp"], unit="ms", utc=True).dt.round("1s")
        df_oi = cast(pd.DataFrame, df_oi[["timestamp", "sumOpenInterest"]].copy())
        df_oi.columns = ["timestamp", "open_interest"]
        start_pd = pd.Timestamp(start_ts)
        end_pd = pd.Timestamp(end_ts)
        filtered_df = cast(pd.DataFrame, df_oi[(df_oi["timestamp"] >= start_pd) & (df_oi["timestamp"] <= end_pd)].copy())
        return cast(pd.DataFrame, filtered_df.drop_duplicates(subset="timestamp"))

    def _fetch_funding_range(
        self,
        *,
        symbol: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> pd.DataFrame:
        """지정 구간의 funding rate 이력을 Binance REST에서 조회합니다."""
        if end_ts < start_ts:
            return pd.DataFrame()

        start_ms = self._to_millis(start_ts)
        end_ms = self._to_millis(end_ts)
        all_funding: list[Any] = []
        while start_ms <= end_ms:
            funding_data = self._safe_api_call(
                self._require_binance().get_funding_rate,
                symbol,
                limit=1000,
                startTime=start_ms,
                endTime=end_ms,
            )
            if not funding_data:
                break
            all_funding.extend(funding_data)
            last_ts = int(funding_data[-1]["fundingTime"])
            next_start_ms = last_ts + 1
            if next_start_ms <= start_ms:
                break
            start_ms = next_start_ms
            if len(funding_data) < 1000:
                break

        if not all_funding:
            return pd.DataFrame()

        df_funding = pd.DataFrame(all_funding)
        df_funding["timestamp"] = pd.to_datetime(df_funding["fundingTime"], unit="ms", utc=True).dt.round("1s")
        df_funding["symbol"] = symbol
        df_funding["funding_rate"] = df_funding["fundingRate"].astype(float)
        return cast(pd.DataFrame, df_funding[["timestamp", "symbol", "funding_rate"]].drop_duplicates())

    def _merge_15m_open_interest(
        self,
        candles_df: pd.DataFrame,
        oi_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """15분봉 DataFrame에 같은 timestamp의 OI 값을 병합합니다."""
        if candles_df.empty:
            return candles_df
        if oi_df.empty:
            raise RuntimeError("15분봉 OI 데이터가 비어 있어 캔들 저장을 중단합니다.")
        merged_df = cast(pd.DataFrame, pd.merge(candles_df.drop(columns=["open_interest"], errors="ignore"), oi_df, on="timestamp", how="left"))
        missing_oi_count = int(merged_df["open_interest"].isna().sum())
        if missing_oi_count > 0:
            raise RuntimeError(f"15분봉 OI 매칭 누락 {missing_oi_count}개로 캔들 저장을 중단합니다.")
        return merged_df

    def find_gap_ranges(
        self,
        *,
        table_name: str,
        interval_seconds: int,
        symbols: list[str],
        max_gaps: int,
        include_non_usdt: bool,
    ) -> list[GapRange]:
        """DB에서 특정 테이블의 timestamp gap을 찾아 누락 구간 리스트로 반환합니다."""
        if table_name not in {"candles", "candles_1m"}:
            raise ValueError(f"지원하지 않는 테이블입니다: {table_name}")

        interval = timedelta(seconds=interval_seconds)
        params: list[Any] = []
        where_clause = ""
        if symbols:
            where_clause = "WHERE symbol = ANY(%s)"
            params.append(symbols)
        elif not include_non_usdt:
            where_clause = "WHERE symbol LIKE %s"
            params.append(f"%{StrategyConfig.QUOTE_ASSET}")

        limit_clause = ""
        if max_gaps > 0:
            limit_clause = "LIMIT %s"
            params.append(max_gaps)

        query = f"""
            WITH ordered AS (
                SELECT
                    symbol,
                    timestamp,
                    LEAD(timestamp) OVER (PARTITION BY symbol ORDER BY timestamp) AS next_ts
                FROM {table_name}
                {where_clause}
            )
            SELECT symbol, timestamp AS gap_start, next_ts AS gap_end
            FROM ordered
            WHERE next_ts IS NOT NULL
              AND next_ts > timestamp + (%s::text)::interval
            ORDER BY symbol, gap_start
            {limit_clause}
        """
        interval_text = f"{interval_seconds} seconds"
        interval_param_index = 1 if where_clause else 0
        params_with_interval = params.copy()
        params_with_interval.insert(interval_param_index, interval_text)

        with self.db._ensure_conn().cursor() as cur:
            cur.execute(query, params_with_interval)
            rows = cur.fetchall()

        gaps: list[GapRange] = []
        for symbol, gap_start, gap_end in rows:
            missing_start = gap_start + interval
            missing_end = gap_end - interval
            missing_bars = int((gap_end - gap_start).total_seconds() // interval_seconds) - 1
            gaps.append(
                GapRange(
                    symbol=str(symbol),
                    gap_start=gap_start,
                    gap_end=gap_end,
                    missing_start=missing_start,
                    missing_end=missing_end,
                    missing_bars=missing_bars,
                )
            )
        return gaps

    def backfill_15m_range(self, symbol: str, start_ts: datetime, end_ts: datetime) -> tuple[int, int]:
        """지정 심볼의 15분봉 캔들과 OI를 조회해 `candles`에 저장합니다."""
        candles_df = self._fetch_klines_range(
            symbol=symbol,
            interval=StrategyConfig.COLLECTOR_REST_INTERVAL,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        oi_df = self._fetch_open_interest_range(symbol=symbol, start_ts=start_ts, end_ts=end_ts)
        final_df = self._merge_15m_open_interest(candles_df, oi_df)
        if self.apply_changes and not final_df.empty:
            self.db.save_candles(final_df)
        return len(final_df), len(oi_df)

    def backfill_1m_range(self, symbol: str, start_ts: datetime, end_ts: datetime) -> int:
        """지정 심볼의 1분봉 캔들을 조회해 `candles_1m`에 저장합니다."""
        candles_df = self._fetch_klines_range(
            symbol=symbol,
            interval=StrategyConfig.COLLECTOR_INTRABAR_INTERVAL,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        if self.apply_changes and not candles_df.empty:
            self.db.save_candles_1m(candles_df)
        return len(candles_df)

    def backfill_funding_range(self, symbol: str, start_ts: datetime, end_ts: datetime) -> int:
        """지정 심볼의 funding rate를 조회해 `funding_rates`에 저장합니다."""
        funding_df = self._fetch_funding_range(symbol=symbol, start_ts=start_ts, end_ts=end_ts)
        if self.apply_changes and not funding_df.empty:
            self.db.save_funding_rates(funding_df)
        return len(funding_df)

    def run_gap_backfill(
        self,
        *,
        symbols: list[str],
        include_15m: bool,
        include_1m: bool,
        include_funding: bool,
        max_gaps: int,
        include_non_usdt: bool,
        scan_only: bool,
    ) -> None:
        """DB에서 발견한 gap 목록을 기준으로 선택된 데이터 종류를 backfill합니다."""
        if include_15m:
            gaps_15m = self.find_gap_ranges(
                table_name="candles",
                interval_seconds=15 * 60,
                symbols=symbols,
                max_gaps=max_gaps,
                include_non_usdt=include_non_usdt,
            )
            self.logger.info(f"15분봉 gap 대상 {len(gaps_15m)}개")
            if scan_only:
                for gap in gaps_15m:
                    self.logger.info(
                        f"[15m scan] {gap.symbol} {gap.missing_start}~{gap.missing_end} "
                        f"missing={gap.missing_bars}"
                    )
            if scan_only:
                gaps_15m = []
            for gap in gaps_15m:
                if gap.symbol in self.oi_unavailable_symbols:
                    self.logger.warning(
                        f"[15m 스킵] {gap.symbol} OI 이력 미지원으로 이미 판단되어 "
                        f"{gap.missing_start}~{gap.missing_end} 구간을 건너뜁니다."
                    )
                    continue
                try:
                    candle_count, oi_count = self.backfill_15m_range(gap.symbol, gap.missing_start, gap.missing_end)
                except OIHistoryUnavailable as exc:
                    self.oi_unavailable_symbols.add(gap.symbol)
                    self.logger.error(
                        f"[15m 실패] {gap.symbol} OI 이력 확보 실패로 이 심볼의 남은 15분봉 gap을 건너뜁니다. "
                        f"range={gap.missing_start}~{gap.missing_end}, missing={gap.missing_bars}, reason={exc}"
                    )
                    continue
                except Exception as exc:
                    self.logger.error(
                        f"[15m 실패] {gap.symbol} {gap.missing_start}~{gap.missing_end} "
                        f"missing={gap.missing_bars}, reason={exc}"
                    )
                    continue
                funding_count = 0
                if include_funding:
                    funding_count = self.backfill_funding_range(gap.symbol, gap.gap_start, gap.gap_end)
                self.logger.info(
                    f"[15m] {gap.symbol} {gap.missing_start}~{gap.missing_end} "
                    f"missing={gap.missing_bars}, candles={candle_count}, oi={oi_count}, funding={funding_count}, apply={self.apply_changes}"
                )

            if self.oi_unavailable_symbols:
                self.logger.warning(
                    "15분봉 OI 이력 미지원/조회 불가 심볼: "
                    f"{', '.join(sorted(self.oi_unavailable_symbols))}. "
                    "데이터 정합성을 위해 해당 심볼의 15분봉 backfill은 저장하지 않았습니다."
                )

        if include_1m:
            gaps_1m = self.find_gap_ranges(
                table_name="candles_1m",
                interval_seconds=60,
                symbols=symbols,
                max_gaps=max_gaps,
                include_non_usdt=include_non_usdt,
            )
            self.logger.info(f"1분봉 gap 대상 {len(gaps_1m)}개")
            if scan_only:
                for gap in gaps_1m:
                    self.logger.info(
                        f"[1m scan] {gap.symbol} {gap.missing_start}~{gap.missing_end} "
                        f"missing={gap.missing_bars}"
                    )
            if scan_only:
                gaps_1m = []
            for gap in gaps_1m:
                candle_count = self.backfill_1m_range(gap.symbol, gap.missing_start, gap.missing_end)
                funding_count = 0
                if include_funding:
                    funding_count = self.backfill_funding_range(gap.symbol, gap.gap_start, gap.gap_end)
                self.logger.info(
                    f"[1m] {gap.symbol} {gap.missing_start}~{gap.missing_end} "
                    f"missing={gap.missing_bars}, candles={candle_count}, funding={funding_count}, apply={self.apply_changes}"
                )

    def run_range_backfill(
        self,
        *,
        symbols: list[str],
        start_ts: datetime,
        end_ts: datetime,
        include_15m: bool,
        include_1m: bool,
        include_funding: bool,
    ) -> None:
        """사용자가 지정한 명시적 기간을 기준으로 선택된 데이터 종류를 backfill합니다."""
        if not symbols:
            raise ValueError("--mode range에서는 --symbols가 필요합니다.")

        for symbol in symbols:
            if include_15m:
                if symbol in self.oi_unavailable_symbols:
                    self.logger.warning(f"[range 15m 스킵] {symbol} OI 이력 미지원 캐시로 건너뜁니다.")
                    continue
                try:
                    candle_count, oi_count = self.backfill_15m_range(symbol, start_ts, end_ts)
                    self.logger.info(f"[range 15m] {symbol} candles={candle_count}, oi={oi_count}, apply={self.apply_changes}")
                except OIHistoryUnavailable as exc:
                    self.oi_unavailable_symbols.add(symbol)
                    self.logger.error(f"[range 15m 실패] {symbol} OI 이력 확보 실패. 저장하지 않습니다. reason={exc}")
                except Exception as exc:
                    self.logger.error(f"[range 15m 실패] {symbol} {start_ts}~{end_ts}, reason={exc}")
            if include_1m:
                candle_count = self.backfill_1m_range(symbol, start_ts, end_ts)
                self.logger.info(f"[range 1m] {symbol} candles={candle_count}, apply={self.apply_changes}")
            if include_funding:
                funding_count = self.backfill_funding_range(symbol, start_ts, end_ts)
                self.logger.info(f"[range funding] {symbol} funding={funding_count}, apply={self.apply_changes}")

    def cleanup_btc_offgrid_rows(self) -> int:
        """BTCUSDT 15분봉 기준에 맞지 않는 `candles` row를 조회하거나 삭제합니다."""
        query = """
            SELECT symbol, timestamp
            FROM candles
            WHERE symbol = 'BTCUSDT'
              AND (
                  EXTRACT(SECOND FROM timestamp) <> 0
                  OR MOD(EXTRACT(MINUTE FROM timestamp)::int, 15) <> 0
              )
            ORDER BY timestamp
        """
        with self.db._ensure_conn().cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

        for symbol, timestamp in rows:
            self.logger.warning(f"BTCUSDT 비정렬 row 발견: {symbol} {timestamp}")

        if self.apply_changes and rows:
            delete_query = """
                DELETE FROM candles
                WHERE symbol = 'BTCUSDT'
                  AND (
                      EXTRACT(SECOND FROM timestamp) <> 0
                      OR MOD(EXTRACT(MINUTE FROM timestamp)::int, 15) <> 0
                  )
            """
            with self.db._ensure_conn().cursor() as cur:
                cur.execute(delete_query)
                deleted_rows = cur.rowcount
            self.logger.warning(f"BTCUSDT 비정렬 row 삭제 완료: {deleted_rows}개")
            return int(deleted_rows)

        self.logger.info(f"BTCUSDT 비정렬 row dry-run: {len(rows)}개")
        return len(rows)


def main() -> None:
    """CLI 인자를 해석하고 gap/range backfill 또는 BTCUSDT 비정렬 row 정리를 실행합니다."""
    parser = argparse.ArgumentParser(
        description="DB gap 기반 15m/OI/1m/funding REST backfill 및 BTCUSDT 비정렬 row 정리 도구"
    )
    parser.add_argument("--mode", choices=["gaps", "range"], default="gaps", help="DB gap 자동 탐색 또는 명시 기간 backfill")
    parser.add_argument("--symbols", default="", help="쉼표 구분 심볼 목록. gaps 모드에서 비우면 전체 심볼 gap을 탐색")
    parser.add_argument("--start", default="", help="range 모드 시작 시각. 예: 2026-04-14T08:35:00+00:00")
    parser.add_argument("--end", default="", help="range 모드 종료 시각. 예: 2026-04-14T09:56:00+00:00")
    parser.add_argument("--skip-15m", action="store_true", help="15분봉/OI backfill을 건너뜁니다.")
    parser.add_argument("--skip-1m", action="store_true", help="1분봉 backfill을 건너뜁니다.")
    parser.add_argument("--skip-funding", action="store_true", help="funding rate backfill을 건너뜁니다.")
    parser.add_argument("--max-gaps", type=int, default=0, help="gaps 모드에서 처리할 최대 gap 수. 0이면 제한 없음")
    parser.add_argument("--max-calls-per-minute", type=int, default=600, help="전역 REST 호출 상한/분")
    parser.add_argument("--delete-btc-offgrid", action="store_true", help="BTCUSDT 15분 비정렬 row를 조회/삭제합니다.")
    parser.add_argument("--include-non-usdt", action="store_true", help="기본 제외되는 USDC 등 비-USDT 심볼도 포함합니다.")
    parser.add_argument("--scan-only", action="store_true", help="REST 호출 없이 DB gap 목록만 출력합니다.")
    parser.add_argument("--apply", action="store_true", help="실제 DB upsert/delete를 수행합니다. 생략하면 dry-run")
    args = parser.parse_args()

    symbols = GapBackfillService._parse_symbol_list(args.symbols)
    if symbols and not args.include_non_usdt:
        original_symbols = symbols
        symbols = GapBackfillService._filter_quote_asset_symbols(symbols)
        excluded_symbols = sorted(set(original_symbols) - set(symbols))
        if excluded_symbols:
            logger.warning(
                f"비-{StrategyConfig.QUOTE_ASSET} 심볼 제외: {', '.join(excluded_symbols)} "
                f"(포함하려면 --include-non-usdt 사용)"
            )
    db = DBManager()
    db.connect()
    db.init_market_data_tables()

    try:
        include_15m = not args.skip_15m
        include_1m = not args.skip_1m
        include_funding = not args.skip_funding
        needs_rest_backfill = (include_15m or include_1m or include_funding) and not args.scan_only
        binance = BinanceClient() if needs_rest_backfill else None
        service = GapBackfillService(
            binance_client=binance,
            db_manager=db,
            apply_changes=bool(args.apply),
            max_calls_per_minute=args.max_calls_per_minute,
            logger=logger,
        )

        logger.info(f"gap backfill 시작: mode={args.mode}, symbols={symbols or 'ALL'}, apply={args.apply}")

        if args.delete_btc_offgrid:
            service.cleanup_btc_offgrid_rows()

        if args.mode == "gaps":
            service.run_gap_backfill(
                symbols=symbols,
                include_15m=include_15m,
                include_1m=include_1m,
                include_funding=include_funding,
                max_gaps=args.max_gaps,
                include_non_usdt=bool(args.include_non_usdt),
                scan_only=bool(args.scan_only),
            )
        else:
            if not args.start or not args.end:
                raise ValueError("--mode range에서는 --start와 --end가 필요합니다.")
            start_ts = GapBackfillService._parse_utc_datetime(args.start)
            end_ts = GapBackfillService._parse_utc_datetime(args.end)
            service.run_range_backfill(
                symbols=symbols,
                start_ts=start_ts,
                end_ts=end_ts,
                include_15m=include_15m,
                include_1m=include_1m,
                include_funding=include_funding,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
