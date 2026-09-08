# 요고비 AI 서버

Python 3.12 · FastAPI. 자연어 파싱과 결과 설명 담당.
**금액 계산은 하지 않는다** — 메인 백엔드(github.com/yogoB/BE_main)가 전담한다.

```bash
uv sync
uv run fastapi dev app/main.py   # http://localhost:8000/docs
uv run pytest
```

헬스체크: `GET /health` → `200 {"status": "ok"}`. API 키 없이 실행할 수 있다.

`POST /parse`는 `{"text":"데이터 20기가 정도 쓰고 넷플릭스 보고 싶어요"}`를 받는다.
`text`는 공백을 제외한 내용이 있어야 하며 최대 4,000자다.
실제 파싱은 `.env.example`을 참고해 `.env`에 `ANTHROPIC_API_KEY`를 설정한 뒤 실행한다.

```bash
uv run --env-file .env fastapi dev app/main.py
```

Claude의 [도구 입력 스키마](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)로
구조화된 응답을 받고 Pydantic으로 검증한다. 모델 호출은 `app/llm/client.py`를 거치며
프롬프트는 `app/prompts/parse.md`에 있다. 테스트는 모델 응답을 스텁하며 실제 API를 호출하지 않는다.

- `confidence < 0.7`: HTTP 200, `required`·`optional`은 `null`, 서버의 고정 확인 질문을 반환한다.
- 추출하지 못한 선택 항목은 `null`이며 임의 기본값을 채우지 않는다.
- 잘못된 요청은 HTTP 422, 키 누락·모델 호출 실패는 HTTP 503 (`detail.code: AI-LLM-001`).
- 잘못되거나 잘린 모델 응답은 HTTP 502 (`detail.code: AI-PARSE-001`, `detail.clarifyingQuestion` 포함).

계약에는 오류 코드만 정의되어 있어 HTTP 상태와 `detail` 외피는 현재 구현 기준이다.
백엔드 연동 시 이 오류 형식의 수신 처리를 확인한다.

| 문서 | 내용 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 원칙·코딩 규칙 (AI 에이전트 자동 로드) |
| [`docs/contract.md`](docs/contract.md) | 백엔드와의 API 계약 |
| [`docs/state.md`](docs/state.md) | 현재 진행 상황 |
