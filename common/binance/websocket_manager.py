"""
Manage Binance websocket connections for market data and user streams.
"""

from __future__ import annotations

import asyncio
import functools
import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Sequence

from binance import AsyncClient, BinanceSocketManager
import websockets

from common.config.strategy_config import StrategyConfig
from common.utils.logger import logger

if TYPE_CHECKING:
    from common.binance.binance_client import BinanceClient

WebSocketCallback = Callable[[Any], Awaitable[None] | None]


def patch_reconnecting_websocket() -> None:
    """Increase the internal python-binance websocket queue size."""
    try:
        from binance.ws.reconnecting_websocket import ReconnectingWebsocket

        original_init = ReconnectingWebsocket.__init__

        @functools.wraps(original_init)
        def patched_init(self, *args: Any, **kwargs: Any) -> Any:
            if "max_queue_size" not in kwargs or kwargs["max_queue_size"] == 100:
                kwargs["max_queue_size"] = 10000
            return original_init(self, *args, **kwargs)

        ReconnectingWebsocket.__init__ = patched_init
        logger.info("바이낸스 내부 웹소켓 큐 크기 패치 적용 완료 (10,000)")
    except Exception as exc:
        logger.error(f"바이낸스 내부 웹소켓 큐 크기 패치 실패: {exc}")


patch_reconnecting_websocket()


