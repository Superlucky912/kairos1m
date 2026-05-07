from __future__ import annotations

import argparse
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging import Logger

from collector.rest_collector import RestCollector
from common.binance.binance_client import BinanceClient
from common.config.base_config import Config
from common.database.manager import DBManager
from common.utils.logger import setup_logger


logger = setup_logger(
    "Kairos.Backfill30D",
    Config.LOG_DIR / "collector" / "backfill_30d.log",
    rotation="daily",
)


class ApiCallRateLimiter:
    """최근 1분 호출 수를 기준으로 one-shot backfill REST 속도를 제한한다."""

    def __init__(self, max_calls_per_minute: int, logger: Logger):
        self.max_calls_per_minute = max(1, int(max_calls_per_minute))
        self.logger = logger
        self._lock = threading.Lock()
        self._events: deque[float] = deque()
        self._blocked_until = 0.0
        self._last_wait_log_at = 0.0

    def acquire(self) -> None:
        while True:
            sleep_seconds = 0.0
            with self._lock:
                now = time.monotonic()
                while self._events and now - self._events[0] >= 60.0:
                    self._events.popleft()

                if now < self._blocked_until:
                    sleep_seconds = max(self._blocked_until - now, 0.1)
                elif len(self._events) < self.max_calls_per_minute:
                    self._events.append(now)
                    return
                else:
                    oldest_ts = self._events[0]
                    sleep_seconds = max(60.0 - (now - oldest_ts) + 0.05, 0.1)

                if now - self._last_wait_log_at >= 5.0:
                    self._last_wait_log_at = now
                    self.logger.info(
                        "Backfill REST rate-limit 대기 "
                        f"(최근 1분 호출 상한={self.max_calls_per_minute}, 대기={sleep_seconds:.1f}초)"
                    )
            time.sleep(sleep_seconds)

    def register_cooldown(self, wait_seconds: float) -> None:
        with self._lock:
            cooldown_until = time.monotonic() + max(wait_seconds, 0.0)
            if cooldown_until > self._blocked_until:
                self._blocked_until = cooldown_until


class OneShotBackfillCollector(RestCollector):
    """현재 active 심볼 스냅샷에 대해 30일 backfill을 한 번만 수행한다."""

    def __init__(
        self,
        binance_client: BinanceClient,
        db_manager: DBManager,
        *,
        days: int,
        max_workers: int,
        max_calls_per_minute: int,
        logger: Logger | None = None,
    ):
        super().__init__(binance_client, db_manager, days=days, logger=logger)
        self.max_workers = max(1, int(max_workers))
        self.rate_limiter = ApiCallRateLimiter(max_calls_per_minute, self.logger)

    def _safe_api_call(self, func, *args, **kwargs):  # type: ignore[override]
        max_retries = 3
        for i in range(max_retries):
            try:
                self.rate_limiter.acquire()
                time.sleep(random.uniform(0.05, 0.15))
                return func(*args, **kwargs)
            except Exception as exc:
                if "1003" in str(exc) or "429" in str(exc):
                    self.logger.warning(
                        f"API 호출 제한 감지, {self.rate_limit_wait}초 cooldown 등록 ({i + 1}/{max_retries})"
                    )
                    self.rate_limiter.register_cooldown(self.rate_limit_wait)
                    if i == max_retries - 1:
                        raise
                    continue

                self.logger.error(f"API 호출 실패: {exc}")
                if i == max_retries - 1:
                    raise
                time.sleep(2)
        return None

    def run_snapshot_collection(
        self,
        symbols: list[str],
        *,
        days: int | None,
        intrabar_1m_candles: int,
    ) -> None:
        if not symbols:
            self.logger.warning("Backfill 대상 심볼이 없어 종료합니다.")
            return

        pending_symbols = list(symbols)
        total_symbols = len(pending_symbols)
        pass_round = 1
        start_time = time.time()

        while pending_symbols and pass_round <= 5:
            current_count = len(pending_symbols)
            self.logger.info(
                f"30일 one-shot backfill {pass_round}차 시작 "
                f"({current_count}/{total_symbols}, workers={self.max_workers})"
            )

            failed_symbols: list[str] = []
            completed = 0

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_map = {
                    executor.submit(
                        self.collect_symbol_data,
                        symbol,
                        days,
                        intrabar_1m_candles,
                    ): symbol
                    for symbol in pending_symbols
                }

                for future in as_completed(future_map):
                    symbol = future_map[future]
                    completed += 1
                    try:
                        success = bool(future.result())
                    except Exception as exc:
                        self.logger.error(f"[{symbol}] backfill worker 실패: {exc}")
                        success = False

                    if not success:
                        failed_symbols.append(symbol)

                    if completed % 25 == 0 or completed == current_count:
                        self.logger.info(
                            "30일 one-shot backfill 진행률 "
                            f"({completed}/{current_count}, 실패 누적={len(failed_symbols)})"
                        )

            if not failed_symbols:
                self.logger.info(f"30일 one-shot backfill 완료 ({total_symbols}개)")
                break

            self.logger.warning(
                f"{pass_round}차 backfill 실패 {len(failed_symbols)}개, 15초 후 재시도"
            )
            pending_symbols = failed_symbols
            pass_round += 1
            time.sleep(15)

        elapsed = time.time() - start_time
        if pending_symbols:
            self.logger.warning(
                f"30일 one-shot backfill 종료: 미완료 {len(pending_symbols)}개 "
                f"(총 {elapsed:.2f}초)"
            )
        else:
            self.logger.info(f"30일 one-shot backfill 총 소요 {elapsed:.2f}초")


def _parse_symbol_list(symbols_arg: str) -> list[str]:
    symbols = [symbol.strip().upper() for symbol in symbols_arg.split(",") if symbol.strip()]
    # 순서 보존 dedupe
    return list(dict.fromkeys(symbols))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="현재 active 심볼 스냅샷 기준 30일치 15m/OI/1m/funding one-shot backfill"
    )
    parser.add_argument("--days", type=int, default=30, help="수집할 과거 일수 (기본 30)")
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="동시 수집 worker 수 (기본 4)",
    )
    parser.add_argument(
        "--max-calls-per-minute",
        type=int,
        default=1200,
        help="전역 REST 호출 상한/분 (기본 1200)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="쉼표 구분 심볼 목록. 비우면 현재 active 심볼 전체",
    )
    args = parser.parse_args()

    binance = BinanceClient()
    db = DBManager()
    db.connect()
    db.init_market_data_tables()

    try:
        collector = OneShotBackfillCollector(
            binance,
            db,
            days=args.days,
            max_workers=args.workers,
            max_calls_per_minute=args.max_calls_per_minute,
            logger=logger,
        )

        symbols = _parse_symbol_list(args.symbols)
        if not symbols:
            symbols = collector.get_active_symbols()

        intrabar_1m_candles = args.days * 24 * 60
        logger.info(
            "30일 one-shot backfill 시작 "
            f"(심볼 {len(symbols)}개, days={args.days}, 1m={intrabar_1m_candles}개, "
            f"workers={args.workers}, rpm={args.max_calls_per_minute})"
        )
        collector.run_snapshot_collection(
            symbols,
            days=args.days,
            intrabar_1m_candles=intrabar_1m_candles,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
