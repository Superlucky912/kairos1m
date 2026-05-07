"""
scalping_backtest_service.py
============================
이 파일은 web API에서 1분봉 단타 백테스트 검증을 실행하고 결과 파일을 읽는 기능을 제공합니다.

실거래 엔진이나 수집기는 실행하지 않고, `trainer.validate_scalping_1m`만 호출합니다.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from common.config.strategy_config import StrategyConfig
from common.database.manager import DBManager
from common.ml.scalping_1m_dataset import Scalping1MConfig, build_scalping_1m_config


PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKTEST_DIR = PROJECT_ROOT / "common" / "logs" / "backtest"
LEGACY_REPORT_PATTERN = "scalping_1m_results_*.md"
SHORT_REPORT_PATTERN = "*_dual.md"


def _is_scalping_report_path(path: Path) -> bool:
    """신규 짧은 리포트명과 기존 긴 리포트명을 모두 허용합니다."""
    return path.suffix == ".md" and (
        path.match(LEGACY_REPORT_PATTERN)
        or path.match(SHORT_REPORT_PATTERN)
    )


@dataclass(frozen=True)
class ScalpingBacktestRequest:
    """1분봉 백테스트 실행 요청 파라미터입니다."""

    model: str = StrategyConfig.SCALPING_1M_MODEL_NAME
    threshold: float = StrategyConfig.SCALPING_1M_ENTRY_SCORE_THRESHOLD
    limit: int | None = None
    candidate_top_n: int = StrategyConfig.SCALPING_1M_CANDIDATE_TOP_N


def list_scalping_reports() -> list[dict[str, Any]]:
    """저장된 1분봉 백테스트 Markdown 리포트 목록을 최신순으로 반환합니다."""
    if not BACKTEST_DIR.exists():
        return []

    report_paths = {
        *BACKTEST_DIR.glob(LEGACY_REPORT_PATTERN),
        *BACKTEST_DIR.glob(SHORT_REPORT_PATTERN),
    }

    reports: list[dict[str, Any]] = []
    for path in sorted(report_paths, key=lambda item: item.stat().st_mtime, reverse=True):
        if not _is_scalping_report_path(path):
            continue
        reports.append(
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified_at": path.stat().st_mtime,
                "summary": parse_report_summary(path),
            }
        )
    return reports


def read_scalping_report(report_name: str) -> dict[str, Any]:
    """리포트 파일 내용을 읽어 반환합니다."""
    safe_name = Path(report_name).name
    path = BACKTEST_DIR / safe_name
    if not path.exists() or not _is_scalping_report_path(path):
        raise FileNotFoundError(f"리포트를 찾을 수 없습니다: {safe_name}")

    content = path.read_text(encoding="utf-8")
    return {
        "name": path.name,
        "content": content,
        "summary": parse_report_summary(path),
        "trades": load_report_trades(path),
        "diagnostic_entries": load_report_diagnostic_entries(path),
        "contract": load_report_contract(path),
    }


def load_report_trades(path: Path) -> list[dict[str, Any]]:
    """전체 거래 JSON이 있으면 우선 사용하고, 없으면 Markdown 최근 거래 테이블을 fallback으로 파싱합니다."""
    json_path = path.with_suffix(".trades.json")
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            trades = payload.get("trades", [])
            return [_normalize_trade_row(dict(trade)) for trade in trades]
        except Exception:
            return parse_report_trades(path)
    return parse_report_trades(path)


def load_report_diagnostic_entries(path: Path) -> list[dict[str, Any]]:
    """전체 거래 JSON에 저장된 진단용 모델 후보 목록을 반환합니다."""
    json_path = path.with_suffix(".trades.json")
    if not json_path.exists():
        return []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        entries = payload.get("diagnostic_entries", [])
        return [_normalize_trade_row(dict(entry)) for entry in entries]
    except Exception:
        return []


def load_report_contract(path: Path) -> dict[str, Any]:
    """web UI와 후속 시각화가 의존할 1분봉 백테스트 JSON 계약 메타데이터를 반환합니다."""
    json_path = path.with_suffix(".trades.json")
    fallback = {
        "model": "",
        "threshold": None,
        "candidate_top_n": None,
        "data_interval": "1m",
        "display_timezone": "Asia/Seoul",
        "storage_timezone": "UTC",
    }
    if not json_path.exists():
        return fallback
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return fallback
    return {
        "model": str(payload.get("model", "")),
        "threshold": payload.get("threshold"),
        "candidate_top_n": payload.get("candidate_top_n"),
        "data_interval": str(payload.get("data_interval", "1m")),
        "display_timezone": str(payload.get("display_timezone", "Asia/Seoul")),
        "storage_timezone": str(payload.get("storage_timezone", "UTC")),
    }


def parse_report_trades(path: Path) -> list[dict[str, Any]]:
    """Markdown 리포트의 최근 거래 테이블을 파싱합니다."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return []

    lines = content.splitlines()
    table_start = -1
    for index, line in enumerate(lines):
        if line.strip().startswith("## 최근 거래 50건"):
            table_start = index + 1
            break
    if table_start < 0:
        return []

    header: list[str] = []
    trades: list[dict[str, Any]] = []
    for line in lines[table_start:]:
        if not line.startswith("|"):
            if trades:
                break
            continue
        columns = [item.strip() for item in line.strip("|").split("|")]
        if not header:
            header = [_normalize_trade_key(column) for column in columns]
            continue
        if columns and set(columns[0]) <= {":", "-"}:
            continue
        if len(columns) != len(header):
            continue
        row = {header[idx]: columns[idx] for idx in range(len(header))}
        row["trade_index"] = len(trades)
        trades.append(_normalize_trade_row(row))
    return trades


