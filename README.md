# 요고비 내레이터

Python 3.12 · FastAPI. **백엔드가 계산한 금액을 한국어 문장으로 바꾼다.** 모델을 쓰지 않는다(D-45).
**금액 계산은 하지 않는다** — 메인 백엔드(github.com/yogoB/BE_main)가 전담한다.
대화 이력도 저장하지 않는다. 무상태다.

계약 상세는 [`docs/contract.md`](docs/contract.md).
**원본은 `BE_main/docs/architecture.md` 이며 이 레포의 것은 사본이다** — 원본이 바뀌면 같은 날 맞춘다.

## 엔드포인트

| | 하는 일 | 계약 |
|---|---|---|
| `POST /narrate` | `CostBreakdown` → 설명 문장 · 사유 0~3줄 · 안내 0~10줄 | §3 |
| `POST /narrate/detections` | 중복 결제 탐지 결과 → 사람 문장 (D-46) | D-46 절 |
| `POST /narrate/switch-timing` | 변경 시점 → 사람 문장 | §3 |
| `POST /operations/subscriptions/check` | 구독 공식 페이지의 월 정가 + 원문 근거 (D-60) | §8 |
| `POST /operations/plans/promotions/check` | 알뜰폰 기간 한정 특가 + 원문 근거 | §9 |
| `GET /health` | `200 {"status": "ok"}` · 토큰 없이 공개 | — |

앞의 셋은 **설명**이다. BE 가 준 숫자를 문장으로 옮길 뿐 새 숫자를 만들지 않는다.
뒤의 둘은 **운영 조회**다. 등록된 공식 페이지를 읽어 원문 그대로 인용하며,
대조·변경 제안·승인·저장은 전부 BE 가 한다. 여기서도 금액을 만들지 않는 것은 같다.

## 흐름

```mermaid
flowchart LR
    U["사용자: '설명 보기'"] -->|"POST /api/v1/recommendations/narrate"| B["BE_main"]
    B -->|"POST /narrate<br/>flycast 사설망"| N["내레이터"]
    N -->|"message · reasons · notices"| B
    H["BE 일일 수집 배치<br/>09:00 KST"] -->|"POST /operations/**"| N
    N -->|"월 정가 · 특가 + 원문 근거"| H

    classDef ours fill:#dbeafe,stroke:#1d4ed8
    class B,N,H ours
```

**BE_main 이 내레이터를 호출한다. 반대 방향은 없다.** 프론트는 BE_main API 만 부르며
내레이터 주소와 내부 토큰을 받지 않는다. 내레이터가 죽어도 결과 화면은 그대로 나간다(D-50).
프론트 연동 명세는 `BE_main/docs/BE_API.md` 에 있다.

## 구성

```
app/
├── main.py                 라우터 등록 · 내부 토큰 인증 (33줄)
├── narrate.py              설명 문장 · 규칙 기반 사유 · 금액 가드 (274줄)
├── detections.py           중복 결제 설명 (84줄)
├── switch_timing.py        변경 시점 설명 (78줄)
├── subscription_check.py   구독 공식가 조회 (125줄)
└── plan_promotions.py      알뜰폰 특가 조회 (132줄)
```

테스트 **104개 통과 · 1개 건너뜀**(`tests/test_narrate.py` 73 · `tests/test_switch_timing.py` 11 ·
`tests/test_detections.py` 7 · `tests/test_subscription_check.py` 4 · `tests/test_plan_promotions.py` 5 · 그 외 5).
외부를 부르는 테스트는 없다 — 공식 출처 조회도 고정 HTML 과 `httpx.MockTransport` 로만 돈다.

## 실행

```bash
uv sync
uv run fastapi dev app/main.py   # http://localhost:8000/docs
uv run pytest
```

API 키 없이 실행할 수 있다. 외부 모델을 호출하는 코드가 없다(D-45).

## 인증

내레이터와 BE_main 의 환경 변수 `NARRATOR_INTERNAL_TOKEN` 에 **동일한 임의 토큰**을 설정한다.
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` 로 한 번 생성해
두 서버의 로컬 `.env` 또는 배포 비밀값에 넣는다. ASCII 공백 없는 토큰을 쓴다.
백엔드는 매 요청에 `Authorization: Bearer <NARRATOR_INTERNAL_TOKEN>` 을 붙인다.
사용자 로그인 토큰과 별개이며 프론트의 Authorization 헤더는 내레이터로 전달하지 않는다.

- `/health` 를 뺀 **모든 엔드포인트**가 막힌다. 토큰 누락·불일치는 401, 서버 토큰 미설정은 503.
- 오류 본문은 `{"detail": {"code": "NARRATOR-AUTH-001"}}` 다 — FastAPI 가 `detail` 로 감싼다.
  **코드는 `detail.code` 에 있다.** 구독 조회의 502 두 코드도 같은 모양이다(계약 §7·§8).
- `/docs` 의 Authorize 에는 내부 토큰만 입력한다.

## 배포 (Fly.io · 사설 전용)

**인터넷에 노출하지 않는다.** 공개 IP 없이 flycast 하나만 두고 BE(`yogob-api`)만 부른다.

```bash
fly deploy --ha=false -a yogob-narrator
fly ips list -a yogob-narrator     # private ingress 한 줄만 나와야 한다
```
