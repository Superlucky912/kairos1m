from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from logging import Logger
from typing import Any, Awaitable, Callable, Sequence

import pandas as pd

from common.binance.binance_client import BinanceClient
from common.binance.websocket_manager import BinanceWSManager
from common.config.strategy_config import StrategyConfig
from common.database.manager import DBManager
from common.ipc.zmq_pubsub import ZMQPublisher
from common.utils.logger import logger as default_logger
from common.utils.time_utils import to_kst

WSMessageCallback = Callable[[Any], Awaitable[None] | None]


class WSCollector:
    """Receive Binance websocket market data and publish it over ZMQ."""

    def __init__(
        self,
        binance_client: BinanceClient,
        db_manager: DBManager | None = None,
        logger: Logger | None = None,
    ):
        self.binance = binance_client
        self.db = db_manager
        self.manager = BinanceWSManager(binance_client)
        self.publisher = ZMQPublisher(StrategyConfig.MARKETDATA_ZMQ_ENDPOINT)
        self._callback: WSMessageCallback | None = None
        self._current_symbols: list[str] = []
        self._primary_interval = StrategyConfig.COLLECTOR_PRIMARY_INTERVAL
        self._intrabar_interval = StrategyConfig.COLLECTOR_INTRABAR_INTERVAL
        self.logger = logger or default_logger
        self._messages_since_heartbeat = 0
        self._last_message_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None
        self._first_message_logged = False
        self._close_event_symbols: dict[pd.Timestamp, set[str]] = {}
        self._kline_event_symbols: dict[pd.Timestamp, set[str]] = {}
        self._close_event_first_seen_at: dict[pd.Timestamp, float] = {}
        self._close_event_tasks: dict[pd.Timestamp, asyncio.Task[None]] = {}
        self._close_event_tracked_symbols: dict[pd.Timestamp, set[str]] = {}
        self._close_event_finalized_at: dict[pd.Timestamp, float] = {}
        self._consecutive_unhealthy_batches = 0
        self._last_ws_reconnect_at = 0.0
        self._pending_ws_reconnect_task: asyncio.Task[None] | None = None

    def _rebase_open_target_snapshots(self, symbols: Sequence[str]) -> int:
        snapshot = set(symbols)
        rebased_targets = 0
        for target_ts, existing_symbols in list(self._close_event_tracked_symbols.items()):
            if target_ts in self._close_event_finalized_at:
                continue
            if existing_symbols == snapshot:
                continue
            self._close_event_tracked_symbols[target_ts] = set(snapshot)
            rebased_targets += 1
        return rebased_targets

    def _reset_runtime_counters(self) -> None:
        self._messages_since_heartbeat = 0
        self._last_message_at = None
        self._last_heartbeat_at = datetime.now(timezone.utc)
        self._first_message_logged = False

    def _subscription_intervals(self) -> list[str]:
        """실시간 웹소켓에서 실제로 구독할 캔들 interval 목록을 반환합니다."""
        intervals = [self._primary_interval]
        if (
            StrategyConfig.COLLECTOR_WS_SUBSCRIBE_INTRABAR
            and self._intrabar_interval != self._primary_interval
        ):
            intervals.append(self._intrabar_interval)
        return intervals

    @property
    def is_running(self) -> bool:
        return self.manager._is_running

    async def start(
        self,
        symbols: Sequence[str],
        callback: WSMessageCallback | None = None,
    ) -> None:
        self._callback = callback
        self._current_symbols = list(symbols)
        self._reset_runtime_counters()
        self._close_event_symbols.clear()
        self._kline_event_symbols.clear()
        self._close_event_first_seen_at.clear()
        self._close_event_tracked_symbols.clear()
        self._close_event_finalized_at.clear()
        self._consecutive_unhealthy_batches = 0
        self._last_ws_reconnect_at = 0.0
        self._pending_ws_reconnect_task = None
        self.logger.info(
            f"웹소켓 수집 시작: {len(symbols)}개 종목, 전송 주소 "
            f"{StrategyConfig.MARKETDATA_ZMQ_ENDPOINT}"
        )
        await self.manager.start(
            symbols,
            self._publish_message,
            intervals=self._subscription_intervals(),
        )
        await self.publisher.send({"type": "symbol_update", "symbols": self._current_symbols})
        self.logger.info("웹소켓 연결 요청 완료")

    async def update_symbols(self, symbols: Sequence[str]) -> None:
        self._current_symbols = list(symbols)
        self._consecutive_unhealthy_batches = 0
        self._reset_runtime_counters()
        self.logger.info(f"웹소켓 종목 갱신: {len(symbols)}개")
        rebased_targets = self._rebase_open_target_snapshots(symbols)
        if rebased_targets:
            self.logger.info(
                f"진행 중 collector close 추적 스냅샷 재기준화: {rebased_targets}개 타겟 봉"
            )
        await self.manager.update_symbols(
            symbols,
            intervals=self._subscription_intervals(),
        )
        await self.publisher.send({"type": "symbol_update", "symbols": self._current_symbols})

    async def stop(self) -> None:
        pending_reconnect_task = self._pending_ws_reconnect_task
        self._pending_ws_reconnect_task = None
        if pending_reconnect_task is not None:
            pending_reconnect_task.cancel()
            try:
                await pending_reconnect_task
            except asyncio.CancelledError:
                pass

        for task in list(self._close_event_tasks.values()):
            task.cancel()
        if self._close_event_tasks:
            await asyncio.gather(*self._close_event_tasks.values(), return_exceptions=True)

        await self.manager.stop()
        self.publisher.close()
        self.logger.info("웹소켓 수집 종료")

    async def _publish_message(self, message: Any) -> None:
        self._messages_since_heartbeat += 1
        self._last_message_at = datetime.now(timezone.utc)
        payload = message["data"] if isinstance(message, dict) and "data" in message else message
        should_publish = True

        if isinstance(payload, dict) and payload.get("e") == "kline":
            kline = payload.get("k")
            if isinstance(kline, dict):
                target_ts = pd.to_datetime(int(kline["t"]), unit="ms", utc=True)
                symbol = str(payload.get("s", ""))
                interval = str(kline.get("i", ""))
                if interval == self._primary_interval:
                    self._register_kline_event(symbol, target_ts)
                    if bool(kline.get("x")):
                        await self._save_finalized_1m_kline(symbol, kline)
                        self._register_close_event(symbol, target_ts)
                else:
                    should_publish = False

        if not self._first_message_logged:
            symbol = payload.get("s", "N/A") if isinstance(payload, dict) else "N/A"
            self.logger.info(f"웹소켓 연결 완료, 첫 메시지 수신: {symbol}")
            self._first_message_logged = True

        await self._maybe_log_heartbeat(message)
        if should_publish:
            await self.publisher.send(message)
        if self._callback is None:
            return

        result = self._callback(message)
        if asyncio.iscoroutine(result):
            await result

    async def _save_finalized_1m_kline(self, symbol: str, kline: dict[str, Any]) -> None:
        """마감된 1분봉 웹소켓 캔들을 `candles_1m`에 저장합니다."""
        if self.db is None:
            return
        row = {
            "timestamp": pd.to_datetime(int(kline["t"]), unit="ms", utc=True),
            "symbol": symbol,
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
            "quote_volume": float(kline["q"]),
            "open_interest": 0.0,
            "trades_count": int(kline["n"]),
            "taker_buy_base": float(kline["V"]),
            "taker_buy_quote": float(kline["Q"]),
        }
        await asyncio.to_thread(self.db.save_candles_1m, pd.DataFrame([row]))

    async def _maybe_log_heartbeat(self, message: Any) -> None:
        now = datetime.now(timezone.utc)
        if self._last_heartbeat_at is not None and (now - self._last_heartbeat_at).total_seconds() < 300:
            return

        payload = message["data"] if isinstance(message, dict) and "data" in message else message
        symbol = payload.get("s", "N/A") if isinstance(payload, dict) else "N/A"
        event_type = payload.get("e", "N/A") if isinstance(payload, dict) else "N/A"
        if self._current_symbols:
            await self.publisher.send({"type": "symbol_update", "symbols": self._current_symbols})

        self.logger.info(
            f"웹소켓 상태 확인: 메시지 {self._messages_since_heartbeat}건, "
            f"마지막 종목 {symbol}, 이벤트 {event_type}, "
            f"마지막 수신 {self._last_message_at.isoformat() if self._last_message_at else 'N/A'}"
        )
        self._messages_since_heartbeat = 0
        self._last_heartbeat_at = now

    def _register_kline_event(self, symbol: str, target_ts: pd.Timestamp) -> None:
        self._close_event_tracked_symbols.setdefault(target_ts, set(self._current_symbols))
        self._kline_event_symbols.setdefault(target_ts, set()).add(symbol)

    def _register_close_event(self, symbol: str, target_ts: pd.Timestamp) -> None:
        loop = asyncio.get_running_loop()
        self._prune_finalized_targets(loop.time())
        if target_ts in self._close_event_finalized_at:
            elapsed_after_finalize = loop.time() - self._close_event_finalized_at[target_ts]
            self.logger.info(
                f"collector close 진단 종료 후 지연 수신 무시: {symbol} "
                f"(target_ts={target_ts}, 종료 후 경과={elapsed_after_finalize:.1f}초)"
            )
            return

        self._close_event_tracked_symbols.setdefault(target_ts, set(self._current_symbols))
        self._close_event_first_seen_at.setdefault(target_ts, loop.time())
        self._close_event_symbols.setdefault(target_ts, set()).add(symbol)

        if target_ts not in self._close_event_tasks:
            self._close_event_tasks[target_ts] = asyncio.create_task(
                self._run_close_event_diagnostics(target_ts)
            )

    async def _run_close_event_diagnostics(self, target_ts: pd.Timestamp) -> None:
        loop = asyncio.get_running_loop()
        try:
            first_seen = self._close_event_first_seen_at.setdefault(target_ts, loop.time())
            batch_deadlines = [
                StrategyConfig.CLOSE_EVENT_BATCH_INTERVAL_SECONDS,
                StrategyConfig.CLOSE_EVENT_MAX_WAIT_SECONDS,
            ]
            unique_deadlines = sorted({deadline for deadline in batch_deadlines if deadline > 0})

            for deadline_seconds in unique_deadlines:
                scheduled_at = first_seen + deadline_seconds
                sleep_seconds = scheduled_at - loop.time()
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)

                tracked_symbols = set(self._close_event_tracked_symbols.get(target_ts, set()))
                kline_symbols = set(self._kline_event_symbols.get(target_ts, set()))
                close_symbols = set(self._close_event_symbols.get(target_ts, set()))
                active_symbols = sorted(kline_symbols & tracked_symbols)
                close_received_symbols = sorted(close_symbols & tracked_symbols)
                missing_kline_symbols = sorted(tracked_symbols - kline_symbols)
                kline_without_close_symbols = sorted((kline_symbols & tracked_symbols) - close_symbols)
                elapsed = loop.time() - first_seen
                is_final_round = deadline_seconds >= StrategyConfig.CLOSE_EVENT_MAX_WAIT_SECONDS

                self.logger.info(
                    f"collector close 수신 요약 (타겟 봉: {to_kst(target_ts)}, "
                    f"기대 심볼: {len(tracked_symbols)}개, kline 활성: {len(active_symbols)}개, "
                    f"close 수신: {len(close_received_symbols)}개, 경과: {elapsed:.1f}초)"
                )

                if not is_final_round:
                    if missing_kline_symbols:
                        self.logger.info(
                            f"collector kline 추가 대기 중 미수신 심볼 {len(missing_kline_symbols)}개: "
                            f"{', '.join(missing_kline_symbols[:10])}"
                            f"{'...' if len(missing_kline_symbols) > 10 else ''}"
                        )
                    if kline_without_close_symbols:
                        self.logger.info(
                            f"collector close 추가 대기 중 미수신 심볼 {len(kline_without_close_symbols)}개: "
                            f"{', '.join(kline_without_close_symbols[:10])}"
                            f"{'...' if len(kline_without_close_symbols) > 10 else ''}"
                        )
                    if not missing_kline_symbols and not kline_without_close_symbols:
                        break
                    continue

                self._update_ws_health(target_ts, len(tracked_symbols), len(active_symbols))
                if missing_kline_symbols:
                    self.logger.warning(
                        f"collector kline 자체 미수신 심볼 {len(missing_kline_symbols)}개: "
                        f"{', '.join(missing_kline_symbols[:10])}"
                        f"{'...' if len(missing_kline_symbols) > 10 else ''}"
                    )
                if kline_without_close_symbols:
                    self.logger.warning(
                        f"collector kline 수신 후 close 미도달 심볼 {len(kline_without_close_symbols)}개: "
                        f"{', '.join(kline_without_close_symbols[:10])}"
                        f"{'...' if len(kline_without_close_symbols) > 10 else ''}"
                    )
                break
        finally:
            finalized_at = loop.time()
            self._close_event_finalized_at[target_ts] = finalized_at
            self._prune_finalized_targets(finalized_at)
            self._close_event_symbols.pop(target_ts, None)
            self._kline_event_symbols.pop(target_ts, None)
            self._close_event_first_seen_at.pop(target_ts, None)
            self._close_event_tracked_symbols.pop(target_ts, None)
            self._close_event_tasks.pop(target_ts, None)

    def _prune_finalized_targets(self, now_monotonic: float) -> None:
        retention_seconds = StrategyConfig.COLLECTOR_WS_FINALIZED_RETENTION_SECONDS
        stale_targets = [
            target_ts
            for target_ts, finalized_at in self._close_event_finalized_at.items()
            if now_monotonic - finalized_at >= retention_seconds
        ]
        for target_ts in stale_targets:
            self._close_event_finalized_at.pop(target_ts, None)

    def _update_ws_health(self, target_ts: pd.Timestamp, tracked_count: int, active_count: int) -> None:
        if tracked_count <= 0:
            return

        active_ratio = active_count / tracked_count
        if active_ratio >= StrategyConfig.COLLECTOR_WS_MIN_ACTIVE_RATIO:
            if self._consecutive_unhealthy_batches > 0:
                self.logger.info(
                    f"collector 웹소켓 활성 심볼 비율 회복 "
                    f"(타겟 봉: {to_kst(target_ts)}, active: {active_count}/{tracked_count}, "
                    f"ratio: {active_ratio:.1%})"
                )
            self._consecutive_unhealthy_batches = 0
            return

        self._consecutive_unhealthy_batches += 1
        self.logger.warning(
            f"collector 웹소켓 활성 심볼 비율 저하 감지 "
            f"(타겟 봉: {to_kst(target_ts)}, active: {active_count}/{tracked_count}, "
            f"ratio: {active_ratio:.1%}, 연속: {self._consecutive_unhealthy_batches})"
        )

        if (
            self._consecutive_unhealthy_batches
            >= StrategyConfig.COLLECTOR_WS_RECONNECT_CONSECUTIVE_BATCHES
        ):
            self._schedule_ws_reconnect(
                reason=(
                    f"활성 심볼 비율 저하 감지 "
                    f"({active_count}/{tracked_count}, {active_ratio:.1%}, "
                    f"{self._consecutive_unhealthy_batches}회 연속)"
                )
            )

    def _schedule_ws_reconnect(self, reason: str) -> None:
        pending_task = self._pending_ws_reconnect_task
        if pending_task is not None and not pending_task.done():
            return
        self._pending_ws_reconnect_task = asyncio.create_task(
            self._perform_ws_reconnect(reason)
        )

    async def _perform_ws_reconnect(self, reason: str) -> None:
        loop = asyncio.get_running_loop()
        cooldown_seconds = StrategyConfig.COLLECTOR_WS_RECONNECT_COOLDOWN_SECONDS
        elapsed_since_last = loop.time() - self._last_ws_reconnect_at
        if elapsed_since_last < cooldown_seconds:
            self.logger.info(
                "collector 웹소켓 self-heal 재연결 스킵 "
                f"(cooldown {cooldown_seconds:.0f}초, 남음 {cooldown_seconds - elapsed_since_last:.1f}초)"
            )
            return

        self._last_ws_reconnect_at = loop.time()
        self.logger.warning(f"collector 웹소켓 self-heal 재연결 시도: {reason}")
        try:
            dropped_messages = await self.manager.hard_restart(
                reason,
                drop_pending_messages=True,
            )
            self._consecutive_unhealthy_batches = 0
            self._reset_runtime_counters()
            await self.publisher.send({"type": "symbol_update", "symbols": self._current_symbols})
            self.logger.info(
                "collector 웹소켓 self-heal 재연결 완료 "
                f"(drop backlog: {dropped_messages}건)"
            )
        except Exception as exc:
            self.logger.error(f"collector 웹소켓 self-heal 재연결 실패: {exc}")
