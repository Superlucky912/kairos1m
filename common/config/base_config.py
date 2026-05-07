"""
BaseConfig.py
=============
이 파일은 전략 수치가 아니라 "실행 환경 자체"에 대한 기초 설정을 담당합니다.

`StrategyConfig`가 매매 규칙과 학습 파라미터를 담는 곳이라면,
이 파일은 DB 접속 정보, API 키, 로그 폴더, 모델 저장 경로처럼
프로젝트가 어디에 연결되고 어디에 파일을 두는지 같은 인프라 설정을 맡습니다.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# .env 파일 로드 (루트 디렉토리 기준)
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent.parent / ".env")

# 프로젝트 루트 경로 (common/config/ 폴더의 상상상위 폴더)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Config:
    """환경변수와 공용 경로를 읽어 프로젝트 전반에 제공하는 인프라 설정 컨테이너."""
    # Binance API
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
    BINANCE_RECV_WINDOW_MS = int(os.getenv("BINANCE_RECV_WINDOW_MS", 10000))
    
    # Database
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", 5432))
    DB_NAME = os.getenv("DB_NAME", "kairos")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    
    # Trading Parameters
    MAX_REGULAR_POSITIONS = int(os.getenv("MAX_REGULAR_POSITIONS", 5))
    SYMBOL_LOSS_COOLDOWN_MINUTES = int(os.getenv("SYMBOL_LOSS_COOLDOWN_MINUTES", 60))
    
    # Directories
    LOG_DIR = BASE_DIR / "common" / "logs"
    LIVE_LOG_DIR = LOG_DIR / "live"
    BACKTEST_LOG_DIR = LOG_DIR / "backtest"
    MODEL_DIR = BASE_DIR / "common" / "models"
    ANSWER_DIR = BASE_DIR / "common" / "docs" / "answer"
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 디렉토리 생성 보장
os.makedirs(Config.LOG_DIR, exist_ok=True)
os.makedirs(Config.LIVE_LOG_DIR, exist_ok=True)
os.makedirs(Config.BACKTEST_LOG_DIR, exist_ok=True)
os.makedirs(Config.MODEL_DIR, exist_ok=True)
os.makedirs(Config.ANSWER_DIR, exist_ok=True)
