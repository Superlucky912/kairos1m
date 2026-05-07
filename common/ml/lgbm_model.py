"""
LGBMModel.py
============
이 파일은 Kairos 프로젝트에서 사용하는 LightGBM 모델의 생애주기를 한 곳에서 관리합니다.

초급 개발자 관점에서 보면 이 클래스의 흐름은 아래 순서로 이해하면 됩니다.

1. `__init__`
   어떤 이름의 모델 파일을 다룰지 결정하고, 저장 경로를 준비합니다.
   이 시점에는 아직 실제 모델이 메모리에 없을 수 있습니다.

2. `train`
   학습용 피처(`X_train`)와 정답(`y_train`)을 받아 LightGBM 모델을 학습합니다.
   검증 데이터가 있으면 조기 종료를 함께 사용해 과적합을 줄입니다.
   학습이 끝나면 메모리에 올라간 모델을 바로 파일로 저장합니다.

3. `load_model`
   디스크에 저장된 모델 파일을 다시 메모리로 불러옵니다.
   실거래나 검증 코드에서는 학습을 매번 다시 하지 않고, 보통 이 경로를 통해 모델을 사용합니다.

4. `predict`
   입력 피처를 받아 예측값을 반환합니다.
   아직 모델이 메모리에 없으면 먼저 `load_model`을 시도하고,
   학습 당시 피처 목록이 남아 있으면 그 목록에 맞춰 입력 컬럼을 다시 정렬합니다.

즉, 이 파일은 "모델을 어떻게 만들고, 어디에 저장하고, 다시 어떻게 불러와서 예측에 쓰는가"
를 일관된 방식으로 감싸는 래퍼 역할을 합니다.
"""

from __future__ import annotations

import os
from typing import Any

import joblib
import lightgbm as lgb
import pandas as pd

from common.config.strategy_config import StrategyConfig
from common.utils.logger import logger

