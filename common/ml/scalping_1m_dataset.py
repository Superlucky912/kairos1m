"""
scalping_1m_dataset.py
======================
이 파일은 1분봉 단타 LightGBM 실험에 필요한 데이터셋을 생성합니다.

`candles_1m` 테이블을 공식 원본으로 읽어 1분봉 전용 라벨과 경량 피처를 만듭니다.
5분봉/15분봉 문맥이 필요할 때는 1분봉에서 합성된 `candles_5m`, `candles_15m`를 사용합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd

from common.config.strategy_config import StrategyConfig
from common.database.manager import DBManager
from common.utils.logger import setup_logger


@dataclass(frozen=True)
class Scalping1MConfig:
    """1분봉 단타 모델의 고정 실험 파라미터입니다."""

    model_name: str = StrategyConfig.SCALPING_1M_MODEL_NAME
    target_tp: float = StrategyConfig.SCALPING_1M_TARGET_TP
    stop_loss: float = StrategyConfig.SCALPING_1M_STOP_LOSS
    horizon_minutes: int = StrategyConfig.SCALPING_1M_HORIZON_MINUTES
    min_data_points: int = StrategyConfig.SCALPING_1M_MIN_DATA_POINTS
    support_resistance_window: int = StrategyConfig.SCALPING_1M_SUPPORT_RESISTANCE_WINDOW
    support_resistance_touch_tolerance: float = StrategyConfig.SCALPING_1M_SUPPORT_RESISTANCE_TOUCH_TOLERANCE
    support_resistance_min_touches: int = StrategyConfig.SCALPING_1M_SUPPORT_RESISTANCE_MIN_TOUCHES
    sl_penalty_weight: float = StrategyConfig.SCALPING_1M_SL_PENALTY_WEIGHT
    timeout_penalty: float = StrategyConfig.SCALPING_1M_TIMEOUT_PENALTY
    pump_min_change_24h: float = StrategyConfig.SCALPING_1M_PUMP_MIN_CHANGE_24H
    pump_strong_change_24h: float = StrategyConfig.SCALPING_1M_PUMP_STRONG_CHANGE_24H
    pump_extreme_change_24h: float = StrategyConfig.SCALPING_1M_PUMP_EXTREME_CHANGE_24H
    pump_max_age_hours: int = StrategyConfig.SCALPING_1M_PUMP_MAX_AGE_HOURS
    use_mtf_features: bool = True
    mtf_lookback_days: int = StrategyConfig.SCALPING_1M_MTF_LOOKBACK_DAYS
    limit_per_symbol: int = StrategyConfig.LIMIT_PER_SYMBOL
    eval_threshold: float = StrategyConfig.EVAL_THRESHOLD


def build_scalping_1m_config(
    *,
    model_name: str = StrategyConfig.SCALPING_1M_MODEL_NAME,
    limit_per_symbol: int | None = None,
    eval_threshold: float | None = None,
) -> Scalping1MConfig:
    """모델명 계열에 맞는 1분봉 실험 설정을 생성합니다."""
    target_tp = StrategyConfig.SCALPING_1M_TARGET_TP
    stop_loss = StrategyConfig.SCALPING_1M_STOP_LOSS
    horizon_minutes = StrategyConfig.SCALPING_1M_HORIZON_MINUTES

    for marker, override in StrategyConfig.SCALPING_1M_MODEL_TARGET_OVERRIDES.items():
        if marker not in model_name:
            continue
        target_tp = float(override["target_tp"])
        stop_loss = float(override["stop_loss"])
        if "horizon_minutes" in override:
            horizon_minutes = int(override["horizon_minutes"])
        elif "h120" in model_name:
            horizon_minutes = int(override["horizon_h120"])
        else:
            horizon_minutes = int(override["horizon_h60"])
        break

    return Scalping1MConfig(
        model_name=model_name,
        target_tp=target_tp,
        stop_loss=stop_loss,
        horizon_minutes=horizon_minutes,
        limit_per_symbol=limit_per_symbol or StrategyConfig.LIMIT_PER_SYMBOL,
        eval_threshold=eval_threshold if eval_threshold is not None else StrategyConfig.EVAL_THRESHOLD,
    )


class Scalping1MDatasetBuilder:
    """
    `candles_1m` 원시 데이터를 1분봉 단타 학습용 데이터셋으로 변환합니다.

    책임은 세 가지입니다.
    1. 1분봉 캔들 조회
    2. TP/SL 선터치 라벨 생성
    3. 단타용 경량 피처 생성 및 학습 입력 분리
    """

    def __init__(
        self,
        db_manager: DBManager | None = None,
        config: Scalping1MConfig | None = None,
    ) -> None:
        """DB 연결과 1분봉 실험 설정을 초기화합니다."""
        self.db = db_manager or DBManager()
        self.config = config or build_scalping_1m_config()
        self.logger = setup_logger("Kairos.Scalping1M", "training.log")

    def get_symbol_list(self) -> list[str]:
        """`candles_1m`에 존재하는 USDT 심볼 목록을 조회합니다."""
        query = """
            SELECT DISTINCT symbol
            FROM candles_1m
            WHERE symbol LIKE %s
            ORDER BY symbol
        """
        try:
            with self.db._ensure_conn().cursor() as cur:
                cur.execute(query, (f"%{StrategyConfig.QUOTE_ASSET}",))
                return [str(row[0]) for row in cur.fetchall()]
        except Exception as exc:
            self.logger.error(f"1분봉 심볼 목록 조회 실패: {exc}")
            return []

    def load_symbol_candles(self, symbol: str, limit: int | None = None) -> pd.DataFrame:
        """특정 심볼의 최신 1분봉 캔들을 시간순으로 조회합니다."""
        row_limit = limit or self.config.limit_per_symbol
        query = """
            SELECT
                timestamp, symbol, open, high, low, close, volume, quote_volume,
                trades_count, taker_buy_base, taker_buy_quote
            FROM (
                SELECT
                    timestamp, symbol, open, high, low, close, volume, quote_volume,
                    trades_count, taker_buy_base, taker_buy_quote
                FROM candles_1m
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT %s
            ) AS recent_rows
            ORDER BY timestamp ASC
        """
        try:
            df = pd.read_sql(query, self.db._ensure_conn(), params=[symbol, row_limit])
        except Exception as exc:
            self.logger.warning(f"[{symbol}] 1분봉 조회 실패: {exc}")
            return pd.DataFrame()

        if df.empty:
            return df

        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return (
            df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    def load_dataset(
        self,
        symbols: list[str] | None = None,
        limit_per_symbol: int | None = None,
    ) -> pd.DataFrame:
        """
        여러 심볼의 1분봉 라벨/피처를 합쳐 학습용 데이터셋을 반환합니다.

        BTC 상대강도 피처가 필요하므로 요청 심볼에 BTCUSDT가 없으면 문맥 심볼로 추가합니다.
        """
        target_symbols = symbols or self.get_symbol_list()
        target_symbols = [symbol for symbol in target_symbols if symbol.endswith(StrategyConfig.QUOTE_ASSET)]
        if "BTCUSDT" not in target_symbols:
            target_symbols.append("BTCUSDT")

        limit = limit_per_symbol or self.config.limit_per_symbol
        self.logger.info(
            f"1분봉 단타 데이터셋 생성 시작: symbols={len(target_symbols)}, limit={limit}, "
            f"TP={self.config.target_tp:.2%}, SL={self.config.stop_loss:.2%}, "
            f"horizon={self.config.horizon_minutes}분"
        )

        frames: list[pd.DataFrame] = []
        for idx, symbol in enumerate(target_symbols, start=1):
            raw_df = self.load_symbol_candles(symbol, limit)
            if raw_df.empty or len(raw_df) < self.config.min_data_points:
                self.logger.warning(f"[{symbol}] 1분봉 데이터 부족으로 제외: rows={len(raw_df)}")
                continue

            labeled_df = self.add_target_label(raw_df)
            featured_df = self.add_symbol_features(labeled_df)
            if featured_df.empty:
                self.logger.warning(f"[{symbol}] 1분봉 피처 결과가 비어 제외합니다.")
                continue
            featured_df = self.add_pump_episode_features(featured_df)
            if symbol != "BTCUSDT" and "pump_base_condition" in featured_df.columns:
                featured_df = cast(
                    pd.DataFrame,
                    featured_df[featured_df["pump_base_condition"] == 1].copy(),
                )
                if featured_df.empty:
                    continue
            frames.append(self._optimize_memory(featured_df, copy=False))
            if idx % 50 == 0 or idx == len(target_symbols):
                self.logger.info(
                    f"1분봉 심볼 피처 생성 진행: {idx}/{len(target_symbols)} "
                    f"(누적 유효 심볼={len(frames)})"
                )

        if not frames:
            self.logger.error("1분봉 데이터셋을 만들 수 있는 심볼이 없습니다.")
            return pd.DataFrame()

        dataset = cast(pd.DataFrame, pd.concat(frames, ignore_index=True, copy=False))
        dataset = self._optimize_memory(dataset, copy=False)
        original_rows = len(dataset)
        dataset = self.add_cross_symbol_features(dataset, candidate_only=True)
        dataset = self.apply_scalping_rank_score(dataset)
        dataset = self.filter_pump_targets(dataset, original_row_count=original_rows)
        dataset = self.add_mtf_features(dataset)
        dataset = self._sanitize_numeric_values(dataset.dropna(subset=["target"]), copy=False)
        dataset = self._optimize_memory(dataset, copy=False)

        tp_count = int((dataset["target_tp"] == 1).sum()) if "target_tp" in dataset.columns else 0
        sl_count = int((dataset["target_sl"] == 1).sum()) if "target_sl" in dataset.columns else 0
        timeout_count = int((dataset["target_timeout"] == 1).sum()) if "target_timeout" in dataset.columns else 0
        self.logger.info(
            f"1분봉 단타 데이터셋 생성 완료: rows={len(dataset)}, cols={dataset.shape[1]}, "
            f"TP={tp_count} ({tp_count / max(len(dataset), 1):.2%}), "
            f"SL={sl_count} ({sl_count / max(len(dataset), 1):.2%}), "
            f"Timeout={timeout_count} ({timeout_count / max(len(dataset), 1):.2%})"
        )
        return dataset

    def add_target_label(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        1분봉 close 진입 기준 TP/SL 선터치 라벨을 생성합니다.

        같은 미래 1분봉 안에서 TP와 SL이 모두 닿으면 순서를 알 수 없으므로 SL로 처리합니다.
        마지막 horizon 구간은 미래 데이터가 부족하므로 제거합니다.
        """
        if df.empty:
            return df

        result = df.copy()
        horizon = self.config.horizon_minutes
        tp_price = result["close"] * (1 + self.config.target_tp)
        sl_price = result["close"] * (1 - self.config.stop_loss)

        result["target"] = 0
        result["target_tp"] = 0
        result["target_sl"] = 0
        result["target_timeout"] = 1
        result["target_reason"] = 0
        result["is_ambiguous"] = 0
        result["label_minutes_to_exit"] = horizon

        found_mask = pd.Series(False, index=result.index)
        for offset in range(1, horizon + 1):
            if bool(found_mask.all()):
                break

            future_high = result["high"].shift(-offset)
            future_low = result["low"].shift(-offset)
            active = ~found_mask & future_high.notna() & future_low.notna()
            tp_hit = active & (future_high >= tp_price)
            sl_hit = active & (future_low <= sl_price)
            both_hit = tp_hit & sl_hit
            only_tp = tp_hit & ~sl_hit
            only_sl = sl_hit & ~tp_hit

            result.loc[only_tp, "target"] = 1
            result.loc[only_tp, "target_tp"] = 1
            result.loc[only_tp, "target_timeout"] = 0
            result.loc[only_tp, "target_reason"] = 1
            result.loc[only_tp, "label_minutes_to_exit"] = offset

            result.loc[only_sl | both_hit, "target"] = 0
            result.loc[only_sl | both_hit, "target_sl"] = 1
            result.loc[only_sl | both_hit, "target_timeout"] = 0
            result.loc[only_sl | both_hit, "target_reason"] = -1
            result.loc[only_sl | both_hit, "label_minutes_to_exit"] = offset
            result.loc[both_hit, "is_ambiguous"] = 1

            found_mask |= tp_hit | sl_hit

        if len(result) <= horizon:
            return pd.DataFrame()
        return result.iloc[:-horizon].copy()

    @staticmethod
    def _calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """종가 Series에서 Wilder 방식 RSI를 계산합니다."""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        relative_strength = average_gain / (average_loss + 1e-9)
        return 100 - (100 / (1 + relative_strength))

    @classmethod
    def _add_trend_indicator_features(
        cls,
        feature_df: pd.DataFrame,
        *,
        prefix: str,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        volume: pd.Series,
    ) -> pd.DataFrame:
        """
        EMA 7/25/99, VWAP, Stoch RSI 기반 추세/모멘텀 피처를 추가합니다.

        절대 가격 레벨은 넣지 않고 현재가 대비 거리, 정렬 여부, 모멘텀 상태만 넣습니다.
        """
        column_prefix = f"{prefix}_" if prefix else ""
        safe_close = close.replace(0, np.nan)
        safe_volume = volume.replace(0, np.nan)
        typical_price = (high + low + safe_close) / 3
        new_features: dict[str, pd.Series] = {}

        ema_7 = safe_close.ewm(span=7, adjust=False).mean()
        ema_25 = safe_close.ewm(span=25, adjust=False).mean()
        ema_99 = safe_close.ewm(span=99, adjust=False).mean()
        new_features[f"{column_prefix}ema_7_gap"] = (safe_close - ema_7) / (safe_close + 1e-9)
        new_features[f"{column_prefix}ema_25_gap"] = (safe_close - ema_25) / (safe_close + 1e-9)
        new_features[f"{column_prefix}ema_99_gap"] = (safe_close - ema_99) / (safe_close + 1e-9)
        new_features[f"{column_prefix}ema_7_25_gap"] = (ema_7 - ema_25) / (safe_close + 1e-9)
        new_features[f"{column_prefix}ema_25_99_gap"] = (ema_25 - ema_99) / (safe_close + 1e-9)
        new_features[f"{column_prefix}ema_bull_stack"] = ((ema_7 > ema_25) & (ema_25 > ema_99)).astype(int)
        new_features[f"{column_prefix}ema_bear_stack"] = ((ema_7 < ema_25) & (ema_25 < ema_99)).astype(int)
        new_features[f"{column_prefix}close_above_ema_7"] = (safe_close > ema_7).astype(int)
        new_features[f"{column_prefix}close_above_ema_25"] = (safe_close > ema_25).astype(int)
        new_features[f"{column_prefix}close_above_ema_99"] = (safe_close > ema_99).astype(int)
        new_features[f"{column_prefix}ema_7_slope_5"] = ema_7.pct_change(5)
        new_features[f"{column_prefix}ema_25_slope_5"] = ema_25.pct_change(5)

        for window in [20, 60, 120]:
            vwap = (typical_price * safe_volume).rolling(window).sum() / (safe_volume.rolling(window).sum() + 1e-9)
            new_features[f"{column_prefix}vwap_{window}_gap"] = (safe_close - vwap) / (safe_close + 1e-9)
            new_features[f"{column_prefix}close_above_vwap_{window}"] = (safe_close > vwap).astype(int)

        rsi_14 = cls._calculate_rsi(safe_close, period=14)
        rsi_low = rsi_14.rolling(14).min()
        rsi_high = rsi_14.rolling(14).max()
        stoch_rsi = ((rsi_14 - rsi_low) / ((rsi_high - rsi_low) + 1e-9)).clip(lower=0, upper=1)
        stoch_rsi_k = stoch_rsi.rolling(3).mean()
        stoch_rsi_d = stoch_rsi_k.rolling(3).mean()
        new_features[f"{column_prefix}rsi_14"] = rsi_14 / 100
        new_features[f"{column_prefix}stoch_rsi"] = stoch_rsi
        new_features[f"{column_prefix}stoch_rsi_k"] = stoch_rsi_k
        new_features[f"{column_prefix}stoch_rsi_d"] = stoch_rsi_d
        new_features[f"{column_prefix}stoch_rsi_kd_gap"] = stoch_rsi_k - stoch_rsi_d
        new_features[f"{column_prefix}stoch_rsi_oversold_rebound"] = (
            (stoch_rsi_k.shift(1) <= 0.20) & (stoch_rsi_k > stoch_rsi_d)
        ).astype(int)
        new_features[f"{column_prefix}stoch_rsi_overbought"] = (stoch_rsi_k >= 0.80).astype(int)
        new_features[f"{column_prefix}stoch_rsi_bull_cross"] = (
            (stoch_rsi_k > stoch_rsi_d) & (stoch_rsi_k.shift(1) <= stoch_rsi_d.shift(1))
        ).astype(int)

        money_flow = typical_price * safe_volume
        typical_delta = typical_price.diff()
        positive_money_flow = money_flow.where(typical_delta > 0, 0.0)
        negative_money_flow = money_flow.where(typical_delta < 0, 0.0).abs()
        money_flow_ratio = (
            positive_money_flow.rolling(14).sum()
            / (negative_money_flow.rolling(14).sum() + 1e-9)
        )
        mfi_14 = 100 - (100 / (1 + money_flow_ratio))
        new_features[f"{column_prefix}mfi_14"] = mfi_14 / 100
        new_features[f"{column_prefix}mfi_overbought"] = (mfi_14 >= 80).astype(int)
        new_features[f"{column_prefix}mfi_oversold"] = (mfi_14 <= 20).astype(int)
        new_features[f"{column_prefix}mfi_rebound"] = ((mfi_14.shift(1) <= 30) & (mfi_14 > mfi_14.shift(1))).astype(int)
        new_features[f"{column_prefix}mfi_fade"] = ((mfi_14.shift(1) >= 80) & (mfi_14 < mfi_14.shift(1))).astype(int)

        cci_sma = typical_price.rolling(20).mean()
        cci_mean_dev = (typical_price - cci_sma).abs().rolling(20).mean()
        cci_20 = (typical_price - cci_sma) / (0.015 * cci_mean_dev + 1e-9)
        new_features[f"{column_prefix}cci_20"] = (cci_20 / 200).clip(lower=-3, upper=3)
        new_features[f"{column_prefix}cci_high"] = (cci_20 >= 100).astype(int)
        new_features[f"{column_prefix}cci_low"] = (cci_20 <= -100).astype(int)
        new_features[f"{column_prefix}cci_cross_up"] = ((cci_20 > -100) & (cci_20.shift(1) <= -100)).astype(int)
        new_features[f"{column_prefix}cci_cross_down"] = ((cci_20 < 100) & (cci_20.shift(1) >= 100)).astype(int)

        ema_12 = safe_close.ewm(span=12, adjust=False).mean()
        ema_26 = safe_close.ewm(span=26, adjust=False).mean()
        macd = ema_12 - ema_26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = macd - macd_signal
        macd_hist_norm = macd_hist / (safe_close + 1e-9)
        new_features[f"{column_prefix}macd_gap"] = macd / (safe_close + 1e-9)
        new_features[f"{column_prefix}macd_signal_gap"] = macd_signal / (safe_close + 1e-9)
        new_features[f"{column_prefix}macd_hist"] = macd_hist_norm
        new_features[f"{column_prefix}macd_hist_slope_3"] = macd_hist_norm - macd_hist_norm.shift(3)
        new_features[f"{column_prefix}macd_bull_cross"] = ((macd > macd_signal) & (macd.shift(1) <= macd_signal.shift(1))).astype(int)
        new_features[f"{column_prefix}macd_bear_cross"] = ((macd < macd_signal) & (macd.shift(1) >= macd_signal.shift(1))).astype(int)

        high_diff = high.diff()
        low_diff = -low.diff()
        plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0.0)
        minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0.0)
        previous_close = safe_close.shift(1)
        true_range = pd.concat(
            [
                (high - low).abs(),
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_14 = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / (atr_14 + 1e-9)
        minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / (atr_14 + 1e-9)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        adx_14 = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        new_features[f"{column_prefix}adx_14"] = adx_14 / 100
        new_features[f"{column_prefix}plus_di_14"] = plus_di / 100
        new_features[f"{column_prefix}minus_di_14"] = minus_di / 100
        new_features[f"{column_prefix}di_gap_14"] = (plus_di - minus_di) / 100

        bb_mid = safe_close.rolling(20).mean()
        bb_std = safe_close.rolling(20).std()
        bb_upper = bb_mid + (bb_std * 2)
        bb_lower = bb_mid - (bb_std * 2)
        bb_width = (bb_upper - bb_lower) / (safe_close + 1e-9)
        new_features[f"{column_prefix}bb_width_20"] = bb_width
        new_features[f"{column_prefix}bb_pos_20"] = (safe_close - bb_lower) / ((bb_upper - bb_lower) + 1e-9)
        new_features[f"{column_prefix}bb_squeeze_20"] = (bb_width <= bb_width.rolling(120).quantile(0.25)).astype(int)
        new_features[f"{column_prefix}bb_upper_break"] = (safe_close > bb_upper).astype(int)
        new_features[f"{column_prefix}bb_mid_hold"] = (safe_close >= bb_mid).astype(int)

        new_features[f"{column_prefix}trend_continuation_long"] = (
            (new_features[f"{column_prefix}ema_bull_stack"] == 1)
            & (new_features[f"{column_prefix}close_above_vwap_20"] == 1)
            & (new_features[f"{column_prefix}macd_hist"] > 0)
            & (new_features[f"{column_prefix}adx_14"] >= 0.20)
            & (new_features[f"{column_prefix}di_gap_14"] > 0)
        ).astype(int)
        new_features[f"{column_prefix}trend_turn_up"] = (
            ((new_features[f"{column_prefix}macd_bull_cross"] == 1) | (new_features[f"{column_prefix}cci_cross_up"] == 1))
            & (new_features[f"{column_prefix}mfi_fade"] == 0)
            & (new_features[f"{column_prefix}stoch_rsi_kd_gap"] > 0)
        ).astype(int)
        new_features[f"{column_prefix}trend_exhaustion_risk"] = (
            (
                (new_features[f"{column_prefix}mfi_overbought"] == 1)
                | (new_features[f"{column_prefix}cci_high"] == 1)
                | (new_features[f"{column_prefix}stoch_rsi_overbought"] == 1)
            )
            & (new_features[f"{column_prefix}macd_hist_slope_3"] < 0)
        ).astype(int)
        new_features[f"{column_prefix}pullback_in_uptrend"] = (
            (new_features[f"{column_prefix}ema_bull_stack"] == 1)
            & (new_features[f"{column_prefix}ema_7_gap"] <= 0)
            & (new_features[f"{column_prefix}ema_25_gap"] >= -0.01)
            & (new_features[f"{column_prefix}mfi_fade"] == 0)
        ).astype(int)
        feature_block = pd.DataFrame(new_features, index=feature_df.index)
        return pd.concat([feature_df, feature_block], axis=1).copy()

    def add_symbol_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """단일 심볼 1분봉 데이터에 단타용 경량 피처를 추가합니다."""
        if df.empty:
            return df

        result = df.sort_values("timestamp").copy()
        close = result["close"].replace(0, np.nan)
        high = result["high"]
        low = result["low"]
        open_ = result["open"]
        volume = result["volume"].replace(0, np.nan)
        quote_volume = result["quote_volume"].replace(0, np.nan)
        candle_range = (high - low).replace(0, np.nan)

        for window in [1, 3, 5, 10, 15]:
            result[f"ret_{window}m"] = close.pct_change(window)
            result[f"log_ret_{window}m"] = np.log(close / close.shift(window))

        for window in [3, 5, 10, 20]:
            result[f"vol_ratio_{window}m"] = result["volume"] / (volume.rolling(window).mean() + 1e-9)
            result[f"quote_vol_ratio_{window}m"] = result["quote_volume"] / (quote_volume.rolling(window).mean() + 1e-9)

        result["quote_value"] = result["close"] * result["volume"]
        result["quote_value_ratio_20m"] = result["quote_value"] / (result["quote_value"].rolling(20).mean() + 1e-9)
        result["volume_accel_5m"] = result["vol_ratio_3m"] - result["vol_ratio_10m"]
        result["quote_volume_accel_5m"] = result["quote_vol_ratio_3m"] - result["quote_vol_ratio_10m"]

        previous_high_5 = high.rolling(5).max().shift(1)
        previous_high_20 = high.rolling(20).max().shift(1)
        low_20 = low.rolling(20).min()
        high_20 = high.rolling(20).max()
        result["high_break_5m"] = (close / (previous_high_5 + 1e-9)) - 1
        result["high_break_20m"] = (close / (previous_high_20 + 1e-9)) - 1
        result["is_high_break_20m"] = (high >= previous_high_20).astype(int)
        result["range_pos_20m"] = (close - low_20) / ((high_20 - low_20) + 1e-9)
        result["dist_to_high_20m"] = (previous_high_20 - close) / (close + 1e-9)

        result["candle_body_ratio"] = (close - open_) / (candle_range + 1e-9)
        result["upper_wick_ratio"] = (high - np.maximum(open_, close)) / (candle_range + 1e-9)
        result["lower_wick_ratio"] = (np.minimum(open_, close) - low) / (candle_range + 1e-9)
        result["range_pct_1m"] = candle_range / (close + 1e-9)
        result["volatility_5m"] = result["ret_1m"].rolling(5).std()
        result["volatility_15m"] = result["ret_1m"].rolling(15).std()

        bullish = (close > open_).astype(int)
        bearish = (close < open_).astype(int)
        result["bull_streak_3m"] = bullish.rolling(3).sum()
        result["bull_streak_5m"] = bullish.rolling(5).sum()
        result["bear_streak_3m"] = bearish.rolling(3).sum()
        result["bear_streak_5m"] = bearish.rolling(5).sum()

        result["change_24h"] = close.pct_change(1440)
        result["quote_volume_24h"] = result["quote_volume"].rolling(1440).sum()
        result["trades_count_ratio_20m"] = result["trades_count"] / (result["trades_count"].rolling(20).mean() + 1e-9)
        result["taker_buy_ratio"] = result["taker_buy_base"] / (result["volume"] + 1e-9)
        result["taker_buy_ratio_3m"] = result["taker_buy_base"].rolling(3).sum() / (
            result["volume"].rolling(3).sum() + 1e-9
        )
        result["taker_buy_ratio_5m"] = result["taker_buy_base"].rolling(5).sum() / (
            result["volume"].rolling(5).sum() + 1e-9
        )
        result["buy_pressure_persistence_3m"] = (
            (result["taker_buy_ratio"] >= 0.52)
            & (result["taker_buy_ratio_3m"] >= 0.52)
            & (result["quote_vol_ratio_3m"] >= 1.05)
            & (result["ret_3m"] > 0)
        ).astype(int)
        result["buy_pressure_persistence_5m"] = (
            (result["taker_buy_ratio_5m"] >= 0.52)
            & (result["quote_vol_ratio_5m"] >= 1.05)
            & (result["ret_5m"] > 0)
            & (result["upper_wick_ratio"] <= 0.55)
        ).astype(int)
        result["buy_pressure_decay_5m"] = (
            result["taker_buy_ratio_3m"] - result["taker_buy_ratio_5m"].shift(3)
        )
        result["volume_price_alignment_5m"] = (
            result["ret_5m"].clip(lower=-0.05, upper=0.05)
            * result["quote_vol_ratio_5m"].clip(lower=0, upper=5)
            * (result["taker_buy_ratio_5m"] - 0.5)
        )
        impulse_3m = (
            (result["ret_3m"] >= 0.006)
            & (result["quote_vol_ratio_3m"] >= 1.05)
            & (result["upper_wick_ratio"] <= 0.65)
        )
        impulse_5m = (
            (result["ret_5m"] >= 0.010)
            & (result["quote_vol_ratio_5m"] >= 1.05)
            & (result["upper_wick_ratio"] <= 0.65)
        )
        result["impulse_3m"] = impulse_3m.astype(int)
        result["impulse_5m"] = impulse_5m.astype(int)
        result["impulse_follow_through_3m"] = (
            (impulse_3m.shift(1).rolling(3).max().fillna(0) == 1)
            & (close >= high.shift(1).rolling(3).max())
            & (result["taker_buy_ratio_3m"] >= 0.50)
        ).astype(int)
        result["impulse_follow_through_5m"] = (
            (impulse_5m.shift(1).rolling(5).max().fillna(0) == 1)
            & (close >= high.shift(1).rolling(5).max())
            & (result["quote_vol_ratio_5m"] >= 1.00)
            & (result["taker_buy_ratio_5m"] >= 0.50)
        ).astype(int)
        result["impulse_failure_5m"] = (
            (impulse_5m.shift(1).rolling(5).max().fillna(0) == 1)
            & (close < close.shift(5))
            & (result["upper_wick_ratio"].rolling(5).max() >= 0.65)
        ).astype(int)
        result = self._add_trend_indicator_features(
            result,
            prefix="",
            close=close,
            high=high,
            low=low,
            volume=volume,
        )
        result = self.add_support_resistance_features(result)
        result = self.add_directional_confirmation_features(result)

        return self._sanitize_numeric_values(result, copy=True)

    def add_directional_confirmation_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        TP/SL 순서 구분을 돕는 돌파 유지력과 실패 돌파 피처를 추가합니다.

        모든 계산은 현재 봉과 과거 봉만 사용해 실시간 close 이벤트에서 재현 가능해야 합니다.
        """
        if df.empty:
            return df

        result = df
        close = result["close"].replace(0, np.nan)
        high = result["high"]
        low = result["low"]
        previous_high_20 = high.rolling(20).max().shift(1)
        previous_high_5 = high.rolling(5).max().shift(1)

        close_break_20m = close > previous_high_20
        wick_break_20m = (high > previous_high_20) & ~close_break_20m
        close_break_5m = close > previous_high_5

        result["breakout_close_20m"] = close_break_20m.astype(int)
        result["breakout_wick_fail_20m"] = wick_break_20m.astype(int)
        result["breakout_hold_3m"] = close_break_20m.rolling(3).sum() / 3.0
        result["breakout_hold_5m"] = close_break_20m.rolling(5).sum() / 5.0
        result["breakout_reclaim_5m"] = (
            (close_break_20m)
            & (close_break_20m.shift(1).rolling(5).sum().fillna(0) == 0)
        ).astype(int)
        result["breakout_failure_pressure_5m"] = (
            wick_break_20m.rolling(5).sum()
            + result["support_breakdown_120m"].rolling(5).sum()
            + (result["upper_wick_ratio"].rolling(5).max() >= 0.70).astype(int)
        )
        result["breakout_quality_follow_5m"] = (
            result["breakout_hold_5m"].fillna(0)
            * result["buy_pressure_persistence_5m"].fillna(0)
            * (1 - result["breakout_wick_fail_20m"].fillna(0))
        )
        result["close_near_recent_high_5m"] = 1 - ((previous_high_5 - close) / (close + 1e-9)).clip(lower=0, upper=1)
        result["low_reclaim_strength_5m"] = (
            (close - low.rolling(5).min()) / (close + 1e-9)
        ).clip(lower=0, upper=0.20)
        result["sl_sweep_reclaim_5m"] = (
            (low <= low.shift(1).rolling(5).min())
            & (close > close.shift(1))
            & (result["taker_buy_ratio_3m"].fillna(0) >= 0.50)
        ).astype(int)
        result["directional_continuation_score"] = (
            result["breakout_quality_follow_5m"].fillna(0) * 2.0
            + result["impulse_follow_through_3m"].fillna(0)
            + result["impulse_follow_through_5m"].fillna(0)
            + result["buy_pressure_persistence_5m"].fillna(0)
            + close_break_5m.astype(int)
            - result["breakout_failure_pressure_5m"].fillna(0)
            - result["impulse_failure_5m"].fillna(0)
        )
        return result

    def load_symbol_timeframe_candles(
        self,
        symbol: str,
        table_name: str,
        start_ts: pd.Timestamp,
        end_ts: pd.Timestamp,
    ) -> pd.DataFrame:
        """특정 심볼의 합성 상위 timeframe 캔들을 조회합니다."""
        if table_name not in {"candles_5m", "candles_15m"}:
            raise ValueError(f"지원하지 않는 상위 timeframe 테이블입니다: {table_name}")

        lookback_start = start_ts - pd.Timedelta(days=self.config.mtf_lookback_days)
        query = f"""
            SELECT
                timestamp, symbol, open, high, low, close, volume, quote_volume,
                open_interest, trades_count, taker_buy_base, taker_buy_quote
            FROM {table_name}
            WHERE symbol = %s
              AND timestamp >= %s
              AND timestamp <= %s
            ORDER BY timestamp ASC
        """
        try:
            df = pd.read_sql(
                query,
                self.db._ensure_conn(),
                params=[symbol, lookback_start.to_pydatetime(), end_ts.to_pydatetime()],
            )
        except Exception as exc:
            self.logger.warning(f"[{symbol}] {table_name} 조회 실패: {exc}")
            return pd.DataFrame()

        if df.empty:
            return df
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return (
            df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    def add_mtf_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        완료된 5분봉/15분봉 문맥 피처를 1분봉 row에 붙입니다.

        상위 timeframe 캔들은 해당 묶음의 마지막 1분봉 close 이후에만 알 수 있으므로,
        `timestamp + (timeframe_minutes - 1)`을 사용해 1분봉 timestamp에 backward merge합니다.
        이 방식은 1분봉 row가 아직 완료되지 않은 상위 봉 값을 보는 미래 누수를 막습니다.
        """
        if df.empty or not self.config.use_mtf_features:
            return df

        symbol_count = df["symbol"].nunique() if "symbol" in df.columns else 0
        if symbol_count > 1:
            frames: list[pd.DataFrame] = []
            for _, group in df.groupby("symbol", sort=False):
                enriched_group = self.add_mtf_features(cast(pd.DataFrame, group.copy()))
                frames.append(self._optimize_memory(enriched_group, copy=False))
            if not frames:
                return df
            return self._optimize_memory(pd.concat(frames, ignore_index=True, copy=False), copy=False)

        symbol = str(df["symbol"].iloc[0])
        start_ts = pd.Timestamp(df["timestamp"].min()).tz_convert("UTC")
        end_ts = pd.Timestamp(df["timestamp"].max()).tz_convert("UTC")
        result = df.sort_values("timestamp").copy()

        for table_name, prefix, timeframe_minutes in [
            ("candles_5m", "mtf_5m", 5),
            ("candles_15m", "mtf_15m", 15),
        ]:
            mtf_df = self.load_symbol_timeframe_candles(symbol, table_name, start_ts, end_ts)
            if mtf_df.empty:
                result = self._fill_missing_mtf_columns(result, prefix)
                continue

            mtf_features = self.build_timeframe_feature_frame(mtf_df, prefix, timeframe_minutes)
            if mtf_features.empty:
                result = self._fill_missing_mtf_columns(result, prefix)
                continue

            result = pd.merge_asof(
                result.sort_values("timestamp"),
                mtf_features.sort_values("available_ts"),
                left_on="timestamp",
                right_on="available_ts",
                direction="backward",
            )
            result = result.drop(columns=["available_ts"], errors="ignore")

        return self._sanitize_numeric_values(result, copy=False)

    def build_timeframe_feature_frame(
        self,
        df: pd.DataFrame,
        prefix: str,
        timeframe_minutes: int,
    ) -> pd.DataFrame:
        """상위 timeframe 캔들에서 1분봉에 붙일 문맥 피처를 계산합니다."""
        if df.empty:
            return df

        result = df.sort_values("timestamp").copy()
        close = result["close"].replace(0, np.nan)
        high = result["high"]
        low = result["low"]
        open_ = result["open"]
        volume = result["volume"].replace(0, np.nan)
        quote_volume = result["quote_volume"].replace(0, np.nan)
        open_interest = result.get("open_interest", pd.Series(0.0, index=result.index)).replace(0, np.nan)
        candle_range = (high - low).replace(0, np.nan)

        feature_df = pd.DataFrame(index=result.index)
        feature_df["available_ts"] = pd.to_datetime(result["timestamp"], utc=True) + pd.Timedelta(
            minutes=timeframe_minutes - 1
        )
        for window in [1, 2, 3, 6, 12]:
            feature_df[f"{prefix}_ret_{window}"] = close.pct_change(window)
            feature_df[f"{prefix}_log_ret_{window}"] = np.log(close / close.shift(window))

        for window in [3, 6, 12]:
            rolling_high = high.rolling(window).max().shift(1)
            rolling_low = low.rolling(window).min().shift(1)
            rolling_range = (rolling_high - rolling_low).abs()
            feature_df[f"{prefix}_range_pos_{window}"] = (close - rolling_low) / (rolling_range + 1e-9)
            feature_df[f"{prefix}_dist_to_high_{window}"] = (rolling_high - close) / (close + 1e-9)
            feature_df[f"{prefix}_dist_to_low_{window}"] = (close - rolling_low) / (close + 1e-9)
            feature_df[f"{prefix}_high_break_{window}"] = (close / (rolling_high + 1e-9)) - 1

        feature_df[f"{prefix}_range_pct"] = candle_range / (close + 1e-9)
        feature_df[f"{prefix}_body_ratio"] = (close - open_) / (candle_range + 1e-9)
        feature_df[f"{prefix}_upper_wick_ratio"] = (high - np.maximum(open_, close)) / (candle_range + 1e-9)
        feature_df[f"{prefix}_lower_wick_ratio"] = (np.minimum(open_, close) - low) / (candle_range + 1e-9)
        feature_df[f"{prefix}_quote_vol_ratio_6"] = result["quote_volume"] / (quote_volume.rolling(6).mean() + 1e-9)
        feature_df[f"{prefix}_quote_vol_ratio_12"] = result["quote_volume"] / (quote_volume.rolling(12).mean() + 1e-9)
        feature_df[f"{prefix}_volume_accel"] = feature_df[f"{prefix}_quote_vol_ratio_6"] - feature_df[f"{prefix}_quote_vol_ratio_12"]
        feature_df[f"{prefix}_taker_buy_ratio"] = result["taker_buy_base"] / (result["volume"] + 1e-9)

        ema_fast = close.ewm(span=6, adjust=False).mean()
        ema_slow = close.ewm(span=20, adjust=False).mean()
        feature_df[f"{prefix}_ema_gap"] = (ema_fast - ema_slow) / (close + 1e-9)
        feature_df[f"{prefix}_close_above_ema_slow"] = (close > ema_slow).astype(int)
        feature_df = self._add_trend_indicator_features(
            feature_df,
            prefix=prefix,
            close=close,
            high=high,
            low=low,
            volume=volume,
        )
        feature_df[f"{prefix}_trend_score"] = (
            feature_df[f"{prefix}_ret_3"].fillna(0) * 2.0
            + feature_df[f"{prefix}_ret_6"].fillna(0)
            + feature_df[f"{prefix}_ema_gap"].fillna(0)
            + feature_df[f"{prefix}_ema_7_25_gap"].fillna(0)
            + feature_df[f"{prefix}_vwap_20_gap"].fillna(0)
            + feature_df[f"{prefix}_macd_hist"].fillna(0)
            + feature_df[f"{prefix}_di_gap_14"].fillna(0)
            + feature_df[f"{prefix}_volume_accel"].fillna(0) * 0.1
        )

        if prefix == "mtf_15m":
            feature_df[f"{prefix}_oi_ret_1"] = open_interest.pct_change(1)
            feature_df[f"{prefix}_oi_ret_4"] = open_interest.pct_change(4)
            feature_df[f"{prefix}_oi_price_align_4"] = (
                feature_df[f"{prefix}_ret_6"].fillna(0) * feature_df[f"{prefix}_oi_ret_4"].fillna(0)
            )
            feature_df[f"{prefix}_oi_volume_ratio"] = open_interest / (volume.rolling(12).mean() + 1e-9)

        return self._sanitize_numeric_values(feature_df, copy=False)

    @staticmethod
    def _fill_missing_mtf_columns(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        """상위 timeframe 데이터가 없을 때 고정 MTF 피처 컬럼을 0으로 채웁니다."""
        result = df
        common_columns = [
            "ret_1", "log_ret_1", "ret_2", "log_ret_2", "ret_3", "log_ret_3",
            "ret_6", "log_ret_6", "ret_12", "log_ret_12",
            "range_pos_3", "dist_to_high_3", "dist_to_low_3", "high_break_3",
            "range_pos_6", "dist_to_high_6", "dist_to_low_6", "high_break_6",
            "range_pos_12", "dist_to_high_12", "dist_to_low_12", "high_break_12",
            "range_pct", "body_ratio", "upper_wick_ratio", "lower_wick_ratio",
            "quote_vol_ratio_6", "quote_vol_ratio_12", "volume_accel",
            "taker_buy_ratio", "ema_gap", "close_above_ema_slow", "trend_score",
            "ema_7_gap", "ema_25_gap", "ema_99_gap", "ema_7_25_gap", "ema_25_99_gap",
            "ema_bull_stack", "ema_bear_stack", "close_above_ema_7", "close_above_ema_25",
            "close_above_ema_99", "ema_7_slope_5", "ema_25_slope_5",
            "vwap_20_gap", "close_above_vwap_20", "vwap_60_gap", "close_above_vwap_60",
            "vwap_120_gap", "close_above_vwap_120", "rsi_14", "stoch_rsi", "stoch_rsi_k",
            "stoch_rsi_d", "stoch_rsi_kd_gap", "stoch_rsi_oversold_rebound",
            "stoch_rsi_overbought", "stoch_rsi_bull_cross",
            "mfi_14", "mfi_overbought", "mfi_oversold", "mfi_rebound", "mfi_fade",
            "cci_20", "cci_high", "cci_low", "cci_cross_up", "cci_cross_down",
            "macd_gap", "macd_signal_gap", "macd_hist", "macd_hist_slope_3",
            "macd_bull_cross", "macd_bear_cross", "adx_14", "plus_di_14", "minus_di_14",
            "di_gap_14", "bb_width_20", "bb_pos_20", "bb_squeeze_20", "bb_upper_break",
            "bb_mid_hold", "trend_continuation_long", "trend_turn_up",
            "trend_exhaustion_risk", "pullback_in_uptrend",
        ]
        extra_columns = []
        if prefix == "mtf_15m":
            extra_columns = ["oi_ret_1", "oi_ret_4", "oi_price_align_4", "oi_volume_ratio"]
        feature_columns = [f"{prefix}_{column}" for column in common_columns + extra_columns]
        missing_block = pd.DataFrame(0.0, index=result.index, columns=feature_columns, dtype="float32")
        return pd.concat(
            [result.drop(columns=feature_columns, errors="ignore"), missing_block],
            axis=1,
            copy=False,
        )

    def filter_pump_targets(self, df: pd.DataFrame, original_row_count: int | None = None) -> pd.DataFrame:
        """
        실제 운용 의도에 맞춰 24시간 상승률이 충분히 큰 펌핑 row만 남깁니다.

        BTCUSDT는 상대강도 피처 산출에는 사용하지만 진입 대상은 아니므로 여기서 제외합니다.
        """
        if df.empty:
            return df

        source_df = df
        if "pump_base_condition" not in source_df.columns:
            source_df = self.add_pump_episode_features(source_df)
        filtered = cast(
            pd.DataFrame,
            source_df[
                (source_df["pump_base_condition"] == 1)
                & (source_df["symbol"] != "BTCUSDT")
            ].copy(),
        )
        source_count = original_row_count if original_row_count is not None else len(df)
        self.logger.info(
            f"24h 펌핑 타겟 필터 적용: "
            f"{source_count} -> {len(filtered)} rows "
            f"(change_24h>={self.config.pump_min_change_24h:.0%})"
        )
        return filtered

    def add_pump_episode_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        현재 시점에 알 수 있는 정보로 펌핑 에피소드 여부와 경과 시간을 계산합니다.

        조건을 만족하기 시작한 시점을 episode 시작으로 보고, 같은 심볼이 조건을 유지하는
        동안의 경과 시간이 `pump_max_age_hours` 이하인 구간만 학습 후보로 사용합니다.
        """
        if df.empty:
            return df

        compact_df = self._optimize_memory(df, copy=False)
        result = compact_df.sort_values(["symbol", "timestamp"], kind="mergesort")
        change_24h = result["change_24h"].fillna(0)
        base_pump_mask = result["change_24h"] >= self.config.pump_min_change_24h
        pump_base_condition = base_pump_mask.astype(int)
        pump_change_excess_24h = (change_24h - self.config.pump_min_change_24h).clip(lower=0)
        pump_strength_score = (
            pump_change_excess_24h / max(self.config.pump_extreme_change_24h - self.config.pump_min_change_24h, 1e-9)
        ).clip(lower=0, upper=2)

        episode_age_parts: list[pd.Series] = []
        for _, group in result.groupby("symbol", sort=False):
            condition = base_pump_mask.loc[group.index].astype(bool)
            episode_start_flags = condition & ~condition.shift(fill_value=False)
            episode_id = episode_start_flags.cumsum()
            start_times = group["timestamp"].where(episode_start_flags).groupby(episode_id).transform("first")
            age_hours = (
                (pd.to_datetime(group["timestamp"], utc=True) - pd.to_datetime(start_times, utc=True))
                .dt.total_seconds()
                .div(3600)
                .fillna(0.0)
            )
            age_hours = age_hours.where(condition, 0.0)
            episode_age_parts.append(pd.Series(age_hours.to_numpy(), index=group.index))

        if episode_age_parts:
            pump_episode_age_hours = pd.concat(episode_age_parts).sort_index()
        else:
            pump_episode_age_hours = pd.Series(0.0, index=result.index, dtype="float32")

        is_pump_regime = (
            (pump_base_condition == 1)
            & (pump_episode_age_hours <= self.config.pump_max_age_hours)
        ).astype(int)
        pump_age_ratio = (
            pump_episode_age_hours / max(float(self.config.pump_max_age_hours), 1.0)
        ).clip(lower=0, upper=1)
        pump_features = pd.DataFrame(
            {
                "pump_base_condition": pump_base_condition,
                "pump_episode_age_hours": pump_episode_age_hours,
                "pump_change_excess_24h": pump_change_excess_24h,
                "pump_strength_score": pump_strength_score,
                "is_strong_pump_24h": (change_24h >= self.config.pump_strong_change_24h).astype(int),
                "is_extreme_pump_24h": (change_24h >= self.config.pump_extreme_change_24h).astype(int),
                "is_pump_regime": is_pump_regime,
                "pump_age_ratio": pump_age_ratio,
                "pump_freshness_score": (1 - pump_age_ratio).clip(lower=0, upper=1),
            },
            index=result.index,
        )
        result = pd.concat(
            [result.drop(columns=list(pump_features.columns), errors="ignore"), pump_features],
            axis=1,
            copy=False,
        )
        result = self.apply_scalping_rank_score(result)
        return self._optimize_memory(result.sort_index(), copy=False)

    def add_support_resistance_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        최근 120개 1분봉 기준 지지/저항 구조 피처를 추가합니다.

        저항선은 과거 120봉의 high 또는 close가 반복해서 근접한 상단 가격대,
        지지선은 과거 120봉의 low 또는 close가 반복해서 근접한 하단 가격대로 봅니다.
        현재 봉 자체를 선 산출에 포함하지 않도록 shift(1)을 사용해 미래 누수를 막습니다.
        """
        if df.empty:
            return df

        result = df.copy()
        window = self.config.support_resistance_window
        tolerance = self.config.support_resistance_touch_tolerance
        min_touches = self.config.support_resistance_min_touches

        high = result["high"]
        low = result["low"]
        close = result["close"].replace(0, np.nan)
        high_or_close = pd.Series(np.maximum(result["high"], result["close"]), index=result.index)
        low_or_close = pd.Series(np.minimum(result["low"], result["close"]), index=result.index)
        previous_high_or_close = high_or_close.shift(1)
        previous_low_or_close = low_or_close.shift(1)
        previous_high = high.shift(1)
        previous_low = low.shift(1)
        previous_close = close.shift(1)

        lowest_low = previous_low.rolling(window).min()
        highest_high = previous_high.rolling(window).max()
        lowest_close = previous_close.rolling(window).min()
        highest_close = previous_close.rolling(window).max()
        support = previous_low_or_close.rolling(window).quantile(0.10)
        resistance = previous_high_or_close.rolling(window).quantile(0.90)
        extreme_support = previous_low_or_close.rolling(window).min()
        extreme_resistance = previous_high_or_close.rolling(window).max()
        support = support.where(support.notna(), extreme_support)
        resistance = resistance.where(resistance.notna(), extreme_resistance)
        result["lowest_low_120m"] = lowest_low
        result["highest_high_120m"] = highest_high
        result["lowest_close_120m"] = lowest_close
        result["highest_close_120m"] = highest_close
        result["extreme_support_120m"] = extreme_support
        result["extreme_resistance_120m"] = extreme_resistance
        result["resistance_120m"] = resistance
        result["support_120m"] = support
        result["dist_to_resistance_120m"] = (resistance - close) / (close + 1e-9)
        result["dist_to_support_120m"] = (close - support) / (close + 1e-9)
        result["dist_to_highest_high_120m"] = (highest_high - close) / (close + 1e-9)
        result["dist_to_lowest_low_120m"] = (close - lowest_low) / (close + 1e-9)

        near_resistance = (
            ((high - resistance).abs() / (close + 1e-9) <= tolerance)
            | ((close - resistance).abs() / (close + 1e-9) <= tolerance)
        ).astype(int)
        near_support = (
            ((low - support).abs() / (close + 1e-9) <= tolerance)
            | ((close - support).abs() / (close + 1e-9) <= tolerance)
        ).astype(int)
        result["resistance_touch_count_120m"] = near_resistance.rolling(window).sum()
        result["support_touch_count_120m"] = near_support.rolling(window).sum()
        result["resistance_touch_density_120m"] = result["resistance_touch_count_120m"] / float(window)
        result["support_touch_density_120m"] = result["support_touch_count_120m"] / float(window)
        result["has_resistance_120m"] = (result["resistance_touch_count_120m"] >= min_touches).astype(int)
        result["has_support_120m"] = (result["support_touch_count_120m"] >= min_touches).astype(int)

        result["resistance_breakout_120m"] = (
            (result["has_resistance_120m"] == 1)
            & (close > resistance * (1 + tolerance))
        ).astype(int)
        result["support_breakdown_120m"] = (
            (result["has_support_120m"] == 1)
            & (close < support * (1 - tolerance))
        ).astype(int)
        result["support_hold_120m"] = (
            (result["has_support_120m"] == 1)
            & (low >= support * (1 - tolerance))
            & (result["dist_to_support_120m"] >= 0)
        ).astype(int)
        result["near_support_entry_120m"] = (
            (result["has_support_120m"] == 1)
            & (result["dist_to_support_120m"] <= tolerance * 3)
            & (result["support_breakdown_120m"] == 0)
        ).astype(int)

        recent_low = low.rolling(20).min()
        middle_low = low.shift(50).rolling(20).min()
        older_low = low.shift(100).rolling(20).min()
        recent_high = high.rolling(20).max()
        middle_high = high.shift(50).rolling(20).max()
        older_high = high.shift(100).rolling(20).max()
        result["higher_lows_120m"] = ((recent_low > middle_low) & (middle_low > older_low)).astype(int)
        result["lower_highs_120m"] = ((recent_high < middle_high) & (middle_high < older_high)).astype(int)
        result["low_slope_120m"] = (recent_low - older_low) / (close + 1e-9)
        result["high_slope_120m"] = (recent_high - older_high) / (close + 1e-9)

        range_120m = (resistance - support).abs()
        result["range_width_120m"] = range_120m / (close + 1e-9)
        result["range_pos_120m"] = (close - support) / (range_120m + 1e-9)
        extreme_range_120m = (highest_high - lowest_low).abs()
        result["extreme_range_width_120m"] = extreme_range_120m / (close + 1e-9)
        result["extreme_range_pos_120m"] = (close - lowest_low) / (extreme_range_120m + 1e-9)
        result["compression_triangle_120m"] = (
            (result["higher_lows_120m"] == 1)
            & (result["lower_highs_120m"] == 1)
            & (result["has_support_120m"] == 1)
            & (result["has_resistance_120m"] == 1)
        ).astype(int)
        result["breakout_quality_120m"] = (
            result["resistance_breakout_120m"]
            * result["quote_vol_ratio_5m"].fillna(0)
            * (1 - result["upper_wick_ratio"].fillna(0).clip(lower=0, upper=1))
            * (1 - result["support_breakdown_120m"])
        )
        result = self.add_wedge_pivot_features(result)
        return result

    def add_wedge_pivot_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        확정된 스윙 피벗으로 기울어진 지지/저항 구조를 계산합니다.

        피벗은 `pivot_span`개 봉이 지난 뒤에만 확정하므로 현재 봉 이후의 정보를 쓰지 않습니다.
        전전/전 고점과 전전/전 저점 배열을 통해 웻지, 하락 채널, 지지선 반응을 분리합니다.
        """
        if df.empty:
            return df

        result = df
        pivot_span = 3
        tolerance = self.config.support_resistance_touch_tolerance
        high = result["high"].astype(float)
        low = result["low"].astype(float)
        close = result["close"].replace(0, np.nan).astype(float)
        open_ = result["open"].astype(float)
        row_index = pd.Series(np.arange(len(result), dtype=float), index=result.index)

        pivot_window = (pivot_span * 2) + 1
        rolling_high = high.rolling(pivot_window, center=True, min_periods=pivot_window).max()
        rolling_low = low.rolling(pivot_window, center=True, min_periods=pivot_window).min()
        raw_swing_high = (
            (high >= rolling_high)
            & (high > high.shift(1))
            & (high >= high.shift(-1))
        )
        raw_swing_low = (
            (low <= rolling_low)
            & (low < low.shift(1))
            & (low <= low.shift(-1))
        )

        confirmed_high_price = high.where(raw_swing_high).shift(pivot_span)
        confirmed_high_idx = row_index.where(raw_swing_high).shift(pivot_span)
        confirmed_low_price = low.where(raw_swing_low).shift(pivot_span)
        confirmed_low_idx = row_index.where(raw_swing_low).shift(pivot_span)

        prev_high, prev2_high, prev_high_idx, prev2_high_idx = self._last_two_pivot_values(
            confirmed_high_price,
            confirmed_high_idx,
        )
        prev_low, prev2_low, prev_low_idx, prev2_low_idx = self._last_two_pivot_values(
            confirmed_low_price,
            confirmed_low_idx,
        )

        result["prev_swing_high_120m"] = prev_high
        result["prev2_swing_high_120m"] = prev2_high
        result["prev_swing_low_120m"] = prev_low
        result["prev2_swing_low_120m"] = prev2_low

        high_gap = (prev_high_idx - prev2_high_idx).replace(0, np.nan)
        low_gap = (prev_low_idx - prev2_low_idx).replace(0, np.nan)
        high_slope_per_bar = (prev_high - prev2_high) / high_gap
        low_slope_per_bar = (prev_low - prev2_low) / low_gap
        support_line = prev2_low + (low_slope_per_bar * (row_index - prev2_low_idx))
        resistance_line = prev2_high + (high_slope_per_bar * (row_index - prev2_high_idx))

        result["wedge_support_line_120m"] = support_line
        result["wedge_resistance_line_120m"] = resistance_line
        result["wedge_support_slope_120m"] = low_slope_per_bar / (close + 1e-9)
        result["wedge_resistance_slope_120m"] = high_slope_per_bar / (close + 1e-9)
        result["dist_to_wedge_support_120m"] = (close - support_line) / (close + 1e-9)
        result["dist_to_wedge_resistance_120m"] = (resistance_line - close) / (close + 1e-9)

        lower_highs = (prev2_high > prev_high).astype(int)
        higher_lows = (prev2_low < prev_low).astype(int)
        lower_lows = (prev2_low > prev_low).astype(int)
        result["pivot_lower_highs_120m"] = lower_highs
        result["pivot_higher_lows_120m"] = higher_lows
        result["pivot_lower_lows_120m"] = lower_lows
        result["pivot_broadening_120m"] = ((prev2_high < prev_high) & (prev2_low > prev_low)).astype(int)
        result["wedge_converging_120m"] = ((lower_highs == 1) & (higher_lows == 1)).astype(int)
        result["wedge_falling_120m"] = ((lower_highs == 1) & (lower_lows == 1)).astype(int)
        result["wedge_descending_pressure_120m"] = (
            lower_highs.astype(float)
            - higher_lows.astype(float) * 0.5
            + lower_lows.astype(float) * 0.5
        )

        support_touch = (
            support_line.notna()
            & (low <= support_line * (1 + tolerance * 2))
            & (close >= support_line * (1 - tolerance))
        )
        support_reclaim = (
            support_line.notna()
            & (low <= support_line * (1 + tolerance * 1.5))
            & (close > support_line)
            & (close >= open_)
            & (result["lower_wick_ratio"].fillna(0) >= 0.25)
            & (result["upper_wick_ratio"].fillna(0) <= 0.65)
        )
        support_break = support_line.notna() & (close < support_line * (1 - tolerance))
        resistance_breakout = (
            resistance_line.notna()
            & (close > resistance_line * (1 + tolerance))
            & (result["upper_wick_ratio"].fillna(0) <= 0.60)
        )
        result["wedge_support_touch_120m"] = support_touch.astype(int)
        result["wedge_support_reclaim_120m"] = support_reclaim.astype(int)
        result["wedge_support_break_120m"] = support_break.astype(int)
        result["wedge_resistance_breakout_120m"] = resistance_breakout.astype(int)
        result["wedge_support_retest_quality_120m"] = (
            support_reclaim.astype(float)
            * (1 - result["upper_wick_ratio"].fillna(0).clip(lower=0, upper=1))
            * result["quote_vol_ratio_5m"].fillna(0).clip(lower=0, upper=5)
            * (1 + result["taker_buy_ratio_3m"].fillna(0).clip(lower=0, upper=1))
        )
        result["wedge_breakout_quality_120m"] = (
            resistance_breakout.astype(float)
            * result["quote_vol_ratio_5m"].fillna(0).clip(lower=0, upper=5)
            * (1 - result["upper_wick_ratio"].fillna(0).clip(lower=0, upper=1))
            * (1 - support_break.astype(float))
        )
        return result

    @staticmethod
    def _last_two_pivot_values(
        event_price: pd.Series,
        event_index: pd.Series,
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """확정 피벗 이벤트 Series에서 현재 시점 기준 전/전전 피벗 값을 반환합니다."""
        count = event_price.notna().cumsum().to_numpy(dtype=int)
        prices = event_price.dropna().to_numpy(dtype=float)
        indices = event_index.dropna().to_numpy(dtype=float)

        last_price = np.full(len(event_price), np.nan, dtype=float)
        prev_price = np.full(len(event_price), np.nan, dtype=float)
        last_index = np.full(len(event_price), np.nan, dtype=float)
        prev_index = np.full(len(event_price), np.nan, dtype=float)

        has_last = count >= 1
        last_price[has_last] = prices[count[has_last] - 1]
        last_index[has_last] = indices[count[has_last] - 1]

        has_prev = count >= 2
        prev_price[has_prev] = prices[count[has_prev] - 2]
        prev_index[has_prev] = indices[count[has_prev] - 2]

        return (
            pd.Series(last_price, index=event_price.index),
            pd.Series(prev_price, index=event_price.index),
            pd.Series(last_index, index=event_price.index),
            pd.Series(prev_index, index=event_price.index),
        )

    def add_cross_symbol_features(self, df: pd.DataFrame, candidate_only: bool = False) -> pd.DataFrame:
        """BTC 상대강도와 시간별 시장 내 순위 피처를 추가합니다."""
        if df.empty:
            return df

        result = self._optimize_memory(df, copy=False)
        btc = result.loc[
            result["symbol"] == "BTCUSDT",
            ["timestamp", "ret_1m", "ret_5m"],
        ].drop_duplicates("timestamp", keep="last")
        btc_context = btc.set_index("timestamp") if not btc.empty else None
        new_features: dict[str, pd.Series] = {}
        if not candidate_only and btc_context is not None:
            btc_ret_1m = result["timestamp"].map(btc_context["ret_1m"]).fillna(0)
            btc_ret_5m = result["timestamp"].map(btc_context["ret_5m"]).fillna(0)
            new_features["btc_ret_1m"] = btc_ret_1m
            new_features["btc_ret_5m"] = btc_ret_5m
            new_features["rel_btc_ret_1m"] = result["ret_1m"] - btc_ret_1m
            new_features["rel_btc_ret_5m"] = result["ret_5m"] - btc_ret_5m
        elif not candidate_only:
            zero_series = pd.Series(0.0, index=result.index, dtype="float32")
            new_features["btc_ret_1m"] = zero_series
            new_features["btc_ret_5m"] = zero_series
            new_features["rel_btc_ret_1m"] = zero_series
            new_features["rel_btc_ret_5m"] = zero_series

        if candidate_only:
            candidate_mask = (
                (result["change_24h"].fillna(0) >= self.config.pump_min_change_24h)
                & (result["symbol"] != "BTCUSDT")
            )
            result = cast(pd.DataFrame, result.loc[candidate_mask].copy())

            if result.empty:
                return result

            new_features = {}
            if btc_context is not None:
                btc_ret_1m = result["timestamp"].map(btc_context["ret_1m"]).fillna(0)
                btc_ret_5m = result["timestamp"].map(btc_context["ret_5m"]).fillna(0)
                new_features["btc_ret_1m"] = btc_ret_1m
                new_features["btc_ret_5m"] = btc_ret_5m
                new_features["rel_btc_ret_1m"] = result["ret_1m"] - btc_ret_1m
                new_features["rel_btc_ret_5m"] = result["ret_5m"] - btc_ret_5m
            else:
                zero_series = pd.Series(0.0, index=result.index, dtype="float32")
                new_features["btc_ret_1m"] = zero_series
                new_features["btc_ret_5m"] = zero_series
                new_features["rel_btc_ret_1m"] = zero_series
                new_features["rel_btc_ret_5m"] = zero_series

        if new_features:
            feature_block = pd.DataFrame(new_features, index=result.index)
            result = pd.concat(
                [result.drop(columns=list(feature_block.columns), errors="ignore"), feature_block],
                axis=1,
                copy=False,
            )
        return self._optimize_memory(result, copy=False)

    def apply_scalping_rank_score(self, df: pd.DataFrame) -> pd.DataFrame:
        """진입 후보 압축용 1분봉 스캘핑 랭킹 점수를 계산합니다."""
        result = df.drop(columns=["scalping_rank_score"], errors="ignore")
        zero_series = pd.Series(0.0, index=result.index, dtype="float32")
        one_series = pd.Series(1.0, index=result.index, dtype="float32")
        scalping_rank_score = (
            result.get("ret_5m", zero_series).fillna(0) * 3.0
            + result.get("quote_vol_ratio_5m", zero_series).fillna(0)
            + result.get("rel_btc_ret_5m", zero_series).fillna(0) * 2.0
            + result.get("pump_strength_score", zero_series).fillna(0) * 1.4
            + result.get("pump_freshness_score", zero_series).fillna(0)
            + result.get("resistance_breakout_120m", zero_series).fillna(0) * 1.0
            + result.get("support_hold_120m", zero_series).fillna(0) * 1.6
            + result.get("near_support_entry_120m", zero_series).fillna(0) * 2.0
            + result.get("wedge_support_reclaim_120m", zero_series).fillna(0) * 2.4
            + result.get("wedge_support_touch_120m", zero_series).fillna(0) * 1.2
            + result.get("wedge_converging_120m", zero_series).fillna(0) * 0.8
            + result.get("wedge_falling_120m", zero_series).fillna(0) * 0.5
            + result.get("wedge_resistance_breakout_120m", zero_series).fillna(0) * 1.4
            + (1 - result.get("range_pos_120m", one_series).fillna(1.0).clip(lower=0, upper=1)) * 1.2
            + result.get("dist_to_resistance_120m", zero_series).fillna(0).clip(lower=0, upper=0.08) * 10.0
            + result.get("dist_to_wedge_resistance_120m", zero_series).fillna(0).clip(lower=0, upper=0.08) * 8.0
            + result.get("compression_triangle_120m", zero_series).fillna(0)
            + result.get("directional_continuation_score", zero_series).fillna(0) * 0.8
            + result.get("breakout_quality_follow_5m", zero_series).fillna(0) * 1.2
            + result.get("wedge_support_retest_quality_120m", zero_series).fillna(0) * 0.4
            + result.get("wedge_breakout_quality_120m", zero_series).fillna(0) * 0.3
            + result.get("sl_sweep_reclaim_5m", zero_series).fillna(0) * 0.5
            - result.get("upper_wick_ratio", zero_series).fillna(0)
            - result.get("support_breakdown_120m", zero_series).fillna(0) * 3.0
            - result.get("wedge_support_break_120m", zero_series).fillna(0) * 3.0
            - result.get("breakout_wick_fail_20m", zero_series).fillna(0) * 1.2
            - result.get("breakout_failure_pressure_5m", zero_series).fillna(0) * 0.6
            - result.get("impulse_failure_5m", zero_series).fillna(0) * 0.8
        ).astype("float32")
        return pd.concat(
            [result, scalping_rank_score.rename("scalping_rank_score")],
            axis=1,
            copy=False,
        )

    def split_data(
        self,
        df: pd.DataFrame,
        blind_test_days: int | None = None,
        val_ratio: float | None = None,
    ) -> tuple[pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]:
        """시간 순서를 유지해 Train, Validation, Blind 세트로 나눕니다."""
        if df.empty:
            return None, None, None

        blind_days = blind_test_days or StrategyConfig.BLIND_TEST_DAYS
        validation_ratio = val_ratio or StrategyConfig.VAL_RATIO
        sorted_df = df.sort_values("timestamp").reset_index(drop=True)
        last_ts = pd.Timestamp(sorted_df["timestamp"].max())
        blind_cutoff = last_ts - pd.Timedelta(days=blind_days)
        blind_df = cast(pd.DataFrame, sorted_df[sorted_df["timestamp"] > blind_cutoff].copy())
        train_val_df = cast(pd.DataFrame, sorted_df[sorted_df["timestamp"] <= blind_cutoff].copy())
        split_idx = int(len(train_val_df) * (1 - validation_ratio))
        train_df = cast(pd.DataFrame, train_val_df.iloc[:split_idx].copy())
        val_df = cast(pd.DataFrame, train_val_df.iloc[split_idx:].copy())
        self.logger.info(
            f"1분봉 데이터 분할 완료: Train={len(train_df)}, Val={len(val_df)}, Blind={len(blind_df)}"
        )
        return train_df, val_df, blind_df

    def prepare_features(
        self,
        df: pd.DataFrame,
        target_mode: str = "tp",
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
        """
        1분봉 데이터셋에서 모델 입력 X, 타겟 y, 샘플 가중치 w를 분리합니다.

        `target_mode="tp"`는 TP 선터치만 양성으로 보는 모델,
        `target_mode="sl"`은 SL 선터치만 양성으로 보는 모델,
        `target_mode="timeout"`은 제한시간 미도달만 양성으로 보는 모델 학습에 사용합니다.
        """
        if df.empty:
            return pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float)

        metadata_cols = {
            "timestamp", "symbol", "open", "high", "low", "close", "volume",
            "quote_volume", "trades_count", "taker_buy_base", "taker_buy_quote",
            "target", "target_tp", "target_sl", "target_timeout", "target_reason", "is_ambiguous", "label_minutes_to_exit",
            "open_interest",
            "quote_value", "quote_volume_24h",
            "lowest_low_120m", "highest_high_120m", "lowest_close_120m", "highest_close_120m",
            "extreme_support_120m", "extreme_resistance_120m", "support_120m", "resistance_120m",
            "prev_swing_high_120m", "prev2_swing_high_120m", "prev_swing_low_120m", "prev2_swing_low_120m",
            "wedge_support_line_120m", "wedge_resistance_line_120m",
            "pump_base_condition", "is_pump_regime",
        }
        X = df.drop(columns=[column for column in metadata_cols if column in df.columns])
        X = self._sanitize_numeric_values(X.select_dtypes(include=["number", "bool"]), copy=False)
        if target_mode == "timeout":
            target_column = "target_timeout"
        elif target_mode == "sl":
            target_column = "target_sl"
        elif target_mode == "tp":
            target_column = "target_tp"
        else:
            target_column = "target"
        y = cast(pd.Series, df[target_column].astype(int)) if target_column in df.columns else pd.Series(dtype=int)

        weights = pd.Series(1.0, index=df.index, dtype=float)
        if "pump_strength_score" in df.columns:
            strength = cast(pd.Series, df["pump_strength_score"]).astype(float).clip(lower=0, upper=2)
            weights *= 1.0 + (strength * 0.35)
        if "is_extreme_pump_24h" in df.columns:
            weights.loc[df["is_extreme_pump_24h"] == 1] *= 1.25
        return X, y, weights

    def _optimize_memory(self, df: pd.DataFrame, copy: bool = True) -> pd.DataFrame:
        """수치형 컬럼을 다운캐스팅해 1분봉 데이터셋 메모리 사용량을 줄입니다."""
        result = df.copy() if copy else df
        float_cols = result.select_dtypes(include=["float64"]).columns
        int_cols = result.select_dtypes(include=["int64"]).columns
        if len(float_cols) > 0:
            result[float_cols] = result[float_cols].astype("float32")
        for column in int_cols:
            result[column] = pd.to_numeric(result[column], downcast="integer")
        return result

    @staticmethod
    def _sanitize_numeric_values(df: pd.DataFrame, copy: bool = True) -> pd.DataFrame:
        """
        수치형 컬럼의 inf/NaN만 0으로 정리합니다.

        대용량 DataFrame 전체에 `replace([np.inf, -np.inf], 0)`를 걸면 pandas가
        모든 블록에 대한 거대한 bool mask를 한 번에 만들어 메모리 피크가 커집니다.
        컬럼 단위로 처리하면 같은 의미를 유지하면서 순간 메모리 사용량을 낮출 수 있습니다.
        """
        result = df.copy() if copy else df
        numeric_cols = result.select_dtypes(include=["number", "bool"]).columns
        for column in numeric_cols:
            result[column] = result[column].replace([np.inf, -np.inf], 0).fillna(0)
        return result
