"""
main.py
=======
이 파일은 Kairos web 도구의 FastAPI 엔트리입니다.

현재 제공 기능은 1분봉 단타 백테스트 실행과 결과 리포트 조회입니다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from common.config.strategy_config import StrategyConfig
from web.api.app.services.scalping_backtest_service import (
    PROJECT_ROOT,
    ScalpingBacktestRequest,
    load_report_trades,
    list_scalping_reports,
    read_top15_label_candles,
    read_trade_replay,
    read_scalping_report,
    run_scalping_backtest,
)


UI_DIR = PROJECT_ROOT / "web" / "ui"


class ScalpingBacktestPayload(BaseModel):
    """1분봉 백테스트 실행 API 요청 바디입니다."""

    model: str = Field(default=StrategyConfig.SCALPING_1M_MODEL_NAME)
    threshold: float = Field(default=StrategyConfig.SCALPING_1M_ENTRY_SCORE_THRESHOLD)
    limit: int | None = Field(default=None, ge=300)
    candidate_top_n: int = Field(default=StrategyConfig.SCALPING_1M_CANDIDATE_TOP_N, ge=1, le=50)


app = FastAPI(title="Kairos Web Backtest", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

assets_dir = UI_DIR / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/")
def index() -> FileResponse:
    """정적 UI의 index.html을 반환합니다."""
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    """API 상태를 반환합니다."""
    return {"status": "ok"}


@app.get("/api/scalping-1m/defaults")
def get_scalping_defaults() -> dict[str, Any]:
    """1분봉 백테스트 UI와 API가 공유할 기본 설정값을 반환합니다."""
    return {
        "model": StrategyConfig.SCALPING_1M_MODEL_NAME,
        "threshold": StrategyConfig.SCALPING_1M_ENTRY_SCORE_THRESHOLD,
        "limit": None,
        "candidate_top_n": StrategyConfig.SCALPING_1M_CANDIDATE_TOP_N,
    }


@app.get("/api/scalping-1m/reports")
def get_scalping_reports() -> list[dict[str, Any]]:
    """1분봉 백테스트 리포트 목록을 반환합니다."""
    return list_scalping_reports()


@app.get("/api/scalping-1m/reports/{report_name}")
def get_scalping_report(report_name: str) -> dict[str, Any]:
    """특정 1분봉 백테스트 리포트를 반환합니다."""
    try:
        return read_scalping_report(report_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/scalping-1m/reports/{report_name}/trades")
def get_scalping_report_trades(report_name: str) -> list[dict[str, Any]]:
    """특정 리포트에 포함된 최근 거래 목록을 반환합니다."""
    try:
        report = read_scalping_report(report_name)
        return load_report_trades(PROJECT_ROOT / "common" / "logs" / "backtest" / report["name"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/scalping-1m/reports/{report_name}/trades/{trade_index}/replay")
def get_scalping_trade_replay(report_name: str, trade_index: int, padding_minutes: int = 180) -> dict[str, Any]:
    """선택한 거래의 1분봉 캔들 복기 데이터를 반환합니다."""
    try:
        return read_trade_replay(report_name, trade_index, padding_minutes=padding_minutes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/scalping-1m/top15-label-candles")
def get_top15_label_candles(hours: int = 24) -> dict[str, Any]:
    """최신 top15 심볼의 1분봉 TP/SL 라벨 캔들을 반환합니다."""
    if hours < 1 or hours > 72:
        raise HTTPException(status_code=400, detail="hours는 1~72 사이여야 합니다.")
    try:
        return read_top15_label_candles(hours=hours)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/scalping-1m/backtests")
def post_scalping_backtest(payload: ScalpingBacktestPayload) -> dict[str, Any]:
    """1분봉 검증 백테스트를 실행합니다."""
    request = ScalpingBacktestRequest(
        model=payload.model,
        threshold=payload.threshold,
        limit=payload.limit,
        candidate_top_n=payload.candidate_top_n,
    )
    try:
        return run_scalping_backtest(request)
    except subprocess.TimeoutExpired as exc:  # type: ignore[name-defined]
        raise HTTPException(status_code=504, detail="백테스트 실행 시간이 초과되었습니다.") from exc