class BinanceWSManager:
    """Manage the Binance futures market-data websocket."""

    def __init__(self, binance_client: BinanceClient | None = None):
        self.binance = binance_client
        self.client: AsyncClient | None = None
        self.bsm: BinanceSocketManager | None = None
        self.current_symbols: list[str] = []
        self.current_intervals: list[str] = [StrategyConfig.COLLECTOR_PRIMARY_INTERVAL]
        self._callback: WebSocketCallback | None = None
        self._is_running = False
        self._run_task: asyncio.Task[None] | None = None
        self._dispatch_queue: asyncio.Queue[Any] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._dispatch_backlog_warned = False
        self._reconnect_lock = asyncio.Lock()

    async def _recv_market_message(
        self,
        stream: Any,
        *,
        timeout_seconds: float,
        received_any_message: bool,
        idle_started_at: float,
    ) -> Any | None:
        if timeout_seconds <= 0:
            return await stream.recv()

        try:
            return await asyncio.wait_for(stream.recv(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            idle_for = max(asyncio.get_running_loop().time() - idle_started_at, timeout_seconds)
            if received_any_message:
                logger.warning(
                    f"시장 웹소켓 무메시지 정체 감지 (마지막 수신 후 {idle_for:.1f}초)"
                )
                return None
            raise TimeoutError(
                f"시장 웹소켓 첫 메시지 지연 감지 (연결 후 {idle_for:.1f}초)"
            ) from exc

    @staticmethod
    def _chunk_streams(streams: list[str], chunk_size: int) -> list[list[str]]:
        """multiplex 연결 하나가 과도하게 커지지 않도록 stream 목록을 나눕니다."""
        safe_chunk_size = max(1, chunk_size)
        chunk_count = max(1, (len(streams) + safe_chunk_size - 1) // safe_chunk_size)
        balanced_chunk_size = max(1, (len(streams) + chunk_count - 1) // chunk_count)
        return [
            streams[index:index + balanced_chunk_size]
            for index in range(0, len(streams), balanced_chunk_size)
        ]

    @staticmethod
    def _raw_market_stream_url(streams: list[str]) -> str:
        """Binance USD-M Futures combined raw websocket URL을 생성합니다."""
        return f"{StrategyConfig.COLLECTOR_WS_RAW_BASE_URL}{'/'.join(streams)}"

    @staticmethod
    def _decode_raw_market_message(raw_message: Any) -> Any:
        """raw websocket 프레임을 python-binance combined stream 형식의 dict로 변환합니다."""
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        if isinstance(raw_message, str):
            return json.loads(raw_message)
        return raw_message

    async def _consume_market_stream_chunk(
        self,
        streams: list[str],
        chunk_index: int,
        total_chunks: int,
        timeout_seconds: float,
    ) -> None:
        """분할된 multiplex stream 하나를 소비하고 공통 dispatch queue로 전달합니다."""
        stream_preview = ", ".join(streams[:5])
        if len(streams) > 5:
            stream_preview += "..."
        stream_list = ", ".join(streams)
        logger.info(
            "바이낸스 선물 시장 웹소켓 chunk 연결 시작 "
            f"({chunk_index}/{total_chunks}, streams={len(streams)}, sample={stream_preview}, "
            f"raw={StrategyConfig.COLLECTOR_WS_USE_RAW_MARKET_STREAM})"
        )

        if StrategyConfig.COLLECTOR_WS_USE_RAW_MARKET_STREAM:
            await self._consume_raw_market_stream_chunk(
                streams,
                chunk_index,
                total_chunks,
                timeout_seconds,
                stream_list,
            )
            return

        if self.bsm is None:
            raise RuntimeError("BinanceSocketManager가 초기화되지 않았습니다.")

        async with self.bsm.futures_multiplex_socket(streams) as stream:
            loop = asyncio.get_running_loop()
            idle_started_at = loop.time()
            received_any_message = False
            while self._is_running:
                try:
                    message = await self._recv_market_message(
                        stream,
                        timeout_seconds=timeout_seconds,
                        received_any_message=received_any_message,
                        idle_started_at=idle_started_at,
                    )
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"{exc} (chunk={chunk_index}/{total_chunks}, "
                        f"streams={len(streams)}, stream_list={stream_list})"
                    ) from exc
                if message is None:
                    logger.warning(
                        "시장 웹소켓 메시지 대기 지속 "
                        f"(chunk={chunk_index}/{total_chunks}, streams={len(streams)}, "
                        f"stream_list={stream_list})"
                    )
                    continue
                idle_started_at = loop.time()
                if not received_any_message:
                    received_any_message = True
                    stream_name = message.get("stream", "N/A") if isinstance(message, dict) else "N/A"
                    logger.info(
                        "바이낸스 선물 시장 웹소켓 첫 메시지 수신 "
                        f"({chunk_index}/{total_chunks}, stream={stream_name})"
                    )
                if message is not None and self._callback is not None:
                    await self._enqueue_message(message)

    async def _consume_raw_market_stream_chunk(
        self,
        streams: list[str],
        chunk_index: int,
        total_chunks: int,
        timeout_seconds: float,
        stream_list: str,
    ) -> None:
        """python-binance 래퍼를 우회해 Binance raw futures multiplex stream을 직접 소비합니다."""
        url = self._raw_market_stream_url(streams)
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=1,
            open_timeout=20,
            max_queue=StrategyConfig.COLLECTOR_WS_RAW_MAX_QUEUE,
        ) as stream:
            loop = asyncio.get_running_loop()
            idle_started_at = loop.time()
            received_any_message = False
            while self._is_running:
                try:
                    raw_message = await self._recv_market_message(
                        stream,
                        timeout_seconds=timeout_seconds,
                        received_any_message=received_any_message,
                        idle_started_at=idle_started_at,
                    )
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"{exc} (raw=True, chunk={chunk_index}/{total_chunks}, "
                        f"streams={len(streams)}, stream_list={stream_list})"
                    ) from exc
                if raw_message is None:
                    logger.warning(
                        "시장 웹소켓 메시지 대기 지속 "
                        f"(raw=True, chunk={chunk_index}/{total_chunks}, streams={len(streams)}, "
                        f"stream_list={stream_list})"
                    )
                    continue

                message = self._decode_raw_market_message(raw_message)
                idle_started_at = loop.time()
                if not received_any_message:
                    received_any_message = True
                    stream_name = message.get("stream", "N/A") if isinstance(message, dict) else "N/A"
                    logger.info(
                        "바이낸스 선물 시장 웹소켓 첫 메시지 수신 "
                        f"(raw=True, {chunk_index}/{total_chunks}, stream={stream_name})"
                    )
                if message is not None and self._callback is not None:
                    await self._enqueue_message(message)

    async def start(
        self,
        symbols: Sequence[str],
        callback: WebSocketCallback,
        intervals: Sequence[str] | None = None,
    ) -> None:
        """Start the market-data websocket loop."""
        self._callback = callback
        self.current_symbols = list(symbols)
        if intervals:
            self.current_intervals = list(dict.fromkeys(intervals))
        self._is_running = True
        self._ensure_dispatch_task()
        if self._run_task is None or self._run_task.done():
            self._run_task = asyncio.create_task(self._run_internal())

    def _ensure_dispatch_task(self) -> None:
        queue = self._dispatch_queue
        if queue is None:
            queue = asyncio.Queue(maxsize=StrategyConfig.COLLECTOR_WS_MESSAGE_QUEUE_SIZE)
            self._dispatch_queue = queue

        task = self._dispatch_task
        if task is None or task.done():
            self._dispatch_task = asyncio.create_task(self._dispatch_messages(queue))

    async def _dispatch_messages(self, queue: asyncio.Queue[Any]) -> None:
        while True:
            message = await queue.get()
            try:
                await self._handle_callback(message)
            finally:
                queue.task_done()

    async def _enqueue_message(self, message: Any) -> None:
        queue = self._dispatch_queue
        if queue is None:
            await self._handle_callback(message)
            return

        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning(
                "시장 웹소켓 전달 큐 포화로 수신 루프가 대기합니다 "
                f"(최대 크기={queue.maxsize})"
            )
            await queue.put(message)

        self._log_dispatch_backlog(queue)

    def _log_dispatch_backlog(self, queue: asyncio.Queue[Any]) -> None:
        warn_threshold = StrategyConfig.COLLECTOR_WS_QUEUE_BACKLOG_WARN_THRESHOLD
        if warn_threshold <= 0:
            return

        backlog = queue.qsize()
        if backlog >= warn_threshold and not self._dispatch_backlog_warned:
            self._dispatch_backlog_warned = True
            logger.warning(
                "시장 웹소켓 전달 대기열 증가 감지 "
                f"(대기={backlog}, 기준={warn_threshold})"
            )
        elif self._dispatch_backlog_warned and backlog <= max(1, warn_threshold // 2):
            self._dispatch_backlog_warned = False
            logger.info(
                "시장 웹소켓 전달 대기열 회복 "
                f"(대기={backlog}, 기준={warn_threshold})"
            )

    async def _stop_run_loop(self) -> None:
        current_client = self.client
        run_task = self._run_task
        self.client = None
        self.bsm = None

        if current_client is not None:
            try:
                await current_client.close_connection()
            except Exception as exc:
                logger.warning(f"시장 웹소켓 연결 종료 실패: {exc}")

        if run_task is not None and not run_task.done():
            run_task.cancel()
            try:
                await asyncio.wait_for(run_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        self._run_task = None

    async def _stop_dispatch_task(self) -> None:
        dispatch_task = self._dispatch_task
        self._dispatch_task = None
        self._dispatch_queue = None
        self._dispatch_backlog_warned = False

        if dispatch_task is None or dispatch_task.done():
            return

        dispatch_task.cancel()
        try:
            await asyncio.wait_for(dispatch_task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    async def hard_restart(
        self,
        reason: str | None = None,
        *,
        drop_pending_messages: bool = True,
    ) -> int:
        """Fully restart the market websocket task."""
        if not self._is_running:
            return 0

        async with self._reconnect_lock:
            reason_text = f": {reason}" if reason else ""
            logger.warning(f"시장 웹소켓 강제 재시작 요청{reason_text}")

            await self._stop_run_loop()

            dropped_messages = 0
            if drop_pending_messages:
                queue = self._dispatch_queue
                if queue is not None:
                    dropped_messages = queue.qsize()
                await self._stop_dispatch_task()
                if dropped_messages > 0:
                    logger.warning(
                        "시장 웹소켓 재시작 전 대기열 폐기 "
                        f"(폐기 건수={dropped_messages})"
                    )

            if self._is_running:
                self._ensure_dispatch_task()
                self._run_task = asyncio.create_task(self._run_internal())

            return dropped_messages

    async def _run_internal(self) -> None:
        try:
            while self._is_running:
                try:
                    if StrategyConfig.COLLECTOR_WS_USE_RAW_MARKET_STREAM:
                        self.client = None
                        self.bsm = None
                    else:
                        api_key = self.binance.api_key if self.binance else None
                        api_secret = self.binance.api_secret if self.binance else None
                        client = await AsyncClient.create(api_key=api_key, api_secret=api_secret)
                        self.client = client
                        self.bsm = BinanceSocketManager(client, user_timeout=60)

                    streams = [
                        f"{symbol.lower()}@kline_{interval}"
                        for symbol in self.current_symbols
                        for interval in self.current_intervals
                    ]
                    stream_chunks = self._chunk_streams(
                        streams,
                        StrategyConfig.COLLECTOR_WS_STREAM_CHUNK_SIZE,
                    )
                    logger.info(
                        "바이낸스 선물 시장 웹소켓 연결 시작 "
                        f"(symbols={len(self.current_symbols)}개, intervals={len(self.current_intervals)}개, "
                        f"streams={len(streams)}개, chunks={len(stream_chunks)}개, "
                        f"raw={StrategyConfig.COLLECTOR_WS_USE_RAW_MARKET_STREAM})"
                    )

                    timeout_seconds = float(StrategyConfig.COLLECTOR_WS_RECV_IDLE_TIMEOUT_SECONDS)
                    consumer_tasks = [
                        asyncio.create_task(
                            self._consume_market_stream_chunk(
                                chunk,
                                chunk_index,
                                len(stream_chunks),
                                timeout_seconds,
                            )
                        )
                        for chunk_index, chunk in enumerate(stream_chunks, start=1)
                    ]
                    done, pending = await asyncio.wait(
                        consumer_tasks,
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._is_running:
                        logger.error(
                            f"시장 웹소켓 오류: {exc}. 5초 후 재시도합니다..."
                        )
                        await asyncio.sleep(5)
                finally:
                    current_client = self.client
                    self.client = None
                    self.bsm = None
                    if current_client is not None:
                        try:
                            await current_client.close_connection()
                        except Exception as exc:
                            logger.warning(
                                f"시장 웹소켓 연결 종료 실패: {exc}"
                            )
        finally:
            self._run_task = None

    async def _handle_callback(self, message: Any) -> None:
        try:
            callback = self._callback
            if callback is None:
                return

            if asyncio.iscoroutinefunction(callback):
                await callback(message)
            else:
                callback(message)
        except Exception as exc:
            logger.error(f"시장 웹소켓 콜백 처리 중 오류: {exc}")

    async def update_symbols(
        self,
        new_symbols: Sequence[str],
        intervals: Sequence[str] | None = None,
    ) -> None:
        """Reconnect the market stream when the symbol set changes."""
        new_interval_list = list(dict.fromkeys(intervals)) if intervals else list(self.current_intervals)
        if set(self.current_symbols) == set(new_symbols) and self.current_intervals == new_interval_list:
            return

        self.current_symbols = list(new_symbols)
        self.current_intervals = new_interval_list
        await self.hard_restart("심볼 갱신 반영")

    async def reconnect(self, reason: str | None = None) -> None:
        """Backward-compatible reconnect entry point."""
        await self.hard_restart(reason, drop_pending_messages=False)

    async def stop(self) -> None:
        """Stop the market-data stream."""
        self._is_running = False
        await self._stop_run_loop()
        await self._stop_dispatch_task()
        logger.info("시장 웹소켓 스트림 종료")


class BinanceUserStreamManager:
    """Manage the Binance futures user-data websocket."""

    def __init__(self, binance_client: "BinanceClient"):
        self.binance = binance_client
        self.client: AsyncClient | None = None
        self.bsm: BinanceSocketManager | None = None
        self._callback: WebSocketCallback | None = None
        self._is_running = False
        self._first_msg_received = False

    async def start(self, callback: WebSocketCallback) -> None:
        self._callback = callback
        self._is_running = True
        asyncio.create_task(self._run_internal())

    async def _run_internal(self) -> None:
        while self._is_running:
            try:
                client = await AsyncClient.create(
                    api_key=self.binance.api_key,
                    api_secret=self.binance.api_secret,
                )
                self.client = client
                self.bsm = BinanceSocketManager(client, user_timeout=60)

                logger.info("바이낸스 사용자 데이터 웹소켓 연결 시작...")
                async with self.bsm.futures_user_socket() as stream:
                    logger.info("바이낸스 USD-M Futures 사용자 데이터 웹소켓 연결 완료")
                    while self._is_running:
                        message = await stream.recv()
                        payload = self._extract_payload(message)
                        event_type = payload.get("e") if isinstance(payload, dict) else None
                        if not self._first_msg_received:
                            self._first_msg_received = True
                            logger.info(f"바이낸스 사용자 데이터 웹소켓 수신 확인 (event={event_type})")
                        if event_type in {"ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE"}:
                            logger.info(f"바이낸스 사용자 데이터 이벤트 수신: {event_type}")

                        if payload and self._callback:
                            await self._handle_callback(payload)
            except Exception as exc:
                if self._is_running:
                    logger.error(f"사용자 데이터 웹소켓 오류: {exc}. 5초 후 재시도합니다...")
                    await asyncio.sleep(5)
            finally:
                current_client = self.client
                self.client = None
                self.bsm = None
                if current_client is not None:
                    await current_client.close_connection()

    @staticmethod
    def _extract_payload(message: Any) -> Any:
        """멀티플렉스 래핑 여부와 무관하게 실제 사용자 이벤트 payload를 꺼냅니다."""
        if isinstance(message, dict) and isinstance(message.get("data"), dict):
            return message["data"]
        return message

    async def _handle_callback(self, message: Any) -> None:
        try:
            callback = self._callback
            if callback is None:
                return

            if asyncio.iscoroutinefunction(callback):
                await callback(message)
            else:
                callback(message)
        except Exception as exc:
            logger.error(f"사용자 데이터 웹소켓 콜백 처리 중 오류: {exc}")

    async def stop(self) -> None:
        self._is_running = False
        current_client = self.client
        self.client = None
        self.bsm = None
        if current_client is not None:
            await current_client.close_connection()
        logger.info("사용자 데이터 스트림 종료")
