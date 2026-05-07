"""
TradingEngine.py
================
이 파일은 Kairos 실거래 시스템의 중심 엔진입니다.

초급 개발자 관점에서는 이 클래스를 "실시간 오케스트레이터"라고 보면 이해가 쉽습니다.
웹소켓으로 시장 데이터를 받고, 캔들 버퍼를 유지하고, 정해진 시점마다 모델 추론을 돌리고,
조건을 만족하면 실제 주문 관리자에게 진입/TP/SL 실행을 맡깁니다.

핵심 흐름은 다음과 같습니다.

1. 시작 시 바이낸스, DB, 주문 관리자, 모델, 피처 생성기 등을 초기화합니다.
2. 심볼별 1분봉 버퍼를 유지하면서 실시간 데이터를 계속 업데이트합니다.
3. 정각 기준 배치 추론 루프가 돌며 "방금 마감된 봉"을 대상으로 모델 예측을 수행합니다.
4. 리스크 조건, 블랙리스트, MDD 제한 등을 통과한 종목만 진입을 시도합니다.
5. 진입 후에는 TP/SL 보호 주문과 DB 기록을 유지하고, 유저 스트림으로 상태를 동기화합니다.

즉, 이 파일은 단순히 모델만 호출하는 코드가 아니라
"실시간 데이터 수집 -> 추론 -> 주문 -> 사후 동기화"까지 이어지는 운영 엔진입니다.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict, cast

if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import numpy as np
import pandas as pd
from common.binance.binance_client import BinanceClient
from common.binance.websocket_manager import BinanceUserStreamManager
from common.config.base_config import Config
from common.config.strategy_config import StrategyConfig
from common.database.manager import DBManager
from common.ipc.zmq_pubsub import ZMQRequestClient, ZMQSubscriber
from common.ml.lgbm_model import LGBMModel
from common.ml.scalping_1m_dataset import Scalping1MConfig, Scalping1MDatasetBuilder, build_scalping_1m_config
from common.ml.scalping_1m_scoring import (
    add_entry_scores,
    apply_entry_gate_with_risk,
    build_active_volatility_mask,
)
from common.ml.trading_rules import TradingRules
from common.utils.locker import PIDLock
from common.utils.logger import setup_logger
from trader.close_event_batch_manager import CloseEventBatchManager
from trader.trader_order_manager import OrderManager

class PositionInfo(TypedDict):
    """메모리에서 추적하는 단일 포지션의 최소 상태 정보입니다."""

    qty: float
    side: str
    entry_p: float

class KairosEngine:
    """실시간 데이터, 모델 추론, 주문 실행을 연결하는 메인 엔진 클래스."""

    def __init__(self):
        """실거래 엔진에 필요한 외부 연결, 모델, 상태 저장소, 안전장치를 초기화합니다."""
        # 로거 초기화 (네임스페이스 분리)
        global logger
        logger = setup_logger("Kairos.Engine", Config.LIVE_LOG_DIR / "live.log", rotation="daily")
        
        self.process_lock: PIDLock = PIDLock("trader")
        self._process_lock_acquired = False
        self.binance: BinanceClient = BinanceClient()
        self.user_ws: BinanceUserStreamManager = BinanceUserStreamManager(self.binance)
        self.db: DBManager = DBManager()
        self.order_manager: OrderManager = OrderManager(self.binance)
        self.scalping_config: Scalping1MConfig = build_scalping_1m_config()
        self.dataset_builder: Scalping1MDatasetBuilder = Scalping1MDatasetBuilder(
            db_manager=self.db,
            config=self.scalping_config,
        )
        self.market_data_subscriber: ZMQSubscriber = ZMQSubscriber(StrategyConfig.MARKETDATA_ZMQ_ENDPOINT)
        self.symbols_rpc_client: ZMQRequestClient = ZMQRequestClient(StrategyConfig.MARKETDATA_SYMBOLS_RPC_ENDPOINT)
        self._zmq_connected_logged: bool = False
        self._first_kline_logged: bool = False
        self._buffer_init_tasks: set[str] = set()
        self._last_symbol_update_signature: tuple[str, ...] = ()
        
        self.tp_model: LGBMModel = LGBMModel(f"{self.scalping_config.model_name}_tp")
        self.sl_model: LGBMModel = LGBMModel(f"{self.scalping_config.model_name}_sl")
        self.timeout_model: LGBMModel = LGBMModel(f"{self.scalping_config.model_name}_timeout")
        try:
            self.tp_model.load_model()
            self.sl_model.load_model()
            self.timeout_model.load_model()
            logger.info(
                f"1분봉 모델 로드 완료: {self.scalping_config.model_name} "
                f"(Threshold: {StrategyConfig.SCALPING_1M_ENTRY_SCORE_THRESHOLD})"
            )
        except Exception as e:
            logger.error(f"모델 로드 실패: {e}")
        
        self.candle_buffer: dict[str, pd.DataFrame] = {}
        self.active_positions: dict[str, PositionInfo] = {}
        self.executing_symbols: set[str] = set()  # 진입 중인 종목을 잠가 중복 실행을 막습니다.
        self.entry_lock: asyncio.Lock = asyncio.Lock()  # 슬롯 확인과 예약을 원자적으로 묶는 전역 진입 잠금입니다.
        self.tp_sl_deployed: set[str] = set()  # 수동 동기화 포지션의 1회성 TP/SL 복구 완료 메모리입니다.
        self.last_inference_ts: dict[str, pd.Timestamp] = {}
        self.exit_pnl_accumulator: dict[str, float] = {}
        self.tracked_symbols: set[str] = {"BTCUSDT"}
        self._funding_rate_cache: dict[str, tuple[float, float]] = {}
        self._state_sync_in_progress: bool = False
        self._pending_sync_exit_tasks: dict[str, asyncio.Task[None]] = {}
        self._periodic_state_sync_task: asyncio.Task[None] | None = None
        self.close_event_batches: CloseEventBatchManager = CloseEventBatchManager(
            logger=logger,
            batch_interval_seconds=StrategyConfig.CLOSE_EVENT_BATCH_INTERVAL_SECONDS,
            max_wait_seconds=StrategyConfig.CLOSE_EVENT_MAX_WAIT_SECONDS,
            get_tracked_symbols=lambda: set(self.tracked_symbols),
            is_running=lambda: self.is_running,
            process_batch=self._process_close_event_batch,
        )
        self.is_running: bool = False
        
        logger.info("Kairos Engine 객체 초기화 완료")

    def _to_native_value(self, val: Any) -> Any:
        """Numpy 타입을 Python 기본 타입으로 변환하는 헬퍼 함수 (DB/JSON 저장용)"""
        if hasattr(val, 'item'): 
            return val.item()
        if isinstance(val, dict): 
            return {k: self._to_native_value(v) for k, v in val.items()}
        if isinstance(val, (list, tuple)): 
            return [self._to_native_value(x) for x in val]
        return val

    def _serialize_value(self, value: Any) -> Any:
        """DB 저장 전 datetime-like 값을 안전하게 직렬화합니다."""
        native_value = self._to_native_value(value)
        if isinstance(native_value, dict):
            return {k: self._serialize_value(v) for k, v in native_value.items()}
        if isinstance(native_value, list):
            return [self._serialize_value(v) for v in native_value]

        iso_fn = getattr(native_value, "isoformat", None)
        if callable(iso_fn):
            return iso_fn()
        return native_value

    def _predict_scalping_probabilities(self, input_data: pd.DataFrame) -> tuple[float, float, float]:
        """1분봉 TP/SL/TIMEOUT 모델 확률을 같은 입력 피처에서 계산합니다."""
        p_tp = float(self.tp_model.predict(input_data).iloc[0])
        p_sl = float(self.sl_model.predict(input_data).iloc[0])
        p_timeout = float(self.timeout_model.predict(input_data).iloc[0])
        return p_tp, p_sl, p_timeout

    def _classify_exit_reason(
        self,
        order_type: str,
        realized_pnl: float,
        is_reduce_only: bool,
    ) -> str:
        """주문 타입과 손익 부호를 함께 보고 청산 사유를 추정합니다."""
        order_type_upper = str(order_type).upper()
        if 'TAKE_PROFIT' in order_type_upper:
            return 'TP'
        if 'STOP' in order_type_upper:
            return 'SL'
        if is_reduce_only:
            if realized_pnl > 0:
                return 'TP'
            if realized_pnl < 0:
                return 'SL'
        return 'MANUAL_EXIT'

    def _get_feature_context_symbols(self, symbols: list[str]) -> list[str]:
        """
        BTC 상대강도 피처 계산에 필요한 문맥 심볼을 배치 대상에 함께 포함합니다.

        1분봉 스캘핑 피처는 BTCUSDT의 1분/5분 수익률을 상대강도 문맥으로 사용합니다.
        """
        context_symbols = list(symbols)
        if "BTCUSDT" in self.candle_buffer and "BTCUSDT" not in context_symbols:
            context_symbols.append("BTCUSDT")
        return context_symbols

    async def _resolve_entry_fill(
        self,
        symbol: str,
        order: dict[str, Any],
        fallback_entry_price: float,
    ) -> tuple[float, float]:
        """시장가 응답에 체결 수량이 비어 있어도 실제 포지션 조회로 수량/진입가를 복구한다."""
        qty = float(order.get('executedQty', 0) or 0)
        entry_p = float(order.get('avgPrice', 0) or 0)
        if qty > 0 and entry_p > 0:
            return qty, entry_p

        loop = asyncio.get_event_loop()
        for _ in range(10):
            positions = await loop.run_in_executor(None, self.binance.get_futures_positions)
            for pos in positions:
                if pos.get('symbol') != symbol:
                    continue
                position_amt = abs(float(pos.get('positionAmt', 0) or 0))
                if position_amt <= 1e-9:
                    continue
                resolved_entry = float(pos.get('entryPrice', 0) or 0)
                if resolved_entry <= 0:
                    resolved_entry = fallback_entry_price
                return position_amt, resolved_entry
            await asyncio.sleep(0.5)

        if qty > 0:
            return qty, entry_p if entry_p > 0 else fallback_entry_price
        return 0.0, fallback_entry_price

    async def start(self):
        """
        실시간 트레이딩 엔진의 전체 생명주기를 시작합니다.

        시작 시 초기 상태 동기화, TP/SL 점검, 심볼 관리 루프, 유저 스트림, 추론 스케줄러를 차례로 띄웁니다.
        이후 `self.is_running`이 유지되는 동안 엔진은 백그라운드 작업들을 계속 돌립니다.
        """
        if self.is_running:
            return
        if not self.process_lock.acquire():
            logger.critical("실거래 엔진 단일 실행 락 획득 실패. 중복 실행 방지를 위해 시작을 중단합니다.")
            self.market_data_subscriber.close()
            self.symbols_rpc_client.close()
            self.db.close()
            return
        self._process_lock_acquired = True
        self.is_running = True
        self.db.connect()
        self.db.init_risk_tables()
        logger.info(f"ZMQ 연결됨: {StrategyConfig.MARKETDATA_ZMQ_ENDPOINT}")
        logger.info("Kairos Realtime Engine 리스크 관리 및 마감 봉 타겟팅 버전 가동 시작 🚀")
        
        try:
            await self._bootstrap_symbols_from_collector()
            await self._sync_initial_state()
            await self._sync_ongoing_tp_sl()
            await self.user_ws.start(self._on_user_message)
            logger.info("User Data Stream 시작 요청 완료")
            self._periodic_state_sync_task = asyncio.create_task(self._periodic_state_sync_loop())
            asyncio.create_task(self._market_data_consumer_loop())
            
            while self.is_running:
                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("Engine 중지 신호 수신")
        except Exception as e:
            logger.error(f"Engine 실행 중 치명적 오류: {e}")
        finally:
            await self.stop()

    async def _bootstrap_symbols_from_collector(self) -> None:
        """collector RPC에서 초기 감시 심볼 목록을 받아 엔진 추적 대상에 반영합니다."""
        try:
            response = await asyncio.wait_for(
                self.symbols_rpc_client.request({"type": "get_symbols"}),
                timeout=5,
            )
            symbols = response.get("symbols", []) if isinstance(response, dict) else []
            if isinstance(symbols, list) and symbols:
                logger.info(f"초기 심볼 목록 조회 완료: {len(symbols)}개")
                await self._apply_symbol_update([str(symbol) for symbol in symbols])
            else:
                logger.warning("초기 심볼 목록 조회 결과가 비어 있어 BTCUSDT 기본값으로 시작합니다.")
        except Exception as exc:
            logger.warning(f"초기 심볼 목록 조회 실패: {exc}")

    def _build_scalping_feature_frame(self, combined_df: pd.DataFrame) -> pd.DataFrame:
        """메모리 1분봉 버퍼에서 실매매용 1분봉 스캘핑 피처를 생성합니다."""
        if combined_df.empty:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        for _, group in combined_df.groupby("symbol", sort=False):
            symbol_df = cast(pd.DataFrame, group.copy())
            featured_df = self.dataset_builder.add_symbol_features(symbol_df)
            if not featured_df.empty:
                frames.append(featured_df)
        if not frames:
            return pd.DataFrame()

        feature_df = pd.concat(frames, ignore_index=True, copy=False)
        feature_df = self.dataset_builder.add_cross_symbol_features(feature_df)
        feature_df = self.dataset_builder.add_pump_episode_features(feature_df)
        feature_df = self.dataset_builder.add_mtf_features(feature_df)
        feature_df = self.dataset_builder.apply_scalping_rank_score(feature_df)
        return feature_df.replace([np.inf, -np.inf], 0).fillna(0)

    async def _process_batch_inference(
        self,
        symbols: list[str],
        target_ts: pd.Timestamp,
        allow_synthetic_snapshot: bool = True,
    ) -> None:
        """
        특정 마감 시점의 여러 종목 데이터를 한 번에 추론하는 배치 실행부입니다.

        여기서는 대상 봉의 최종 데이터를 보정하고,
        피처를 만든 뒤,
        심볼별로 확률을 계산하고,
        리스크 필터를 통과한 경우만 실제 진입으로 넘깁니다.
        """
        try:
            batch_started_at = time.perf_counter()
            requested_symbols = sorted(set(symbols))
            requested_symbol_set = set(requested_symbols)
            context_symbols = self._get_feature_context_symbols(requested_symbols)
            if StrategyConfig.ENGINE_REST_MARKET_ENRICH_ENABLED:
                await self._update_target_row_with_market_data(context_symbols, target_ts)
            market_data_ready_at = time.perf_counter()
            eligible_symbols: list[str] = []
            skipped_symbols: list[str] = []
            synthesized_symbols: list[str] = []
            full_data: list[pd.DataFrame] = []
            context_symbol_rows: set[str] = set()
            for symbol in context_symbols:
                df = self.candle_buffer.get(symbol)
                if df is None or df.empty:
                    if symbol in requested_symbol_set:
                        skipped_symbols.append(symbol)
                    continue
                snapshot_df = cast(pd.DataFrame, df[df['timestamp'] <= target_ts])
                if snapshot_df.empty:
                    if symbol in requested_symbol_set:
                        skipped_symbols.append(symbol)
                        logger.warning(f"[{symbol}] 직전 1분봉 스냅샷이 없어 이번 배치에서 제외합니다. (target_ts={target_ts})")
                    continue
                latest_timestamp_series = cast(pd.Series, snapshot_df['timestamp'])
                latest_snapshot_ts = pd.Timestamp(latest_timestamp_series.iloc[-1])
                if latest_snapshot_ts < target_ts:
                    if not allow_synthetic_snapshot:
                        if symbol in requested_symbol_set:
                            skipped_symbols.append(symbol)
                            logger.debug(
                                f"[{symbol}] close 기준 target row가 없어 이번 배치에서 제외합니다. "
                                f"(latest_ts={latest_snapshot_ts}, target_ts={target_ts})"
                            )
                        continue
                    last_row = snapshot_df.iloc[-1]
                    last_price = float(last_row['close'])
                    synthetic_row = {
                        'timestamp': target_ts,
                        'symbol': symbol,
                        'open': last_price,
                        'high': last_price,
                        'low': last_price,
                        'close': last_price,
                        'volume': 0.0,
                        'quote_volume': 0.0,
                        'open_interest': float(last_row.get('open_interest', 0.0)),
                        'trades_count': 0,
                        'taker_buy_base': 0.0,
                        'taker_buy_quote': 0.0,
                        'funding_rate': float(last_row.get('funding_rate', 0.0)),
                    }
                    snapshot_df = pd.concat([snapshot_df, pd.DataFrame([synthetic_row])], ignore_index=True)
                    if symbol in requested_symbol_set:
                        synthesized_symbols.append(symbol)
                        logger.debug(
                            f"[{symbol}] 직전 1분봉이 비어 있어 마지막 관측값으로 close 스냅샷을 생성합니다. "
                            f"(source_ts={latest_snapshot_ts}, target_ts={target_ts})"
                        )
                if len(snapshot_df) < StrategyConfig.MIN_DATA_POINTS:
                    if symbol in requested_symbol_set:
                        skipped_symbols.append(symbol)
                        logger.warning(
                            f"[{symbol}] 히스토리 부족으로 이번 배치에서 제외합니다. "
                            f"({len(snapshot_df)}/{StrategyConfig.MIN_DATA_POINTS})"
                        )
                    continue
                if symbol in requested_symbol_set:
                    eligible_symbols.append(symbol)
                context_symbol_rows.add(symbol)
                full_data.append(cast(pd.DataFrame, snapshot_df.tail(StrategyConfig.MIN_DATA_POINTS)))
            if not full_data:
                logger.warning("배치 추론 대상 버퍼가 비어 있어 이번 배치를 건너뜁니다.")
                return
            if "BTCUSDT" not in context_symbol_rows:
                logger.warning(
                    f"BTCUSDT 문맥이 아직 준비되지 않아 이번 배치 추론을 보류합니다. "
                    f"(target_ts={target_ts}, requested={len(requested_symbols)}개)"
                )
                return
            if skipped_symbols:
                logger.warning(
                    f"배치 제외 심볼 {len(skipped_symbols)}개: "
                    f"{', '.join(sorted(skipped_symbols)[:10])}"
                    f"{'...' if len(skipped_symbols) > 10 else ''}"
                )
            if synthesized_symbols:
                logger.info(
                    f"직전 1분봉 스냅샷 보정 {len(synthesized_symbols)}개: "
                    f"{', '.join(sorted(synthesized_symbols)[:10])}"
                    f"{'...' if len(synthesized_symbols) > 10 else ''}"
                )
            combined_df = pd.concat(full_data, ignore_index=True)
            data_ready_at = time.perf_counter()
            
            df_feat = await asyncio.to_thread(self._build_scalping_feature_frame, combined_df)
            if df_feat.empty:
                logger.warning("배치 추론용 feature 데이터가 비어 있어 이번 배치를 건너뜁니다.")
                return
            feature_ready_at = time.perf_counter()
            
            loop = asyncio.get_event_loop()
            current_balance = await loop.run_in_executor(None, self.binance.get_futures_balance)
            
            # API 에러 등으로 잔고를 못 가져온 경우 방어
            if current_balance is None:
                logger.error("❌ 잔고 정보를 가져올 수 없어 이번 배치를 건너뜁니다.")
                return

            today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            last_reset_str = self.db.get_system_state_text('last_reset_date', '1970-01-01')
            
            peak_balance = self.db.get_system_state('peak_balance', current_balance)
            if peak_balance is None:
                peak_balance = current_balance
            
            if today_str != last_reset_str:
                # 새로운 날이 시작되면 고점 잔고 리셋
                peak_balance = current_balance
                self.db.set_system_state('peak_balance', peak_balance)
                self.db.set_system_state_text('last_reset_date', today_str)
                logger.warning(f"🆕 일일 리스크 지표 리셋 완료 ({today_str}, Peak: {peak_balance:.2f})")
            elif current_balance > peak_balance:
                peak_balance = current_balance
                self.db.set_system_state('peak_balance', peak_balance)
            
            mdd = (peak_balance - current_balance) / peak_balance if peak_balance > 0 else 0
            is_mdd_broken = mdd >= StrategyConfig.DAILY_MDD_LIMIT
            now_utc = datetime.now(timezone.utc)
            global_sl_count, global_last_sl_at = self.db.get_recent_global_sl_info(
                StrategyConfig.GLOBAL_SL_CIRCUIT_LOOKBACK_HOURS
            )
            global_sl_block_until: datetime | None = None
            global_sl_block_reason = ""
            if global_last_sl_at is not None:
                single_sl_until = global_last_sl_at + timedelta(minutes=StrategyConfig.GLOBAL_SL_COOLDOWN_MINUTES)
                if now_utc < single_sl_until:
                    global_sl_block_until = single_sl_until
                    global_sl_block_reason = f"최근 SL 후 {StrategyConfig.GLOBAL_SL_COOLDOWN_MINUTES}분 쿨다운"
                if global_sl_count >= StrategyConfig.GLOBAL_SL_CIRCUIT_THRESHOLD:
                    circuit_until = global_last_sl_at + timedelta(hours=StrategyConfig.GLOBAL_SL_CIRCUIT_COOLDOWN_HOURS)
                    if now_utc < circuit_until and (
                        global_sl_block_until is None or circuit_until > global_sl_block_until
                    ):
                        global_sl_block_until = circuit_until
                        global_sl_block_reason = (
                            f"최근 {StrategyConfig.GLOBAL_SL_CIRCUIT_LOOKBACK_HOURS}시간 SL "
                            f"{global_sl_count}회"
                        )
            is_global_sl_blocked = global_sl_block_until is not None
            risk_ready_at = time.perf_counter()
            
            sorted_symbols = sorted(eligible_symbols)
            target_rows_df = cast(pd.DataFrame, df_feat[df_feat['timestamp'] == target_ts])
            target_symbol_rows = cast(
                pd.DataFrame,
                target_rows_df.drop_duplicates(subset=['symbol'], keep='last').set_index('symbol', drop=False),
            )
            sl_info_map = self.db.get_symbols_sl_info(sorted_symbols)
            processed_symbols: set[str] = set()
            max_prob = 0.0
            top_symbol = "N/A"
            scored_rows: list[pd.Series] = []
            for symbol in sorted_symbols:
                target = target_symbol_rows.loc[[symbol]] if symbol in target_symbol_rows.index else pd.DataFrame()
                if target.empty:
                    symbol_buffer = self.candle_buffer.get(symbol)
                    latest_symbol_ts = None
                    if symbol_buffer is not None and not symbol_buffer.empty:
                        latest_symbol_ts = pd.Timestamp(cast(pd.Series, symbol_buffer['timestamp']).iloc[-1])
                    logger.warning(
                        f"[{symbol}] 마감 봉(target_ts={target_ts}) 데이터가 없어 이번 배치에서 제외합니다. "
                        f"(latest_ts={latest_symbol_ts})"
                    )
                    continue
                
                input_data, _, _ = self.dataset_builder.prepare_features(target, target_mode="tp")
                if input_data.empty:
                    logger.warning(f"[{symbol}] 모델 입력 데이터가 비어 있어 이번 배치에서 건너뜁니다.")
                    continue
                
                p_tp, p_sl, p_timeout = self._predict_scalping_probabilities(input_data)
                prob = p_tp
                scored = target.iloc[0].copy()
                scored["p_tp"] = p_tp
                scored["p_sl"] = p_sl
                scored["p_timeout"] = p_timeout
                scored_rows.append(scored)
                processed_symbols.add(symbol)
                if prob > max_prob:
                    max_prob = prob
                    top_symbol = symbol
                
                actual_ts = target.iloc[0]['timestamp']
                self.last_inference_ts[symbol] = actual_ts

            entry_candidates: list[tuple[float, str, dict[str, Any]]] = []
            if scored_rows:
                scored_df = pd.DataFrame(scored_rows)
                scored_df = add_entry_scores(
                    scored_df,
                    target_tp=self.scalping_config.target_tp,
                    stop_loss=self.scalping_config.stop_loss,
                )
                candidate_pool = cast(
                    pd.DataFrame,
                    scored_df.sort_values("scalping_rank_score", ascending=False)
                    .head(StrategyConfig.TOP_COIN_COUNT)
                    .copy(),
                )
                active_mask = build_active_volatility_mask(candidate_pool)
                candidate_pool = cast(pd.DataFrame, candidate_pool[active_mask].copy())
                candidate_pool = apply_entry_gate_with_risk(
                    candidate_pool,
                    StrategyConfig.SCALPING_1M_ENTRY_SCORE_THRESHOLD,
                    target_tp=self.scalping_config.target_tp,
                    stop_loss=self.scalping_config.stop_loss,
                )

                for _, row in candidate_pool.sort_values(
                    ["entry_rank_score", "tp_lift", "p_tp", "scalping_rank_score"],
                    ascending=False,
                ).iterrows():
                    symbol = str(row["symbol"])
                    prob = float(row["p_tp"])

                    sl_count, last_sl_at = sl_info_map.get(symbol, (0, None))
                    is_blacklisted = False
                    if sl_count >= StrategyConfig.MAX_SL_COUNT_FOR_BLACKLIST:
                        if last_sl_at and (now_utc - last_sl_at).total_seconds() < StrategyConfig.BLACKLIST_DURATION_HOURS * 3600:
                            is_blacklisted = True

                    if symbol in self.active_positions:
                        logger.info(f"[{symbol}] 포지션 유지 중 (P_TP: {prob:.2%}, Entry: {self.active_positions[symbol].get('entry_p', 0)})")
                        continue
                    if is_mdd_broken:
                        logger.warning(f"[{symbol}] MDD 차단 (진입 시도 불가, P_TP: {prob:.2%}, MDD: {mdd:.2%})")
                        continue
                    if is_global_sl_blocked:
                        block_until_text = global_sl_block_until.isoformat() if global_sl_block_until else "N/A"
                        logger.warning(
                            f"[{symbol}] 전체 SL 차단 (진입 시도 불가, P_TP: {prob:.2%}, "
                            f"reason={global_sl_block_reason}, until={block_until_text})"
                        )
                        continue
                    if is_blacklisted:
                        logger.warning(f"[{symbol}] 블랙리스트 차단 (손절 {sl_count}회, 쿨다운, P_TP: {prob:.2%})")
                        continue

                    target_row = cast(dict[Any, Any], row.to_dict())
                    entry_data = {str(k): v for k, v in target_row.items()}
                    entry_data["base_pred_proba"] = prob
                    entry_data["model_name"] = self.scalping_config.model_name
                    entry_data["data_interval"] = "1m"
                    entry_candidates.append((float(row["entry_rank_score"]), symbol, entry_data))

            scoring_ready_at = time.perf_counter()
            if entry_candidates:
                entry_candidates.sort(key=lambda item: item[0], reverse=True)
                ranking_preview = ", ".join(
                    f"{rank + 1}.{symbol}(score={score:.4f})"
                    for rank, (score, symbol, _) in enumerate(entry_candidates[:10])
                )
                logger.info(f"🏁 진입 후보 랭킹: {ranking_preview}")

                for rank, (score, symbol, entry_data) in enumerate(entry_candidates, start=1):
                    if len(self.active_positions) + len(self.executing_symbols) >= StrategyConfig.MAX_SLOTS:
                        logger.info("진입 후보 랭킹 처리 중 최대 슬롯이 채워져 남은 후보를 건너뜁니다.")
                        break
                    entry_data['rank_at_entry'] = rank
                    entry_data['rank_candidate_count'] = len(entry_candidates)
                    prob = float(entry_data.get("p_tp", 0.0))
                    logger.success(
                        f"🎯 [{symbol}] 랭킹 {rank}/{len(entry_candidates)} 후보 진입 시도 시작 "
                        f"(P_TP: {prob:.2%}, score={score:.4f})"
                    )
                    await self._execute_entry(symbol, entry_data, prob, current_balance=current_balance)

            entry_ready_at = time.perf_counter()
            
            if max_prob > 0:
                logger.info(
                    f"📊 [배치 요약] {top_symbol} (P_TP: {max_prob:.2%}) 포함 분석 완료. "
                    f"최고 TP 확률: {max_prob:.2%}, 점수 임계치: {StrategyConfig.SCALPING_1M_ENTRY_SCORE_THRESHOLD:.4f}"
                )
            
            # 보유 포지션의 TP/SL 누락 여부를 점검하고 자동 복구합니다.
            await self._sync_ongoing_tp_sl()
            batch_finished_at = time.perf_counter()
            logger.info(
                "⏱️ 배치 처리 시간 "
                f"(대상={len(requested_symbols)}개, 처리={len(processed_symbols)}개, 후보={len(entry_candidates)}개): "
                f"REST보정={market_data_ready_at - batch_started_at:.2f}s, "
                f"데이터조립={data_ready_at - market_data_ready_at:.2f}s, "
                f"피처={feature_ready_at - data_ready_at:.2f}s, "
                f"리스크={risk_ready_at - feature_ready_at:.2f}s, "
                f"스코어링={scoring_ready_at - risk_ready_at:.2f}s, "
                f"진입={entry_ready_at - scoring_ready_at:.2f}s, "
                f"보호점검={batch_finished_at - entry_ready_at:.2f}s, "
                f"전체={batch_finished_at - batch_started_at:.2f}s"
            )
                
        except Exception:
            logger.error("배치 추론 오류", exc_info=True)

    async def _process_close_event_batch(
        self,
        target_ts: pd.Timestamp,
        ready_symbols: list[str],
    ) -> None:
        """close 이벤트로 늦게 도착한 심볼 묶음을 엔진 추론 경로로 전달합니다."""
        batch_symbols = self._get_feature_context_symbols(ready_symbols)
        await self._process_batch_inference(batch_symbols, target_ts, allow_synthetic_snapshot=False)

    async def _report_top_features(self, symbol: str, data_dict: dict[str, Any], prob: float) -> None:
        """진입 성공 시 모델의 판단 근거가 된 주요 특징들의 현재값을 출력합니다."""
        try:
            mod = self.tp_model.model
            if mod is None:
                return
            # Booster 객체는 feature_importance() 메서드를 사용해야 합니다.
            importances = mod.feature_importance()
            feature_names = mod.feature_name()
            
            # 중요도 순으로 정렬 후 상위 10개 추출
            top_10_indices = np.argsort(importances)[-10:][::-1]
            
            report = f"\n📊 [{symbol}] 진입 근거 분석 (P: {prob:.2%})\n"
            report += "--------------------------------------\n"
            report += f"{'Feature Name':<25} | {'Value':<10}\n"
            report += "--------------------------------------\n"
            for i in top_10_indices:
                name = feature_names[i]
                val = data_dict.get(name, 0.0)
                report += f"{name:<25} | {val:>10.4f}\n"
            report += "--------------------------------------"
            logger.info(report)
        except Exception as e:
            logger.warning(f"리포트 생성 실패: {e}")

    async def _update_target_row_with_market_data(self, symbols, target_ts):
        """실시간 OI/펀딩비를 가져와 버퍼 내의 정확한 타겟 행(target_ts)에 주입합니다."""
        semaphore = asyncio.Semaphore(15)
        async def fetch_item(sym):
            """단일 심볼의 최신 OI와 funding 데이터를 REST로 보정합니다."""
            async with semaphore:
                try:
                    loop = asyncio.get_event_loop()
                    oi_data = await loop.run_in_executor(None, self.binance.get_open_interest, sym)
                    oi = float(oi_data['openInterest'])
                    now = time.time()
                    cached_funding = self._funding_rate_cache.get(sym)
                    if (
                        cached_funding is not None
                        and now - cached_funding[1] < StrategyConfig.FUNDING_RATE_CACHE_SECONDS
                    ):
                        funding = cached_funding[0]
                    else:
                        funding_data = await loop.run_in_executor(None, self.binance.get_funding_rate, sym, 1)
                        funding = float(funding_data[0]['fundingRate']) if funding_data else 0.0
                        self._funding_rate_cache[sym] = (funding, now)
                    return sym, oi, funding
                except Exception:
                    return sym, None, None

        results = await asyncio.gather(*[fetch_item(s) for s in symbols])
        for sym, oi, funding in results:
            if sym in self.candle_buffer and not self.candle_buffer[sym].empty:
                df = self.candle_buffer[sym]
                mask = df['timestamp'] == target_ts
                if mask.any():
                    idx = df[mask].index[-1]
                    if oi is not None:
                        df.at[idx, 'open_interest'] = oi
                    if funding is not None:
                        if 'funding_rate' not in df.columns:
                            df['funding_rate'] = 0.0
                        df.at[idx, 'funding_rate'] = funding

    async def _market_data_consumer_loop(self) -> None:
        """ZMQ 시장 데이터 구독 메시지를 받아 심볼 업데이트와 캔들 업데이트로 분기합니다."""
        while self.is_running:
            try:
                message = await self.market_data_subscriber.recv()
                if not self._zmq_connected_logged:
                    logger.info("ZMQ 데이터 수신 시작")
                    self._zmq_connected_logged = True
                self._on_ws_message(message)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Market data consumer error: {exc}")
                await asyncio.sleep(1)

    def _on_ws_message(self, msg):
        """ZMQ로 받은 collector 메시지를 엔진 내부 캔들 버퍼와 close 배치 관리자에 반영합니다."""
        try:
            if isinstance(msg, dict) and msg.get('type') == 'symbol_update':
                symbols = msg.get('symbols', [])
                if isinstance(symbols, list):
                    signature = tuple(sorted(str(symbol) for symbol in symbols))
                    if signature == self._last_symbol_update_signature:
                        return
                    self._last_symbol_update_signature = signature
                    logger.info(f"ZMQ 심볼 목록 수신: {len(symbols)}개")
                    asyncio.create_task(self._apply_symbol_update(symbols))
                return
            if 'data' in msg:
                msg = msg['data']
            if msg.get('e') != 'kline':
                return
            symbol, k = msg['s'], msg['k']
            is_close_event = bool(k.get('x'))
            target_ts = pd.to_datetime(int(k['t']), unit='ms', utc=True)
            if symbol not in self.tracked_symbols:
                if (
                    is_close_event
                    and self.close_event_batches.is_timed_out_symbol(symbol, target_ts)
                ):
                    self.close_event_batches.register_close(symbol, target_ts)
                return
            if not self._first_kline_logged:
                logger.info(f"ZMQ 캔들 수신 시작: {symbol}")
                self._first_kline_logged = True
            if symbol not in self.candle_buffer and symbol not in self._buffer_init_tasks:
                self.candle_buffer[symbol] = pd.DataFrame(columns=[
                    'timestamp', 'symbol', 'open', 'high', 'low', 'close', 'volume',
                    'quote_volume', 'open_interest', 'trades_count', 'taker_buy_base',
                    'taker_buy_quote', 'funding_rate'
                ])
                self._buffer_init_tasks.add(symbol)
                asyncio.create_task(self._initialize_candle_buffers([symbol], log_summary=False))
            self.close_event_batches.register_kline(symbol, target_ts)
            self._update_buffer(symbol, k)
            if is_close_event:
                self.close_event_batches.register_close(symbol, target_ts)
        except Exception:
            pass

    def _update_buffer(self, symbol, k):
        """Binance kline payload 하나를 표준 row로 변환해 심볼 버퍼에 반영합니다."""
        if symbol not in self.candle_buffer:
            return
        df = self.candle_buffer[symbol]
        ts_pd = pd.to_datetime(int(k['t']), unit='ms', utc=True)
        new_row = {
            'timestamp': ts_pd, 'open': float(k['o']), 'high': float(k['h']), 
            'low': float(k['l']), 'close': float(k['c']), 'volume': float(k['v']),
            'quote_volume': float(k['q']), 'trades_count': int(k['n']), 
            'taker_buy_base': float(k['V']), 'taker_buy_quote': float(k['Q']), 
            'symbol': symbol, 'open_interest': float(k.get('oi', 0.0)),
            'funding_rate': float(k.get('funding_rate', 0.0)),
        }
        if ts_pd in df['timestamp'].values:
            idx = df[df['timestamp'] == ts_pd].index[-1]
            for col in new_row.keys():
                if col in df.columns:
                    df.at[idx, col] = new_row[col]
        else:
            self.candle_buffer[symbol] = pd.concat([df, pd.DataFrame([new_row])]) \
                .drop_duplicates(subset='timestamp', keep='last') \
                .sort_values('timestamp') \
                .tail(StrategyConfig.CANDLE_LIMIT_LIVE)

    async def _apply_symbol_update(self, symbols: list[str]) -> None:
        """collector가 알려준 최신 감시 심볼 목록을 추적 집합과 버퍼 초기화에 반영합니다."""
        try:
            target_symbols = set(symbols)
            target_symbols.add("BTCUSDT")
            for symbol in self.active_positions.keys():
                target_symbols.add(symbol)
            self.tracked_symbols = set(target_symbols)
            rebased_targets = self.close_event_batches.rebase_open_target_snapshots(self.tracked_symbols)

            current_symbols = set(self.candle_buffer.keys())
            added = target_symbols - current_symbols
            removed = current_symbols - target_symbols
            underfilled = {
                symbol for symbol in target_symbols
                if symbol not in self.candle_buffer
                or len(self.candle_buffer[symbol]) < StrategyConfig.MIN_DATA_POINTS
            }

            if not added and not removed and not underfilled:
                return

            if added:
                logger.info(f"➕ 새 감시 종목 추가: {', '.join(sorted(added))}")
            if removed:
                logger.info(f"➖ 기존 감시 종목 제외: {', '.join(sorted(removed))}")

            if rebased_targets:
                logger.info(f"진행 중 close 추적 스냅샷 재기준화: {rebased_targets}개 타겟 봉")

            for sym in removed:
                del self.candle_buffer[sym]
                if sym in self.last_inference_ts:
                    del self.last_inference_ts[sym]

            logger.info(f"🔍 감시 대상 종목 갱신 완료: {len(target_symbols)}개 유지")
            symbols_to_initialize = sorted(added | underfilled)
            if symbols_to_initialize:
                await self._initialize_candle_buffers(symbols_to_initialize)
        except Exception as e:
            logger.error(f"심볼 업데이트 처리 오류: {e}")

    async def _initialize_candle_buffers(self, symbols, log_summary: bool = True):
        """감시 대상 종목들의 과거 캔들 데이터를 병렬로 로드하여 버퍼를 초기화합니다."""
        new_symbols = [
            s for s in symbols
            if s not in self.candle_buffer or len(self.candle_buffer[s]) < StrategyConfig.MIN_DATA_POINTS
        ]
        if not new_symbols:
            return

        semaphore = asyncio.Semaphore(10)  # 동시 요청 수를 제한해 안정성을 유지합니다.
        
        async def fetch_and_buffer(sym):
            """단일 심볼의 최신 DB 캔들을 읽어 메모리 버퍼를 초기화합니다."""
            async with semaphore:
                try:
                    # DB 히스토리를 기본으로 깔고, 그 사이 들어온 WS 최신 행은 병합해 덮어씁니다.
                    db_df = await asyncio.to_thread(self.db.get_candles_1m, sym, StrategyConfig.CANDLE_LIMIT_LIVE)
                    existing_df = self.candle_buffer.get(sym)
                    if existing_df is not None and not existing_df.empty:
                        merged_df = pd.concat([db_df, existing_df], ignore_index=True) if not db_df.empty else existing_df.copy()
                        merged_df = (
                            merged_df
                            .drop_duplicates(subset='timestamp', keep='last')
                            .sort_values('timestamp')
                            .tail(StrategyConfig.CANDLE_LIMIT_LIVE)
                        )
                        self.candle_buffer[sym] = merged_df
                    elif not db_df.empty:
                        self.candle_buffer[sym] = db_df.tail(StrategyConfig.CANDLE_LIMIT_LIVE)
                    elif sym not in self.candle_buffer:
                        self.candle_buffer[sym] = pd.DataFrame(columns=[
                            'timestamp', 'symbol', 'open', 'high', 'low', 'close', 'volume',
                            'quote_volume', 'open_interest', 'trades_count', 'taker_buy_base',
                            'taker_buy_quote', 'funding_rate'
                        ])
                except Exception as e:
                    logger.warning(f"[{sym}] 초기 버퍼 로드 실패: {e}")
                finally:
                    self._buffer_init_tasks.discard(sym)

        # 병렬 실행 (gather)
        await asyncio.gather(*[fetch_and_buffer(s) for s in new_symbols])
        if log_summary:
            logger.info(f"초기 버퍼 로드 완료: {len(new_symbols)}개 종목")

    async def _sync_initial_state(self, silent: bool = False) -> None:
        """계정 잔고 및 포지션을 거래소와 동기화합니다."""
        if self._state_sync_in_progress:
            return

        self._state_sync_in_progress = True
        try:
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(None, self.binance.get_futures_positions)
            balance = await loop.run_in_executor(None, self.binance.get_futures_balance)
            
            new_positions: dict[str, PositionInfo] = {
                p['symbol']: {
                    'qty': abs(float(p['positionAmt'])),
                    'side': 'BUY' if float(p['positionAmt']) > 0 else 'SELL',
                    'entry_p': float(p.get('entryPrice', 0))
                } for p in positions if abs(float(p['positionAmt'])) > 1e-7
            }
            
            added = set(new_positions.keys()) - set(self.active_positions.keys())
            removed = set(self.active_positions.keys()) - set(new_positions.keys())
            db_open_symbols = self.db.get_open_trade_symbols()
            exchange_position_symbols = set(new_positions.keys())
            stale_db_open_symbols = db_open_symbols - exchange_position_symbols - set(self.executing_symbols) - removed
            if removed:
                logger.info(f"🔄 포지션 종료 감지: {removed}")
                for sym in removed:
                    self.tp_sl_deployed.discard(sym)
                    self._schedule_missing_position_sync_exit(sym)
                    loop.run_in_executor(None, self.order_manager.client.cancel_all_orders, sym)
                    logger.info(f"🧹 [{sym}] 포지션 종료 확인. 유저 스트림/체결 내역 정산을 잠시 기다립니다.")

            if stale_db_open_symbols:
                logger.warning(
                    "DB에는 OPEN이지만 거래소 포지션이 없는 심볼 감지: "
                    f"{', '.join(sorted(stale_db_open_symbols))}"
                )
                for sym in sorted(stale_db_open_symbols):
                    self.tp_sl_deployed.discard(sym)
                    self.db.record_trade_event(
                        sym,
                        "STALE_DB_OPEN_WITHOUT_EXCHANGE_POSITION",
                        {
                            "reason": "DB_OPEN_WITHOUT_EXCHANGE_POSITION",
                            "grace_seconds": StrategyConfig.MISSING_POSITION_SYNC_EXIT_GRACE_SECONDS,
                        },
                    )
                    self._schedule_missing_position_sync_exit(sym)
                    loop.run_in_executor(None, self.order_manager.client.cancel_all_orders, sym)
                    logger.warning(f"🧹 [{sym}] 거래소 포지션 없음. 지연 정산 예약 및 잔여 주문 취소를 요청했습니다.")

            self.active_positions = new_positions
            
            orphan_symbols: list[str] = []

            for symbol, pos in self.active_positions.items():
                # 진입 처리 중인 종목은 레이스 컨디션을 피하기 위해 건너뜁니다.
                if symbol in self.executing_symbols:
                    continue
                    
                if self.db.get_latest_open_trade(symbol) is None:
                    logger.info(f"📝 [{symbol}] DB 기록 없는 고아 포지션 감지. 자동 동기화를 수행합니다.")
                    
                    data_dict: dict[str, Any] = {
                        "note": "Auto-synced from Binance (Sync Loop)",
                        "feature_snapshot_skipped": True,
                        "position_side": pos.get("side", "BUY"),
                        "data_interval": "1m",
                    }
                    prob = 0.0

                    self.db.save_trade_entry(
                        symbol, 
                        float(pos['entry_p']), 
                        float(pos['qty']), 
                        StrategyConfig.LEVERAGE, 
                        float(prob), 
                        data_dict,
                        entry_source='MANUAL_SYNC',
                        training_excluded=True,
                    )
                    orphan_symbols.append(symbol)
            
            if orphan_symbols:
                logger.info("🚑 고아 포지션 감지됨. 즉시 TP/SL 자가 치유를 시작합니다.")
                await self._sync_ongoing_tp_sl(symbols=orphan_symbols)
            
            # 고점 잔고 로드 및 위험 지표 업데이트
            peak_balance = self.db.get_system_state('peak_balance', balance)
            if balance > peak_balance:
                self.db.set_system_state('peak_balance', balance)
                peak_balance = balance
            
            if not silent:
                pos_summary = ", ".join([f"{s}({p['qty']:.2f})" for s, p in self.active_positions.items()])
                logger.info(f"💰 잔고: {balance:.2f} USDT (Peak: {peak_balance:.2f}) | 관리 포지션: {len(self.active_positions)}개 [{pos_summary}]")
        except Exception as e:
            if not silent:
                logger.error(f"상태 동기화 실패: {e}")
        finally:
            self._state_sync_in_progress = False

    async def _periodic_state_sync_loop(self) -> None:
        """유저 스트림 누락에 대비해 REST 포지션 상태를 주기적으로 대조합니다."""
        sync_interval_seconds = StrategyConfig.POSITION_REST_SYNC_INTERVAL_SECONDS
        while self.is_running:
            await asyncio.sleep(sync_interval_seconds)
            try:
                await self._sync_initial_state(silent=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"주기적 포지션 동기화 실패: {exc}")

    async def _delayed_account_state_sync(self) -> None:
        """계정 업데이트 수신 후 짧게 기다렸다가 REST 상태를 확인합니다."""
        await asyncio.sleep(StrategyConfig.ACCOUNT_UPDATE_SYNC_DELAY_SECONDS)
        await self._sync_initial_state(silent=True)

    def _schedule_missing_position_sync_exit(self, symbol: str) -> None:
        """포지션 없음 감지 후 유저 스트림 정산을 기다리는 지연 정산 작업을 예약합니다."""
        pending_task = self._pending_sync_exit_tasks.get(symbol)
        if pending_task is not None and not pending_task.done():
            return
        self._pending_sync_exit_tasks[symbol] = asyncio.create_task(
            self._finalize_missing_position_after_grace(symbol)
        )

    def _clear_exit_accumulator(self, symbol: str) -> None:
        """특정 심볼의 청산 PnL 누적 메모리를 정리합니다."""
        self.exit_pnl_accumulator = {
            key: value for key, value in self.exit_pnl_accumulator.items()
            if not key.startswith(f"{symbol}:")
        }

    def _cancel_pending_sync_exit(self, symbol: str) -> None:
        """유저 스트림에서 정상 정산된 심볼의 지연 fallback 작업을 취소합니다."""
        pending_task = self._pending_sync_exit_tasks.pop(symbol, None)
        if pending_task is not None and not pending_task.done():
            pending_task.cancel()

    async def _finalize_missing_position_after_grace(self, symbol: str) -> None:
        """유저 스트림을 기다린 뒤에도 OPEN이면 체결 내역으로 청산 손익을 복구합니다."""
        try:
            await asyncio.sleep(StrategyConfig.MISSING_POSITION_SYNC_EXIT_GRACE_SECONDS)
            latest_open_trade = self.db.get_latest_open_trade(symbol)
            if latest_open_trade is None:
                self._clear_exit_accumulator(symbol)
                return

            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(None, self.binance.get_futures_positions)
            still_open = any(
                pos.get("symbol") == symbol and abs(float(pos.get("positionAmt", 0) or 0)) > 1e-7
                for pos in positions
            )
            if still_open:
                return

            recovered_exit = await self._recover_exit_from_account_trades(symbol, latest_open_trade)
            if recovered_exit is not None:
                exit_price, pnl, roe, reason = recovered_exit
                exit_updated = self.db.update_trade_exit(symbol, exit_price, pnl, roe, reason)
                if exit_updated:
                    if reason == "SL":
                        self.db.record_symbol_sl(symbol)
                    elif reason == "TP":
                        self.db.clear_symbol_sl(symbol)
                self.db.record_trade_event(
                    symbol,
                    "MISSING_POSITION_EXIT_RECOVERED",
                    {
                        "reason": reason,
                        "exit_price": float(exit_price),
                        "pnl": float(pnl),
                        "roe": float(roe),
                    },
                )
                logger.warning(f"[{symbol}] 유저 스트림 누락 청산을 체결 내역으로 복구했습니다. ({reason}, PnL: {pnl:.2f})")
            else:
                updated = self.db.update_trade_exit(symbol, 0.0, 0.0, 0.0, 'SYNC_EXIT')
                self.db.record_trade_event(
                    symbol,
                    "STALE_DB_OPEN_SYNC_EXIT",
                    {
                        "reason": "DB_OPEN_WITHOUT_EXCHANGE_POSITION_AFTER_GRACE",
                        "updated": bool(updated),
                    },
                )
                logger.warning(f"[{symbol}] 체결 내역 복구 실패. 최종 fallback으로 SYNC_EXIT 0 정산했습니다.")
            self._clear_exit_accumulator(symbol)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[{symbol}] 지연 청산 정산 실패: {exc}")
        finally:
            self._pending_sync_exit_tasks.pop(symbol, None)

    async def _recover_exit_from_account_trades(
        self,
        symbol: str,
        latest_open_trade: dict[str, Any],
    ) -> tuple[float, float, float, str] | None:
        """Binance 계정 체결 내역에서 누락된 청산 체결가와 실현손익을 복구합니다."""
        entry_time = latest_open_trade.get("entry_time")
        start_time_ms: int | None = None
        if isinstance(entry_time, datetime):
            start_time_ms = int(entry_time.timestamp() * 1000) - 1000

        loop = asyncio.get_event_loop()
        account_trades = await loop.run_in_executor(
            None,
            self.binance.get_futures_account_trades,
            symbol,
            start_time_ms,
            100,
        )
        realized_trades: list[dict[str, Any]] = []
        for trade in account_trades:
            if not isinstance(trade, dict):
                continue
            try:
                realized_pnl = float(trade.get("realizedPnl", 0) or 0)
            except (TypeError, ValueError):
                continue
            if abs(realized_pnl) <= 1e-12:
                continue
            realized_trades.append(trade)

        if not realized_trades:
            return None

        total_pnl = 0.0
        weighted_exit_value = 0.0
        total_qty = 0.0
        for trade in realized_trades:
            price = float(trade.get("price", 0) or 0)
            qty = abs(float(trade.get("qty", 0) or 0))
            realized_pnl = float(trade.get("realizedPnl", 0) or 0)
            total_pnl += realized_pnl
            if price > 0 and qty > 0:
                weighted_exit_value += price * qty
                total_qty += qty

        exit_price = weighted_exit_value / total_qty if total_qty > 0 else 0.0
        entry_price = float(latest_open_trade.get("entry_price", 0) or 0)
        entry_qty = float(latest_open_trade.get("qty", total_qty) or total_qty)
        leverage = float(latest_open_trade.get("leverage", StrategyConfig.LEVERAGE) or StrategyConfig.LEVERAGE)
        margin_used = (entry_price * entry_qty) / leverage if entry_price > 0 and entry_qty > 0 and leverage > 0 else 0.0
        roe = total_pnl / margin_used if margin_used > 0 else 0.0
        reason = "TP" if total_pnl > 0 else "SL" if total_pnl < 0 else "MANUAL_EXIT"
        return exit_price, total_pnl, roe, reason

    async def _on_user_message(self, msg: dict[str, Any]) -> None:
        """바이낸스 유저 스트림 메시지 처리 (진입/청산 정밀 판정)"""
        try:
            event_type = msg.get('e')
            
            # --- 1. 주문 체결 업데이트 (ORDER_TRADE_UPDATE) ---
            if event_type == 'ORDER_TRADE_UPDATE':
                o = msg['o']
                symbol = o['s']
                status = o['X']  # NEW, FILLED, CANCELED 등
                side = o['S']  # BUY, SELL
                order_type = o['ot']  # MARKET, STOP_MARKET 등
                order_id = str(o.get('i', ''))
                is_filled = (status == 'FILLED')
                is_partial_fill = (status == 'PARTIALLY_FILLED')
                
                if is_filled or is_partial_fill:
                    execution_price = float(o['ap'])
                    realized_pnl = float(o['rp'])  # 해당 체결 이벤트의 실현 손익
                    is_reduce_only = o.get('R', False)
                    qty = float(o['z'])  # 누적 체결 수량
                    exit_accumulator_key = f"{symbol}:{order_id}"

                    if is_reduce_only and order_id:
                        accumulated_pnl = self.exit_pnl_accumulator.get(exit_accumulator_key, 0.0)
                        accumulated_pnl += realized_pnl
                        self.exit_pnl_accumulator[exit_accumulator_key] = accumulated_pnl
                    else:
                        accumulated_pnl = realized_pnl

                    if is_partial_fill:
                        if is_reduce_only:
                            logger.info(
                                f"[주문 알림] {symbol} {side} {order_type} | 상태: {status} | "
                                f"누적 실현손익: {accumulated_pnl:.2f}"
                            )
                        return
                    
                    is_exit_signal = (accumulated_pnl != 0 or is_reduce_only)
                    
                    if is_exit_signal:
                        reason = self._classify_exit_reason(order_type, accumulated_pnl, is_reduce_only)

                        roe = 0.0
                        latest_trade = self.db.get_latest_open_trade(symbol)
                        if latest_trade:
                            entry_price = float(latest_trade.get('entry_price', 0.0) or 0.0)
                            entry_qty = float(latest_trade.get('qty', qty) or qty)
                            if entry_price > 0 and entry_qty > 0 and StrategyConfig.LEVERAGE > 0:
                                margin_used = (entry_price * entry_qty) / StrategyConfig.LEVERAGE
                                if margin_used > 0:
                                    roe = accumulated_pnl / margin_used

                        exit_updated = self.db.update_trade_exit(
                            symbol=symbol,
                            exit_price=execution_price,
                            pnl=accumulated_pnl,
                            roe=roe,
                            reason=reason,
                        )
                        if exit_updated:
                            self._cancel_pending_sync_exit(symbol)
                            if reason == "SL":
                                self.db.record_symbol_sl(symbol)
                            elif reason == "TP":
                                self.db.clear_symbol_sl(symbol)
                        self.db.record_trade_event(
                            symbol,
                            "EXIT_FILLED",
                            {
                                "reason": reason,
                                "order_type": order_type,
                                "side": side,
                                "qty": float(qty),
                                "execution_price": float(execution_price),
                                "realized_pnl": float(accumulated_pnl),
                                "roe": float(roe),
                                "is_reduce_only": bool(is_reduce_only),
                            },
                            order_id=order_id,
                        )
                        self.exit_pnl_accumulator.pop(exit_accumulator_key, None)
                    else:
                        direction = 'Long' if side == 'BUY' else 'Short'
                        # 진입 시에는 즉시 반영하여 중복 진입 방지
                        self.active_positions[symbol] = PositionInfo(qty=qty, side=side, entry_p=execution_price)
                        self.db.record_trade_event(
                            symbol,
                            "USER_STREAM_ENTRY_FILLED",
                            {
                                "side": side,
                                "order_type": order_type,
                                "qty": float(qty),
                                "execution_price": float(execution_price),
                            },
                            order_id=order_id,
                        )
                        logger.success(f"🚀 [{symbol}] {direction} 체결 확인 (Qty: {qty}, Price: {execution_price})")
                
                else:
                    # NEW, CANCELED 등 기타 상태는 1회성 로깅
                    logger.info(f"[주문 알림] {symbol} {side} {order_type} | 상태: {status}")

            # --- 2. 잔고 및 포지션 업데이트 (ACCOUNT_UPDATE) ---
            elif event_type == 'ACCOUNT_UPDATE':
                asyncio.create_task(self._delayed_account_state_sync())

        except Exception as e:
            logger.error(f"User Message 처리 오류: {e}")

    async def _execute_entry(
        self,
        symbol: str,
        data: dict[str, Any],
        prob: float,
        current_balance: float | None = None,
    ) -> None:
        """AI 신호를 실제 진입 주문으로 바꾸되 슬롯 예약과 보호 주문 실패 방어를 보장합니다."""
        async with self.entry_lock:
            occupied_slots = len(self.active_positions) + len(self.executing_symbols)
            if symbol in self.executing_symbols:
                logger.info(f"⏭️ [{symbol}] 이미 진입 주문 처리 중입니다. 중복 요청 스킵.")
                return
            if symbol in self.active_positions:
                logger.info(f"⏭️ [{symbol}] 이미 보유 중인 포지션입니다. 중복 진입을 건너뜁니다.")
                return
            if occupied_slots >= StrategyConfig.MAX_SLOTS:
                logger.warning(
                    f"🚫 [{symbol}] 진입 신호(P: {prob:.2%})는 유효하나, "
                    f"예약 포함 최대 슬롯({StrategyConfig.MAX_SLOTS}) 초과로 진입하지 않습니다."
                )
                return
            self.executing_symbols.add(symbol)
        
        try:
            # 슬롯당 현금에 레버리지를 곱해 진입 금액을 계산합니다.
            loop = asyncio.get_event_loop()
            available_balance = await loop.run_in_executor(None, self.binance.get_futures_available_balance)

            if available_balance is None or available_balance <= 0:
                logger.error("❌ 주문 가능 잔고 확인 불가로 진입을 중단합니다.")
                return

            remaining_slots = max(1, StrategyConfig.MAX_SLOTS - len(self.active_positions))
            cash_per_slot = (available_balance * StrategyConfig.ENTRY_MARGIN_SAFETY_RATIO) / remaining_slots
            entry_value = cash_per_slot * StrategyConfig.LEVERAGE
            
            logger.info(
                f"🚀 [{symbol}] 진입 실행 "
                f"(주문 가능 잔고: {available_balance:.2f} USDT, "
                f"할당 자금: {cash_per_slot:.2f} USDT, 레버리지: {StrategyConfig.LEVERAGE}배)"
            )
            self.db.record_trade_event(
                symbol,
                "ENTRY_SIGNAL_ACCEPTED",
                {
                    "prob": float(prob),
                    "available_balance": float(available_balance),
                    "cash_per_slot": float(cash_per_slot),
                    "entry_value": float(entry_value),
                    "reserved_slots": len(self.executing_symbols),
                    "active_slots": len(self.active_positions),
                },
            )
            order = await loop.run_in_executor(None, self.order_manager.execute_market_buy, symbol, entry_value)
            
            if order:
                fallback_entry_price = float(data.get('close', 0))
                qty, entry_p = await self._resolve_entry_fill(symbol, order, fallback_entry_price)
                if qty <= 0:
                    logger.error(f"[{symbol}] 실제 체결 수량을 확인하지 못했습니다. 주문 응답: {order}")
                    self.executing_symbols.discard(symbol)
                    return

                tp_p, sl_p = TradingRules.get_tp_sl_prices(entry_p, position_side='BUY')
                native_data = self._to_native_value(data)
                serializable_data = {
                    str(k): self._serialize_value(v)
                    for k, v in cast(dict[Any, Any], native_data).items()
                }
                
                trade_id = self.db.save_trade_entry(
                    symbol,
                    float(entry_p),
                    float(qty),
                    StrategyConfig.LEVERAGE,
                    float(prob),
                    serializable_data,
                    entry_source='AI',
                    training_excluded=False,
                )
                self.db.record_trade_event(
                    symbol,
                    "ENTRY_FILLED",
                    {
                        "qty": float(qty),
                        "entry_price": float(entry_p),
                        "order_id": str(order.get("orderId", "")),
                        "prob": float(prob),
                    },
                    trade_id=trade_id,
                    order_id=str(order.get("orderId", "")),
                )

                success = await loop.run_in_executor(
                    None,
                    self.order_manager.setup_tp_sl,
                    symbol,
                    qty,
                    tp_p,
                    sl_p,
                    'BUY',
                )
                self.db.record_trade_event(
                    symbol,
                    "PROTECTION_ORDER_RESULT",
                    {
                        "success": bool(success),
                        "tp_price": float(tp_p),
                        "sl_price": float(sl_p),
                        "qty": float(qty),
                    },
                    trade_id=trade_id,
                )
                if not success:
                    logger.critical(
                        f"🛑 [{symbol}] TP/SL 보호 주문 배치 실패. "
                        "무보호 포지션 방지를 위해 즉시 reduce-only 청산을 시도합니다."
                    )
                    close_success = await loop.run_in_executor(
                        None,
                        self.order_manager.close_position,
                        symbol,
                        qty,
                        'BUY',
                    )
                    self.db.record_trade_event(
                        symbol,
                        "PROTECTION_FAILED_FORCE_EXIT",
                        {
                            "close_success": bool(close_success),
                            "qty": float(qty),
                            "entry_price": float(entry_p),
                        },
                        trade_id=trade_id,
                    )
                    if close_success:
                        self.db.update_trade_exit(
                            symbol=symbol,
                            exit_price=float(entry_p),
                            pnl=0.0,
                            roe=0.0,
                            reason="PROTECTION_FAILED_EXIT",
                        )
                        return

                    self.active_positions[symbol] = PositionInfo(qty=qty, side='BUY', entry_p=entry_p)
                    logger.critical(f"🛑 [{symbol}] 강제 청산 실패. 무보호 포지션을 메모리에 등록하고 자가 복구 대상에 유지합니다.")
                    return

                self.tp_sl_deployed.discard(symbol)
                
                self.active_positions[symbol] = PositionInfo(qty=qty, side='BUY', entry_p=entry_p)
                logger.success(f"🔥 [{symbol}] AI 진입 주문 성공! (확률: {float(prob):.2%}, Qty: {qty}, Price: {entry_p})")
                
                await self._report_top_features(symbol, data, prob)
        except Exception as e:
            logger.error(f"진입 실행 중 오류: {e}")
        finally:
            self.executing_symbols.discard(symbol)

    async def _sync_ongoing_tp_sl(self, symbols: list[str] | None = None):
        """보유 포지션의 TP/SL 주문 존재 여부를 확인하고 누락 시 자가 복구합니다."""
        try:
            if symbols is None:
                symbols_to_check = list(self.active_positions.keys())
            else:
                symbols_to_check = [
                    symbol for symbol in dict.fromkeys(symbols)
                    if symbol in self.active_positions
                ]

            for symbol in symbols_to_check:
                pos = self.active_positions[symbol]
                loop = asyncio.get_event_loop()
                trade_meta = self.db.get_latest_open_trade_meta(symbol)
                entry_source = str(trade_meta.get('entry_source', 'AI')) if trade_meta else 'AI'
                is_manual_sync_entry = entry_source == 'MANUAL_SYNC'

                # 수동 동기화 포지션은 TP/SL 복구를 한 번 성공한 뒤에는 반복 재검증하지 않습니다.
                if is_manual_sync_entry and symbol in self.tp_sl_deployed:
                    continue

                position_side = str(pos.get('side', 'BUY'))
                exit_side = TradingRules.get_exit_order_side(position_side)
                trade = trade_meta or self.db.get_latest_open_trade(symbol)

                if trade:
                    entry_p = float(trade['entry_price'])
                else:
                    # DB에 기록이 없으면 바이낸스 포지션 정보에서 진입가를 복원합니다.
                    entry_p = float(pos.get('entry_p', 0) or 0)
                    if entry_p <= 0:
                        ticker = await loop.run_in_executor(None, self.binance.get_futures_symbol_ticker, symbol)
                        if not ticker:
                            continue
                        entry_p = float(ticker['price'])

                qty = abs(float(pos.get('qty', 0) or 0))
                if qty <= 1e-9:
                    logger.warning(f"[{symbol}] 현재 포지션 수량이 0이라 TP/SL 점검을 건너뜁니다.")
                    continue

                calc_tp_p, calc_sl_p = TradingRules.get_tp_sl_prices(
                    entry_p,
                    position_side=position_side,
                )
                symbol_info = self.order_manager.get_symbol_info(symbol)
                if symbol_info:
                    tick_size = float(symbol_info["tick_size"])
                    price_precision = int(symbol_info["price_precision"])
                    expected_tp_p = (
                        float(round(calc_tp_p - (calc_tp_p % tick_size), price_precision))
                        if calc_tp_p > 0 else None
                    )
                    expected_sl_p = (
                        float(round(calc_sl_p - (calc_sl_p % tick_size), price_precision))
                        if calc_sl_p > 0 else None
                    )
                else:
                    expected_tp_p = calc_tp_p if calc_tp_p > 0 else None
                    expected_sl_p = calc_sl_p if calc_sl_p > 0 else None

                open_orders = await loop.run_in_executor(None, self.binance.get_futures_open_orders, symbol)
                algo_orders = await loop.run_in_executor(None, self.binance.get_futures_algo_open_orders, symbol)
                # 숨겨진 Conditional 주문까지 보려면 최근 주문 이력도 함께 확인해야 합니다.
                recent_all = await loop.run_in_executor(None, self.binance.get_futures_all_orders, symbol, 20)
                
                # [🔥 원천 데이터 로깅]
                logger.info(f"🔍 [{symbol}] 주문 감시 RAW 데이터 분석")
                if open_orders:
                    for o in open_orders:
                        logger.info(f"   - OpenOrder: Type={o.get('type')}, Status={o.get('status')}")
                if algo_orders:
                    for a in algo_orders:
                        if a.get('symbol') == symbol:
                            logger.info(
                                "   - AlgoOrder: "
                                f"Type={a.get('type')}, OrigType={a.get('origType')}, "
                                f"Side={a.get('side')}, Trigger={a.get('triggerPrice') or a.get('stopPrice')}, "
                                f"Qty={a.get('quantity') or a.get('origQty') or a.get('qty')}, "
                                f"Name={a.get('algoName')}, Status={a.get('algoStatus') or a.get('status')}"
                            )
                if recent_all:
                    live_recent = [r for r in recent_all if r.get('status') == 'NEW']
                    for r in live_recent:
                        logger.info(f"   - RecentAll(NEW): Type={r.get('type')}, Status={r.get('status')}, ID={r.get('orderId')}")

                recent_new_orders = [r for r in recent_all if r.get('status') == 'NEW']
                protection_state = self.order_manager.inspect_existing_tp_sl_orders(
                    open_orders=open_orders,
                    algo_orders=algo_orders,
                    recent_orders=recent_new_orders,
                    expected_side=exit_side,
                    expected_tp_price=expected_tp_p,
                    expected_sl_price=expected_sl_p,
                    expected_quantity=qty,
                    entry_price=entry_p,
                    position_side=position_side,
                )
                has_tp = bool(protection_state["has_tp"])
                has_sl = bool(protection_state["has_sl"])
                tp_covers = bool(protection_state["tp_covers"])
                sl_covers = bool(protection_state["sl_covers"])
                tp_qty = float(protection_state["tp_qty"])
                sl_qty = float(protection_state["sl_qty"])

                if has_tp and has_sl and tp_covers and sl_covers:
                    if is_manual_sync_entry:
                        self.tp_sl_deployed.add(symbol)
                    logger.info(
                        f"[{symbol}] 기존 TP/SL 조건부 주문 확인 완료 "
                        f"(포지션: {qty}, TP수량: {tp_qty}, SL수량: {sl_qty})"
                    )
                    continue

                has_undercovered_order = (has_tp and not tp_covers) or (has_sl and not sl_covers)
                if has_undercovered_order:
                    logger.warning(
                        f"⚠️ [{symbol}] TP/SL 수량 미달 감지. 기존 조건부 주문을 취소하고 전체 수량으로 재등록합니다. "
                        f"(포지션: {qty}, TP수량: {tp_qty}, SL수량: {sl_qty})"
                    )
                    await loop.run_in_executor(None, self.order_manager.client.cancel_all_orders, symbol)
                    self.db.record_trade_event(
                        symbol,
                        "PROTECTION_QTY_MISMATCH_REPAIR",
                        {
                            "position_qty": float(qty),
                            "tp_qty": float(tp_qty),
                            "sl_qty": float(sl_qty),
                            "expected_tp": float(expected_tp_p or 0),
                            "expected_sl": float(expected_sl_p or 0),
                        },
                    )
                    has_tp = False
                    has_sl = False

                if not has_tp or not has_sl:
                    logger.warning(
                        f"⚠️ [{symbol}] TP/SL 누락 감지 (API 기준). 복구를 시도합니다. "
                        f"(TP: {has_tp}, SL: {has_sl}, 포지션: {qty}, TP수량: {tp_qty}, SL수량: {sl_qty})"
                    )
                    tp_p = float(expected_tp_p or 0) if not has_tp else 0
                    sl_p = float(expected_sl_p or 0) if not has_sl else 0

                    if tp_p > 0 or sl_p > 0:
                        logger.warning(f"⚠️ [{symbol}] TP/SL 누락 복구 시도 (TP_target: {tp_p}, SL_target: {sl_p})")
                        success = await loop.run_in_executor(
                            None,
                            self.order_manager.setup_tp_sl,
                            symbol,
                            qty,
                            tp_p,
                            sl_p,
                            position_side,
                        )
                        self.db.record_trade_event(
                            symbol,
                            "PROTECTION_REPAIR_RESULT",
                            {
                                "success": bool(success),
                                "tp_missing": not has_tp,
                                "sl_missing": not has_sl,
                                "tp_target": float(tp_p),
                                "sl_target": float(sl_p),
                                "position_qty": float(qty),
                                "existing_tp_qty": float(tp_qty),
                                "existing_sl_qty": float(sl_qty),
                            },
                        )
                        if success:
                            if is_manual_sync_entry:
                                self.tp_sl_deployed.add(symbol)
        except Exception as e:
            logger.error(f"TP/SL 자가 복구 중 오류: {e}")

    async def stop(self):
        """엔진 종료 시 백그라운드 작업, 웹소켓, ZMQ, DB, 프로세스 락을 정리합니다."""
        self.is_running = False
        try:
            periodic_state_sync_task = self._periodic_state_sync_task
            self._periodic_state_sync_task = None
            if periodic_state_sync_task is not None:
                periodic_state_sync_task.cancel()
                try:
                    await periodic_state_sync_task
                except asyncio.CancelledError:
                    pass
            pending_sync_exit_tasks = list(self._pending_sync_exit_tasks.values())
            self._pending_sync_exit_tasks.clear()
            for task in pending_sync_exit_tasks:
                task.cancel()
            if pending_sync_exit_tasks:
                await asyncio.gather(*pending_sync_exit_tasks, return_exceptions=True)
            await self.close_event_batches.shutdown()
            await self.user_ws.stop()
            self.market_data_subscriber.close()
            self.symbols_rpc_client.close()
            self.db.close()
        except Exception:
            pass
        finally:
            if self._process_lock_acquired:
                self.process_lock.release()
                self._process_lock_acquired = False

if __name__ == "__main__":
    engine = KairosEngine()
    try:
        asyncio.run(engine.start())
    except Exception:
        pass
