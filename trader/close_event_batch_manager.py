from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pandas as pd

from common.utils.time_utils import to_kst


type CloseBatchProcessor = Callable[[pd.Timestamp, list[str]], Awaitable[None]]
type RunningStateProvider = Callable[[], bool]
type TrackedSymbolsProvider = Callable[[], set[str]]


class CloseEventBatchManager:
    """close 이벤트 기반 추가 배치 추론의 상태와 스케줄링을 전담합니다."""

    def __init__(
        self,
        *,
        logger: Any,
        batch_interval_seconds: float,
        max_wait_seconds: float,
        get_tracked_symbols: TrackedSymbolsProvider,
        is_running: RunningStateProvider,
        process_batch: CloseBatchProcessor,
    ) -> None:
        self.logger = logger
        self.batch_interval_seconds = batch_interval_seconds
        self.max_wait_seconds = max_wait_seconds
        self.get_tracked_symbols = get_tracked_symbols
        self.is_running = is_running
        self.process_batch = process_batch

        self._close_event_symbols: dict[pd.Timestamp, set[str]] = {}
        self._close_event_kline_symbols: dict[pd.Timestamp, set[str]] = {}
        self._close_event_processed_symbols: dict[pd.Timestamp, set[str]] = {}
        self._close_event_first_seen_at: dict[pd.Timestamp, float] = {}
        self._close_event_tasks: dict[pd.Timestamp, asyncio.Task[None]] = {}
        self._close_event_finalized_at: dict[pd.Timestamp, float] = {}
        self._close_event_timeout_symbols: dict[pd.Timestamp, set[str]] = {}
        self._close_event_tracked_symbols: dict[pd.Timestamp, set[str]] = {}

    def rebase_open_target_snapshots(self, tracked_symbols: set[str]) -> int:
        """Replace pending batch snapshots with the latest tracked symbols."""
        snapshot = set(tracked_symbols)
        rebased_targets = 0
        for target_ts, existing_symbols in list(self._close_event_tracked_symbols.items()):
            if target_ts in self._close_event_finalized_at:
                continue
            if existing_symbols == snapshot:
                continue
            self._close_event_tracked_symbols[target_ts] = set(snapshot)
            rebased_targets += 1
        return rebased_targets

    def register_kline(self, symbol: str, target_ts: pd.Timestamp) -> None:
        self._close_event_tracked_symbols.setdefault(
            target_ts,
            set(self.get_tracked_symbols()),
        )
        kline_symbols = self._close_event_kline_symbols.setdefault(target_ts, set())
        kline_symbols.add(symbol)

    def register_close(self, symbol: str, target_ts: pd.Timestamp) -> None:
        """close 이벤트를 누적하고 필요하면 배치 스케줄러를 시작합니다."""
        loop = asyncio.get_running_loop()
        if target_ts in self._close_event_finalized_at:
            timeout_symbols = self._close_event_timeout_symbols.get(target_ts, set())
            if symbol in timeout_symbols:
                elapsed_after_finalize = loop.time() - self._close_event_finalized_at[target_ts]
                self.logger.warning(
                    f"close 이벤트 timeout 이후 지연 수신: {symbol} "
                    f"(target_ts={target_ts}, timeout 후 경과={elapsed_after_finalize:.1f}초)"
                )
                timeout_symbols.discard(symbol)
                if not timeout_symbols:
                    self._close_event_timeout_symbols.pop(target_ts, None)
            return

        self.register_kline(symbol, target_ts)
        self._close_event_tracked_symbols.setdefault(
            target_ts,
            set(self.get_tracked_symbols()),
        )
        first_seen = self._close_event_first_seen_at.setdefault(target_ts, loop.time())
        pending_symbols = self._close_event_symbols.setdefault(target_ts, set())
        is_new_symbol = symbol not in pending_symbols
        pending_symbols.add(symbol)
        if is_new_symbol:
            elapsed = loop.time() - first_seen
            if elapsed >= self.batch_interval_seconds:
                self.logger.info(
                    f"close 이벤트 지연 수신: {symbol} "
                    f"(target_ts={target_ts}, 경과={elapsed:.1f}초)"
                )

        if target_ts not in self._close_event_tasks:
            self._close_event_tasks[target_ts] = asyncio.create_task(
                self._run_close_event_batch(target_ts)
            )

    async def shutdown(self) -> None:
        """대기 중인 close 이벤트 배치 작업을 모두 취소합니다."""
        for task in list(self._close_event_tasks.values()):
            task.cancel()
        if self._close_event_tasks:
            await asyncio.gather(*self._close_event_tasks.values(), return_exceptions=True)

    def is_timed_out_symbol(self, symbol: str, target_ts: pd.Timestamp) -> bool:
        """이미 timeout 처리된 target_ts에서 해당 심볼의 지연 close 로그 대상인지 확인합니다."""
        timeout_symbols = self._close_event_timeout_symbols.get(target_ts)
        if timeout_symbols is None:
            return False
        return symbol in timeout_symbols

    async def _run_close_event_batch(self, target_ts: pd.Timestamp) -> None:
        timed_out_symbols: set[str] = set()
        loop = asyncio.get_running_loop()
        try:
            first_seen = self._close_event_first_seen_at.setdefault(target_ts, loop.time())
            batch_deadlines = [self.batch_interval_seconds, self.max_wait_seconds]
            unique_deadlines = sorted({deadline for deadline in batch_deadlines if deadline > 0})

            for batch_round, deadline_seconds in enumerate(unique_deadlines, start=1):
                if not self.is_running():
                    break

                scheduled_at = first_seen + deadline_seconds
                sleep_seconds = scheduled_at - loop.time()
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)

                tracked_symbols = set(
                    self._close_event_tracked_symbols.get(
                        target_ts,
                        self.get_tracked_symbols(),
                    )
                )
                active_symbols = set(self._close_event_kline_symbols.get(target_ts, set())) & tracked_symbols
                closed_symbols = self._close_event_symbols.get(target_ts, set())
                processed_symbols = self._close_event_processed_symbols.setdefault(target_ts, set())
                ready_symbols = sorted((closed_symbols & active_symbols) - processed_symbols)
                inactive_symbols = sorted(tracked_symbols - active_symbols)
                elapsed = loop.time() - first_seen
                is_final_round = deadline_seconds >= self.max_wait_seconds

                if ready_symbols:
                    phase = "추가 " if batch_round > 1 else ""
                    self.logger.info(
                        f"⏰ close 이벤트 {phase}배치 추론 시작 (타겟 봉: {to_kst(target_ts)}, "
                        f"종목: {len(tracked_symbols)}개, kline 활성: {len(active_symbols)}개, "
                        f"close 수신: {len(closed_symbols & active_symbols)}개, "
                        f"이번 배치: {len(ready_symbols)}개, 경과: {elapsed:.1f}초)"
                    )
                    await self.process_batch(target_ts, ready_symbols)
                    processed_symbols.update(ready_symbols)

                if (
                    not inactive_symbols
                    and active_symbols
                    and active_symbols.issubset(processed_symbols)
                ):
                    break

                if not is_final_round:
                    pending_close_symbols = sorted(active_symbols - closed_symbols)
                    if pending_close_symbols:
                        self.logger.info(
                            f"close 이벤트 추가 대기 중 미수신 심볼 {len(pending_close_symbols)}개: "
                            f"{', '.join(pending_close_symbols[:10])}"
                            f"{'...' if len(pending_close_symbols) > 10 else ''}"
                        )
                    if inactive_symbols:
                        self.logger.info(
                            f"close 이벤트 추가 대기 중 kline 미수신 심볼 {len(inactive_symbols)}개: "
                            f"{', '.join(inactive_symbols[:10])}"
                            f"{'...' if len(inactive_symbols) > 10 else ''}"
                        )

                if is_final_round:
                    missing_close_symbols = sorted(active_symbols - closed_symbols)
                    unprocessed_close_symbols = sorted((closed_symbols & active_symbols) - processed_symbols)
                    remaining_symbols = sorted(active_symbols - processed_symbols)
                    timed_out_symbols = set(missing_close_symbols) | set(inactive_symbols)
                    if remaining_symbols:
                        self.logger.warning(
                            f"close 이벤트 대기 시간 초과로 제외 심볼 {len(remaining_symbols)}개: "
                            f"{', '.join(remaining_symbols[:10])}"
                            f"{'...' if len(remaining_symbols) > 10 else ''}"
                        )
                    if missing_close_symbols:
                        self.logger.warning(
                            f"close 미수신 심볼 {len(missing_close_symbols)}개: "
                            f"{', '.join(missing_close_symbols[:10])}"
                            f"{'...' if len(missing_close_symbols) > 10 else ''}"
                        )
                    if unprocessed_close_symbols:
                        self.logger.warning(
                            f"close 수신 후 미처리 심볼 {len(unprocessed_close_symbols)}개: "
                            f"{', '.join(unprocessed_close_symbols[:10])}"
                            f"{'...' if len(unprocessed_close_symbols) > 10 else ''}"
                        )
                    if inactive_symbols:
                        self.logger.info(
                            f"close 이벤트 무kline 심볼 {len(inactive_symbols)}개: "
                            f"{', '.join(inactive_symbols[:10])}"
                            f"{'...' if len(inactive_symbols) > 10 else ''}"
                        )
                    break
        finally:
            self._close_event_finalized_at[target_ts] = loop.time()
            if timed_out_symbols:
                self._close_event_timeout_symbols[target_ts] = timed_out_symbols
            else:
                self._close_event_timeout_symbols.pop(target_ts, None)
            self._close_event_symbols.pop(target_ts, None)
            self._close_event_kline_symbols.pop(target_ts, None)
            self._close_event_processed_symbols.pop(target_ts, None)
            self._close_event_first_seen_at.pop(target_ts, None)
            self._close_event_tracked_symbols.pop(target_ts, None)
            self._close_event_tasks.pop(target_ts, None)
