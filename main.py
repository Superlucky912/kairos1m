"""
main.py
=======
이 파일은 Kairos 실거래 엔진을 실행하는 가장 바깥쪽 진입점입니다.

실제 핵심 로직은 `trader/trading_engine.py`에 있지만,
운영 환경에서는 보통 이 파일을 실행해 엔진 전체를 시작합니다.
즉, 이 파일은 얇은 부트스트랩 계층입니다.
"""

import asyncio
import sys
if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from trader.trading_engine import KairosEngine
from common.utils.logger import logger

async def main():
    """실거래 엔진을 생성하고 비동기 실행 루프를 시작합니다."""
    logger.info("========================================")
    logger.info("   Kairos Trading Bot 가동 (NEW START)   ")
    logger.info("========================================")
    
    try:
        engine = KairosEngine()
        await engine.start()
    except KeyboardInterrupt:
        logger.info("사용자에 의해 프로그램이 중단되었습니다.")
    except Exception as e:
        logger.critical(f"프로그램 실행 중 예기치 못한 오류 발생: {e}")
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
