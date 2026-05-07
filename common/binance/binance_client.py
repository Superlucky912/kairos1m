"""
BinanceClient.py
================
이 파일은 바이낸스 거래소와 통신하는 가장 낮은 단계의 클라이언트입니다.

프로젝트의 다른 모듈들은 직접 Binance SDK를 여기저기서 호출하지 않고,
가능하면 이 클래스를 통해서만 거래소와 통신하도록 구성되어 있습니다.
그 이유는 다음과 같습니다.

1. API 호출 방식을 한 곳에 모아두면 예외 처리와 로깅을 통일할 수 있습니다.
2. 실거래에서 쓰는 주문/계좌/시세 호출을 상위 로직과 분리할 수 있습니다.
3. 타입 정리와 라이브러리 버전 차이 대응을 한 파일 안에서 처리할 수 있습니다.

즉, 이 파일은 단순 유틸이 아니라
"거래소와 대화하는 표준 창구" 역할을 하는 래퍼 계층입니다.
"""

from __future__ import annotations

import time
from typing import Any

from binance import Client
from binance.exceptions import BinanceAPIException

from common.config.base_config import Config
from common.config.strategy_config import StrategyConfig
from common.utils.logger import logger

class BinanceClient:
    """바이낸스 REST API 호출을 프로젝트 표준 방식으로 감싸는 저수준 래퍼."""

    def __init__(self):
        self.api_key: str | None = Config.BINANCE_API_KEY
        self.api_secret: str | None = Config.BINANCE_API_SECRET
        self.client: Client | None = None
        self.bsm = None
        self.time_offset_ms = 0
        self._last_time_sync_at = 0.0

        self._initialize_client()

    def _initialize_client(self) -> None:
        """API 키를 이용해 Binance REST Client를 만들고 기본 연결 상태를 준비합니다."""
        try:
            if not self.api_key or not self.api_secret:
                logger.warning("Binance API Key/Secret이 설정되지 않았습니다. Read-Only 모드로 작동할 수 있습니다.")

            self.client = Client(self.api_key, self.api_secret)
            try:
                self.sync_server_time_offset(force=True)
            except Exception as sync_error:
                logger.warning(f"Binance 서버 시간 초기 동기화 실패: {sync_error}")
            logger.info("Binance REST Client 초기화 완료")
        except Exception as e:
            logger.error(f"Binance Client 초기화 중 오류 발생: {e}")

    def _require_client(self) -> Client:
        """클라이언트가 반드시 존재해야 하는 호출부에서 안전하게 client 객체를 꺼냅니다."""
        if self.client is None:
            raise RuntimeError("Binance REST Client가 초기화되지 않았습니다.")
        return self.client

    def _require_api_key(self) -> str:
        if self.api_key is None:
            raise RuntimeError("BINANCE_API_KEY가 설정되지 않았습니다.")
        return self.api_key

    def _require_api_secret(self) -> str:
        if self.api_secret is None:
            raise RuntimeError("BINANCE_API_SECRET이 설정되지 않았습니다.")
        return self.api_secret

    def sync_server_time_offset(self, force: bool = False) -> int:
        """Binance 서버 시각과 로컬 시각 차이를 동기화합니다."""
        now = time.time()
        if not force and self._last_time_sync_at and (now - self._last_time_sync_at) < 300:
            return self.time_offset_ms

        server_time = self._require_client().futures_time()
        server_time_ms = int(server_time["serverTime"])
        local_time_ms = int(time.time() * 1000)
        self.time_offset_ms = server_time_ms - local_time_ms
        self._last_time_sync_at = now

        # python-binance의 signed request도 같은 오프셋을 사용하도록 맞춰 둡니다.
        self._require_client().timestamp_offset = self.time_offset_ms
        logger.info(f"Binance 서버 시간 오프셋 동기화 완료 ({self.time_offset_ms}ms)")
        return self.time_offset_ms

    def _get_signed_timestamp_ms(self, force_sync: bool = False) -> int:
        """수동 서명 요청용 timestamp를 Binance 서버 기준으로 생성합니다."""
        try:
            offset_ms = self.sync_server_time_offset(force=force_sync)
        except Exception as sync_error:
            logger.warning(f"Binance 서버 시간 동기화 실패. 로컬 시각으로 대체합니다: {sync_error}")
            offset_ms = self.time_offset_ms
        return int(time.time() * 1000 + offset_ms)

    def _call_signed_sdk_request(
        self,
        request_fn: Any,
        *args: Any,
        log_context: str,
        **kwargs: Any,
    ) -> Any:
        """python-binance signed request에 recvWindow와 -1021 재시도를 공통 적용합니다."""
        params = dict(kwargs)
        params.setdefault("recvWindow", Config.BINANCE_RECV_WINDOW_MS)

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self.sync_server_time_offset(force=(attempt > 0))
            except Exception as sync_error:
                logger.warning(f"{log_context} 서버 시간 동기화 실패: {sync_error}")

            try:
                return request_fn(*args, **params)
            except BinanceAPIException as exc:
                last_error = exc
                if exc.code == -1021 and attempt == 0:
                    logger.warning(
                        f"{log_context} timestamp 오차 감지(-1021). 서버 시간 재동기화 후 1회 재시도합니다."
                    )
                    continue
                raise
            except Exception as exc:
                last_error = exc
                raise

        if last_error is not None:
            raise last_error

        raise RuntimeError(f"{log_context} signed request failed")

    def _post_manual_signed_request(
        self,
        base_url: str,
        endpoint: str,
        params: dict[str, Any],
        log_context: str,
    ) -> Any:
        """SDK 바깥의 수동 서명 REST 요청을 보정된 timestamp로 전송합니다."""
        import hashlib
        import hmac
        import requests

        api_key = self._require_api_key()
        api_secret = self._require_api_secret()
        headers = {
            'X-MBX-APIKEY': api_key,
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0',
        }

        last_response: Any = None
        for attempt in range(2):
            signed_params = {k: v for k, v in params.items() if v is not None}
            signed_params['recvWindow'] = Config.BINANCE_RECV_WINDOW_MS
            signed_params['timestamp'] = self._get_signed_timestamp_ms(force_sync=(attempt > 0))

            query_string = '&'.join([f"{k}={v}" for k, v in sorted(signed_params.items())])
            signature = hmac.new(api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

            last_response = requests.post(
                f"{base_url}{endpoint}?{query_string}&signature={signature}",
                headers=headers,
            )

            if last_response.status_code == 200:
                return last_response

            try:
                error_payload = last_response.json()
            except Exception:
                error_payload = {}

            if error_payload.get("code") == -1021 and attempt == 0:
                logger.warning(f"{log_context} timestamp 오차 감지(-1021). 서버 시간 재동기화 후 1회 재시도합니다.")
                self.sync_server_time_offset(force=True)
                continue

            return last_response

        return last_response

    def get_top_symbols_by_change(self, limit: int = 50, min_volume: float = 1000000.0) -> list[str]:
        """
        24시간 변동률과 거래대금을 기준으로 감시 대상 후보 심볼을 뽑습니다.

        이 메서드는 "지금 어떤 종목들을 실시간 감시할 것인가"를 결정할 때 주로 사용됩니다.
        단순 변동률만 보지 않고, 실제 거래 가능한 선물 영구계약이며
        일정 수준 이상의 거래대금을 가진 심볼만 남기는 점이 핵심입니다.
        """
        try:
            client = self._require_client()

            # 1. 전체 선물 종목 정보 가져오기 (필터링용)
            exchange_info = client.futures_exchange_info()
            active_symbols = {
                s['symbol']: s for s in exchange_info['symbols'] 
                if s['status'] == 'TRADING' and s['quoteAsset'] == StrategyConfig.QUOTE_ASSET and s['contractType'] == 'PERPETUAL'
            }

            # 2. 전 종목 티커 가져오기
            tickers = client.futures_ticker()

            # 3. 필터링 및 정렬 (QUOTE_ASSET 종목만 포함)
            valid_tickers: list[dict[str, Any]] = []
            for t in tickers:
                symbol = t['symbol']
                if symbol in active_symbols and float(t['quoteVolume']) >= min_volume:
                    valid_tickers.append(t)
            
            # 수익률(priceChangePercent) 기준 내림차순 정렬
            sorted_tickers = sorted(valid_tickers, key=lambda x: float(x['priceChangePercent']), reverse=True)
            
            top_symbols = [t['symbol'] for t in sorted_tickers[:limit]]
            logger.info(f"선물 {StrategyConfig.QUOTE_ASSET} 종목 중 수익률 상위 {len(top_symbols)}개 추출 완료 (최소 거래대금: ${min_volume/1e6:.1f}M)")
            logger.info(f"상위 {len(top_symbols)}개 종목: {', '.join(top_symbols)}")
            return top_symbols
        except Exception as e:
            logger.error(f"활성 상위 종목 추출 중 오류: {e}")
            return []

    def get_exchange_info(self) -> Any:
        """선물 시장의 심볼 목록과 정밀도, 필터 정보 같은 메타데이터를 가져옵니다."""
        try:
            return self._require_client().futures_exchange_info()
        except Exception as e:
            logger.error(f"Exchange Info 조회 중 오류 발생: {e}")
            raise e

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        startTime: int | None = None,
        endTime: int | None = None,
    ) -> Any:
        """특정 심볼의 선물 캔들 데이터를 가져옵니다."""
        try:
            return self._require_client().futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit,
                startTime=startTime,
                endTime=endTime,
            )
        except Exception as e:
            logger.error(f"Klines 조회 중 오류 발생 ({symbol}): {e}")
            raise e

    def get_open_interest(self, symbol: str) -> Any:
        """특정 심볼의 현재 미결제약정(Open Interest)을 가져옵니다."""
        try:
            return self._require_client().futures_open_interest(symbol=symbol)
        except Exception as e:
            logger.error(f"OI 조회 중 오류 발생 ({symbol}): {e}")
            raise e

    def get_funding_rate(
        self,
        symbol: str,
        limit: int = 100,
        startTime: int | None = None,
        endTime: int | None = None,
    ) -> Any:
        """특정 심볼의 펀딩비 이력을 기간 조건과 함께 가져옵니다."""
        try:
            return self._require_client().futures_funding_rate(
                symbol=symbol,
                limit=limit,
                startTime=startTime,
                endTime=endTime,
            )
        except Exception as e:
            logger.error(f"Funding Rate 조회 중 오류 발생 ({symbol}): {e}")
            raise e

    def get_open_interest_history(
        self,
        symbol: str,
        period: str,
        limit: int = 500,
        startTime: int | None = None,
        endTime: int | None = None,
    ) -> Any:
        """특정 심볼의 과거 미결제약정 이력 데이터를 가져옵니다."""
        try:
            return self._require_client().futures_open_interest_hist(
                symbol=symbol,
                period=period,
                limit=limit,
                startTime=startTime,
                endTime=endTime,
            )
        except Exception as e:
            logger.error(f"OI 이력 조회 중 오류 발생 ({symbol}): {e}")
            raise e

    def get_futures_balance(self) -> float:
        """선물 계좌에서 실거래 기준이 되는 USDT 잔고를 찾아 반환합니다."""
        try:
            balances = self._call_signed_sdk_request(
                self._require_client().futures_account_balance,
                log_context="Futures Balance 조회",
            )
            for b in balances:
                if b['asset'] == 'USDT':
                    return float(b['balance'])
            return 0.0
        except Exception as e:
            logger.error(f"선물 잔고 조회 중 오류 발생: {e}")
            raise e

    def get_futures_available_balance(self) -> float:
        """신규 주문에 실제로 사용할 수 있는 USDT 주문 가능 잔고를 반환합니다."""
        try:
            balances = self._call_signed_sdk_request(
                self._require_client().futures_account_balance,
                log_context="Futures Available Balance 조회",
            )
            for b in balances:
                if b['asset'] != 'USDT':
                    continue
                available_balance = b.get('availableBalance')
                if available_balance is not None:
                    return float(available_balance)
                return float(b['balance'])
            return 0.0
        except Exception as e:
            logger.error(f"선물 주문 가능 잔고 조회 중 오류 발생: {e}")
            raise e

    def get_futures_positions(self) -> Any:
        """현재 열려 있는 모든 선물 포지션 정보를 가져옵니다."""
        try:
            return self._call_signed_sdk_request(
                self._require_client().futures_position_information,
                log_context="Futures Position 조회",
            )
        except Exception as e:
            logger.error(f"포지션 정보 조회 중 오류 발생: {e}")
            raise e

    def set_leverage(self, symbol: str, leverage: int) -> Any:
        """심볼의 레버리지를 설정합니다."""
        try:
            return self._call_signed_sdk_request(
                self._require_client().futures_change_leverage,
                symbol=symbol,
                leverage=leverage,
                log_context=f"[{symbol}] 레버리지 설정",
            )
        except Exception as e:
            logger.error(f"레버리지 설정 중 오류 발생 ({symbol}): {e}")
            raise e

    def set_margin_type(self, symbol: str, margin_type: str = 'ISOLATED') -> Any:
        """심볼의 마진 모드(ISOLATED/CROSSED)를 변경합니다."""
        try:
            return self._call_signed_sdk_request(
                self._require_client().futures_change_margin_type,
                symbol=symbol,
                marginType=margin_type,
                log_context=f"[{symbol}] 마진 모드 설정",
            )
        except Exception as e:
            # 이미 해당 모드인 경우 에러가 발생하므로 무시 처리
            if "No need to change margin type" in str(e):
                return
            logger.warning(f"마진 모드 설정 중 오류 발생 ({symbol}): {e}")

    def create_futures_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        price: float | None = None,
        stopPrice: float | None = None,
        reduce_only: bool = False,
        **kwargs: Any,
    ) -> Any:
        """
        선물 주문을 생성하는 범용 엔트리입니다.

        상위 계층은 이 메서드 하나로 시장가 진입, reduce-only 청산, 조건부 주문 등을 만들 수 있습니다.
        주문 종류에 따라 불필요한 파라미터를 제거하는 방어 로직도 이 안에서 같이 처리합니다.
        """
        try:
            params: dict[str, Any] = {
                'symbol': symbol,
                'side': side,
                'type': order_type,
                'quantity': quantity,
                'reduceOnly': reduce_only,
            }
            if price is not None:
                params['price'] = price
            if stopPrice is not None:
                params['stopPrice'] = stopPrice

            # 추가 파라미터 반영 (workingType, priceProtect 등)
            params.update(kwargs)

            # 시장가 및 조건부 시장가 주문 시 지정가는 불필요
            if order_type in ['MARKET', 'STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                params.pop('price', None)

            # closePosition=True인 경우 reduceOnly가 포함되면 에러(-1106)가 발생합니다.
            if kwargs.get('closePosition'):
                params.pop('quantity', None)
                params.pop('reduceOnly', None)

            # STOP_MARKET/TAKE_PROFIT_MARKET은 stopPrice가 필수
            return self._call_signed_sdk_request(
                self._require_client().futures_create_order,
                log_context=f"[{symbol}] Futures 주문 생성",
                **params,
            )
        except Exception as e:
            logger.error(f"선물 주문 생성 중 오류 발생 ({symbol} {side}): {e}")
            raise e

    def get_futures_open_orders(self, symbol: str) -> list[Any]:
        """특정 심볼의 현재 대기 주문 목록을 리스트 형태로 반환합니다."""
        try:
            result = self._call_signed_sdk_request(
                self._require_client().futures_get_open_orders,
                symbol=symbol,
                log_context=f"[{symbol}] 대기 주문 조회",
            )
            return result if isinstance(result, list) else [result]
        except Exception as e:
            logger.error(f"대기 주문 조회 중 오류 발생 ({symbol}): {e}")
            return []

    def get_futures_all_orders(self, symbol: str, limit: int = 10) -> list[Any]:
        """특정 심볼의 최근 주문 이력을 조회해 리스트 형태로 반환합니다."""
        try:
            result = self._call_signed_sdk_request(
                self._require_client().futures_get_all_orders,
                symbol=symbol,
                limit=limit,
                log_context=f"[{symbol}] 전체 주문 이력 조회",
            )
            return result if isinstance(result, list) else [result]
        except Exception as e:
            logger.error(f"전체 주문 이력 조회 실패 ({symbol}): {e}")
            return []

    def get_futures_account_trades(
        self,
        symbol: str,
        start_time_ms: int | None = None,
        limit: int = 100,
    ) -> list[Any]:
        """특정 심볼의 계정 체결 내역을 조회해 실현손익 복구에 사용합니다."""
        try:
            params: dict[str, Any] = {
                "symbol": symbol,
                "limit": limit,
            }
            if start_time_ms is not None:
                params["startTime"] = start_time_ms
            result = self._call_signed_sdk_request(
                self._require_client().futures_account_trades,
                log_context=f"[{symbol}] 계정 체결 내역 조회",
                **params,
            )
            return result if isinstance(result, list) else [result]
        except Exception as e:
            logger.error(f"계정 체결 내역 조회 실패 ({symbol}): {e}")
            return []

    def get_futures_symbol_ticker(self, symbol: str) -> Any | None:
        """특정 심볼의 현재가(티커) 정보를 가져옵니다."""
        try:
            return self._require_client().futures_symbol_ticker(symbol=symbol)
        except Exception as e:
            logger.error(f"티커 조회 중 오류 발생 ({symbol}): {e}")
            return None

    def get_futures_algo_open_orders(self, symbol: str | None = None) -> list[Any]:
        """조건부(Algo) 대기 주문 목록을 조회합니다."""
        try:
            params: dict[str, Any] = {}
            if symbol:
                params['symbol'] = symbol
            res = self._call_signed_sdk_request(
                self._require_client().futures_get_open_algo_orders,
                log_context=f"[{symbol or 'ALL'}] Algo 대기 주문 조회",
                **params,
            )
            if isinstance(res, dict):
                if isinstance(res.get('orders'), list):
                    return res['orders']
                if isinstance(res.get('data'), list):
                    return res['data']
            return res if isinstance(res, list) else []
        except Exception as e:
            logger.debug(f"조건부 주문 조회 실패 ({symbol or 'ALL'}): {e}")
            return []

    def cancel_algo_order_v1(self, symbol: str, algo_id: str) -> Any | None:
        """특정 조건부 주문을 취소합니다."""
        try:
            algo_id_value: int | str = int(algo_id) if str(algo_id).isdigit() else algo_id
            return self._call_signed_sdk_request(
                self._require_client().futures_cancel_algo_order,
                symbol=symbol,
                algoId=algo_id_value,
                log_context=f"[{symbol}] Algo Order 취소",
            )
        except Exception as e:
            logger.error(f"Algo Order 취소 실패 ({symbol}): {e}")
            return None

    def cancel_all_orders(self, symbol: str) -> Any:
        """특정 종목의 일반/조건부 대기 주문을 모두 취소합니다."""
        try:
            res = self._call_signed_sdk_request(
                self._require_client().futures_cancel_all_open_orders,
                symbol=symbol,
                log_context=f"[{symbol}] 일반 주문 전체 취소",
            )

            try:
                self._call_signed_sdk_request(
                    self._require_client().futures_cancel_all_algo_open_orders,
                    symbol=symbol,
                    log_context=f"[{symbol}] Algo 주문 전체 취소",
                )
                logger.info(f"🧹 [{symbol}] 잔여 조건부 주문 전체 취소 완료")
            except Exception as cancel_all_algo_error:
                logger.warning(f"[{symbol}] 조건부 주문 전체 취소 실패, 개별 취소로 재시도합니다: {cancel_all_algo_error}")

            algo_orders = self.get_futures_algo_open_orders(symbol=symbol)
            for algo_order in algo_orders:
                algo_id = algo_order.get('algoId')
                if algo_id is None:
                    continue
                self.cancel_algo_order_v1(symbol, str(algo_id))
                logger.info(f"🧹 [{symbol}] 잔여 조건부 주문 개별 취소 완료 (AlgoID: {algo_id})")

            return res
        except Exception as e:
            logger.error(f"주문 일괄 취소 중 오류 발생 ({symbol}): {e}")
            raise e

    def futures_post_algo_order_v1(
        self,
        symbol: str,
        side: str,
        algotype: str,
        quantity: float,
        trigger_price: float,
        limit_price: float | None = None,
    ) -> Any | None:
        """
        공식 'Algo Order API'를 사용하여 조건부 주문(STOP/TP)을 전송합니다.
        - limit_price가 있으면 지정가(STOP, TAKE_PROFIT), 없으면 시장가(STOP_MARKET, TAKE_PROFIT_MARKET)로 작동합니다.
        """
        try:
            base_url = "https://fapi.binance.com"
            endpoint = "/fapi/v1/algoOrder"
            is_stop = 'STOP' in algotype.upper()

            # 지정가 vs 시장가 타입 결정
            if limit_price is not None:
                order_type = 'STOP' if is_stop else 'TAKE_PROFIT'
            else:
                order_type = 'STOP_MARKET' if is_stop else 'TAKE_PROFIT_MARKET'

            params: dict[str, Any] = {
                'symbol': symbol,
                'side': side,
                'algoType': 'CONDITIONAL',
                'type': order_type,
                'quantity': float(quantity),
                'triggerPrice': float(trigger_price),
                'reduceOnly': 'true',
                'workingType': 'MARK_PRICE',
            }

            if limit_price is not None:
                params['price'] = float(limit_price)
                params['timeInForce'] = 'GTC'

            logger.info(f"[{symbol}] /fapi/v1/algoOrder ({order_type}) 전송 시도...")
            res = self._post_manual_signed_request(
                base_url=base_url,
                endpoint=endpoint,
                params=params,
                log_context=f"[{symbol}] /fapi/v1/algoOrder ({order_type})",
            )

            if res.status_code == 200:
                data = res.json()
                logger.info(f"[{symbol}] Algo Order 성공 (AlgoID: {data.get('algoId')})")
                return data
            else:
                logger.error(f"Algo Order 실패: {res.text}")
                return None
        except Exception as e:
            logger.error(f"Algo Order 예외: {e}")
            return None

    # 기존 중복 구현은 정리하고 현재 구현만 유지합니다.
