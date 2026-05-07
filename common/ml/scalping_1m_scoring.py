"""
scalping_1m_scoring.py
======================
1분봉 단타 백테스트와 실매매가 함께 쓰는 후보 필터 및 점수 계산 규칙입니다.

백테스트와 실거래의 진입 판단식이 갈라지면 결과 비교가 무의미해지므로,
확률 모델 출력 이후의 하드 게이트와 랭킹 점수는 이 파일에서만 관리합니다.
"""

from __future__ import annotations

from typing import cast

import pandas as pd

from common.config.strategy_config import StrategyConfig


def feature_series(df: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    """후보 필터에서 필요한 피처가 없을 때 기본값 Series를 반환합니다."""
    if column in df.columns:
        return cast(pd.Series, df[column]).fillna(default)
    return pd.Series(default, index=df.index, dtype=float)


def build_active_volatility_mask(candidate_pool: pd.DataFrame) -> pd.Series:
    """
    현재 1분봉 진입 후보가 실제 변동성 구간에 있는지 판단합니다.

    24시간 수익률 상위 펌핑 종목이어도 이미 식은 구간은 제외하기 위해,
    최근 변동성, 거래량 증가, 상방 모멘텀을 동시에 요구합니다.
    """
    range_pct_1m = feature_series(candidate_pool, "range_pct_1m")
    volatility_5m = feature_series(candidate_pool, "volatility_5m")
    volatility_15m = feature_series(candidate_pool, "volatility_15m")
    quote_vol_ratio_5m = feature_series(candidate_pool, "quote_vol_ratio_5m")
    quote_value_ratio_20m = feature_series(candidate_pool, "quote_value_ratio_20m")
    ret_5m = feature_series(candidate_pool, "ret_5m")
    rel_btc_ret_5m = feature_series(candidate_pool, "rel_btc_ret_5m")
    high_break_5m = feature_series(candidate_pool, "high_break_5m")
    resistance_breakout_120m = feature_series(candidate_pool, "resistance_breakout_120m")
    upper_wick_ratio = feature_series(candidate_pool, "upper_wick_ratio")
    support_hold_120m = feature_series(candidate_pool, "support_hold_120m")
    near_support_entry_120m = feature_series(candidate_pool, "near_support_entry_120m")
    support_breakdown_120m = feature_series(candidate_pool, "support_breakdown_120m")
    range_pos_120m = feature_series(candidate_pool, "range_pos_120m", default=1.0)
    wedge_support_touch = feature_series(candidate_pool, "wedge_support_touch_120m")
    wedge_support_reclaim = feature_series(candidate_pool, "wedge_support_reclaim_120m")
    wedge_support_break = feature_series(candidate_pool, "wedge_support_break_120m")

    has_current_range = range_pct_1m >= 0.0015
    has_recent_volatility = (volatility_5m >= 0.0010) | (volatility_15m >= 0.0012)
    has_volume_expansion = (quote_vol_ratio_5m >= 1.05) | (quote_value_ratio_20m >= 1.05)
    has_upside_momentum = (
        (ret_5m >= 0.0020)
        | (rel_btc_ret_5m >= 0.0015)
        | (high_break_5m >= -0.0010)
        | (resistance_breakout_120m == 1)
    )
    support_reaction = (
        ((support_hold_120m == 1) | (near_support_entry_120m == 1))
        & (support_breakdown_120m == 0)
        & (range_pos_120m <= 0.60)
        & ((ret_5m >= -0.0040) | (rel_btc_ret_5m >= 0.0) | (quote_vol_ratio_5m >= 1.00))
    )
    wedge_support_reaction = (
        ((wedge_support_touch == 1) | (wedge_support_reclaim == 1))
        & (wedge_support_break == 0)
        & ((ret_5m >= -0.0060) | (rel_btc_ret_5m >= -0.0010) | (quote_vol_ratio_5m >= 1.00))
    )
    no_exhaustion_wick = upper_wick_ratio <= 0.80

    return (
        (has_current_range | has_recent_volatility | support_reaction | wedge_support_reaction)
        & (has_volume_expansion | support_reaction | wedge_support_reaction)
        & (has_upside_momentum | support_reaction | wedge_support_reaction)
        & no_exhaustion_wick
    )


def add_entry_scores(
    df: pd.DataFrame,
    *,
    target_tp: float,
    stop_loss: float,
) -> pd.DataFrame:
    """TP/SL/TIMEOUT 모델 확률에서 실매매와 백테스트 공통 진입 점수를 계산합니다."""
    result = df.copy()
    tp_baseline = float(result["p_tp"].median()) if "p_tp" in result.columns and not result.empty else 0.0
    sl_baseline = float(result["p_sl"].median()) if "p_sl" in result.columns and not result.empty else 0.0
    timeout_baseline = float(result["p_timeout"].median()) if "p_timeout" in result.columns and not result.empty else 0.0

    result["tp_lift"] = result["p_tp"] - tp_baseline
    result["sl_lift"] = result["p_sl"] - sl_baseline
    result["timeout_lift"] = result["p_timeout"] - timeout_baseline
    result["entry_score"] = (
        (result["tp_lift"] * target_tp)
        - (result["sl_lift"].clip(lower=0) * stop_loss * StrategyConfig.SCALPING_1M_SL_RISK_WEIGHT)
        - (result["timeout_lift"].clip(lower=0) * StrategyConfig.SCALPING_1M_TIMEOUT_RISK_WEIGHT)
    )
    result["entry_rank_score"] = (
        (result["tp_lift"] * 3.0)
        + result["p_tp"]
        - (result["sl_lift"].clip(lower=0) * 0.8)
        - (result["timeout_lift"].clip(lower=0) * 0.25)
    )
    result["entry_rank_score"] += build_entry_structure_bonus(result)
    result["pred_proba"] = result["p_tp"]
    return result


def build_entry_structure_bonus(candidate_pool: pd.DataFrame) -> pd.Series:
    """지지 매수와 돌파 매매 구조가 좋은 후보에 랭킹 보너스를 부여합니다."""
    support_hold = feature_series(candidate_pool, "support_hold_120m")
    near_support = feature_series(candidate_pool, "near_support_entry_120m")
    support_breakdown = feature_series(candidate_pool, "support_breakdown_120m")
    range_pos = feature_series(candidate_pool, "range_pos_120m", default=1.0).clip(lower=0, upper=1.5)
    dist_to_resistance = feature_series(candidate_pool, "dist_to_resistance_120m", default=0.0)
    dist_to_wedge_resistance = feature_series(candidate_pool, "dist_to_wedge_resistance_120m", default=0.0)
    resistance_breakout = feature_series(candidate_pool, "resistance_breakout_120m")
    breakout_quality = feature_series(candidate_pool, "breakout_quality_120m")
    breakout_follow = feature_series(candidate_pool, "breakout_quality_follow_5m")
    wick_fail = feature_series(candidate_pool, "breakout_wick_fail_20m")
    failure_pressure = feature_series(candidate_pool, "breakout_failure_pressure_5m")
    upper_wick = feature_series(candidate_pool, "upper_wick_ratio")
    wedge_support_reclaim = feature_series(candidate_pool, "wedge_support_reclaim_120m")
    wedge_support_touch = feature_series(candidate_pool, "wedge_support_touch_120m")
    wedge_support_break = feature_series(candidate_pool, "wedge_support_break_120m")
    wedge_converging = feature_series(candidate_pool, "wedge_converging_120m")
    wedge_falling = feature_series(candidate_pool, "wedge_falling_120m")
    wedge_breakout = feature_series(candidate_pool, "wedge_resistance_breakout_120m")
    wedge_retest_quality = feature_series(candidate_pool, "wedge_support_retest_quality_120m")
    wedge_breakout_quality = feature_series(candidate_pool, "wedge_breakout_quality_120m")

    support_bonus = (
        ((support_hold == 1) | (near_support == 1)).astype(float) * 0.18
        + near_support.astype(float) * 0.12
        + (1 - range_pos).clip(lower=0, upper=1) * 0.12
        + dist_to_resistance.clip(lower=0, upper=0.08) * 1.20
        + wedge_support_reclaim.astype(float) * 0.30
        + wedge_support_touch.astype(float) * 0.16
        + wedge_converging.astype(float) * 0.10
        + wedge_falling.astype(float) * 0.06
        + dist_to_wedge_resistance.clip(lower=0, upper=0.08) * 1.00
        + wedge_retest_quality.clip(lower=0, upper=5) * 0.05
        - support_breakdown.astype(float) * 0.35
        - wedge_support_break.astype(float) * 0.40
    )
    breakout_bonus = (
        resistance_breakout.astype(float) * 0.10
        + wedge_breakout.astype(float) * 0.16
        + breakout_quality.clip(lower=0, upper=3) * 0.04
        + breakout_follow.clip(lower=0, upper=1) * 0.12
        + wedge_breakout_quality.clip(lower=0, upper=5) * 0.04
        - wick_fail.astype(float) * 0.18
        - failure_pressure.clip(lower=0, upper=5) * 0.04
        - upper_wick.clip(lower=0, upper=1) * 0.04
    )
    return support_bonus + breakout_bonus


def build_entry_structure_masks(
    candidate_pool: pd.DataFrame,
    *,
    target_tp: float,
    stop_loss: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """지지 매수와 돌파 매매 구조 조건을 계산합니다."""
    close_to_support = feature_series(candidate_pool, "dist_to_support_120m", default=1.0)
    dist_to_resistance = feature_series(candidate_pool, "dist_to_resistance_120m", default=0.0)
    close_to_wedge_support = feature_series(candidate_pool, "dist_to_wedge_support_120m", default=1.0)
    dist_to_wedge_resistance = feature_series(candidate_pool, "dist_to_wedge_resistance_120m", default=0.0)
    range_pos = feature_series(candidate_pool, "range_pos_120m", default=1.0)
    support_hold = feature_series(candidate_pool, "support_hold_120m")
    near_support = feature_series(candidate_pool, "near_support_entry_120m")
    support_breakdown = feature_series(candidate_pool, "support_breakdown_120m")
    wedge_support_touch = feature_series(candidate_pool, "wedge_support_touch_120m")
    wedge_support_reclaim = feature_series(candidate_pool, "wedge_support_reclaim_120m")
    wedge_support_break = feature_series(candidate_pool, "wedge_support_break_120m")
    wedge_converging = feature_series(candidate_pool, "wedge_converging_120m")
    wedge_falling = feature_series(candidate_pool, "wedge_falling_120m")
    resistance_breakout = feature_series(candidate_pool, "resistance_breakout_120m")
    wedge_resistance_breakout = feature_series(candidate_pool, "wedge_resistance_breakout_120m")
    breakout_close = feature_series(candidate_pool, "breakout_close_20m")
    breakout_wick_fail = feature_series(candidate_pool, "breakout_wick_fail_20m")
    breakout_quality = feature_series(candidate_pool, "breakout_quality_120m")
    wedge_breakout_quality = feature_series(candidate_pool, "wedge_breakout_quality_120m")
    breakout_follow = feature_series(candidate_pool, "breakout_quality_follow_5m")
    upper_wick = feature_series(candidate_pool, "upper_wick_ratio")
    lower_wick = feature_series(candidate_pool, "lower_wick_ratio")
    quote_vol_ratio_5m = feature_series(candidate_pool, "quote_vol_ratio_5m")
    taker_buy_ratio_3m = feature_series(candidate_pool, "taker_buy_ratio_3m", default=0.5)
    ret_1m = feature_series(candidate_pool, "ret_1m")
    ret_3m = feature_series(candidate_pool, "ret_3m")
    mtf15_bear = feature_series(candidate_pool, "mtf_15m_ema_bear_stack")

    horizontal_support_setup = (
        ((support_hold == 1) | (near_support == 1))
        & (support_breakdown == 0)
        & (range_pos <= 0.62)
        & (close_to_support <= max(stop_loss * 1.75, 0.035))
        & (dist_to_resistance >= max(target_tp * 0.60, 0.015))
        & (upper_wick <= 0.75)
        & (mtf15_bear == 0)
    )
    wedge_support_setup = (
        ((wedge_support_touch == 1) | (wedge_support_reclaim == 1))
        & (wedge_support_break == 0)
        & ((wedge_converging == 1) | (wedge_falling == 1))
        & (close_to_wedge_support >= -max(stop_loss * 0.40, 0.008))
        & (close_to_wedge_support <= max(stop_loss * 1.50, 0.030))
        & (dist_to_wedge_resistance >= max(target_tp * 0.70, 0.020))
        & ((wedge_support_reclaim == 1) | (lower_wick >= 0.25) | (ret_1m >= 0.0) | (ret_3m >= -0.002))
        & (upper_wick <= 0.70)
        & (taker_buy_ratio_3m >= 0.42)
        & (mtf15_bear == 0)
    )
    support_setup = horizontal_support_setup | wedge_support_setup
    breakout_setup = (
        ((resistance_breakout == 1) | (wedge_resistance_breakout == 1) | (breakout_close == 1))
        & (support_breakdown == 0)
        & (wedge_support_break == 0)
        & (dist_to_resistance >= -0.010)
        & (dist_to_wedge_resistance >= -0.012)
        & (upper_wick <= 0.65)
        & (breakout_wick_fail == 0)
        & (
            (breakout_quality > 0)
            | (wedge_breakout_quality > 0)
            | (breakout_follow > 0)
            | (quote_vol_ratio_5m >= 1.05)
        )
        & (mtf15_bear == 0)
    )
    return support_setup, breakout_setup, support_setup | breakout_setup


def apply_entry_gate(candidate_pool: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """1분봉 진입 하드 게이트와 랭킹 임계값을 적용합니다."""
    return apply_entry_gate_with_risk(
        candidate_pool,
        threshold,
        target_tp=StrategyConfig.SCALPING_1M_TARGET_TP,
        stop_loss=StrategyConfig.SCALPING_1M_STOP_LOSS,
    )


def apply_entry_gate_with_risk(
    candidate_pool: pd.DataFrame,
    threshold: float,
    *,
    target_tp: float,
    stop_loss: float,
) -> pd.DataFrame:
    """TP/SL 가격폭을 반영한 1분봉 진입 하드 게이트와 랭킹 임계값을 적용합니다."""
    if candidate_pool.empty:
        return candidate_pool

    expected_price_edge = (candidate_pool["p_tp"] * target_tp) - (candidate_pool["p_sl"] * stop_loss)
    support_setup, breakout_setup, structure_gate = build_entry_structure_masks(
        candidate_pool,
        target_tp=target_tp,
        stop_loss=stop_loss,
    )

    probability_gate = (
        (candidate_pool["tp_lift"] >= StrategyConfig.SCALPING_1M_MIN_TP_LIFT)
        & (candidate_pool["sl_lift"] <= StrategyConfig.SCALPING_1M_MAX_SL_LIFT)
        & (candidate_pool["timeout_lift"] <= StrategyConfig.SCALPING_1M_MAX_TIMEOUT_LIFT)
        & ((candidate_pool["tp_lift"] - candidate_pool["sl_lift"]) >= StrategyConfig.SCALPING_1M_MIN_TP_SL_LIFT_EDGE)
        & (expected_price_edge >= StrategyConfig.SCALPING_1M_MIN_EXPECTED_PRICE_EDGE)
        & (candidate_pool["p_timeout"] <= StrategyConfig.SCALPING_1M_MAX_TIMEOUT_PROB)
        & structure_gate
    )
    if StrategyConfig.SCALPING_1M_REQUIRE_PUMP_BASE_CONDITION:
        probability_gate &= feature_series(candidate_pool, "pump_base_condition", default=0.0) == 1
    if StrategyConfig.SCALPING_1M_REQUIRE_TP_ABOVE_SL:
        probability_gate &= candidate_pool["p_tp"] > candidate_pool["p_sl"]
    if StrategyConfig.SCALPING_1M_REQUIRE_MTF15_EMA_BULL:
        probability_gate &= feature_series(candidate_pool, "mtf_15m_ema_bull_stack", default=0.0) == 1
    if StrategyConfig.SCALPING_1M_BLOCK_MTF5_TREND_TURN_UP:
        probability_gate &= feature_series(candidate_pool, "mtf_5m_trend_turn_up", default=0.0) == 0
    if StrategyConfig.SCALPING_1M_BLOCK_MTF5_TREND_CONTINUATION:
        probability_gate &= feature_series(candidate_pool, "mtf_5m_trend_continuation_long", default=0.0) == 0

    support_not_broken = feature_series(candidate_pool, "support_breakdown_120m", default=0.0) == 0
    wedge_support_not_broken = feature_series(candidate_pool, "wedge_support_break_120m", default=0.0) == 0

    result = cast(
        pd.DataFrame,
        candidate_pool[
            probability_gate
            & (candidate_pool["entry_rank_score"] >= threshold)
            & support_not_broken
            & wedge_support_not_broken
            & (candidate_pool["symbol"] != "BTCUSDT")
        ].copy(),
    )
    if not result.empty:
        result["entry_setup"] = "unknown"
        result.loc[support_setup.loc[result.index], "entry_setup"] = "support"
        result.loc[breakout_setup.loc[result.index], "entry_setup"] = "breakout"
    return result
