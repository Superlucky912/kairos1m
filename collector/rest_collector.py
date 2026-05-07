from __future__ import annotations

import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from logging import Logger
from typing import Any, Callable, cast

import pandas as pd

from common.binance.binance_client import BinanceClient
from common.config.strategy_config import StrategyConfig
from common.database.manager import DBManager
from common.utils.logger import logger as default_logger


class RestCollector:
    """REST 기반 과거 캔들 수집, OI/funding 보정, DB 적재를 담당한다."""

    def __init__(self, binance_client: BinanceClient, db_manager: DBManager, days: int = 30, logger: Logger | None = None):
        self.api = binance_client
        self.db = db_manager
        self.interval = StrategyConfig.COLLECTOR_REST_INTERVAL
        self.intrabar_interval = StrategyConfig.COLLECTOR_INTRABAR_INTERVAL
        self.days = days
        self.required_candles = days * 24 * 4
        self.scheduled_intrabar_candles = max(0, int(StrategyConfig.COLLECTOR_1M_REST_BACKFILL_MINUTES))
        self.rate_limit_wait = 65
        self.logger = logger or default_logger
        self.logger.info(
            f"REST 수집기 준비 완료 ({self.interval}, 기본 {self.days}일, 목표 {self.required_candles}개, "
            f"{self.intrabar_interval} backfill {self.scheduled_intrabar_candles}개)"
        )

    def get_active_symbols(self) -> list[str]:
        """거래 가능한 USDT-M perpetual 심볼 목록을 반환한다."""
        exchange_info = self.api.get_exchange_info()
        if not exchange_info:
            return []

        symbols = [
            s["symbol"]
            for s in exchange_info["symbols"]
            if s["status"] == "TRADING"
            and s["quoteAsset"] == StrategyConfig.QUOTE_ASSET
            and s["contractType"] == "PERPETUAL"
        ]
        self.logger.info(f"활성 {StrategyConfig.QUOTE_ASSET}-M 종목 수: {len(symbols)}")
        return symbols

    def _safe_api_call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        max_retries = 3
        for i in range(max_retries):
            try:
                time.sleep(random.uniform(0.5, 1.0))
                return func(*args, **kwargs)
            except Exception as e:
                if "1003" in str(e) or "429" in str(e):
                    self.logger.warning(f"API 호출 제한 감지, {self.rate_limit_wait}초 대기 ({i + 1}/{max_retries})")
                    time.sleep(self.rate_limit_wait)
                else:
                    self.logger.error(f"API 호출 실패: {e}")
                    if i == max_retries - 1:
                        raise e
                    time.sleep(2)
        return None

    def _fetch_klines(self, symbol: str, interval: str, target_count: int) -> list[Any]:
        all_klines: list[Any] = []
        end_time: int | None = None
        while len(all_klines) < target_count:
            fetch_limit = min(target_count - len(all_klines), 1000)
            klines = self._safe_api_call(
                self.api.get_klines,
                symbol,
                interval,
                limit=fetch_limit,
                endTime=end_time,
            )
            if not klines:
                break
            all_klines = klines + all_klines
            end_time = klines[0][0] - 1
            if len(klines) < fetch_limit:
                break
        return all_klines

    @staticmethod
    def _klines_to_frame(all_klines: list[Any], symbol: str, *, default_open_interest: float = 0.0) -> pd.DataFrame:
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
        df_klines["open_interest"] = float(default_open_interest)
        return df_klines

    def fetch_symbol_candle_data(self, symbol: str, days: int | None = None) -> pd.DataFrame:
        """특정 심볼의 캔들 데이터와 OI 데이터를 수집해 하나의 DataFrame으로 합친다."""
        target_count = (days * 24 * 4) if days is not None else self.required_candles
        batch_size = 500

        all_klines = self._fetch_klines(symbol, self.interval, target_count)
        df_klines = self._klines_to_frame(all_klines, symbol)
        if df_klines.empty:
            return pd.DataFrame()

        # 15분봉 OI는 별도 REST 응답으로 덮어쓰므로, merge 전에 기본값 컬럼을 제거해
        # pandas가 `open_interest_x/open_interest_y`를 만들지 않게 한다.
        df_klines = df_klines.drop(columns=["open_interest"], errors="ignore")

        all_oi: list[Any] = []
        oi_end_time: int | None = None
        first_kline_ms = all_klines[0][0]
        while len(all_oi) < target_count:
            fetch_limit = min(target_count - len(all_oi), batch_size)
            oi_data = self._safe_api_call(
                self.api.get_open_interest_history,
                symbol,
                self.interval,
                limit=fetch_limit,
                endTime=oi_end_time,
            )
            if not oi_data:
                break
            all_oi = oi_data + all_oi
            oi_end_time = oi_data[0]["timestamp"] - 1
            if oi_data[0]["timestamp"] <= first_kline_ms or len(oi_data) < fetch_limit:
                break

        if all_oi:
            df_oi = pd.DataFrame(all_oi)
            df_oi["timestamp"] = pd.to_datetime(df_oi["timestamp"], unit="ms", utc=True).dt.round("1s")
            df_oi = cast(pd.DataFrame, df_oi[["timestamp", "sumOpenInterest"]].copy())
            df_oi.columns = ["timestamp", "open_interest"]
            df_oi = df_oi.drop_duplicates(subset="timestamp")
            df_klines = cast(pd.DataFrame, pd.merge(df_klines, df_oi, on="timestamp", how="left"))
            df_klines["open_interest"] = df_klines["open_interest"].ffill().bfill().fillna(0.0)
        else:
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
        ].astype({"open_interest": float})
        return cast(pd.DataFrame, result)

    def fetch_symbol_intrabar_data(self, symbol: str, candles_count: int) -> pd.DataFrame:
        """특정 심볼의 1분봉 캔들을 수집해 `candles_1m` 저장용 DataFrame으로 만든다."""
        if candles_count <= 0:
            return pd.DataFrame()

        all_klines = self._fetch_klines(symbol, self.intrabar_interval, candles_count)
        df_klines = self._klines_to_frame(all_klines, symbol)
        if df_klines.empty:
            return df_klines

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
        ].astype({"open_interest": float})
        return cast(pd.DataFrame, result)

    def collect_symbol_data(
        self,
        symbol: str,
        days: int | None = None,
        intrabar_1m_candles: int = 0,
    ) -> bool:
        """단일 심볼에 대한 REST 수집과 DB 적재를 수행한다."""
        try:
            final_df = self.fetch_symbol_candle_data(symbol, days)
            if final_df.empty:
                return False

            self.db.save_candles(final_df)

            if intrabar_1m_candles > 0:
                intrabar_df = self.fetch_symbol_intrabar_data(symbol, intrabar_1m_candles)
                if not intrabar_df.empty:
                    self.db.save_candles_1m(intrabar_df)

            funding_data = self._safe_api_call(self.api.get_funding_rate, symbol, limit=100)
            if funding_data:
                df_funding = pd.DataFrame(funding_data)
                df_funding["timestamp"] = pd.to_datetime(df_funding["fundingTime"], unit="ms", utc=True).dt.round("1s")
                df_funding["symbol"] = symbol
                df_funding["funding_rate"] = df_funding["fundingRate"].astype(float)
                self.db.save_funding_rates(df_funding[["timestamp", "symbol", "funding_rate"]])

            match_rate = (final_df["open_interest"] > 0).mean() * 100
            self.logger.debug(f"[{symbol}] collected={len(final_df)} rows (OI match: {match_rate:.1f}%)")
            return True
        except Exception as e:
            self.logger.error(f"[{symbol}] 수집 실패: {e}")
            return False

    def run_parallel_collection(self, days: int | None = None, intrabar_1m_candles: int = 0) -> None:
        """전체 심볼에 대한 배치 REST 수집을 병렬로 수행한다."""
        symbols = self.get_active_symbols()
        if not symbols:
            return

        cpu_count = os.cpu_count() or 4
        max_workers = max(1, cpu_count - 3)

        pending_symbols = symbols.copy()
        pass_round = 1
        total_symbols = len(symbols)
        start_time = time.time()

        while pending_symbols and pass_round <= 5:
            current_count = len(pending_symbols)
            self.logger.info(f"REST 수집 {pass_round}차 시작 ({current_count}/{total_symbols})")

            random.shuffle(pending_symbols)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(
                    executor.map(
                        lambda sym: self.collect_symbol_data(
                            sym,
                            days=days,
                            intrabar_1m_candles=intrabar_1m_candles,
                        ),
                        pending_symbols,
                    )
                )

            failed_symbols = [sym for sym, success in zip(pending_symbols, results) if not success]
            if not failed_symbols:
                self.logger.info(f"전체 종목 수집 완료 ({total_symbols}개)")
                break

            self.logger.warning(f"{pass_round}차 수집 실패 {len(failed_symbols)}개, 10초 후 재시도")
            pending_symbols = failed_symbols
            pass_round += 1
            time.sleep(10)

        end_time = time.time()
        self.logger.info(f"REST 수집 완료 ({end_time - start_time:.2f}초)")
