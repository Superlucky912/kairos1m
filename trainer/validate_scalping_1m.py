"""
validate_scalping_1m.py
=======================
이 파일은 1분봉 단타 LightGBM 모델을 최근 Blind 구간에서 검증합니다.

실매매 엔진에는 연결하지 않고, `candles_1m` 데이터만 사용해
StrategyConfig의 1분봉 TP/SL/Horizon 설정 기준으로 독립 백테스트를 수행합니다.
TP/SL/TIMEOUT 모델을 분리해 제한시간 미도달 위험도 점수에 반영합니다.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from typing import Any, cast

import numpy as np
import pandas as pd

from common.config.base_config import Config
from common.config.strategy_config import StrategyConfig
from common.ml.lgbm_model import LGBMModel
from common.ml.scalping_1m_dataset import (
    Scalping1MConfig,
    Scalping1MDatasetBuilder,
    build_scalping_1m_config,
)
from common.ml.scalping_1m_scoring import (
    add_entry_scores,
    apply_entry_gate_with_risk,
    build_active_volatility_mask,
    feature_series,
)
from common.utils.logger import setup_logger


logger = setup_logger("Kairos-Scalping1M-Backtest", Config.BACKTEST_LOG_DIR / "scalping_1m_validation.log")


def _json_safe_value(value: Any) -> Any:
    """백테스트 거래/피처 값을 JSON으로 저장 가능한 값으로 변환합니다."""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, float):
        if np.isnan(value) or np.isinf(value):
            return 0.0
        return value
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    return str(value)


def _kst_iso(value: Any) -> str:
    """표시/리포트용 timestamp를 KST ISO 문자열로 변환합니다."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("Asia/Seoul").isoformat()


def _extract_entry_features(row: pd.Series) -> dict[str, Any]:
    """
    진입 시점의 판단 근거 피처를 저장합니다.

    웹 복기에서 "왜 샀는가"를 확인하기 위한 스냅샷이므로,
    원시 OHLCV와 모델 확률/점수/지지저항/수익률 피처를 모두 포함합니다.
    """
    return {str(key): _json_safe_value(value) for key, value in row.items()}


def _target_reason_text(row: pd.Series) -> str:
    """라벨 target_reason 값을 사람이 읽는 거래 결과 문자열로 변환합니다."""
    target_reason = int(row.get("target_reason", 0))
    if target_reason == 1:
        return "TP"
    if target_reason == -1:
        return "SL"
    return "TIMEOUT"


def _active_position_symbols(active_positions: list[dict[str, Any]]) -> list[str]:
    """현재 열려 있는 포지션 심볼 목록을 반환합니다."""
    return [str(position["symbol"]) for position in active_positions]


def _build_diagnostic_entry(
    row: pd.Series,
    *,
    entry_time: pd.Timestamp,
    entry_decision: str,
    miss_reason: str,
    blocked_by: str,
    candidate_stage: str,
    active_positions: list[dict[str, Any]],
    candidate_rank: int = 1,
    would_pass_active_volatility: bool | None = None,
    would_pass_entry_gate: bool | None = None,
    would_pass_cooldown: bool | None = None,
) -> dict[str, Any]:
    """차트 복기용 후보 row에 실제 미진입 사유를 붙여 저장합니다."""
    return {
        "symbol": str(row["symbol"]),
        "entry_time": entry_time,
        "exit_time": entry_time,
        "entry_price": float(row["close"]),
        "exit_price": float(row["close"]),
        "reason": _target_reason_text(row),
        "entry_type": "DIAGNOSTIC",
        "entry_decision": entry_decision,
        "miss_reason": miss_reason,
        "blocked_by": blocked_by,
        "candidate_stage": candidate_stage,
        "candidate_rank": candidate_rank,
        "active_slots": len(active_positions),
        "max_slots": StrategyConfig.MAX_SLOTS,
        "active_positions": _active_position_symbols(active_positions),
        "would_pass_active_volatility": would_pass_active_volatility,
        "would_pass_entry_gate": would_pass_entry_gate,
        "would_pass_cooldown": would_pass_cooldown,
        "pnl_roe": 0.0,
        "pred_proba": float(row["p_tp"]),
        "p_tp": float(row["p_tp"]),
        "p_sl": float(row["p_sl"]),
        "p_timeout": float(row["p_timeout"]),
        "tp_lift": float(row.get("tp_lift", 0.0)),
        "sl_lift": float(row.get("sl_lift", 0.0)),
        "timeout_lift": float(row.get("timeout_lift", 0.0)),
        "entry_score": float(row["entry_score"]),
        "entry_rank_score": float(row["entry_rank_score"]),
        "rank_at_entry": candidate_rank,
        "balance": 0.0,
        "drawdown": 0.0,
        "entry_features": _extract_entry_features(row),
    }


def _serialize_trades(trades_df: pd.DataFrame) -> list[dict[str, Any]]:
    """거래 DataFrame 전체를 JSON 저장용 record 목록으로 변환합니다."""
    records: list[dict[str, Any]] = []
    if trades_df.empty:
        return records
    for trade_index, (_, row) in enumerate(trades_df.iterrows()):
        record = {str(key): _json_safe_value(value) for key, value in row.items()}
        if "entry_time" in row:
            record["entry_time_kst"] = _kst_iso(row["entry_time"])
        if "exit_time" in row:
            record["exit_time_kst"] = _kst_iso(row["exit_time"])
        record["trade_index"] = trade_index
        records.append(record)
    return records


def _serialize_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """일반 DataFrame을 JSON 저장용 record 목록으로 변환합니다."""
    records: list[dict[str, Any]] = []
    if df.empty:
        return records
    for _, row in df.iterrows():
        records.append({str(key): _json_safe_value(value) for key, value in row.items()})
    return records


def _utc_day_key(timestamp: pd.Timestamp) -> str:
    """실거래 엔진의 일일 리스크 리셋 기준과 맞춘 UTC 날짜 키를 반환합니다."""
    ts = pd.Timestamp(timestamp)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%d")


