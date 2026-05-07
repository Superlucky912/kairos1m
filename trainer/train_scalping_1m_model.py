"""
train_scalping_1m_model.py
==========================
이 파일은 `candles_1m` 기반 1분봉 단타 LightGBM 모델을 학습하는 실행 엔트리입니다.

기존 15분봉 전역 모델 학습 파일과 분리되어 있으며,
현재 기본 실험 기준은 StrategyConfig의 1분봉 TP/SL/Horizon 설정을 따릅니다.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any, cast

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from common.config.strategy_config import StrategyConfig
from common.ml.lgbm_model import LGBMModel
from common.ml.scalping_1m_dataset import (
    Scalping1MConfig,
    Scalping1MDatasetBuilder,
    build_scalping_1m_config,
)
from common.utils.logger import setup_logger


train_logger = setup_logger("Kairos.Train1M", "training.log")


def _build_train_params(y_train: pd.Series | None = None, task_name: str = "TP") -> dict[str, Any]:
    """1분봉 단타 모델용 LightGBM 파라미터를 준비합니다."""
    params = StrategyConfig.LGBM_PARAMS.copy()
    params.update(
        {
            "learning_rate": 0.03,
            "num_leaves": 31,
            "min_data_in_leaf": 80,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "lambda_l1": 1.0,
            "lambda_l2": 1.0,
            "seed": 42,
            "verbose": -1,
        }
    )
    if y_train is not None and not y_train.empty:
        positive_count = int((y_train == 1).sum())
        negative_count = int((y_train == 0).sum())
        if positive_count > 0 and negative_count > 0:
            scale_pos_weight = min(20.0, max(1.0, (negative_count / positive_count) * 0.5))
            params["scale_pos_weight"] = scale_pos_weight
            train_logger.info(
                f"1분봉 {task_name} positive class weight 적용: "
                f"positive={positive_count}, negative={negative_count}, scale_pos_weight={scale_pos_weight:.2f}"
            )
    return params


def _evaluate_validation(
    model: LGBMModel,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    val_df: pd.DataFrame,
    threshold: float,
    task_name: str,
) -> pd.DataFrame:
    """검증 세트 예측과 주요 분류 지표를 로그로 남깁니다."""
    pred_values = np.asarray(model.predict(X_val), dtype=float)
    eval_df = val_df.loc[X_val.index].copy()
    eval_df["pred_proba"] = pred_values
    eval_df["bin_pred"] = (eval_df["pred_proba"] >= threshold).astype(int)

    if len(set(y_val.astype(int).tolist())) >= 2:
        auc = roc_auc_score(y_val, pred_values)
    else:
        auc = 0.0
        train_logger.warning("Validation 타겟이 한 클래스뿐이라 AUC를 0으로 기록합니다.")

    precision = precision_score(y_val, eval_df["bin_pred"], zero_division=cast(Any, 0))
    recall = recall_score(y_val, eval_df["bin_pred"], zero_division=cast(Any, 0))
    f1 = f1_score(y_val, eval_df["bin_pred"], zero_division=cast(Any, 0))
    accuracy = accuracy_score(y_val, eval_df["bin_pred"])

    signal_rows = eval_df[eval_df["bin_pred"] == 1]
    signal_count = len(signal_rows)
    if task_name == "TP":
        positive_reason = 1
    elif task_name == "SL":
        positive_reason = -1
    else:
        positive_reason = 0
    positive_count = int((signal_rows["target_reason"] == positive_reason).sum()) if signal_count else 0
    positive_rate = positive_count / signal_count * 100 if signal_count else 0.0

    train_logger.info(f"=== 1분봉 단타 {task_name} Validation 결과 ===")
    train_logger.info(
        f"Threshold={threshold:.2f} | AUC={auc:.4f} | Accuracy={accuracy:.4f} | "
        f"Precision={precision:.4f} | Recall={recall:.4f} | F1={f1:.4f}"
    )
    train_logger.info(
        f"신호 상세 | PositiveSignals={signal_count}, 실제 {task_name}={positive_count}, "
        f"Hit Rate={positive_rate:.2f}%"
    )
    if len(eval_df) > 0:
        for percentile in [10, 5, 1, 0.1]:
            cutoff = float(np.quantile(eval_df["pred_proba"], 1 - percentile / 100))
            train_logger.info(f"상위 {percentile}% 확률 커트라인: {cutoff:.4f}")

    return eval_df


def _save_feature_importance(
    model: LGBMModel,
    X_train: pd.DataFrame,
    config: Scalping1MConfig,
    task_name: str,
) -> None:
    """학습된 1분봉 모델의 피처 중요도 Markdown 리포트를 저장합니다."""
    if model.model is None:
        return

    importance = pd.DataFrame(
        {
            "feature": X_train.columns,
            "importance_gain": model.model.feature_importance(importance_type="gain"),
            "importance_split": model.model.feature_importance(importance_type="split"),
        }
    ).sort_values("importance_gain", ascending=False)

    output_path = StrategyConfig.MODEL_DIR / f"feature_importance_{model.model_name}.md"
    with open(output_path, "w", encoding="utf-8") as file:
        file.write(f"# 1분봉 단타 모델 피처 중요도 리포트\n\n")
        file.write(f"- **생성 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (KST)\n")
        file.write(f"- **모델명**: {model.model_name}\n")
        file.write(f"- **타겟**: {task_name}\n")
        file.write(f"- **TP/SL/Horizon**: {config.target_tp:.1%} / {config.stop_loss:.1%} / {config.horizon_minutes}분\n")
        file.write(f"- **Threshold**: {config.eval_threshold:.2f}\n\n")
        file.write("## Feature Importance\n")
        file.write(importance.to_markdown(index=False))
    train_logger.info(f"1분봉 피처 중요도 리포트 저장 완료: {output_path}")


def _train_single_binary_model(
    *,
    model_name: str,
    task_name: str,
    X_train: pd.DataFrame,
    y_train: pd.Series,
    w_train: pd.Series,
    X_val: pd.DataFrame,
    y_val: pd.Series,
    w_val: pd.Series,
    val_df: pd.DataFrame,
    config: Scalping1MConfig,
) -> LGBMModel | None:
    """TP, SL, TIMEOUT 중 하나의 선터치 이벤트만 양성으로 보는 binary 모델을 학습합니다."""
    train_logger.info(
        f"1분봉 {task_name} 학습 입력 준비 완료 | "
        f"Train={len(X_train)}, Val={len(X_val)}, Feature={X_train.shape[1]}, "
        f"Positive={int((y_train == 1).sum())}"
    )
    model = LGBMModel(model_name=model_name)
    evals_result = model.train(
        X_train,
        y_train,
        X_val,
        y_val,
        weight_train=w_train,
        weight_val=w_val,
        params=_build_train_params(y_train, task_name=task_name),
    )

    if model.model is None:
        train_logger.error(f"1분봉 {task_name} 모델 학습 후 모델 객체가 비어 있습니다.")
        return None

    best_iter = model.model.best_iteration or len(evals_result.get("train", {}).get("auc", []))
    if best_iter and "valid" in evals_result and "auc" in evals_result["valid"]:
        valid_auc = evals_result["valid"]["auc"][best_iter - 1]
        train_auc = evals_result["train"]["auc"][best_iter - 1]
        train_logger.info(f"[{task_name} Best Iteration: {best_iter}] Train AUC={train_auc:.4f} | Valid AUC={valid_auc:.4f}")

    _evaluate_validation(model, X_val, y_val, val_df, config.eval_threshold, task_name)
    _save_feature_importance(model, X_train, config, task_name)
    return model


def train_scalping_1m_model(
    model_name: str = StrategyConfig.SCALPING_1M_MODEL_NAME,
    limit_per_symbol: int | None = None,
    symbols: list[str] | None = None,
) -> tuple[LGBMModel | None, LGBMModel | None, LGBMModel | None]:
    """1분봉 단타 TP 선터치, SL 선터치, TIMEOUT 모델을 독립적으로 학습하고 저장합니다."""
    config = build_scalping_1m_config(
        model_name=model_name,
        limit_per_symbol=limit_per_symbol or StrategyConfig.LIMIT_PER_SYMBOL,
    )
    train_logger.info(f"=== 1분봉 단타 모델 학습 시작: {config.model_name} ===")
    builder = Scalping1MDatasetBuilder(config=config)
    dataset = builder.load_dataset(symbols=symbols, limit_per_symbol=config.limit_per_symbol)
    if dataset.empty:
        train_logger.error("1분봉 학습 데이터셋이 비어 있습니다.")
        return None, None, None

    train_df, val_df, _ = builder.split_data(dataset)
    if train_df is None or val_df is None or train_df.empty or val_df.empty:
        train_logger.error("1분봉 Train/Validation 분할에 실패했습니다.")
        return None, None, None

    X_train, y_tp_train, w_tp_train = builder.prepare_features(train_df, target_mode="tp")
    X_val, y_tp_val, w_tp_val = builder.prepare_features(val_df, target_mode="tp")
    _, y_sl_train, w_sl_train = builder.prepare_features(train_df, target_mode="sl")
    _, y_sl_val, w_sl_val = builder.prepare_features(val_df, target_mode="sl")
    _, y_timeout_train, w_timeout_train = builder.prepare_features(train_df, target_mode="timeout")
    _, y_timeout_val, w_timeout_val = builder.prepare_features(val_df, target_mode="timeout")
    if X_train.empty or X_val.empty:
        train_logger.error("1분봉 모델 입력 피처가 비어 있습니다.")
        return None, None, None

    tp_model = _train_single_binary_model(
        model_name=f"{config.model_name}_tp",
        task_name="TP",
        X_train=X_train,
        y_train=y_tp_train,
        w_train=w_tp_train,
        X_val=X_val,
        y_val=y_tp_val,
        w_val=w_tp_val,
        val_df=val_df,
        config=config,
    )
    sl_model = _train_single_binary_model(
        model_name=f"{config.model_name}_sl",
        task_name="SL",
        X_train=X_train,
        y_train=y_sl_train,
        w_train=w_sl_train,
        X_val=X_val,
        y_val=y_sl_val,
        w_val=w_sl_val,
        val_df=val_df,
        config=config,
    )
    timeout_model = _train_single_binary_model(
        model_name=f"{config.model_name}_timeout",
        task_name="TIMEOUT",
        X_train=X_train,
        y_train=y_timeout_train,
        w_train=w_timeout_train,
        X_val=X_val,
        y_val=y_timeout_val,
        w_val=w_timeout_val,
        val_df=val_df,
        config=config,
    )
    train_logger.info(f"=== 1분봉 단타 모델 학습 완료: {config.model_name} ===")
    return tp_model, sl_model, timeout_model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="1분봉 단타 LightGBM 모델 학습")
    parser.add_argument("--model", type=str, default=StrategyConfig.SCALPING_1M_MODEL_NAME, help="저장할 모델명")
    parser.add_argument("--limit", type=int, default=StrategyConfig.LIMIT_PER_SYMBOL, help="심볼당 로드할 최근 1분봉 수")
    parser.add_argument("--symbols", type=str, default="", help="쉼표로 구분한 학습 심볼 목록")
    args = parser.parse_args()

    symbol_list = [item.strip().upper() for item in args.symbols.split(",") if item.strip()] or None
    train_scalping_1m_model(model_name=args.model, limit_per_symbol=args.limit, symbols=symbol_list)
