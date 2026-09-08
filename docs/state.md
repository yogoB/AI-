# 현재 상태

> 세션 시작 시 먼저 읽는다. 작업 끝나면 갱신한다.
> 최종 갱신: 2026-09-08

## 완료
- 계약 확정 (`docs/contract.md`)
- FastAPI 스캐폴딩: Python 3.12, `pyproject.toml`, `uv.lock`, `app/main.py`
- 헬스체크: `GET /health` → `200 {"status": "ok"}` (API 키 불필요)
- `POST /parse`: Pydantic 요청·응답 검증, 고정 서비스 ID, 선택 항목 미추출 시 `null`
- `confidence < 0.7`: 추출값을 비우고 서버의 고정 확인 질문 반환
- Claude 호출을 `app/llm/client.py`로 모으고 프롬프트를 `app/prompts/parse.md`로 분리
- 키 누락·호출 실패 `AI-LLM-001`, 잘못된 모델 응답 `AI-PARSE-001` 처리
- 검증: `uv sync --locked`, `uv run pytest -q` (38개 통과), 실제 서버의 `/health`, `/docs`, `/openapi.json` 응답 확인
  - 테스트 시 Starlette/httpx 및 AnyIO 의존성의 사용 중단 예정 경고 2건 발생
  - `/parse`는 고정 응답·HTTP 전송 스텁으로 검증. 실제 Claude 호출과 추출 품질은 미검증

## 다음 할 일
3. `POST /narrate` — 문구 규칙(§5) 준수 테스트
4. `POST /ocr` — 여유가 있을 때만

백엔드 연동 시 확인: 계약에 미정의된 HTTP 오류 상태와 `detail` 외피는 README의 현재 구현 기준으로 수신 처리 확인.

## 막힌 것
- (없음)
