## 2026-05-03 scalping_1m_dataset PerformanceWarning 개선

### 작업 배경
- 사용자 질문:
  - `DataFrame is highly fragmented` PerformanceWarning을 개선할 수 있는지 확인 요청.
- 원인:
  - `common/ml/scalping_1m_dataset.py`의 `_add_trend_indicator_features()`에서 EMA, VWAP, RSI, MFI, CCI, MACD, ADX, Bollinger, trend 피처를 `feature_df[col] = ...` 방식으로 다수 반복 삽입하고 있었다.
  - pandas는 컬럼을 하나씩 반복 삽입하면 내부 block이 조각나면서 `PerformanceWarning`을 발생시킨다.

### 변경 내용
- `common/ml/scalping_1m_dataset.py`
  - `_add_trend_indicator_features()` 내부에서 신규 피처를 바로 DataFrame에 삽입하지 않도록 변경했다.
  - `new_features: dict[str, pd.Series]`에 피처 Series를 모두 모은다.
  - 마지막에 `pd.DataFrame(new_features, index=feature_df.index)`로 feature block을 만들고 `pd.concat([feature_df, feature_block], axis=1).copy()`로 한 번에 결합한다.
  - 계산식과 컬럼명은 유지했다.

### 검증
- 문법 검증:
  - `.\venv\Scripts\python.exe -m py_compile common\ml\scalping_1m_dataset.py`
- 경고 재현 샘플:
  - `_add_trend_indicator_features()`를 240개 row 샘플 DataFrame에 직접 실행했다.
  - 결과:
    - 생성 컬럼 수: `59`
    - `PerformanceWarning`: `0`

### 판단
- 경고가 발생하던 trend/BB 피처 블록은 개선됐다.
- 학습/백테스트 전체에서 다른 함수의 반복 컬럼 삽입 경고가 남을 수는 있지만, 이번에 사용자 로그에 나온 직접 위치는 해결했다.