class LGBMModel:
    """학습/저장/로드/예측을 한 인터페이스로 묶는 LightGBM 래퍼 클래스."""

    def __init__(self, model_name: str = "lgbm_v1"):
        """
        모델 식별자와 저장 경로를 초기화합니다.

        `model_name`은 단순한 표시 이름이 아니라 파일명에도 직접 연결됩니다.
        예를 들어 `global_model_v1`이라면 `<MODEL_DIR>/global_model_v1.pkl` 파일을 다루게 됩니다.
        """
        self.model_name = model_name
        self.model_path = StrategyConfig.MODEL_DIR / f"{model_name}.pkl"
        self.model: Any | None = None
        logger.info(f"LGBMModel 초기화 ({model_name})")

    def train(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: pd.DataFrame | None = None,
        y_val: pd.Series | None = None,
        weight_train: pd.Series | None = None,
        weight_val: pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        LightGBM 모델을 학습하고, 학습 결과를 파일로 저장합니다.

        흐름은 다음과 같습니다.
        1. 사용할 학습 파라미터를 준비합니다.
        2. 학습용/검증용 데이터를 LightGBM Dataset으로 감쌉니다.
        3. 조기 종료와 평가 로그 콜백을 붙여 학습합니다.
        4. 학습이 끝난 모델을 `self.model`에 보관합니다.
        5. 바로 `save_model()`을 호출해 디스크에도 저장합니다.

        반환값 `evals_result`는 학습 중 기록된 평가 지표 이력입니다.
        나중에 학습 품질을 확인하거나 로그를 남길 때 사용할 수 있습니다.
        """

        # 호출자가 별도 파라미터를 주지 않으면 프로젝트 기본 설정을 그대로 사용합니다.
        train_params = params or StrategyConfig.LGBM_PARAMS.copy()

        logger.info(f"모델 학습 시작: {self.model_name} | Samples={len(X_train)}")
        dtrain = lgb.Dataset(X_train, label=y_train, weight=weight_train)

        # 검증셋이 없어도 학습은 가능하므로, 기본값은 train 셋만 등록합니다.
        valid_sets = [dtrain]
        valid_names = ['train']

        if X_val is not None and y_val is not None:
            dval = lgb.Dataset(X_val, label=y_val, weight=weight_val, reference=dtrain)
            valid_sets.append(dval)
            valid_names.append('valid')
            logger.info(f"검증 세트 포함: Val Samples={len(X_val)}")

        callbacks: list[Any] = [
            lgb.log_evaluation(period=1000),
        ]
        evals_result: dict[str, Any] = {}
        callbacks.append(lgb.record_evaluation(evals_result))

        if X_val is not None and y_val is not None:
            callbacks.insert(0, lgb.early_stopping(stopping_rounds=StrategyConfig.EARLY_STOPPING_ROUNDS))

        self.model = lgb.train(
            train_params,
            dtrain,
            num_boost_round=StrategyConfig.NUM_BOOST_ROUND,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
        )

        # 학습 직후 저장해 두면, 이후 실거래/검증 프로세스가 같은 모델 파일을 재사용할 수 있습니다.
        self.save_model()
        return evals_result

    def predict(self, X: pd.DataFrame) -> pd.Series:
        """
        입력 피처 DataFrame을 받아 예측값을 반환합니다.

        이 메서드는 "예측 전에 모델이 메모리에 있는가?"를 먼저 확인합니다.
        없으면 저장된 파일에서 자동으로 로드하려고 시도합니다.

        또한 학습 당시 사용된 피처 이름 목록이 모델 안에 남아 있으면,
        현재 입력 `X`에서 그 컬럼만 다시 골라 예측에 사용합니다.
        이렇게 해야 추론 시점에 피처가 추가되었더라도 학습 당시 구조와 맞춰 안전하게 예측할 수 있습니다.
        """
        if self.model is None:
            self.load_model()
        
        model = self.model
        if model is None:
            # 모델 파일이 없거나 로드에 실패하면, 호출부가 완전히 죽지 않도록 0 시리즈를 반환합니다.
            logger.error(f"실패: {self.model_name} 모델을 로드할 수 없습니다.")
            return pd.Series([0] * len(X))

        # 학습 시 사용된 피처 명단이 있으면 해당 피처만 추출해
        # 추론 시점의 입력 스키마와 학습 시점의 스키마를 맞춥니다.
        try:
            feature_names = model.feature_name()
            if feature_names:
                # 모델이 실제로 학습한 컬럼만 골라 예측 입력을 고정합니다.
                X_input = X[feature_names]
            else:
                X_input = X
        except Exception as e:
            # 예측 자체를 막기보다, 컬럼 정렬 단계 실패 시 원본 입력으로 한 번 더 시도합니다.
            logger.error(f"피처 구성 중 오류 발생: {e}")
            X_input = X

        return pd.Series(model.predict(X_input))

    def save_model(self) -> None:
        """
        현재 메모리에 올라와 있는 모델을 파일로 저장합니다.

        `train()`이 끝난 직후 주로 호출되며, 저장 폴더가 없으면 먼저 생성합니다.
        """
        if self.model is not None:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            joblib.dump(self.model, self.model_path)
            logger.info(f"모델 저장 완료: {self.model_path}")

    def load_model(self) -> None:
        """
        디스크에 저장된 모델 파일을 메모리로 불러옵니다.

        이 메서드는 실거래나 검증 코드에서 가장 자주 간접적으로 사용됩니다.
        아직 `self.model`이 비어 있을 때 `predict()`가 먼저 이 메서드를 호출할 수 있습니다.
        """
        if os.path.exists(self.model_path):
            self.model = joblib.load(self.model_path)
            logger.info(f"모델 로드 완료: {self.model_path}")
        else:
            logger.warning(f"로드할 모델 파일이 없습니다: {self.model_path}")