def read_trade_replay(report_name: str, trade_index: int, padding_minutes: int = 180) -> dict[str, Any]:
    """
    선택한 거래의 진입/청산 구간 전후 1분봉 캔들을 조회합니다.

    새 리포트는 전체 거래 JSON을 사용하고, 구형 리포트는 Markdown의 최근 50건만 복기합니다.
    """
    safe_name = Path(report_name).name
    path = BACKTEST_DIR / safe_name
    if not path.exists() or not _is_scalping_report_path(path):
        raise FileNotFoundError(f"리포트를 찾을 수 없습니다: {safe_name}")

    trades = load_report_trades(path)
    if trade_index < 0 or trade_index >= len(trades):
        raise IndexError(f"거래 인덱스를 찾을 수 없습니다: {trade_index}")

    padding_minutes = max(1, min(int(padding_minutes), 720))
    trade = trades[trade_index]
    symbol = str(trade["symbol"])
    entry_time = pd.Timestamp(trade["entry_time_kst"])
    exit_time = pd.Timestamp(trade["exit_time_kst"])
    start_time = entry_time - timedelta(minutes=padding_minutes)
    end_time = exit_time + timedelta(minutes=padding_minutes)

    db = DBManager()
    try:
        query = """
            SELECT timestamp, open, high, low, close, volume
            FROM candles_1m
            WHERE symbol = %s
              AND timestamp >= %s
              AND timestamp <= %s
            ORDER BY timestamp ASC
        """
        candles_df = pd.read_sql(
            query,
            db._ensure_conn(),
            params=[symbol, start_time.to_pydatetime(), end_time.to_pydatetime()],
        )
    finally:
        db.close()

    if not candles_df.empty:
        candles_df["timestamp"] = pd.to_datetime(candles_df["timestamp"], utc=True).dt.tz_convert("Asia/Seoul")
        if "entry_price" not in trade:
            trade["entry_price"] = _nearest_close(candles_df, entry_time)
        if "exit_price" not in trade:
            trade["exit_price"] = _nearest_close(candles_df, exit_time)

    candles = [
        {
            "timestamp": row["timestamp"].isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"] or 0),
        }
        for _, row in candles_df.iterrows()
    ]
    return {
        "report": safe_name,
        "trade": trade,
        "candles": candles,
        "data_interval": "1m",
        "padding_minutes": padding_minutes,
    }