def _summarize_trades(trades_df: pd.DataFrame) -> dict[str, float]:
    """거래 결과를 핵심 수치로 요약합니다."""
    if trades_df.empty:
        return {
            "entries": 0.0,
            "tp_count": 0.0,
            "sl_count": 0.0,
            "timeout_count": 0.0,
            "tp_rate": 0.0,
            "final_balance": StrategyConfig.INITIAL_BALANCE,
            "max_drawdown": 0.0,
        }

    entries = len(trades_df)
    tp_count = int((trades_df["reason"] == "TP").sum())
    sl_count = int((trades_df["reason"] == "SL").sum())
    timeout_count = int((trades_df["reason"] == "TIMEOUT").sum())
    tp_rate = tp_count / entries * 100 if entries else 0.0
    final_balance = float(trades_df["balance"].iloc[-1])
    max_drawdown = float(trades_df["drawdown"].max()) if "drawdown" in trades_df.columns else 0.0
    return {
        "entries": float(entries),
        "tp_count": float(tp_count),
        "sl_count": float(sl_count),
        "timeout_count": float(timeout_count),
        "tp_rate": tp_rate,
        "final_balance": final_balance,
        "max_drawdown": max_drawdown,
    }


def _build_daily_trade_summary(trades_df: pd.DataFrame, blind_df: pd.DataFrame) -> pd.DataFrame:
    """Blind 구간의 KST 일자별 진입 횟수와 실현 손익을 요약합니다."""
    blind_dates: list[Any] = []
    if not blind_df.empty and "timestamp" in blind_df.columns:
        blind_dates = sorted(
            pd.to_datetime(blind_df["timestamp"], utc=True)
            .dt.tz_convert("Asia/Seoul")
            .dt.date
            .dropna()
            .unique()
            .tolist()
        )

    if trades_df.empty:
        return pd.DataFrame(
            [
                {
                    "day": index + 1,
                    "date": date.isoformat(),
                    "entries": 0,
                    "tp_count": 0,
                    "sl_count": 0,
                    "timeout_count": 0,
                    "pnl_amount": 0.0,
                    "return_pct": 0.0,
                    "avg_pnl_roe": 0.0,
                }
                for index, date in enumerate(blind_dates)
            ]
        )

    trades = trades_df.copy()
    trades["entry_date_kst"] = pd.to_datetime(trades["entry_time"], utc=True).dt.tz_convert("Asia/Seoul").dt.date
    if "pnl_amount" not in trades.columns:
        ordered = trades.sort_values("exit_time").copy()
        ordered["pnl_amount"] = ordered["balance"].diff()
        if not ordered.empty:
            ordered.iloc[0, ordered.columns.get_loc("pnl_amount")] = (
                float(ordered.iloc[0]["balance"]) - StrategyConfig.INITIAL_BALANCE
            )
        trades["pnl_amount"] = ordered["pnl_amount"].reindex(trades.index).fillna(0.0)

    summary_dates = blind_dates or sorted(trades["entry_date_kst"].dropna().unique().tolist())
    rows: list[dict[str, Any]] = []
    for index, date in enumerate(summary_dates, start=1):
        day_trades = trades[trades["entry_date_kst"] == date]
        entries = len(day_trades)
        pnl_amount = float(day_trades["pnl_amount"].sum()) if entries else 0.0
        rows.append(
            {
                "day": index,
                "date": date.isoformat(),
                "entries": entries,
                "tp_count": int((day_trades["reason"] == "TP").sum()) if entries else 0,
                "sl_count": int((day_trades["reason"] == "SL").sum()) if entries else 0,
                "timeout_count": int((day_trades["reason"] == "TIMEOUT").sum()) if entries else 0,
                "pnl_amount": pnl_amount,
                "return_pct": pnl_amount / StrategyConfig.INITIAL_BALANCE * 100,
                "avg_pnl_roe": float(day_trades["pnl_roe"].mean() * 100) if entries else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _align_model_features(model: LGBMModel, X: pd.DataFrame) -> pd.DataFrame | None:
    """
    저장된 LightGBM 모델의 학습 피처 순서에 맞춰 검증 입력을 정렬합니다.

    누락 피처가 있으면 현재 데이터셋과 저장 모델의 스키마가 다른 상태이므로,
    잘못된 백테스트 결과를 만들지 않도록 검증을 중단합니다.
    """
    if model.model is None:
        return None

    feature_names = list(model.model.feature_name())
    if not feature_names:
        return X

    missing_features = [feature for feature in feature_names if feature not in X.columns]
    extra_features = [feature for feature in X.columns if feature not in feature_names]
    if missing_features:
        logger.error(
            "1분봉 모델/피처 스키마 불일치로 검증을 중단합니다. "
            "현재 저장 모델은 이전 피처셋으로 학습되었습니다. 누락 피처=%s, 추가 피처=%s. "
            "trainer.train_scalping_1m_model로 모델을 다시 학습한 뒤 검증하세요.",
            missing_features[:20],
            extra_features[:20],
        )
        return None

    if extra_features:
        logger.info("검증 입력의 추가 피처 %s개는 저장 모델에 맞춰 제외합니다.", len(extra_features))
    return X.reindex(columns=feature_names).replace([np.inf, -np.inf], 0).fillna(0)


def _resolve_1m_exit(
    position: dict[str, Any],
    candle: pd.Series,
    current_ts: pd.Timestamp,
) -> tuple[str | None, float | None]:
    """
    현재 1분봉에서 포지션 청산 여부와 청산가를 반환합니다.

    같은 1분봉 안에서 TP/SL이 모두 닿으면 보수적으로 SL로 처리합니다.
    """
    tp_price = float(position["tp_price"])
    sl_price = float(position["sl_price"])
    high = float(candle["high"])
    low = float(candle["low"])

    tp_hit = high >= tp_price
    sl_hit = low <= sl_price
    if tp_hit and sl_hit:
        return "SL", sl_price
    if tp_hit:
        return "TP", tp_price
    if sl_hit:
        return "SL", sl_price
    elapsed_minutes = (current_ts - pd.Timestamp(position["entry_time"])).total_seconds() / 60
    if elapsed_minutes >= int(position["horizon"]):
        return "TIMEOUT", float(candle["close"])
    return None, None


def _load_exit_price_frame(
    builder: Scalping1MDatasetBuilder,
    eval_df: pd.DataFrame,
    *,
    horizon_minutes: int,
) -> pd.DataFrame:
    """백테스트 포지션 청산용 원본 1분봉 가격 흐름을 DB에서 조회합니다."""
    if eval_df.empty:
        return pd.DataFrame()

    symbols = sorted({str(symbol) for symbol in eval_df["symbol"].dropna().unique() if str(symbol) != "BTCUSDT"})
    if not symbols:
        return pd.DataFrame()

    start_ts = pd.Timestamp(eval_df["timestamp"].min()).tz_convert("UTC")
    end_ts = pd.Timestamp(eval_df["timestamp"].max()).tz_convert("UTC") + pd.Timedelta(minutes=horizon_minutes)
    query = """
        SELECT timestamp, symbol, open, high, low, close
        FROM candles_1m
        WHERE symbol = ANY(%s)
          AND timestamp >= %s
          AND timestamp <= %s
        ORDER BY timestamp ASC, symbol ASC
    """
    try:
        price_df = pd.read_sql(
            query,
            builder.db._ensure_conn(),
            params=[symbols, start_ts.to_pydatetime(), end_ts.to_pydatetime()],
        )
    except Exception as exc:
        logger.error("1분봉 청산 가격 조회 실패: %s", exc)
        return pd.DataFrame()

    if price_df.empty:
        return price_df
    price_df["timestamp"] = pd.to_datetime(price_df["timestamp"], utc=True)
    return cast(
        pd.DataFrame,
        price_df.drop_duplicates(["timestamp", "symbol"], keep="last").sort_values(["timestamp", "symbol"]),
    )


def simulate_scalping_trades(
    eval_df: pd.DataFrame,
    threshold: float,
    *,
    use_cooldown: bool,
    config: Scalping1MConfig,
    candidate_top_n: int,
    price_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    1분봉 예측 결과를 시간순으로 순회하며 복리 기반 단타 백테스트를 수행합니다.

    후보는 각 timestamp에서 변동/거래량 기반 `scalping_rank_score` 상위 N개로 먼저 압축한 뒤,
    TP lift를 강하게 반영한 `entry_rank_score` 기준으로 최종 랭킹을 매깁니다.
    """
    if eval_df.empty:
        return {
            "trades": pd.DataFrame(),
            "summary": _summarize_trades(pd.DataFrame()),
            "filter_stats": {},
            "diagnostic_entries": pd.DataFrame(),
        }

    sorted_df = eval_df.sort_values(["timestamp", "symbol"]).reset_index(drop=True)
    time_groups = sorted_df.groupby("timestamp", sort=True)
    entry_timestamps = list(time_groups.groups.keys())

    price_source = price_df if price_df is not None and not price_df.empty else sorted_df
    price_source = price_source.sort_values(["timestamp", "symbol"]).reset_index(drop=True)
    price_groups = price_source.groupby("timestamp", sort=True)
    timestamps = sorted(set(entry_timestamps) | set(price_groups.groups.keys()))

    balance = float(StrategyConfig.INITIAL_BALANCE)
    peak_balance = balance
    daily_peak_balance = balance
    current_utc_day: str | None = None
    active_positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    symbol_consecutive_sl: dict[str, int] = {}
    symbol_ban_until: dict[str, pd.Timestamp] = {}
    filter_stats = {
        "timestamps": 0,
        "rows_total": 0,
        "rows_after_candidate_rank": 0,
        "rows_after_active_volatility": 0,
        "rows_after_probability_gate": 0,
        "rows_after_threshold": 0,
        "entries_opened": 0,
        "blocked_full_slots": 0,
        "open_positions_remaining": 0,
        "exit_price_rows": len(price_source),
        "cooldown_blocked": 0,
        "daily_mdd_limit": StrategyConfig.DAILY_MDD_LIMIT,
        "max_daily_mdd": 0.0,
        "daily_mdd_blocked": 0,
        "daily_mdd_block_events": 0,
    }

    def build_watch_pool(group_df: pd.DataFrame) -> pd.DataFrame:
        """CANDIDATE 표시용 관심 후보 풀을 실제 후보 압축 순서와 맞춰 만듭니다."""
        pool = cast(
            pd.DataFrame,
            group_df.sort_values("scalping_rank_score", ascending=False).head(candidate_top_n).copy(),
        )
        if pool.empty:
            return pool
        active_mask = build_active_volatility_mask(pool)
        pool = cast(pd.DataFrame, pool[active_mask].copy())
        if pool.empty:
            return pool
        if "support_breakdown_120m" in pool.columns:
            pool = cast(pd.DataFrame, pool[pool["support_breakdown_120m"] == 0].copy())
        pool = cast(pd.DataFrame, pool[pool["symbol"] != "BTCUSDT"].copy())
        if pool.empty:
            return pool
        return cast(
            pd.DataFrame,
            pool.sort_values(
                ["entry_rank_score", "tp_lift", "p_tp", "scalping_rank_score"],
                ascending=False,
            ).copy(),
        )

    def append_diagnostic(
        row: pd.Series,
        *,
        entry_decision: str,
        miss_reason: str,
        blocked_by: str,
        candidate_stage: str,
        candidate_rank: int = 1,
        would_pass_active_volatility: bool | None = True,
        would_pass_entry_gate: bool | None = None,
        would_pass_cooldown: bool | None = None,
    ) -> None:
        diagnostic_rows.append(
            _build_diagnostic_entry(
                row,
                entry_time=ts_pd,
                entry_decision=entry_decision,
                miss_reason=miss_reason,
                blocked_by=blocked_by,
                candidate_stage=candidate_stage,
                active_positions=active_positions,
                candidate_rank=candidate_rank,
                would_pass_active_volatility=would_pass_active_volatility,
                would_pass_entry_gate=would_pass_entry_gate,
                would_pass_cooldown=would_pass_cooldown,
            )
        )

    for _, ts in enumerate(timestamps):
        ts_pd = pd.Timestamp(ts)
        utc_day = _utc_day_key(ts_pd)
        if utc_day != current_utc_day:
            current_utc_day = utc_day
            daily_peak_balance = balance

        group = cast(pd.DataFrame, time_groups.get_group(ts)) if ts in time_groups.groups else pd.DataFrame()
        price_group = cast(pd.DataFrame, price_groups.get_group(ts)) if ts in price_groups.groups else pd.DataFrame()
        if not group.empty:
            filter_stats["timestamps"] += 1
            filter_stats["rows_total"] += len(group)

        # 1. 기존 포지션 청산을 먼저 처리합니다.
        still_open: list[dict[str, Any]] = []
        for position in active_positions:
            symbol_rows = price_group[price_group["symbol"] == position["symbol"]]
            if symbol_rows.empty:
                still_open.append(position)
                continue

            candle = symbol_rows.iloc[0]
            position["max_high"] = max(float(position["max_high"]), float(candle["high"]))
            position["min_low"] = min(float(position["min_low"]), float(candle["low"]))
            reason, exit_price = _resolve_1m_exit(position, candle, ts_pd)
            if reason is None or exit_price is None:
                still_open.append(position)
                continue

            price_change = (exit_price - float(position["entry_price"])) / float(position["entry_price"])
            fee = StrategyConfig.FEE + StrategyConfig.SLIPPAGE
            pnl_roe = (price_change * StrategyConfig.LEVERAGE) - (fee * StrategyConfig.LEVERAGE)
            pnl_amount = float(position["margin"]) * pnl_roe
            balance += pnl_amount
            peak_balance = max(peak_balance, balance)
            daily_peak_balance = max(daily_peak_balance, balance)
            drawdown = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0.0

            if use_cooldown:
                if reason == "SL":
                    symbol = str(position["symbol"])
                    symbol_consecutive_sl[symbol] = symbol_consecutive_sl.get(symbol, 0) + 1
                    if symbol_consecutive_sl[symbol] >= StrategyConfig.MAX_SL_COUNT_FOR_BLACKLIST:
                        symbol_ban_until[symbol] = ts_pd + timedelta(hours=StrategyConfig.BLACKLIST_DURATION_HOURS)
                elif reason == "TP":
                    symbol_consecutive_sl[str(position["symbol"])] = 0

            trades.append(
                {
                    "symbol": position["symbol"],
                    "entry_time": position["entry_time"],
                    "exit_time": ts_pd,
                    "entry_price": position["entry_price"],
                    "exit_price": exit_price,
                    "reason": reason,
                    "pnl_roe": pnl_roe,
                    "pnl_amount": pnl_amount,
                    "pred_proba": position["pred_proba"],
                    "p_tp": position["p_tp"],
                    "p_sl": position["p_sl"],
                    "p_timeout": position["p_timeout"],
                    "tp_lift": position["tp_lift"],
                    "sl_lift": position["sl_lift"],
                    "timeout_lift": position["timeout_lift"],
                    "entry_score": position["entry_score"],
                    "entry_rank_score": position["entry_rank_score"],
                    "entry_features": position["entry_features"],
                    "rank_at_entry": position["rank_at_entry"],
                    "balance": balance,
                    "drawdown": drawdown,
                    "max_high": position["max_high"],
                    "min_low": position["min_low"],
                }
            )

        active_positions = still_open

        if group.empty:
            continue

        watch_pool = build_watch_pool(group)
        watch_row = watch_pool.iloc[0] if not watch_pool.empty else None

        daily_mdd = (
            (daily_peak_balance - balance) / daily_peak_balance
            if daily_peak_balance > 0
            else 0.0
        )
        filter_stats["max_daily_mdd"] = max(filter_stats["max_daily_mdd"], daily_mdd)
        if daily_mdd >= StrategyConfig.DAILY_MDD_LIMIT:
            filter_stats["daily_mdd_blocked"] += len(group)
            filter_stats["daily_mdd_block_events"] += 1
            if watch_row is not None:
                append_diagnostic(
                    watch_row,
                    entry_decision="NOT_ENTERED",
                    miss_reason="DAILY_MDD_BLOCK",
                    blocked_by=f"UTC 일일 MDD {daily_mdd:.2%} >= {StrategyConfig.DAILY_MDD_LIMIT:.2%}",
                    candidate_stage="risk_gate",
                    would_pass_entry_gate=None,
                    would_pass_cooldown=None,
                )
            continue

        if len(active_positions) >= StrategyConfig.MAX_SLOTS:
            filter_stats["blocked_full_slots"] += len(group)
            if watch_row is not None:
                append_diagnostic(
                    watch_row,
                    entry_decision="NOT_ENTERED",
                    miss_reason="SLOT_FULL",
                    blocked_by=f"active_slots={len(active_positions)} >= max_slots={StrategyConfig.MAX_SLOTS}",
                    candidate_stage="slot_gate",
                    would_pass_entry_gate=None,
                    would_pass_cooldown=None,
                )
            continue

        # 2. 신규 진입 후보를 변동/거래량 랭킹으로 압축한 뒤 확률로 정렬합니다.
        held_symbols = {str(position["symbol"]) for position in active_positions}
        candidate_pool = cast(
            pd.DataFrame,
            group.sort_values("scalping_rank_score", ascending=False).head(candidate_top_n).copy(),
        )
        filter_stats["rows_after_candidate_rank"] += len(candidate_pool)
        if candidate_pool.empty:
            continue

        active_volatility_mask = build_active_volatility_mask(candidate_pool)
        candidate_pool = cast(pd.DataFrame, candidate_pool[active_volatility_mask].copy())
        filter_stats["rows_after_active_volatility"] += len(candidate_pool)
        if candidate_pool.empty:
            continue

        before_probability_gate = len(candidate_pool)
        pre_gate_pool = candidate_pool.copy()
        candidate_pool = apply_entry_gate_with_risk(
            candidate_pool,
            threshold,
            target_tp=config.target_tp,
            stop_loss=config.stop_loss,
        )
        filter_stats["rows_after_probability_gate"] += len(candidate_pool)
        if candidate_pool.empty:
            if not pre_gate_pool.empty:
                row = pre_gate_pool.sort_values(
                    ["entry_rank_score", "tp_lift", "p_tp", "scalping_rank_score"],
                    ascending=False,
                ).iloc[0]
                append_diagnostic(
                    row,
                    entry_decision="NOT_ENTERED",
                    miss_reason="ENTRY_GATE_FAIL",
                    blocked_by="hard gate 또는 entry_rank_score threshold 미통과",
                    candidate_stage="entry_gate",
                    would_pass_entry_gate=False,
                    would_pass_cooldown=None,
                )
            continue

        pre_held_pool = candidate_pool.copy()
        candidate_pool = cast(
            pd.DataFrame,
            candidate_pool[
                ~candidate_pool["symbol"].isin(held_symbols)
            ].copy(),
        )
        filter_stats["rows_after_threshold"] += min(before_probability_gate, len(candidate_pool))
        if candidate_pool.empty:
            held_blocked = pre_held_pool[pre_held_pool["symbol"].isin(held_symbols)]
            if not held_blocked.empty:
                row = held_blocked.sort_values(
                    ["entry_rank_score", "tp_lift", "p_tp", "scalping_rank_score"],
                    ascending=False,
                ).iloc[0]
                append_diagnostic(
                    row,
                    entry_decision="NOT_ENTERED",
                    miss_reason="HELD_SYMBOL",
                    blocked_by=f"{row['symbol']} 포지션 이미 보유 중",
                    candidate_stage="held_symbol_gate",
                    would_pass_entry_gate=True,
                    would_pass_cooldown=None,
                )
            continue

        if use_cooldown:
            pre_cooldown_pool = candidate_pool.copy()
            allowed_mask = []
            for _, row in candidate_pool.iterrows():
                symbol = str(row["symbol"])
                banned_until = symbol_ban_until.get(symbol)
                is_allowed = banned_until is None or ts_pd >= banned_until
                if not is_allowed:
                    filter_stats["cooldown_blocked"] += 1
                allowed_mask.append(is_allowed)
            candidate_pool = cast(pd.DataFrame, candidate_pool[pd.Series(allowed_mask, index=candidate_pool.index)])

        if candidate_pool.empty:
            if use_cooldown:
                cooldown_blocked = pre_cooldown_pool[
                    ~pre_cooldown_pool.index.isin(candidate_pool.index)
                ]
                if not cooldown_blocked.empty:
                    row = cooldown_blocked.sort_values(
                        ["entry_rank_score", "tp_lift", "p_tp", "scalping_rank_score"],
                        ascending=False,
                    ).iloc[0]
                    append_diagnostic(
                        row,
                        entry_decision="NOT_ENTERED",
                        miss_reason="COOLDOWN_BLOCK",
                        blocked_by=f"{row['symbol']} 손절 쿨다운/블랙리스트",
                        candidate_stage="cooldown_gate",
                        would_pass_entry_gate=True,
                        would_pass_cooldown=False,
                    )
            continue

        available_slots = StrategyConfig.MAX_SLOTS - len(active_positions)
        margin_per_slot = balance / StrategyConfig.MAX_SLOTS
        ranked_candidates = candidate_pool.sort_values(
            ["entry_rank_score", "tp_lift", "p_tp", "scalping_rank_score"],
            ascending=False,
        )
        selected_candidates = ranked_candidates.head(available_slots)
        missed_rank_candidates = ranked_candidates.iloc[available_slots:]
        if not missed_rank_candidates.empty:
            row = missed_rank_candidates.iloc[0]
            append_diagnostic(
                row,
                entry_decision="NOT_ENTERED",
                miss_reason="RANK_NOT_SELECTED",
                blocked_by=f"available_slots={available_slots} 밖의 후보",
                candidate_stage="final_rank",
                candidate_rank=available_slots + 1,
                would_pass_entry_gate=True,
                would_pass_cooldown=True if use_cooldown else None,
            )
        for rank, (_, row) in enumerate(selected_candidates.iterrows(), start=1):
            entry_price = float(row["close"])
            active_positions.append(
                {
                    "symbol": str(row["symbol"]),
                    "entry_time": ts_pd,
                    "entry_price": entry_price,
                    "tp_price": entry_price * (1 + config.target_tp),
                    "sl_price": entry_price * (1 - config.stop_loss),
                    "horizon": config.horizon_minutes,
                    "margin": margin_per_slot,
                    "pred_proba": float(row["p_tp"]),
                    "p_tp": float(row["p_tp"]),
                    "p_sl": float(row["p_sl"]),
                    "p_timeout": float(row["p_timeout"]),
                    "tp_lift": float(row["tp_lift"]),
                    "sl_lift": float(row["sl_lift"]),
                    "timeout_lift": float(row["timeout_lift"]),
                    "entry_score": float(row["entry_score"]),
                    "entry_rank_score": float(row["entry_rank_score"]),
                    "entry_features": _extract_entry_features(row),
                    "rank_at_entry": rank,
                    "max_high": entry_price,
                    "min_low": entry_price,
                }
            )
            filter_stats["entries_opened"] += 1

    filter_stats["open_positions_remaining"] = len(active_positions)
    trades_df = pd.DataFrame(trades)
    diagnostics_df = pd.DataFrame(diagnostic_rows)
    if not diagnostics_df.empty:
        diagnostics_df = cast(
            pd.DataFrame,
            diagnostics_df.sort_values("entry_rank_score", ascending=False)
            .head(StrategyConfig.SCALPING_1M_DIAGNOSTIC_ENTRY_LIMIT)
            .sort_values("entry_time"),
        )
    summary = _summarize_trades(trades_df)
    if trades_df.empty:
        summary["final_balance"] = balance
    summary["return_pct"] = (summary["final_balance"] - StrategyConfig.INITIAL_BALANCE) / StrategyConfig.INITIAL_BALANCE * 100
    return {
        "trades": trades_df,
        "summary": summary,
        "filter_stats": filter_stats,
        "diagnostic_entries": diagnostics_df,
    }


def build_diagnostic_entries(
    eval_df: pd.DataFrame,
    *,
    candidate_top_n: int,
    limit: int = StrategyConfig.SCALPING_1M_DIAGNOSTIC_ENTRY_LIMIT,
) -> pd.DataFrame:
    """
    실제 진입 필터를 통과하지 못해 거래가 0건이어도 모델 후보를 차트에 표시하기 위한 진단 row를 만듭니다.

    각 timestamp에서 변동성 필터와 지지선 붕괴 제외만 적용한 뒤,
    모델 점수 기준 최상위 후보 1개를 뽑고 전체 기간에서 상위 limit개만 저장합니다.
    """
    if eval_df.empty:
        return pd.DataFrame()

    diagnostic_rows: list[dict[str, Any]] = []
    sorted_df = eval_df.sort_values(["timestamp", "symbol"]).reset_index(drop=True)
    for _, group in sorted_df.groupby("timestamp", sort=True):
        candidate_pool = cast(
            pd.DataFrame,
            group.sort_values("scalping_rank_score", ascending=False).head(candidate_top_n).copy(),
        )
        if candidate_pool.empty:
            continue

        active_mask = build_active_volatility_mask(candidate_pool)
        candidate_pool = cast(pd.DataFrame, candidate_pool[active_mask].copy())
        if candidate_pool.empty:
            continue

        if "support_breakdown_120m" in candidate_pool.columns:
            candidate_pool = cast(pd.DataFrame, candidate_pool[candidate_pool["support_breakdown_120m"] == 0].copy())
        candidate_pool = cast(pd.DataFrame, candidate_pool[candidate_pool["symbol"] != "BTCUSDT"].copy())
        if candidate_pool.empty:
            continue

        ranked = candidate_pool.sort_values(
            ["entry_rank_score", "tp_lift", "p_tp", "scalping_rank_score"],
            ascending=False,
        )
        row = ranked.iloc[0]
        target_reason = int(row.get("target_reason", 0))
        if target_reason == 1:
            reason = "TP"
        elif target_reason == -1:
            reason = "SL"
        else:
            reason = "TIMEOUT"

        diagnostic_rows.append(
            {
                "symbol": str(row["symbol"]),
                "entry_time": pd.Timestamp(row["timestamp"]),
                "exit_time": pd.Timestamp(row["timestamp"]),
                "entry_price": float(row["close"]),
                "exit_price": float(row["close"]),
                "reason": reason,
                "entry_type": "DIAGNOSTIC",
                "pnl_roe": 0.0,
                "pred_proba": float(row["p_tp"]),
                "p_tp": float(row["p_tp"]),
                "p_sl": float(row["p_sl"]),
                "p_timeout": float(row["p_timeout"]),
                "tp_lift": float(row.get("tp_lift", 0.0)),
                "sl_lift": float(row.get("sl_lift", 0.0)),
                "timeout_lift": float(row.get("timeout_lift", 0.0)),
                "entry_score": float(row["entry_score"]),
                "entry_rank_score": float(row["entry_rank_score"]),
                "rank_at_entry": 1,
                "balance": 0.0,
                "drawdown": 0.0,
                "entry_features": _extract_entry_features(row),
            }
        )

    if not diagnostic_rows:
        return pd.DataFrame()

    diagnostics_df = pd.DataFrame(diagnostic_rows)
    return cast(
        pd.DataFrame,
        diagnostics_df.sort_values("entry_rank_score", ascending=False).head(limit).sort_values("entry_time"),
    )


def validate_scalping_1m(
    model_name: str = StrategyConfig.SCALPING_1M_MODEL_NAME,
    threshold: float = StrategyConfig.SCALPING_1M_ENTRY_SCORE_THRESHOLD,
    limit_per_symbol: int | None = None,
    candidate_top_n: int = StrategyConfig.SCALPING_1M_CANDIDATE_TOP_N,
) -> None:
    """저장된 1분봉 단타 모델을 Blind 구간에서 검증하고 Markdown 리포트를 저장합니다."""
    config = build_scalping_1m_config(
        model_name=model_name,
        limit_per_symbol=limit_per_symbol or StrategyConfig.LIMIT_PER_SYMBOL,
        eval_threshold=threshold,
    )
    logger.info(f"=== 1분봉 단타 Blind 검증 시작: {model_name} ===")

    tp_model = LGBMModel(model_name=f"{model_name}_tp")
    sl_model = LGBMModel(model_name=f"{model_name}_sl")
    timeout_model = LGBMModel(model_name=f"{model_name}_timeout")
    tp_model.load_model()
    sl_model.load_model()
    timeout_model.load_model()
    if tp_model.model is None or sl_model.model is None or timeout_model.model is None:
        logger.error("1분봉 TP/SL/TIMEOUT 모델 로드 실패. 학습 후 검증하세요. model=%s", model_name)
        return

    builder = Scalping1MDatasetBuilder(config=config)
    dataset = builder.load_dataset(limit_per_symbol=config.limit_per_symbol)
    if dataset.empty:
        logger.error("1분봉 검증 데이터셋이 비어 있습니다.")
        return

    _, _, blind_df = builder.split_data(dataset)
    if blind_df is None or blind_df.empty:
        logger.error("1분봉 Blind 세트가 비어 있습니다.")
        return

    X_blind, _, _ = builder.prepare_features(blind_df, target_mode="tp")
    X_tp_aligned = _align_model_features(tp_model, X_blind)
    X_sl_aligned = _align_model_features(sl_model, X_blind)
    X_timeout_aligned = _align_model_features(timeout_model, X_blind)
    if X_tp_aligned is None or X_sl_aligned is None or X_timeout_aligned is None:
        return

    eval_df = blind_df.loc[X_blind.index].copy()
    eval_df["p_tp"] = np.asarray(tp_model.model.predict(X_tp_aligned), dtype=float)
    eval_df["p_sl"] = np.asarray(sl_model.model.predict(X_sl_aligned), dtype=float)
    eval_df["p_timeout"] = np.asarray(timeout_model.model.predict(X_timeout_aligned), dtype=float)
    tp_baseline = float(eval_df["p_tp"].median())
    sl_baseline = float(eval_df["p_sl"].median())
    timeout_baseline = float(eval_df["p_timeout"].median())
    eval_df = add_entry_scores(
        eval_df,
        target_tp=config.target_tp,
        stop_loss=config.stop_loss,
    )
    exit_price_df = _load_exit_price_frame(
        builder,
        eval_df,
        horizon_minutes=config.horizon_minutes,
    )
    if exit_price_df.empty:
        logger.warning("청산용 원본 1분봉 가격 데이터가 비어 있어 후보 데이터 기준으로 청산을 계산합니다.")
    else:
        logger.info(
            "청산용 원본 1분봉 가격 데이터 로드 완료: rows=%s, symbols=%s, range=%s~%s",
            len(exit_price_df),
            exit_price_df["symbol"].nunique(),
            exit_price_df["timestamp"].min(),
            exit_price_df["timestamp"].max(),
        )

    cooldown_off = simulate_scalping_trades(
        eval_df,
        threshold,
        use_cooldown=False,
        config=config,
        candidate_top_n=candidate_top_n,
        price_df=exit_price_df,
    )
    cooldown_on = simulate_scalping_trades(
        eval_df,
        threshold,
        use_cooldown=True,
        config=config,
        candidate_top_n=candidate_top_n,
        price_df=exit_price_df,
    )
    comparison = {"OFF": cooldown_off, "ON": cooldown_on}
    selected = cooldown_on
    selected_summary = cast(dict[str, float], selected["summary"])
    diagnostic_entries_df = cast(pd.DataFrame, selected.get("diagnostic_entries", pd.DataFrame()))
    daily_summaries = {
        mode_name: _build_daily_trade_summary(cast(pd.DataFrame, result["trades"]), blind_df)
        for mode_name, result in comparison.items()
    }
    selected_daily_summary = daily_summaries["ON"]
    logger.info(
        "1분봉 Blind 결과 | Entries=%s | TP=%s | SL=%s | Timeout=%s | TP Rate=%.2f%% | Final=%.2f | MDD=%.2f%%",
        int(selected_summary["entries"]),
        int(selected_summary["tp_count"]),
        int(selected_summary["sl_count"]),
        int(selected_summary["timeout_count"]),
        selected_summary["tp_rate"],
        selected_summary["final_balance"],
        selected_summary["max_drawdown"] * 100,
    )
    for _, row in selected_daily_summary.iterrows():
        logger.info(
            "1분봉 Blind %d일차(%s) | Entries=%d | TP=%d | SL=%d | Timeout=%d | PnL=%+.4f USDT | Return=%+.2f%%",
            int(row["day"]),
            str(row["date"]),
            int(row["entries"]),
            int(row["tp_count"]),
            int(row["sl_count"]),
            int(row["timeout_count"]),
            float(row["pnl_amount"]),
            float(row["return_pct"]),
        )

    output_path = Config.LOG_DIR / "backtest" / f"{model_name}_dual.md"
    trades_json_path = output_path.with_suffix(".trades.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write(f"# 1분봉 단타 Blind 검증 결과 - {model_name}\n\n")
        file.write(f"- **실행 시각**: {pd.Timestamp.now(tz='Asia/Seoul').strftime('%Y-%m-%d %H:%M:%S')} (KST)\n")
        file.write("- **시간 기준**: DB 저장/모델 계산은 UTC, 리포트와 웹 표시 시간은 KST(Asia/Seoul)\n")
        if not blind_df.empty and "timestamp" in blind_df.columns:
            blind_start_kst = _kst_iso(blind_df["timestamp"].min())
            blind_end_kst = _kst_iso(blind_df["timestamp"].max())
            file.write(f"- **Blind 표시 구간**: {blind_start_kst} ~ {blind_end_kst} (KST)\n")
        file.write(f"- **TP/SL/Horizon**: {config.target_tp:.1%} / {config.stop_loss:.1%} / {config.horizon_minutes}분\n")
        file.write(f"- **Entry Rank Score Threshold**: {threshold:.4f}\n")
        file.write(
            f"- **Entry Score**: `tp_lift * TP - max(sl_lift, 0) * SL * {StrategyConfig.SCALPING_1M_SL_RISK_WEIGHT:.2f} "
            f"- max(timeout_lift, 0) * {StrategyConfig.SCALPING_1M_TIMEOUT_RISK_WEIGHT:.4f}`\n"
        )
        file.write(
            "- **Entry Rank Score**: `tp_lift * 3.0 + p_tp - max(sl_lift, 0) * 0.8 "
            "- max(timeout_lift, 0) * 0.25`\n"
        )
        file.write(
            f"- **Probability Baseline**: `p_tp={tp_baseline:.4f}`, "
            f"`p_sl={sl_baseline:.4f}`, `p_timeout={timeout_baseline:.4f}`\n"
        )
        file.write(
            f"- **Hard Gate**: `tp_lift >= {StrategyConfig.SCALPING_1M_MIN_TP_LIFT:.2f}`, "
            f"`sl_lift <= {StrategyConfig.SCALPING_1M_MAX_SL_LIFT:.2f}`, "
            f"`timeout_lift <= {StrategyConfig.SCALPING_1M_MAX_TIMEOUT_LIFT:.2f}`, "
            f"`tp_lift - sl_lift >= {StrategyConfig.SCALPING_1M_MIN_TP_SL_LIFT_EDGE:.2f}`, "
            f"`p_tp * TP - p_sl * SL >= {StrategyConfig.SCALPING_1M_MIN_EXPECTED_PRICE_EDGE:.4f}`, "
            f"`pump_base_condition = {StrategyConfig.SCALPING_1M_REQUIRE_PUMP_BASE_CONDITION}`, "
            f"`p_timeout <= {StrategyConfig.SCALPING_1M_MAX_TIMEOUT_PROB:.2f}`, "
            f"`mtf15_ema_bull = {StrategyConfig.SCALPING_1M_REQUIRE_MTF15_EMA_BULL}`, "
            f"`block_mtf5_turn_up = {StrategyConfig.SCALPING_1M_BLOCK_MTF5_TREND_TURN_UP}`, "
            f"`block_mtf5_trend_continuation = {StrategyConfig.SCALPING_1M_BLOCK_MTF5_TREND_CONTINUATION}`, "
            f"`p_tp > p_sl = {StrategyConfig.SCALPING_1M_REQUIRE_TP_ABOVE_SL}`\n"
        )
        file.write(
            f"- **Risk Gate**: UTC 일자별 고점 대비 MDD "
            f"`>= {StrategyConfig.DAILY_MDD_LIMIT * 100:.2f}%` 도달 시 신규 진입 차단\n"
        )
        file.write(f"- **후보 압축**: timestamp별 scalping_rank_score 상위 {candidate_top_n}개\n\n")

        file.write("## 손절 제한 OFF/ON 비교\n")
        file.write("| 모드 | 진입 | TP | SL | Timeout | TP율 | 최종 잔고 | 수익률 | MDD |\n")
        file.write("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|\n")
        for mode_name, result in comparison.items():
            summary = cast(dict[str, float], result["summary"])
            file.write(
                f"| {mode_name} | {int(summary['entries'])} | {int(summary['tp_count'])} | "
                f"{int(summary['sl_count'])} | {int(summary['timeout_count'])} | "
                f"{summary['tp_rate']:.2f}% | {summary['final_balance']:.2f} | "
                f"{summary['return_pct']:+.2f}% | {summary['max_drawdown'] * 100:.2f}% |\n"
            )

        file.write("\n## 일자별 진입/손익\n")
        file.write("손익은 각 거래의 진입일(KST) 기준으로 집계합니다.\n")
        for mode_name, daily_summary in daily_summaries.items():
            file.write(f"\n### 손절 제한 {mode_name}\n")
            if daily_summary.empty:
                file.write("- 거래 없음\n")
                continue
            display_daily = daily_summary.rename(
                columns={
                    "day": "일차",
                    "date": "날짜(KST)",
                    "entries": "진입",
                    "tp_count": "TP",
                    "sl_count": "SL",
                    "timeout_count": "Timeout",
                    "pnl_amount": "손익(USDT)",
                    "return_pct": "수익률(초기자금대비)",
                    "avg_pnl_roe": "평균 ROE",
                }
            ).copy()
            for column in ["손익(USDT)", "수익률(초기자금대비)", "평균 ROE"]:
                display_daily[column] = display_daily[column].map(lambda value: f"{float(value):+.2f}%")
            display_daily["손익(USDT)"] = daily_summary["pnl_amount"].map(lambda value: f"{float(value):+.4f}")
            file.write(display_daily.to_markdown(index=False))
            file.write("\n")

        file.write("\n## 필터 통계\n")
        for mode_name, result in comparison.items():
            stats = cast(dict[str, int], result["filter_stats"])
            file.write(f"- **{mode_name}**: {stats}\n")
        file.write(f"- **DIAGNOSTIC 후보 저장**: {len(diagnostic_entries_df)}건\n")
        file.write(
            "- **CANDIDATE 미진입 사유**: `miss_reason`과 `blocked_by`는 차트 hover tooltip에서 확인합니다. "
            "`ENTRY_GATE_FAIL`, `HELD_SYMBOL`, `SLOT_FULL`, `COOLDOWN_BLOCK`, "
            "`DAILY_MDD_BLOCK`, `RANK_NOT_SELECTED` 등이 기록됩니다.\n"
        )

        trades_df = cast(pd.DataFrame, selected["trades"])
        if not trades_df.empty:
            file.write("\n## 최근 거래 50건 (KST)\n")
            display_df = trades_df.tail(50).copy()
            display_df["entry_time_kst"] = pd.to_datetime(display_df["entry_time"], utc=True).dt.tz_convert("Asia/Seoul")
            display_df["exit_time_kst"] = pd.to_datetime(display_df["exit_time"], utc=True).dt.tz_convert("Asia/Seoul")
            columns = [
                "entry_time_kst", "exit_time_kst", "symbol", "reason", "pnl_roe",
                "pnl_amount", "entry_price", "exit_price",
                "p_tp", "p_sl", "p_timeout", "tp_lift", "sl_lift", "timeout_lift",
                "entry_score", "entry_rank_score", "rank_at_entry", "balance", "drawdown",
            ]
            for column in columns:
                if column not in display_df.columns:
                    display_df[column] = 0.0
            file.write(display_df[columns].to_markdown(index=False))

    selected_trades_df = cast(pd.DataFrame, selected["trades"])
    trades_payload = {
        "model": model_name,
        "threshold": threshold,
        "candidate_top_n": candidate_top_n,
        "data_interval": "1m",
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "generated_at_kst": pd.Timestamp.now(tz="Asia/Seoul").isoformat(),
        "display_timezone": "Asia/Seoul",
        "storage_timezone": "UTC",
        "summary": selected_summary,
        "daily_summary": _serialize_records(selected_daily_summary),
        "trades": _serialize_trades(selected_trades_df),
        "diagnostic_entries": _serialize_trades(diagnostic_entries_df),
    }
    with open(trades_json_path, "w", encoding="utf-8") as file:
        json.dump(trades_payload, file, ensure_ascii=False, indent=2)
    logger.info(f"1분봉 전체 거래 JSON 저장 완료: {trades_json_path}")

    print("\n" + "=" * 60)
    print(f"   [1분봉 단타 Blind 검증 완료 - {model_name}]")
    for mode_name, result in comparison.items():
        summary = cast(dict[str, float], result["summary"])
        print(
            f"   - {mode_name}: Entries={int(summary['entries'])}, TP={int(summary['tp_count'])}, "
            f"SL={int(summary['sl_count'])}, Timeout={int(summary['timeout_count'])}, "
            f"TP Rate={summary['tp_rate']:.2f}%, Final={summary['final_balance']:.2f}, "
            f"MDD={summary['max_drawdown'] * 100:.2f}%"
        )
    if not selected_daily_summary.empty:
        print("   - ON 일자별:")
        for _, row in selected_daily_summary.iterrows():
            print(
                f"     {int(row['day'])}일차({row['date']}): Entries={int(row['entries'])}, "
                f"TP={int(row['tp_count'])}, SL={int(row['sl_count'])}, Timeout={int(row['timeout_count'])}, "
                f"PnL={float(row['pnl_amount']):+.4f} USDT, Return={float(row['return_pct']):+.2f}%"
            )
    print(f"   리포트: {output_path}")
    print("=" * 60)
    logger.info(f"1분봉 단타 Blind 검증 리포트 저장 완료: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="1분봉 단타 LightGBM Blind 검증")
    parser.add_argument("--model", type=str, default=StrategyConfig.SCALPING_1M_MODEL_NAME, help="검증할 모델명")
    parser.add_argument("--threshold", type=float, default=StrategyConfig.SCALPING_1M_ENTRY_SCORE_THRESHOLD, help="기대값 진입 점수 임계값")
    parser.add_argument("--limit", type=int, default=StrategyConfig.LIMIT_PER_SYMBOL, help="심볼당 로드할 최근 1분봉 수")
    parser.add_argument("--candidate-top-n", type=int, default=15, help="timestamp별 사전 후보 압축 개수")
    args = parser.parse_args()

    validate_scalping_1m(
        model_name=args.model,
        threshold=args.threshold,
        limit_per_symbol=args.limit,
        candidate_top_n=args.candidate_top_n,
    )
