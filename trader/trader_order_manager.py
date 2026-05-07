"""
OrderManager.py
===============
실제 주문 실행과 보호 주문 배치를 담당하는 모듈입니다.
"""

from __future__ import annotations

from typing import Any

from common.binance.binance_client import BinanceClient
from common.config.strategy_config import StrategyConfig
from common.ml.trading_rules import TradingRules
from common.utils.logger import logger


class OrderManager:
    """
    Binance 선물 주문 실행 계층입니다.

    상위 엔진이 진입 여부를 결정하면, 이 모듈이 실제 수량 계산,
    마진/레버리지 설정, 시장가 진입, TP/SL 보호 주문 등록을 수행합니다.
    """

    def __init__(self, client: BinanceClient):
        """Binance 클라이언트와 심볼 메타데이터 캐시를 준비합니다."""
        self.client = client
        self._exchange_info_cache: dict[str, Any] | None = None
        self._entry_setup_done_symbols: set[str] = set()
        logger.info("OrderManager 초기화 완료")

    def ensure_entry_trading_setup(self, symbol: str) -> None:
        """심볼별 마진 모드와 레버리지는 최초 진입 전에만 설정합니다."""
        if symbol in self._entry_setup_done_symbols:
            return

        try:
            self.client.set_margin_type(symbol, "ISOLATED")
            self.client.set_leverage(symbol, StrategyConfig.LEVERAGE)
            self._entry_setup_done_symbols.add(symbol)
        except Exception as e:
            logger.warning(f"[{symbol}] 진입 전 거래 설정 캐시 실패: {e}")

    def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        """
        심볼별 주문 정밀도와 필터 정보를 조회합니다.
        """
        try:
            if self._exchange_info_cache is None:
                self._exchange_info_cache = self.client.get_exchange_info()
            if self._exchange_info_cache is None:
                return None

            exchange_info = self._exchange_info_cache
            for symbol_info in exchange_info["symbols"]:
                if symbol_info["symbol"] != symbol:
                    continue

                info: dict[str, Any] = {
                    "price_precision": int(symbol_info["pricePrecision"]),
                    "qty_precision": int(symbol_info["quantityPrecision"]),
                    "tick_size": 0,
                    "step_size": 0,
                }
                for filter_info in symbol_info["filters"]:
                    if filter_info["filterType"] == "PRICE_FILTER":
                        info["tick_size"] = filter_info["tickSize"]
                    elif filter_info["filterType"] == "LOT_SIZE":
                        info["step_size"] = filter_info["stepSize"]
                return info
            return None
        except Exception as e:
            logger.error(f"심볼 정보 조회 중 오류 발생 ({symbol}): {e}")
            return None

    def execute_market_buy(self, symbol: str, quote_amount: float) -> dict[str, Any] | None:
        """
        지정한 USDT 기준 금액으로 시장가 롱 진입을 실행합니다.
        """
        try:
            info = self.get_symbol_info(symbol)
            ticker = self.client.get_futures_symbol_ticker(symbol)
            if not info or not ticker:
                return None

            curr_price = float(ticker["price"])
            self.ensure_entry_trading_setup(symbol)

            raw_qty = quote_amount / curr_price
            step_size = float(info["step_size"])
            qty_precision = info["qty_precision"]
            quantity = float(round(raw_qty - (raw_qty % step_size), qty_precision))

            if quantity <= 0:
                logger.error(
                    f"[{symbol}] 계산된 주문 수량이 0입니다. "
                    f"(Quote: {quote_amount}, Price: {curr_price}, Step: {step_size})"
                )
                return None

            logger.info(f"[{symbol}] 시장가 롱 진입 시도: Qty={quantity}")
            order = self.client.create_futures_order(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                quantity=quantity,
            )
            logger.info(f"[{symbol}] 진입 성공: OrderID={order.get('orderId')}")
            return order
        except Exception as e:
            logger.error(f"[{symbol}] 시장가 진입 중 오류: {e}")
            return None

    @staticmethod
    def _normalize_order_marker(value: Any) -> str:
        """Binance 응답의 주문 타입/상태 문자열을 비교 가능한 표준 형태로 바꿉니다."""
        if value is None:
            return ""
        return str(value).upper().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _as_positive_float(value: Any) -> float | None:
        """주문 응답의 가격/수량 필드를 양수 float로 변환합니다."""
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric > 0 else None

    def _normalize_quantity(self, symbol: str, quantity: float) -> float:
        """심볼 LOT_SIZE에 맞춰 보호 주문 수량을 거래소 허용 단위로 내림 정규화합니다."""
        info = self.get_symbol_info(symbol)
        if not info:
            return float(quantity)

        step_size = float(info["step_size"])
        qty_precision = int(info["qty_precision"])
        if step_size <= 0:
            return float(round(quantity, qty_precision))
        normalized_quantity = quantity - (quantity % step_size)
        return float(round(normalized_quantity, qty_precision))

    @classmethod
    def _extract_order_trigger_price(cls, raw_order: dict[str, Any]) -> float | None:
        """조건부 주문 응답에서 사용할 수 있는 트리거 가격 필드를 추출합니다."""
        for key in ("triggerPrice", "stopPrice", "activatePrice", "price"):
            trigger_price = cls._as_positive_float(raw_order.get(key))
            if trigger_price is not None:
                return trigger_price
        return None

    @classmethod
    def _extract_order_quantity(cls, raw_order: dict[str, Any]) -> float | None:
        """조건부 주문 응답에서 주문 수량 필드를 추출합니다."""
        for key in ("quantity", "origQty", "origQtyValue", "q", "qty"):
            quantity = cls._as_positive_float(raw_order.get(key))
            if quantity is not None:
                return quantity
        return None

    @staticmethod
    def _order_identity(source: str, raw_order: dict[str, Any]) -> tuple[str, str]:
        """여러 주문 조회 API에 같은 주문이 중복 노출될 때 합산 중복을 막기 위한 식별자를 만듭니다."""
        for key in ("algoId", "clientAlgoId", "orderId", "clientOrderId"):
            value = raw_order.get(key)
            if value is not None:
                return key, str(value)
        return source, str(id(raw_order))

    @staticmethod
    def _prices_match(actual: float | None, expected: float | None) -> bool:
        """거래소 응답 가격과 기대 가격이 허용 오차 안에서 같은지 확인합니다."""
        if actual is None or expected is None or expected <= 0:
            return False
        tolerance = max(abs(expected) * 1e-6, 1e-8)
        return abs(actual - expected) <= tolerance

    @classmethod
    def _infer_tp_sl_from_trigger(
        cls,
        trigger_price: float | None,
        entry_price: float | None,
        position_side: str | None,
    ) -> tuple[bool, bool]:
        """트리거 가격과 진입가 관계로 TP/SL 여부를 보조 추론합니다."""
        if trigger_price is None:
            return False, False

        normalized_position_side = cls._normalize_order_marker(position_side)
        normalized_entry_price = cls._as_positive_float(entry_price)
        if normalized_entry_price is None:
            return False, False

        if normalized_position_side in {"SELL", "SHORT"}:
            return trigger_price < normalized_entry_price, trigger_price > normalized_entry_price
        return trigger_price > normalized_entry_price, trigger_price < normalized_entry_price

    @classmethod
    def detect_existing_tp_sl_orders(
        cls,
        open_orders: list[Any] | None = None,
        algo_orders: list[Any] | None = None,
        recent_orders: list[Any] | None = None,
        expected_side: str | None = None,
        expected_tp_price: float | None = None,
        expected_sl_price: float | None = None,
        entry_price: float | None = None,
        position_side: str | None = None,
    ) -> tuple[bool, bool]:
        """실제 주문 type/algoName/triggerPrice를 기준으로 TP/SL 존재를 판별합니다."""
        has_tp = False
        has_sl = False
        normalized_expected_side = cls._normalize_order_marker(expected_side)

        candidate_groups = (
            ("open", list(open_orders or [])),
            ("algo", list(algo_orders or [])),
            ("recent", list(recent_orders or [])),
        )

        for source, orders in candidate_groups:
            for raw_order in orders:
                if not isinstance(raw_order, dict):
                    continue

                order_side = cls._normalize_order_marker(raw_order.get("side") or raw_order.get("S"))
                if normalized_expected_side and order_side and order_side != normalized_expected_side:
                    continue

                markers = {
                    cls._normalize_order_marker(raw_order.get("type")),
                    cls._normalize_order_marker(raw_order.get("origType")),
                    cls._normalize_order_marker(raw_order.get("ot")),
                    cls._normalize_order_marker(raw_order.get("algoName")),
                }
                markers.discard("")

                if any(marker.startswith("TAKE_PROFIT") for marker in markers):
                    has_tp = True
                if any(
                    marker in {"STOP", "STOP_MARKET"} or marker.startswith("STOP_LOSS")
                    for marker in markers
                ):
                    has_sl = True

                is_algo_candidate = (
                    source == "algo"
                    or raw_order.get("algoId") is not None
                    or raw_order.get("clientAlgoId") is not None
                )
                if not is_algo_candidate:
                    if has_tp and has_sl:
                        break
                    continue

                trigger_price = cls._extract_order_trigger_price(raw_order)
                if cls._prices_match(trigger_price, expected_tp_price):
                    has_tp = True
                if cls._prices_match(trigger_price, expected_sl_price):
                    has_sl = True

                inferred_tp, inferred_sl = cls._infer_tp_sl_from_trigger(
                    trigger_price=trigger_price,
                    entry_price=entry_price,
                    position_side=position_side,
                )
                if inferred_tp:
                    has_tp = True
                if inferred_sl:
                    has_sl = True

                if has_tp and has_sl:
                    break

            if has_tp and has_sl:
                break

        return has_tp, has_sl

    @classmethod
    def inspect_existing_tp_sl_orders(
        cls,
        open_orders: list[Any] | None = None,
        algo_orders: list[Any] | None = None,
        recent_orders: list[Any] | None = None,
        expected_side: str | None = None,
        expected_tp_price: float | None = None,
        expected_sl_price: float | None = None,
        expected_quantity: float | None = None,
        entry_price: float | None = None,
        position_side: str | None = None,
    ) -> dict[str, Any]:
        """
        TP/SL 존재 여부와 각 보호 주문이 덮는 수량을 함께 계산합니다.

        기존 구현은 주문 존재만 확인했기 때문에, 부분 수량 TP/SL도 정상으로 볼 수 있었습니다.
        이 메서드는 현재 포지션 전체 수량을 보호하는지까지 함께 판단합니다.
        """
        has_tp = False
        has_sl = False
        tp_qty = 0.0
        sl_qty = 0.0
        seen_orders: set[tuple[str, str]] = set()
        normalized_expected_side = cls._normalize_order_marker(expected_side)

        candidate_groups = (
            ("open", list(open_orders or [])),
            ("algo", list(algo_orders or [])),
            ("recent", list(recent_orders or [])),
        )

        for source, orders in candidate_groups:
            for raw_order in orders:
                if not isinstance(raw_order, dict):
                    continue

                order_side = cls._normalize_order_marker(raw_order.get("side") or raw_order.get("S"))
                if normalized_expected_side and order_side and order_side != normalized_expected_side:
                    continue

                order_key = cls._order_identity(source, raw_order)
                if order_key in seen_orders:
                    continue
                seen_orders.add(order_key)

                markers = {
                    cls._normalize_order_marker(raw_order.get("type")),
                    cls._normalize_order_marker(raw_order.get("origType")),
                    cls._normalize_order_marker(raw_order.get("ot")),
                    cls._normalize_order_marker(raw_order.get("algoName")),
                }
                markers.discard("")
                trigger_price = cls._extract_order_trigger_price(raw_order)

                is_tp = any(marker.startswith("TAKE_PROFIT") for marker in markers)
                is_sl = any(
                    marker in {"STOP", "STOP_MARKET"} or marker.startswith("STOP_LOSS")
                    for marker in markers
                )
                if cls._prices_match(trigger_price, expected_tp_price):
                    is_tp = True
                if cls._prices_match(trigger_price, expected_sl_price):
                    is_sl = True

                inferred_tp, inferred_sl = cls._infer_tp_sl_from_trigger(
                    trigger_price=trigger_price,
                    entry_price=entry_price,
                    position_side=position_side,
                )
                is_tp = is_tp or inferred_tp
                is_sl = is_sl or inferred_sl

                order_quantity = cls._extract_order_quantity(raw_order) or 0.0
                if is_tp:
                    has_tp = True
                    tp_qty += order_quantity
                if is_sl:
                    has_sl = True
                    sl_qty += order_quantity

        expected_qty = float(expected_quantity or 0.0)
        min_cover_qty = expected_qty * 0.999 if expected_qty > 0 else 0.0
        tp_covers = has_tp and (expected_qty <= 0 or tp_qty >= min_cover_qty)
        sl_covers = has_sl and (expected_qty <= 0 or sl_qty >= min_cover_qty)

        return {
            "has_tp": has_tp,
            "has_sl": has_sl,
            "tp_qty": tp_qty,
            "sl_qty": sl_qty,
            "tp_covers": tp_covers,
            "sl_covers": sl_covers,
            "expected_qty": expected_qty,
        }

    def setup_tp_sl(
        self,
        symbol: str,
        quantity: float,
        tp_price: float,
        sl_price: float,
        position_side: str = "BUY",
    ) -> bool:
        """기존 조건부 주문이 있으면 중복 등록을 막고, 없으면 TP/SL을 배치합니다."""
        try:
            if quantity <= 0:
                return False

            info = self.get_symbol_info(symbol)
            if not info:
                return False

            normalized_quantity = self._normalize_quantity(symbol, float(quantity))
            if normalized_quantity <= 0:
                logger.error(f"[{symbol}] 보호 주문 수량 정규화 결과가 0입니다. (원본 수량: {quantity})")
                return False

            exit_side = TradingRules.get_exit_order_side(position_side)
            tick_size = float(info["tick_size"])
            price_precision = info["price_precision"]
            tp_p = float(round(tp_price - (tp_price % tick_size), price_precision)) if tp_price > 0 else None
            sl_p = float(round(sl_price - (sl_price % tick_size), price_precision)) if sl_price > 0 else None

            logger.info(
                f"[{symbol}] 검증된 Algo V1 TP/SL 설정 시도 "
                f"(Qty: {normalized_quantity}, TP: {tp_p}, SL: {sl_p})"
            )

            algo_orders = self.client.get_futures_algo_open_orders(symbol=symbol)
            has_existing_tp, has_existing_sl = self.detect_existing_tp_sl_orders(
                algo_orders=algo_orders,
                expected_side=exit_side,
                expected_tp_price=tp_p,
                expected_sl_price=sl_p,
                position_side=position_side,
            )

            if has_existing_tp or has_existing_sl:
                logger.info(f"[{symbol}] 기존 조건부 주문 감지 (TP: {has_existing_tp}, SL: {has_existing_sl})")

            sl_ok = sl_p is None or has_existing_sl
            if sl_p is not None and not has_existing_sl:
                res = self.client.futures_post_algo_order_v1(
                    symbol=symbol,
                    side=exit_side,
                    algotype="STOP_LOSS_MARKET",
                    quantity=normalized_quantity,
                    trigger_price=sl_p,
                )
                if res:
                    logger.success(f"✅[{symbol}] SL 보호 주문(V1) 등록 완료")
                    sl_ok = True
            elif sl_p is not None and has_existing_sl:
                logger.info(f"[{symbol}] 기존 SL 조건부 주문이 있어 중복 배치를 건너뜁니다.")

            tp_ok = tp_p is None or has_existing_tp
            if tp_p is not None and not has_existing_tp:
                res = self.client.futures_post_algo_order_v1(
                    symbol=symbol,
                    side=exit_side,
                    algotype="TAKE_PROFIT_MARKET",
                    quantity=normalized_quantity,
                    trigger_price=tp_p,
                )
                if res:
                    logger.success(f"✅[{symbol}] TP 보호 주문(V1) 등록 완료")
                    tp_ok = True
            elif tp_p is not None and has_existing_tp:
                logger.info(f"[{symbol}] 기존 TP 조건부 주문이 있어 중복 배치를 건너뜁니다.")

            return sl_ok and tp_ok
        except Exception as e:
            logger.error(f"[{symbol}] V1 TP/SL 설정 중 최종 오류: {e}")
            return False

    def close_position(
        self,
        symbol: str,
        quantity: float,
        position_side: str = "BUY",
    ) -> bool:
        """보호 주문 실패나 운영자 요청 시 reduce-only 시장가 주문으로 포지션을 종료합니다."""
        try:
            self.client.cancel_all_orders(symbol)
            if quantity > 0:
                exit_side = TradingRules.get_exit_order_side(position_side)
                self.client.create_futures_order(
                    symbol=symbol,
                    side=exit_side,
                    order_type="MARKET",
                    quantity=quantity,
                    reduce_only=True,
                )
                logger.info(f"[{symbol}] 포지션 종료 완료")
            return True
        except Exception as e:
            logger.error(f"[{symbol}] 포지션 종료 중 오류: {e}")
            return False