def read_top15_label_candles(hours: int = 24) -> dict[str, Any]:
    """
    최신 1분봉 기준 top15 심볼의 하루치 캔들과 TP/SL 선터치 라벨을 반환합니다.

    각 캔들의 close를 진입가로 보고, 이후 horizon 안에서 현재 설정 TP가 먼저 닿으면 `TP`,
    현재 설정 SL이 먼저 닿으면 `SL`, 둘 다 없으면 `NONE`으로 표시합니다.
    """
    config = build_scalping_1m_config()
    db = DBManager()
    try:
        latest_ts = _fetch_latest_1m_timestamp(db)
        start_ts = latest_ts - pd.Timedelta(hours=hours)
        context_start_ts = start_ts - pd.Timedelta(minutes=1440)
        label_end_ts = latest_ts + pd.Timedelta(minutes=config.horizon_minutes)
        symbols = _fetch_top15_symbols(db, latest_ts, context_start_ts)
        candles_by_symbol = _fetch_label_source_candles(db, symbols, start_ts, label_end_ts)
    finally:
        db.close()

    charts: list[dict[str, Any]] = []
    for symbol in symbols:
        symbol_df = candles_by_symbol[candles_by_symbol["symbol"] == symbol].copy()
        if symbol_df.empty:
            continue
        symbol_df = symbol_df.sort_values("timestamp").reset_index(drop=True)
        visible_df = symbol_df[symbol_df["timestamp"] <= latest_ts].copy()
        label_rows = _label_visible_1m_candles(
            source_df=symbol_df,
            visible_df=visible_df,
            target_tp=config.target_tp,
            stop_loss=config.stop_loss,
            horizon_minutes=config.horizon_minutes,
        )
        charts.append({"symbol": symbol, "candles": label_rows})

    return {
        "start": start_ts.tz_convert("Asia/Seoul").isoformat(),
        "end": latest_ts.tz_convert("Asia/Seoul").isoformat(),
        "hours": hours,
        "target_tp": config.target_tp,
        "stop_loss": config.stop_loss,
        "horizon_minutes": config.horizon_minutes,
        "symbols": symbols,
        "charts": charts,
    }


def _fetch_latest_1m_timestamp(db: DBManager) -> pd.Timestamp:
    """top15 라벨 맵을 만들 수 있을 만큼 심볼이 모인 최신 1분봉 timestamp를 조회합니다."""
    query = """
        WITH max_ts AS (
            SELECT MAX(timestamp) AS ts
            FROM candles_1m
        )
        SELECT candles_1m.timestamp
        FROM candles_1m, max_ts
        WHERE candles_1m.timestamp >= max_ts.ts - interval '7 days'
          AND candles_1m.symbol LIKE '%%USDT'
        GROUP BY candles_1m.timestamp
        HAVING COUNT(DISTINCT candles_1m.symbol) >= 15
        ORDER BY candles_1m.timestamp DESC
        LIMIT 1
    """
    with db._ensure_conn().cursor() as cur:
        cur.execute(query)
        value = cur.fetchone()[0]
    if value is None:
        raise FileNotFoundError("candles_1m 데이터가 없습니다.")
    return pd.Timestamp(value, tz="UTC") if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value).tz_convert("UTC")


