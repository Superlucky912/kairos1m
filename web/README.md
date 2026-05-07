# Kairos Web Backtest Tool

`web/`은 Kairos 백테스트 확인용 내부 도구입니다.

## 목적
- 1분봉 단타 백테스트 실행
- 1분봉 TP/SL dual 모델 검증 리포트 조회
- 손절 제한 OFF/ON 결과 비교
- 최근 거래 Markdown 리포트 확인

## 구조
- `web/api`: FastAPI API
- `web/ui`: 정적 HTML/CSS/JS UI

## Python 의존성 설치
루트에서 아래 명령으로 웹 API 의존성을 설치합니다.

```powershell
pip install -e .[web]
```

주요 의존성:
- `fastapi`
- `uvicorn`
- `pydantic`

## 1분봉 백테스트 아티팩트
`trainer/validate_scalping_1m.py`를 실행하면 아래 경로에 Markdown 리포트가 생성됩니다.

```text
common/logs/backtest/scalping_1m_results_<model>_dual.md
```

## API 실행
루트에서:

```powershell
uvicorn web.api.app.main:app --reload --app-dir .
```

기본 엔드포인트:
- `GET /api/health`
- `GET /api/scalping-1m/reports`
- `GET /api/scalping-1m/reports/{report_name}`
- `GET /api/scalping-1m/reports/{report_name}/trades`
- `GET /api/scalping-1m/reports/{report_name}/trades/{trade_index}/replay`
- `GET /api/scalping-1m/top15-label-candles`
- `POST /api/scalping-1m/backtests`

## UI 접속
API를 실행한 뒤 브라우저에서 아래 주소로 접속합니다.

```text
http://127.0.0.1:8000
```

UI에서 실행되는 백테스트는 `trainer.validate_scalping_1m` 검증 전용 엔트리입니다.
실거래 엔진, 주문, 수집기는 실행하지 않습니다.
