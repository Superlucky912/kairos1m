from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from collector.rest_collector import RestCollector
from collector.synthesize_timeframes_main import TimeframeSynthesizer
from collector.ws_collector import WSCollector
from common.binance.binance_client import BinanceClient
from common.config.base_config import Config
from common.config.strategy_config import StrategyConfig
from common.database.manager import DBManager
from common.ipc.zmq_pubsub import ZMQReplyServer
from common.utils.logger import setup_logger


KST = ZoneInfo("Asia/Seoul")
logger = setup_logger("Kairos.Collector", Config.LOG_DIR / "collector" / "collector.log", rotation="daily")


class CollectorService:
    """Own websocket streaming and periodic REST persistence."""

    def __init__(self, bootstrap_days: int, scheduled_days: int = 1):
        self.bootstrap_days = bootstrap_days
        self.scheduled_days = scheduled_days
        self.binance = BinanceClient()
        self.db = DBManager()
        self.db.connect()
        self.rest_collector = RestCollector(self.binance, self.db, days=bootstrap_days, logger=logger)
        self.ws_collector = WSCollector(self.binance, db_manager=self.db, logger=logger)
        self.symbol_rpc = ZMQReplyServer(StrategyConfig.MARKETDATA_SYMBOLS_RPC_ENDPOINT)
        self._rest_task: asyncio.Task[None] | None = None
        self._symbol_task: asyncio.Task[None] | None = None
        self._symbol_rpc_task: asyncio.Task[None] | None = None
        self._synthesis_task: asyncio.Task[None] | None = None
        self._current_ws_symbols: list[str] = []

    def _get_next_ws_symbol_apply_time(self, now: datetime) -> datetime:
        """
        웹소켓 심볼 교체는 현재 1분봉의 close 이벤트가 흐른 뒤 적용합니다.

        심볼 교체를 분봉 중간에 즉시 반영하면 기존 구독 심볼의 close 이벤트가
        마지막에 끊길 수 있으므로, 다음 1분 경계 + close 대기 시간 이후로 미룹니다.
        """
        boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return boundary + timedelta(seconds=StrategyConfig.CLOSE_EVENT_MAX_WAIT_SECONDS)

    async def start(self) -> None:
        symbols = self._select_ws_symbols()
        if not symbols:
            raise RuntimeError("No active symbols available for collector startup")

        self._current_ws_symbols = list(symbols)
        logger.info(f"수집기 시작: 웹소켓 {len(symbols)}개 종목 연결")
        await self.ws_collector.start(symbols)
        self._rest_task = asyncio.create_task(self._run_rest_sync_loop())
        self._symbol_task = asyncio.create_task(self._run_symbol_refresh_loop())
        self._symbol_rpc_task = asyncio.create_task(self._run_symbol_rpc_loop())
        if StrategyConfig.COLLECTOR_SYNTHESIZE_TIMEFRAMES_ENABLED:
            self._synthesis_task = asyncio.create_task(self._run_timeframe_synthesis_loop())

        if self.bootstrap_days > 0:
            bootstrap_1m_candles = self.bootstrap_days * 24 * 60
            logger.info(
                f"Running bootstrap REST sync for {self.bootstrap_days} days "
                f"(1m backfill={bootstrap_1m_candles} candles)"
            )
            await asyncio.to_thread(
                self.rest_collector.run_parallel_collection,
                self.bootstrap_days,
                intrabar_1m_candles=bootstrap_1m_candles,
            )

    async def run_forever(self) -> None:
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self._rest_task is not None:
            self._rest_task.cancel()
            try:
                await self._rest_task
            except asyncio.CancelledError:
                pass

        if self._symbol_task is not None:
            self._symbol_task.cancel()
            try:
                await self._symbol_task
            except asyncio.CancelledError:
                pass

        if self._symbol_rpc_task is not None:
            self._symbol_rpc_task.cancel()
            try:
                await self._symbol_rpc_task
            except asyncio.CancelledError:
                pass

        if self._synthesis_task is not None:
            self._synthesis_task.cancel()
            try:
                await self._synthesis_task
            except asyncio.CancelledError:
                pass

        if self.ws_collector.is_running:
            await self.ws_collector.stop()

        self.symbol_rpc.close()
        self.db.close()
        logger.info("Collector service stopped")

    async def _run_symbol_rpc_loop(self) -> None:
        while True:
            try:
                request = await self.symbol_rpc.recv()
                req_type = request.get("type") if isinstance(request, dict) else None
                if req_type == "get_symbols":
                    await self.symbol_rpc.send({"symbols": list(self._current_ws_symbols)})
                else:
                    await self.symbol_rpc.send({"error": "unsupported_request"})
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"?? ?? RPC ??: {exc}")
                await asyncio.sleep(1)

    async def _run_rest_sync_loop(self) -> None:
        while True:
            now = datetime.now(KST)
            quarter_minute = (now.minute // 15) * 15
            target = now.replace(
                minute=quarter_minute,
                second=StrategyConfig.REST_SYNC_DELAY_SECONDS,
                microsecond=0,
            )
            if now >= target:
                target += timedelta(minutes=15)

            wait_seconds = max((target - now).total_seconds(), 1.0)
            logger.info(f"다음 DB 저장 예정: {target.isoformat()} ({wait_seconds / 60:.1f}분 후)")
            await asyncio.sleep(wait_seconds)

            logger.info("DB 저장중...")
            try:
                await asyncio.to_thread(
                    self.rest_collector.run_parallel_collection,
                    self.scheduled_days,
                    intrabar_1m_candles=self.rest_collector.scheduled_intrabar_candles,
                )
                logger.info("DB 저장 완료")
            except Exception as exc:
                logger.error(f"예약된 DB 저장 실패: {exc}")

    def _next_synthesis_time(self, now: datetime, minutes: int, delay_seconds: int) -> datetime:
        """다음 timeframe 경계와 지연 시간을 합친 실행 시각을 계산합니다."""
        boundary = now.replace(second=0, microsecond=0)
        minute_remainder = boundary.minute % minutes
        if minute_remainder:
            boundary += timedelta(minutes=minutes - minute_remainder)
        target = boundary + timedelta(seconds=delay_seconds)
        if now >= target:
            target = boundary + timedelta(minutes=minutes, seconds=delay_seconds)
        return target

    async def _run_timeframe_synthesis_loop(self) -> None:
        """
        `candles_1m`에서 `candles_5m`/`candles_15m`를 주기적으로 합성합니다.

        5분봉은 5분 경계 직후 실행하고, 15분봉은 `candles` REST/OI 저장이 끝난 뒤 실행되도록
        더 긴 지연을 둡니다. collector의 웹소켓/REST 수집 task와 분리해 합성 실패가 수집 중단으로
        이어지지 않게 합니다.
        """
        try:
            logger.info("timeframe 합성 스케줄 시작: 5m/15m")
            for timeframe in ("5m", "15m"):
                await self._run_timeframe_synthesis_once(timeframe)

            next_runs = {
                "5m": self._next_synthesis_time(
                    datetime.now(KST),
                    5,
                    StrategyConfig.COLLECTOR_SYNTH_5M_DELAY_SECONDS,
                ),
                "15m": self._next_synthesis_time(
                    datetime.now(KST),
                    15,
                    StrategyConfig.COLLECTOR_SYNTH_15M_DELAY_SECONDS,
                ),
            }
            logger.info(
                "다음 timeframe 합성 예정: 5m=%s, 15m=%s",
                next_runs["5m"].isoformat(),
                next_runs["15m"].isoformat(),
            )

            while True:
                now = datetime.now(KST)
                wait_seconds = min((target - now).total_seconds() for target in next_runs.values())
                await asyncio.sleep(max(wait_seconds, 1.0))

                now = datetime.now(KST)
                for timeframe in ("5m", "15m"):
                    if now < next_runs[timeframe]:
                        continue
                    await self._run_timeframe_synthesis_once(timeframe)
                    minutes = 5 if timeframe == "5m" else 15
                    delay_seconds = (
                        StrategyConfig.COLLECTOR_SYNTH_5M_DELAY_SECONDS
                        if timeframe == "5m"
                        else StrategyConfig.COLLECTOR_SYNTH_15M_DELAY_SECONDS
                    )
                    next_runs[timeframe] = self._next_synthesis_time(
                        datetime.now(KST),
                        minutes,
                        delay_seconds,
                    )
                    logger.info("%s 다음 합성 예정: %s", timeframe, next_runs[timeframe].isoformat())
        except asyncio.CancelledError:
            logger.info("timeframe 합성 스케줄 종료")
            raise

    async def _run_timeframe_synthesis_once(self, timeframe: str) -> None:
        """단일 timeframe 합성을 thread로 실행해 event loop를 막지 않습니다."""
        try:
            summary = await asyncio.to_thread(self._run_timeframe_synthesis_sync, timeframe)
            if summary is not None:
                logger.info("%s 합성 완료: %s", timeframe, summary)
        except Exception as exc:
            logger.error("%s 합성 실패: %s", timeframe, exc)

    def _run_timeframe_synthesis_sync(self, timeframe: str) -> dict[str, int] | None:
        """collector 스케줄에서 호출하는 동기 합성 실행 본문입니다."""
        if timeframe not in TimeframeSynthesizer.TIMEFRAME_SPEC:
            raise ValueError(f"지원하지 않는 timeframe입니다: {timeframe}")

        report_path = Path("common/logs/backtest/synthesize_timeframes_collector.json")
        synthesizer = TimeframeSynthesizer(apply=True, batch_days=1, report_path=report_path)
        try:
            spec = TimeframeSynthesizer.TIMEFRAME_SPEC[timeframe]
            target_table = str(spec["table"])
            with synthesizer.conn.cursor() as cur:
                cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM candles_1m")
                source_min, source_max = cur.fetchone()
                cur.execute(f"SELECT MAX(timestamp) FROM {target_table}")
                target_max = cur.fetchone()[0]

            if source_min is None or source_max is None:
                logger.warning("%s 합성 스킵: candles_1m 데이터 없음", timeframe)
                return None

            source_end = source_max + timedelta(minutes=1)
            if target_max is None:
                lookback = timedelta(hours=StrategyConfig.COLLECTOR_SYNTH_EMPTY_TABLE_LOOKBACK_HOURS)
                start_ts = max(source_min, source_end - lookback)
            else:
                start_ts = target_max

            if start_ts >= source_end:
                logger.info("%s 합성 스킵: 최신 상태(start=%s, end=%s)", timeframe, start_ts, source_end)
                return None

            results = synthesizer.run([timeframe], start_ts=start_ts, end_ts=source_end)
            summary = TimeframeSynthesizer.summarize(results).get(timeframe, {})
            return {key: int(value) for key, value in summary.items()}
        finally:
            synthesizer.close()

    async def _run_symbol_refresh_loop(self) -> None:
        while True:
            try:
                now = datetime.now(KST)
                target = now.replace(minute=10, second=0, microsecond=0)
                if now >= target:
                    target += timedelta(hours=1)

                wait_seconds = max((target - now).total_seconds(), 1.0)
                logger.info(f"다음 심볼 갱신 예정: {target.isoformat()} ({wait_seconds / 60:.1f}분 후)")
                await asyncio.sleep(wait_seconds)
                symbols = self._select_ws_symbols()
                if symbols and symbols != self._current_ws_symbols and self.ws_collector.is_running:
                    apply_at = self._get_next_ws_symbol_apply_time(datetime.now(KST))
                    apply_wait_seconds = max((apply_at - datetime.now(KST)).total_seconds(), 0.0)
                    if apply_wait_seconds > 0:
                        logger.info(
                            f"웹소켓 심볼 갱신 예약: {apply_at.isoformat()} "
                            f"(현재 봉 close 보호를 위해 {apply_wait_seconds:.1f}초 후 적용)"
                        )
                        await asyncio.sleep(apply_wait_seconds)
                    self._current_ws_symbols = list(symbols)
                    await self.ws_collector.update_symbols(symbols)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"웹소켓 종목 갱신 실패: {exc}")
                await asyncio.sleep(60)

    def _select_ws_symbols(self) -> list[str]:
        symbols = self.binance.get_top_symbols_by_change(limit=StrategyConfig.TOP_COIN_COUNT)
        if "BTCUSDT" not in symbols:
            symbols.append("BTCUSDT")
        if not symbols:
            symbols = ["BTCUSDT"]
        logger.info(f"웹소켓 대상 {len(symbols)}개 선정: {', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''}")
        return symbols


async def _async_main() -> None:
    parser = argparse.ArgumentParser(description="Kairos collector service")
    parser.add_argument("--days", type=int, default=0, help="Startup REST bootstrap days")
    parser.add_argument("--scheduled-days", type=int, default=1, help="REST sync days every 15 minutes + delay")
    args = parser.parse_args()

    service = CollectorService(bootstrap_days=args.days, scheduled_days=args.scheduled_days)
    await service.run_forever()


def main() -> None:
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        logger.info("수집기 종료 요청 수신")


if __name__ == "__main__":
    main()