def _fetch_top15_symbols(db: DBManager, latest_ts: pd.Timestamp, context_start_ts: pd.Timestamp) -> list[str]:
    """최신 시점 기준 24시간 수익률 상위 15개 USDT 심볼을 조회합니다."""
    latest_cutoff_ts = latest_ts - pd.Timedelta(minutes=5)
    query = """
        WITH latest_rows AS (
            SELECT DISTINCT ON (symbol)
                symbol,
                timestamp AS latest_ts,
                close AS latest_close
            FROM candles_1m
            WHERE timestamp >= %s
              AND timestamp <= %s
              AND symbol LIKE '%%USDT'
            ORDER BY symbol, timestamp DESC
        ),
        base_rows AS (
            SELECT DISTINCT ON (symbol)
                symbol,
                close AS base_close
            FROM candles_1m
            WHERE timestamp <= %s
              AND symbol LIKE '%%USDT'
            ORDER BY symbol, timestamp DESC
        )
        SELECT latest_rows.symbol
        FROM latest_rows
        JOIN base_rows ON latest_rows.symbol = base_rows.symbol
        WHERE latest_rows.symbol <> 'BTCUSDT'
          AND base_rows.base_close > 0
        ORDER BY ((latest_rows.latest_close - base_rows.base_close) / base_rows.base_close) DESC
        LIMIT 15
    """
    with db._ensure_conn().cursor() as cur:
        cur.execute(query, [latest_cutoff_ts.to_pydatetime(), latest_ts.to_pydatetime(), context_start_ts.to_pydatetime()])
        return [str(row[0]) for row in cur.fetchall()]


def _fetch_label_source_candles(
    db: DBManager,
    symbols: list[str],
    start_ts: pd.Timestamp,
    label_end_ts: pd.Timestamp,
) -> pd.DataFrame:
    """라벨 계산에 필요한 표시 구간과 horizon 여분 캔들을 조회합니다."""
    if not symbols:
        return pd.DataFrame()
    query = """
        SELECT timestamp, symbol, open, high, low, close, volume
        FROM candles_1m
        WHERE symbol IN %s
          AND timestamp >= %s
          AND timestamp <= %s
        ORDER BY symbol ASC, timestamp ASC
    """
    candles_df = pd.read_sql(
        query,
        db._ensure_conn(),
        params=[tuple(symbols), start_ts.to_pydatetime(), label_end_ts.to_pydatetime()],
    )
    if candles_df.empty:
        return candles_df
    candles_df["timestamp"] = pd.to_datetime(candles_df["timestamp"], utc=True)
    return candles_df


def _label_visible_1m_candles(
    *,
    source_df: pd.DataFrame,
    visible_df: pd.DataFrame,
    target_tp: float,
    stop_loss: float,
    horizon_minutes: int,
) -> list[dict[str, Any]]:
    """표시 대상 1분봉에 horizon 내 TP/SL 선터치 라벨을 붙입니다."""
    labels: list[str] = []
    source_high = source_df["high"].astype(float).reset_index(drop=True)
    source_low = source_df["low"].astype(float).reset_index(drop=True)
    source_close = source_df["close"].astype(float).reset_index(drop=True)
    visible_count = len(visible_df)

    for index in range(visible_count):
        entry_price = float(source_close.iloc[index])
        tp_price = entry_price * (1 + target_tp)
        sl_price = entry_price * (1 - stop_loss)
        label = "NONE"
        for offset in range(1, horizon_minutes + 1):
            future_index = index + offset
            if future_index >= len(source_df):
                break
            tp_hit = float(source_high.iloc[future_index]) >= tp_price
            sl_hit = float(source_low.iloc[future_index]) <= sl_price
            if sl_hit:
                label = "SL"
                break
            if tp_hit:
                label = "TP"
                break
        labels.append(label)

    candles: list[dict[str, Any]] = []
    visible_reset = visible_df.reset_index(drop=True)
    for index, row in visible_reset.iterrows():
        candles.append(
            {
                "timestamp": pd.Timestamp(row["timestamp"]).tz_convert("Asia/Seoul").isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"] or 0),
                "label": labels[index],
            }
        )
    return candles


