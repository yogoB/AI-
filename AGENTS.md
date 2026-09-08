# 요고비 AI 서버

Python 3.12 · FastAPI. 챗봇 입력을 구조화하고, 계산 결과를 자연어로 설명한다.
메인 백엔드는 별도 레포다: github.com/yogoB/BE_main

---

## 절대 원칙

1. **금액을 계산하지 않는다.** 더하기 빼기 곱하기 전부 금지.
   숫자는 백엔드가 준 값을 그대로 옮기기만 한다.
2. **금액을 지어내지 않는다.** 응답에 없는 요금·할인액을 문장에 넣지 않는다.
3. **무상태.** 대화 이력은 백엔드가 저장한다. 여기서 세션을 들지 않는다.
4. `confidence`가 낮으면 **되묻는다.** 추측해서 파라미터를 채우지 않는다.

이 4개를 어기면 서비스가 틀린 금액을 안내하게 된다. 예외 없다.

## 책임 범위

| 하는 것 | 안 하는 것 |
|---|---|
| 자연어 → `RecommendationRequest` 추출 | 금액 계산 |
| `CostBreakdown` → 자연어 설명 | 요금제 추천 로직 |
| 사용량 스크린샷 OCR (선택 기능) | DB 접근, 사용자 인증 |

## 엔드포인트

```
POST /parse    { "text": "..." }        → 파라미터 + confidence
POST /narrate  { "breakdown": {...} }   → 한국어 설명
POST /ocr      { "image": "base64" }    → 사용량 추출 (선택)
```

계약 상세는 `docs/contract.md`. **원본은 BE_main/docs/architecture.md이며, 이 레포의 것은 사본이다.**

## 실행

```bash
uv sync
uv run fastapi dev app/main.py   # :8000
uv run pytest
```

## 코딩 규칙

- LLM 출력은 **Pydantic 모델로 검증**한다. 문자열 정규식으로 뜯지 않는다.
- 프롬프트는 `app/prompts/` 아래 파일로 분리한다. 코드에 인라인 금지.
- 외부 모델 호출은 `app/llm/client.py` 한 곳만 거친다.
- 사용자 대면 문구는 `docs/contract.md` §5의 표현을 쓴다.
- 테스트는 실제 모델을 호출하지 않는다. 고정 응답으로 스텁한다.

## 스코프 밖 — 제안도 하지 말 것

- 금액 계산·추천 로직 (백엔드 담당)
- 벡터 DB, RAG, 파인튜닝, 에이전트 프레임워크 — 3주 프로젝트다. 하지 않는다.

## 세션 규칙

- 작업이 끝나면 `docs/state.md`를 갱신한다.
- 커밋 앞에 `[codex]` / `[claude]` / `[human]` 태그를 붙인다.
