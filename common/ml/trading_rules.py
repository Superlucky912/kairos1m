"""
TradingRules.py
===============
이 파일은 백테스트와 실거래가 공통으로 참조하는 "기계적 매매 규칙"을 담고 있습니다.

중요한 점은, 진입 신호를 만드는 모델과
실제로 언제 청산해야 하는지를 판단하는 규칙은 서로 다른 책임이라는 것입니다.
이 파일은 그중에서도 TP/SL 가격과 청산 주문 방향 계산 같은 후자 영역을 담당합니다.
"""

from common.config.strategy_config import StrategyConfig

class TradingRules:
    """
    백테스트와 실매매가 함께 사용하는 매매 규칙 모음입니다.

    상태를 들고 있는 객체라기보다,
    포지션 처리에 필요한 판단 함수를 정적 메서드 형태로 묶어 둔 규칙 라이브러리에 가깝습니다.
    """
    
    @staticmethod
    def normalize_position_side(position_side: str) -> str:
        """포지션 방향 문자열을 BUY/SELL로 정규화합니다."""
        return 'SELL' if str(position_side).upper() == 'SELL' else 'BUY'

    @staticmethod
    def get_exit_order_side(position_side: str) -> str:
        """포지션 청산에 필요한 반대 주문 side를 반환합니다."""
        normalized_side = TradingRules.normalize_position_side(position_side)
        return 'BUY' if normalized_side == 'SELL' else 'SELL'

    @staticmethod
    def get_tp_sl_prices(
        entry_price: float,
        position_side: str = 'BUY',
    ) -> tuple[float, float]:
        """포지션 방향에 따라 TP, SL 가격을 계산합니다."""
        normalized_side = TradingRules.normalize_position_side(position_side)
        if normalized_side == 'SELL':
            tp_price = entry_price * (1 - StrategyConfig.TARGET_THRESHOLD)
            sl_price = entry_price * (1 + StrategyConfig.LABEL_STOP_LOSS)
            return tp_price, sl_price

        tp_price = entry_price * (1 + StrategyConfig.TARGET_THRESHOLD)
        sl_price = entry_price * (1 - StrategyConfig.LABEL_STOP_LOSS)
        return tp_price, sl_price