def parse_report_summary(path: Path) -> dict[str, Any]:
    """Markdown 리포트의 OFF/ON 요약 테이블을 간단한 dict로 파싱합니다."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return {}

    summary: dict[str, Any] = {}
    for line in content.splitlines():
        if not line.startswith("| OFF |") and not line.startswith("| ON |"):
            continue

        columns = [item.strip() for item in line.strip("|").split("|")]
        if len(columns) < 9:
            continue

        mode = columns[0]
        summary[mode] = {
            "entries": _to_int(columns[1]),
            "tp": _to_int(columns[2]),
            "sl": _to_int(columns[3]),
            "timeout": _to_int(columns[4]),
            "tp_rate": _to_float(columns[5].replace("%", "")),
            "final_balance": _to_float(columns[6]),
            "return_pct": _to_float(columns[7].replace("%", "")),
            "mdd": _to_float(columns[8].replace("%", "")),
        }
    return summary


def _normalize_trade_key(value: str) -> str:
    """Markdown 거래 테이블 헤더를 API용 snake_case 키로 바꿉니다."""
    return value.strip().lower().replace(" ", "_")


def _normalize_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    """Markdown 문자열 거래 row를 UI에서 쓰기 쉬운 타입으로 변환합니다."""
    normalized = dict(row)
    for key in [
        "entry_price", "exit_price", "pnl_roe", "pred_proba", "p_tp", "p_sl", "p_timeout",
        "tp_lift", "sl_lift", "timeout_lift",
        "entry_score", "entry_rank_score", "balance", "drawdown",
    ]:
        if key in normalized:
            normalized[key] = _to_float(str(normalized[key]))
    for key in ["rank_at_entry", "trade_index"]:
        if key in normalized:
            normalized[key] = _to_int(str(normalized[key]))
    for key in ["entry_time", "exit_time"]:
        kst_key = f"{key}_kst"
        if key in normalized and kst_key not in normalized:
            normalized[kst_key] = _to_kst_iso(normalized[key])
        elif kst_key in normalized:
            normalized[kst_key] = _to_kst_iso(normalized[kst_key])
    if "entry_features" in normalized:
        normalized["entry_features"] = _normalize_entry_features(normalized["entry_features"])
    return normalized


def _to_kst_iso(value: Any) -> str:
    """웹 표시용 timestamp를 KST ISO 문자열로 정규화합니다."""
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert("Asia/Seoul").isoformat()
    except Exception:
        return str(value)


def _normalize_entry_features(value: Any) -> dict[str, Any]:
    """문자열로 저장된 구형 entry_features를 웹 표시용 dict로 복구합니다."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}

    text = value.strip()
    try:
        parsed = json.loads(text)
    except Exception:
        try:
            parsed = ast.literal_eval(text)
        except Exception:
            return {"raw": text}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _nearest_close(candles_df: pd.DataFrame, target_time: pd.Timestamp) -> float:
    """목표 시각에 가장 가까운 1분봉 close를 반환합니다."""
    if candles_df.empty:
        return 0.0
    target = pd.Timestamp(target_time).tz_convert("Asia/Seoul")
    diffs = (candles_df["timestamp"] - target).abs()
    nearest_index = diffs.idxmin()
    return float(candles_df.loc[nearest_index, "close"])


def run_scalping_backtest(request: ScalpingBacktestRequest, timeout_seconds: int = 1800) -> dict[str, Any]:
    """
    `trainer.validate_scalping_1m`을 별도 프로세스로 실행하고 생성된 최신 리포트를 반환합니다.

    이 함수는 검증 전용 엔트리만 호출하므로 주문/수집/실거래 로직을 건드리지 않습니다.
    """
    command = [
        sys.executable,
        "-m",
        "trainer.validate_scalping_1m",
        "--model",
        request.model,
        "--threshold",
        str(request.threshold),
        "--candidate-top-n",
        str(request.candidate_top_n),
    ]
    if request.limit is not None:
        command.extend(["--limit", str(request.limit)])

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )

    reports = list_scalping_reports()
    latest_report = reports[0] if reports else None
    return {
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "latest_report": latest_report,
        "command": " ".join(command),
    }


def _to_int(value: str) -> int:
    """문자열 숫자를 int로 변환합니다."""
    try:
        return int(re.sub(r"[^0-9-]", "", value))
    except ValueError:
        return 0


def _to_float(value: str) -> float:
    """문자열 숫자를 float로 변환합니다."""
    try:
        return float(re.sub(r"[^0-9.+-]", "", value))
    except ValueError:
        return 0.0
