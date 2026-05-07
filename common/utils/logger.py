"""
logger.py
=========
이 파일은 프로젝트 전체의 로깅 규칙을 한 곳에서 정의하는 공용 로거 설정 모듈입니다.

각 파일이 직접 `logging` 설정을 반복하지 않고,
`setup_logger()`를 통해 같은 포맷, 같은 파일 구조, 같은 레벨 규칙을 공유하도록 만든 기반 계층입니다.

핵심 역할은 다음과 같습니다.
1. 콘솔과 파일 출력 형식 통일
2. 날짜별 또는 크기별 로그 로테이션 지원
3. `SUCCESS` 같은 커스텀 로그 레벨 제공
4. 컴포넌트별 로거 이름과 저장 위치를 일관되게 관리
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any, cast

from common.config.base_config import Config

# SUCCESS 레벨 추가 (loguru 스타일 호환)
SUCCESS_LEVEL_NUM = 25
logging.addLevelName(SUCCESS_LEVEL_NUM, "SUCCESS")

class KairosLogger(logging.Logger):
    """기본 logging.Logger에 success 레벨 메서드를 추가한 프로젝트 전용 로거 클래스."""

    def success(self, message: str, *args: Any, **kws: Any) -> None:
        if self.isEnabledFor(SUCCESS_LEVEL_NUM):
            self._log(SUCCESS_LEVEL_NUM, message, args, **kws)

def success(self: logging.Logger, message: str, *args: Any, **kws: Any) -> None:
    if self.isEnabledFor(SUCCESS_LEVEL_NUM):
        self._log(SUCCESS_LEVEL_NUM, message, args, **kws)

setattr(logging.Logger, "success", success)

def setup_logger(name: str = "Kairos", log_file: str | Path = "kairos.log", rotation: str = "size") -> KairosLogger:
    """
    프로젝트 표준 형식의 로거를 생성하거나 기존 로거를 재사용합니다.

    `name`은 로그 메시지에 찍히는 컴포넌트 이름이고,
    `log_file`은 실제 파일 저장 위치를 결정합니다.
    그래서 `Kairos.Train` 같은 이름과 `training.log` 같은 파일 경로는 서로 독립적으로 조합할 수 있습니다.
    """
    logger = cast(KairosLogger, logging.getLogger(name))
    
    # 같은 이름의 로거를 여러 번 초기화하면 핸들러가 중복으로 붙을 수 있으므로,
    # 이미 설정된 로거는 그대로 재사용합니다.
    if logger.handlers:
        return logger

    # 레벨 설정
    logger.setLevel(Config.LOG_LEVEL)

    formatter = logging.Formatter(
        '[%(asctime)s | %(levelname)s | %(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 콘솔 출력은 개발 중 실시간 확인용이고, 파일 출력은 추적/회고용입니다.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 상대 경로를 주면 logs/<컴포넌트>/<날짜>.log 구조로 저장합니다.
    today = datetime.now().strftime("%Y-%m-%d")
    log_name = os.path.splitext(str(log_file))[0]
    
    # 파일 출력 설정
    if os.path.isabs(str(log_file)):
        file_path = Path(log_file)
    else:
        # 예: "training.log" -> logs/training/2026-03-31.log
        file_path = Config.LOG_DIR / log_name / f"{today}.log"
        
    os.makedirs(file_path.parent, exist_ok=True)

    if rotation == "daily":
        # 날짜별 로깅 (자정에 교체, 30일치 유지)
        file_handler = TimedRotatingFileHandler(
            file_path,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding='utf-8'
        )
        # 파일명에 날짜가 붙도록 설정 (기본은 .YYYY-MM-DD가 뒤에 붙음)
    else:
        # 크기별 로깅 (10MB씩 5개까지 유지)
        file_handler = RotatingFileHandler(
            file_path, 
            maxBytes=10*1024*1024, 
            backupCount=5, 
            encoding='utf-8'
        )
    
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 하위 로거가 상위 로거로 다시 전파되면 같은 로그가 여러 번 찍힐 수 있으므로 차단합니다.
    if name != "Kairos":
        logger.propagate = False

    return logger

# 기본 전역 로거는 기존 코드와의 호환용 진입점입니다.
logger = setup_logger("Kairos", "kairos.log")
