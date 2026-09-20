# 백엔드 계약

> **원본: `BE_main/docs/architecture.md`.** 이 파일은 사본이다.
> 원본이 바뀌면 같은 날 맞춘다. 여기서 먼저 바꾸지 않는다.
> **이 절들을 고쳤으면 `BE_main/scripts/contract_audit.py --ai <이 레포>` 를 돌려라** —
> 항목이 **통째로 빠진 것**을 잡는다(사본 없는 환경에서는 건너뛴다).
> **값이 바뀐 것은 못 잡는다.** 표지가 문서 어딘가에 한 번이라도 있으면 통과라,
> 실제로 크기 상한 `1MB`→`10MB` 와 대상 서비스 `iCloud+`→`OneDrive` 가 통과했다(2026-09-20 확인).
> **값 대조는 사람이 한다.** 이 검사기를 문장 대조 도구로 믿지 마라.
>
> **현재 명세는 §1 · 회원 인증 경계 · 개인정보 처리 경계 · §3 · §6 · §7 · §8 이다.**
> "연동 확인 사항"과 본문 중간의 `D-NN(날짜)` 줄은 **그때 무엇을 확인했고 왜 그렇게 정했는지**를
> 남긴 기록이다 — 덧붙이기만 하고 지우지 않는다. **날짜가 붙은 줄은 현재 동작이 아닐 수 있다.**
> 지금 모양은 위 절들이 옳다.

## 1. 흐름

내레이터가 불리는 길은 둘이다. **둘 다 BE 가 부른다 — 반대 방향은 없다.**

```mermaid
flowchart LR
    U["사용자: 결과 화면에서<br/>'설명 보기'"] -->|"POST /api/v1/recommendations/narrate"| B["BE_main"]
    B -->|"POST /narrate<br/>flycast 사설망"| N["내레이터"]
    N -->|"message · reasons · notices"| B
    H["BE 일일 수집 배치<br/>09:00 KST"] -->|"POST /operations/subscriptions/check"| N
    N -->|"월 정가 + 원문 근거"| H
    H --> P["카탈로그 대조 → 변경 제안<br/>→ 운영자 승인"]

    classDef ours fill:#dbeafe,stroke:#1d4ed8
    class B,N,H,P ours
```

- **설명 경로**는 결과를 만들 때가 아니라 사용자가 설명을 펼칠 때 돈다(D-50). 내레이터가 죽어도 결과 화면은
  그대로 나간다. BE 는 같은 요청 본문으로 1순위만 다시 계산해 넘긴다 — 무상태 경로라 그게 가장 단순하다.
- **운영 경로**는 사용자 요청과 무관한 배치다(D-60). 내레이터는 공식 페이지를 읽어 원문 그대로 인용만 하고,
  대조·제안·승인·저장은 BE 가 한다. 승인은 사람이 한다.
- 옛 챗봇 경로(`POST /api/v1/chat/messages` → `/parse` → `/narrate`)는 **D-44 로 폐기됐다.**
  BE 에 그 컨트롤러도 `chat/` 패키지도 없다. `/parse`·`/ocr` 도 D-45 로 함께 사라졌다.

**내레이터는 백엔드를 호출하지 않는다.** 백엔드가 내레이터를 호출한다. 단방향이다.
프론트는 BE_main API만 호출하며 내레이터에 직접 요청하지 않는다.
BE 원본 §2에 맞춰 두 서버가 비밀 환경 변수 `NARRATOR_INTERNAL_TOKEN`을 공유한다.
BE가 `Authorization: Bearer <NARRATOR_INTERNAL_TOKEN>`을 생성하며 프론트의 사용자 인증 헤더는 전달하지 않는다.
`/narrate`는 토큰 누락·불일치 시 401, 서버 토큰 미설정·잘못된 설정 시 503
(`detail.code: NARRATOR-AUTH-001`)으로 차단하고 모델을 호출하지 않는다. `/health`는 토큰 없이 사용한다.
프론트 참고 명세는 BE의 `docs/BE_API.md`에서 관리한다. AI의 실제 네트워크 접근 제한은 배포 시 적용한다.

## 회원 인증 경계 (2026-09-10, BE 원본 §3 반영)

비회원 추천·계산기·카탈로그는 계속 공개한다. 회원의 개인 데이터는 BE 내부 userId로 관리한다.
BE는 자체 가입·로그인과 Google OIDC를 제공하고 `/api/v1/me`에서 현재 회원만 조회한다.
회원 인증은 15분 HttpOnly JWT 쿠키 + 브라우저 확인 쿠키 + DB 발급 지문이며 회원 변경 요청은 CSRF가 필요하다.
자체 가입은 이메일 검증 토큰이 필요하고 비밀번호 재설정·로그인 세션 조회/폐기가 추가됐다. 가입 메일 요청만으로 계정은 생성되지 않으며 재설정은 CSRF 필수·자동 로그인 없음이다.
미존재·Google 전용 계정의 재설정 링크는 사용 불가. 회원 JWT는 절대 15분·유휴 5분, 세션 회수와 자격 증명 버전 검사를 적용한다.
상세는 BE `docs/auth.md`. 어느 것도 AI 요청/응답 필드에 영향 없다.
Google 로그인 시작/콜백은 `/oauth2/authorization/google`, `/login/oauth2/code/google`이다.
BE의 `/api/v1/auth/signup`, `/login`, `/logout`, `/logout-all`, `/csrf`, `/google/link`, `/password` 상세는
`BE_main/docs/auth.md`를 따른다. 계정은 이메일만으로 자동 병합하지 않고 기존 비밀번호·Google 재확인 후 연결한다.
Google 전용 회원은 동일 Google sub 재확인으로 자체 비밀번호를 추가한다.
**이 회원 쿠키·JWT·비밀번호·Google 토큰은 AI로 전달하지 않는다.** AI는 기존 NARRATOR_INTERNAL_TOKEN만 검증한다.
AI 모델·API 요청/응답에는 회원 인증 필드를 추가하지 않는다.

## 개인정보 처리 경계 (2026-09-12, BE V5/V6 반영)

`GET /api/v1/privacy-policy`는 공개이며 `GET /api/v1/me/consent`,
`POST /api/v1/me/consent/marketing {agree}`, `DELETE /api/v1/me`는 현재 회원 전용이다.
변경 요청에는 CSRF가 필요하다. 탈퇴하면 회원·인증·외부 구독 분석 데이터는 즉시 삭제한다.
`payment_record`는 외부 구독 내역만 저장하며 CASCADE와 12개월 분석 보유 정책을 유지한다.
별도 법정 보존 대상으로 확인된 사본만 BE 내부 `retained_payment_record`에 보관한다.
회원 FK·이메일·인증 정보는 복사하지 않고 기록별 법적 근거·기산일·확정 만료일을 요구한다.
자동 5년 보존이나 완전한 익명화를 뜻하지 않는다. 일반 회원 API 및 AI는 사본을 조회/설정하지 않는다.
상세 보유·파기 범위는 BE `docs/privacy.md`를 따른다. AI 런타임·요청/응답 필드는 변경되지 않는다.

## 3. POST /narrate

요청 (백엔드가 그대로 전달)
```json
{
  "planId": 42,
  "monthlyTotal": 71300, "baseline": 89000, "monthlySavings": 17700,
  "annualSavings": 212400,
  "planName": "5G 슬림+", "carrier": "SKT",
  "breakdown": [
    { "label": "5G 슬림+ 기본료", "amount": 55000, "provenance": "OFFICIAL" },
    { "label": "선택약정 25% 할인", "amount": -13750, "provenance": "DERIVED" },
    { "label": "가족결합 할인", "amount": -5000, "provenance": "USER_PROVIDED" },
    { "label": "넷플릭스 스탠다드", "amount": 13500, "provenance": "OFFICIAL",
      "note": "제휴 혜택으로 4,000원 할인 적용" }
  ],
  "missingInputs": [ { "field": "hasFamilyBundle", "impact": "...",
                       "howToFind": "통신사 마이페이지 > 결합 상품" } ],
  "candidateCount": 127,
  "currentMonthlyTotal": 72500
}
```

원본 §3의 `planId`, `annualSavings`, `missingInputs[].howToFind`, `candidateCount`를 수용한다.
`candidateCount`는 정렬 대상이 된 후보 요금제 수다. 기준 카탈로그 1,706개 중 1,645개는 제휴 혜택도
약정할인도 없어 절감액이 0이라, 그런 요금제에는 이 값이 사유의 유일한 근거다.
`missingInputs[].field`는 **값을 열거로 묶지 않는다.** BE가 안내 종류를 늘려도(`ageLimit`이 실제로 그랬다)
422로 설명 전체를 막지 않기 위해서다. 모르는 값은 그 항목만 문장에서 빠진다. 구조는 계속 엄격하게 본다.
기존 요청도 받을 수 있도록 위 3개 필드는 선택 값이다. 설명에 새 금액이나 문구를 추가하지 않는다.
백엔드는 설명할 `CostResult` 한 건과 상위 응답의 `missingInputs`를 합쳐 전달한다.
공통 응답 외피(`data`, `warnings`)와 `accuracy`, `results` 목록 전체는 요청에 포함하지 않는다.
D-55(2026-09-18): `/recommendations` 응답에 `minimalChange`(현재 통신사 안 최저)가 생겼다. `/narrate` 요청에는 싣지 않는다 — 설명 대상은 1순위 한 건 그대로다.
D-53(2026-09-18): 공개 `GET /api/v1/stats/savings`(랜딩 절감액 표본, 금액만)가 생겼다. 내레이터와 무관하며 `/narrate` 에 싣지 않는다.
D-51(2026-09-18): `CostResult`·`current` 에 `semiannualSavings`(×6)가 생겼고 `/me/saved-results` 가 신설됐다. **`/narrate` 요청에는 싣지 않는다** — BE 가 계약 필드만 남겨 보낸다.
D-50(2026-09-18): BE 는 `/narrate` 를 추천 응답 시점이 아니라 **프론트가 설명을 펼칠 때**(`POST /api/v1/recommendations/narrate`) 부른다. 이 서버의 계약·동작은 그대로다 — 호출 빈도만 줄고, 장애가 나도 결과 화면은 영향을 받지 않는다.
G-29·G-30(2026-09-17): `/recommendations` 응답에 `current`(지금 쓰는 요금제의 금액과 1순위 대비 절감액)가 생겼고,
요청에 `optional.currentPlanId`가 생겼다. **`/narrate` 요청에는 넣지 않는다** — 설명 대상은 여전히 1순위 한 건이다.
`missingInputs[].field`에 `currentPlanId`·`familyBundleDiscountKrw`가 올 수 있다. 열거로 묶지 않는 설계 그대로,
모르는 필드는 그 항목만 문장에서 빠지고 `notices`에는 `impact`가 그대로 실린다.
D-18(2026-09-15): BE의 우체국·스마트초이스 연동과 `CostResult.priceCrossCheck` 필드를 제거했다.
D-20(2026-09-16, 사용자 승인): `priceCrossCheck`가 `/recommendations` 응답에 **복구됐다**. `/narrate` 요청에는 넣지 않는다 — BE가 계약 9필드만 남겨 보낸다(`AiGateway.NARRATE_FIELDS`). `extra=forbid`는 유지한다. 계약 밖 필드는 422로 거부하며, 그 거부가 곧 표류 감지다(BE는 422를 장애로 삼켜 사유가 조용히 사라지므로 BE 쪽에서 막는다). 카탈로그는 검수·승인된 CSV를 DB에 반영한다. 정보 오류 제보 `POST /api/v1/catalog/reports`는 BE가 접수만 하며 AI 호출이나 가격 자동 수정은 하지 않는다.

응답
```jsonc
{
  "message": "“SKT 5G 슬림+”의 실제 내시는 금액은 월 71,300원이에요. ...",  // 고정 템플릿
  "reasons": [                                                            // 0~3개. 모델 또는 규칙
    "따로 내시던 넷플릭스 스탠다드 13,500원이 요금제에 포함돼 있어요.",
    "선택약정 25% 할인으로 월 13,750원이 빠져요."
  ]
}
```

D-19(2026-09-16): `reasons`를 응답에 더했다. 요청 필드는 그대로다. 원본 승인 반영본이다.

### 규칙

- **`breakdown`에 없는 금액을 문장에 넣지 않는다.** 합계를 다시 계산하지도 않는다.
- **`message`도 `reasons`도 모델이 만들지 않는다.** 둘 다 규칙이 만드는 고정 템플릿이다(D-45).
  같은 입력이면 항상 같은 문장이 나온다 — 재현되지 않는 설명은 금액 서비스에서 신뢰를 깎는다.
- `reasons`는 `rule_reasons()` 가 만든다(D-38, 사용자 승인 2026-09-17).
  제휴 혜택 줄 → 할인 줄 → 절감액(월·연 한 문장) → 후보 수 순으로 최대 3문장이며
  `ESTIMATED` 줄은 근거로 쓰지 않는다. 후보 수 문장은 자리가 남을 때만 들어가 실제 혜택을 밀어내지 않는다.
  근거가 없으면 빈 배열도 가능하고 `message`는 어느 경우에도 정상이다.
- 요청에 없는 금액이 섞인 줄은 나가기 전에 버린다(`app/narrate.py` 의 `AMOUNT` 가드).
  모델이 없어진 지금도 가드는 남겨 둔다 — 문장을 새로 쓸 때 실수로 숫자를 넣는 것을 막는 자리다.
  근거는 `breakdown` 항목뿐이므로 미사용 혜택은 사유에도 등장하지 않는다.
- **`currentMonthlyTotal`(선택, 원)** — 지금 쓰는 요금제로 같은 구독을 유지했을 때의 실질월비용
  (BE 응답 `current.cost.monthlyTotal`). BE 는 `optional.currentPlanId` 를 받았을 때만 싣고,
  없으면 필드 자체를 넣지 않는다. **있으면 절감의 기준이 정가가 아니라 지금이다.**
  - `monthlyTotal < currentMonthlyTotal` → "지금 내시는 월 {현재}원보다 월 {차}원 덜 내요."
  - 같으면 → "지금 내시는 금액과 같아요."
  - 크면 → "지금보다 월 {차}원 더 내는 조합이에요 — 원하시는 데이터·구독을 다 담으면 이렇게 돼요."
  - 정가 문장 두 개("아무 할인 없이 정가로…"·"월 N원 절약…")는 **이 필드가 없을 때만** 나간다.
    같은 화면에서 두 기준이 싸우지 않게 한다 — 히어로가 "지금보다 얼마"인데 문장이
    "절약되는 금액은 없어요"라고 말하던 모순이 이 필드가 생긴 이유다(사용자 승인 2026-09-18).
- **`currentMonthlySavings`(선택, 원, 음수 가능)** — 지금 대비 절감액(BE 응답 `current.monthlySavings`
  = 현재 − 추천). 이 값이 오면 내레이터는 **빼지 않고 옮기기만 한다.** 절대 원칙 1을 글자 그대로 지킨다.
  - 아직 안 오는 동안만 직접 뺀다(과도기). BE 배포가 끝나면 그 분기를 지운다.
  - `currentMonthlyTotal` 과 함께 왔는데 **두 수가 서로 빼지지 않으면 '지금' 기준으로 말하지 않고**
    정가 기준으로 물러난다. 422 로 설명 전체를 죽이지 않는다 — 안 맞는 두 수를 나란히 놓느니 덜 말한다.
  - 사유의 절감액은 계속 `baseline` 기준이라 문구를 "정가보다"로 바꿨다. 같은 화면에서
    message 의 '지금'과 사유의 '지금'이 다른 수를 가리키던 것을 없앴다.
- `provenance`는 BE `Provenance` enum 의 **4값**이다: `OFFICIAL`·`DERIVED`·`USER_PROVIDED`·`ESTIMATED`.
  가족결합 할인은 `USER_PROVIDED` 로 온다(`FamilyBundleDiscountRule`) — 통신사별 결합 할인표가
  카탈로그에 없어 사용자가 적어 준 금액을 그대로 쓰기 때문이다.
- **값을 열거로 묶지 않는다.** BE 가 출처를 늘릴 때마다 422 가 났고(`USER_PROVIDED` 가 실제로 그랬다 —
  가족결합 사용자 전원의 설명이 사라졌다) BE 가 장애로 삼켜 아무도 알아채지 못했다.
  모르는 출처는 문장을 붙이지 않고 사유의 근거로도 쓰지 않는다. 구조는 계속 엄격하게 본다.
- 우리가 계산하지 않은 값은 밝힌다. `ESTIMATED` 는 "추정치예요", `USER_PROVIDED` 는 "적어 주신 금액이에요".
  `ESTIMATED` 는 사유의 근거로 쓰지 않는다(확정된 할인처럼 읽힌다). `USER_PROVIDED` 는 사용자가 확인한
  확정 금액이라 근거로 쓴다.
- `missingInputs`가 있으면 마지막에 무엇을 더 알려주면 정확해지는지 한 문장 덧붙인다.
- 3~5문장. 표나 목록을 만들지 않는다. 화면이 이미 보여준다.

## 6. 사용자 대면 문구

표현이 흔들리면 아마추어처럼 보인다. 아래를 고정해서 쓴다.

| 개념 | 쓸 표현 | 쓰지 말 것 |
|---|---|---|
| `monthlyTotal` | "실제 내시는 금액" | "총비용", "실질비용" |
| `monthlySavings` | "월 N원 절약" | "N원 이득", "세이브" |
| `ESTIMATED` | "추정치예요" | "약", "~쯤" |
| 미사용 혜택 | "안 쓰시는 혜택은 계산에서 뺐어요" | "무가치", "쓸모없는" |
| 해지 제안 | "정리해 보실래요?" | "해지하세요", "낭비 중입니다" |

## 7. 에러

백엔드가 이 코드로 받는다.

| 코드 | 상황 |
|---|---|
| `NARRATOR-AUTH-001` | 내부 토큰 누락·불일치 401, 서버 토큰 미설정 503 (`app/main.py`) |
| `CATALOG-SOURCE-UNAVAILABLE` | 공식 페이지를 읽지 못함 502 (§8) |
| `CATALOG-SOURCE-CHANGED` | 페이지는 읽었으나 값을 확인하지 못함 502 (§8) · 행 단위 실패 사유 (§9) |
| `CATALOG-SOURCE-UNAVAILABLE` | §9 에서는 행 단위 실패 사유로도 쓴다 (200 안의 `failures`) |

**설명 경로(`/narrate*`)가 내는 코드는 첫 줄 하나뿐이다.** 모델을 호출하지 않으므로 모델 실패 코드가 없다(D-45).
뒤의 둘은 §8 운영 조회 전용이며 사용자 화면에 닿지 않는다.
계약 위반은 코드가 아니라 **422**(Pydantic 검증)로 나가고, BE 는 그것을 `Unavailable` 로 흡수해
설명 없이 금액만 내보낸다. 422 는 계약이 어긋났다는 신호이지 사용자에게 보여줄 오류가 아니다.

> 옛 코드(`AI-PARSE-001`·`AI-LLM-001`·`AI-OCR-001`·`AI-CATALOG-001`)는 `/parse`·`/ocr`·
> `/catalog/candidates` 와 함께 D-45 로 사라졌다. BE 에 그 코드를 처리하는 분기가 남아 있다면 죽은 코드다.

## 연동 확인 사항 (2026-09-08)

- BE `RecommendationRequest.Required.monthlyDataGb`는 `Integer`이며 추천 서비스는 양수만 허용한다.
- BE 추천 서비스는 `wantedServiceIds`가 비어 있으면 거절한다.
- 테스트용 실제 BE로 추천 → `/narrate` 금액 보존을 검증했다.
  AI는 TestClient, 모델만 고정 응답이며 BE·PostgreSQL은 실제 프로세스다.
- 2026-09-09: BE 단일 발화 게이트웨이의 실제 HTTP 왕복·되묻기·필터 폴백 검증 완료(모델만 스텁).
  **이 경로는 D-44(챗봇 폐기)·D-45(모델 제거)로 지금은 없다.** 되묻기(`confidence`)도 `/parse` 기능이라
  함께 사라졌다 — 코드에 `confidence` 는 0건이다. 당시 검증 기록으로만 남긴다.
- 2026-09-16: 카탈로그 후보 조회(`/catalog/candidates`)를 §5로 더했다.
- **2026-09-18: 그 절과 `/parse`·`/ocr` 절을 지웠다.** D-45 로 엔드포인트 자체가 사라졌다.
- **2026-09-20: 확인 완료 — BE 는 `/catalog/candidates` 를 부르지 않는다.**
  `CatalogDailyHarvest` 에 AI 클라이언트가 주입돼 있지 않다(생성자 인자 4개 전부 DB·제안·관리자·상한).
  클래스 주석에 남은 `POST /catalog/candidates` 한 줄은 죽은 문장이다 — BE 쪽에서 지우면 된다.
  대신 그 구독 수집 자리는 §8 이 채운다.
  현재 라우터는 `/narrate`·`/narrate/detections`·`/narrate/switch-timing`·
  `/operations/subscriptions/check`·`/health` 다섯이다(`app/main.py`).


## D-46 반영 (2026-09-17)

원본 `BE_main/docs/architecture.md` §3의 같은 날 변경을 옮긴 사본이다.

```jsonc
// 내레이터: POST /narrate 200
{
  "message": "“SKT 5G 슬림+”의 실제 내시는 금액은 월 71,300원이에요. ...",  // 고정 템플릿
  "reasons": [                                                            // 0~3개. 규칙이 만든다
    "따로 내시던 넷플릭스 스탠다드 13,500원이 요금제에 포함돼 있어요.",
    "선택약정 25% 할인으로 월 13,750원이 빠져요."
  ],
  "notices": [                                                            // 0~10개. 화면 ⓘ 안내 (D-46)
    "가족 결합 시 최대 11,000원 추가 절감 가능 — 통신사 마이페이지 > 결합 상품"
  ]
}
```

`notices`는 `missingInputs`의 `impact`·`howToFind`를 **고쳐 쓰지 않고 이은 것**이다(D-46). 화면이 조립하던 것을
서버로 모아 표현이 갈라지지 않게 한다. 300자를 넘는 줄은 자르지 않고 통째로 뺀다 — 잘린 안내는 오해를 만든다.

```jsonc
// 내레이터: POST /narrate/detections 200  (D-46)
// 요청 { "findings": [{ "rule", "targetName", "wastedAmount", "provenance" }] }
{
  "lines": [{ "title": "요금제에 포함된 구독을 따로 결제 중", "target": "넷플릭스",
              "amount": "월 13,500원",            // ESTIMATED 면 "최대 월 13,500원"
              "how": "요금제 혜택으로 이미 제공돼요. 개별 결제를 해지하면 그만큼 줄어요." }],
  "summary": "겹치는 결제 1건을 찾았어요. 해지·변경은 각 서비스에서 직접 해주세요 — 요고비는 금액만 알려드려요."
}
```

대상 이름은 **BE가 카탈로그에서 찾아 넘긴다** — 내레이터는 카탈로그를 모른다. 모르는 규칙 코드는 제목에
그대로 두고 `how`를 비운다. 새 규칙이 화면에서 조용히 사라지지 않게 하기 위해서다.
요약은 **건수만** 말한다. 금액을 더하는 순간 계산이고 계산은 BE 몫이다(절대 원칙 2).


## 8. POST /operations/subscriptions/check (D-60, 2026-09-20)

구독 상품의 **공식 페이지에 적힌 월 정가**를 원문 근거와 함께 돌려준다.
D-45 로 구독 수집이 멈춘 뒤 `CatalogDailyHarvest` 의 구독 쪽이 비어 있었다 — 그 자리를 채운다.

**읽고 인용만 한다.** 우리 카탈로그 값과의 대조·변경 제안·승인·저장은 전부 BE 몫이다.
가격을 계산하지도, 고치지도, 기억하지도 않는다(절대 원칙 2·3).

```jsonc
// 요청
{ "serviceName": "Apple Music" }   // Spotify | Apple Music | iCloud+ 셋 중 하나. 그 외는 422
```

```jsonc
// 200
{
  "serviceName": "Apple Music",
  "sourceUrl": "https://www.apple.com/kr/apple-music/",  // 서버가 고른다. 요청으로 받지 않는다
  "checkedAt": "2026-09-20T02:11:48.512Z",
  "sourceHash": "3f9c…",                                  // 원문 sha256. 같으면 페이지가 안 바뀐 것
  "offers": [
    { "tierName": "개인", "price": 8900, "currency": "KRW", "billingPeriod": "MONTH",
      "evidence": "개인 ₩8,900/월" }                      // 페이지에서 오린 원문 그대로
  ]
}
```

### 규칙

- **URL 을 요청으로 받지 않는다.** 대상은 코드에 박아 둔 3개뿐이고 리다이렉트도 따르지 않는다 —
  내부망으로 우회시킬 입력을 애초에 만들지 않는다. HTML 이 아니거나 1MB 를 넘으면 읽지 않는다.
- **한 상품의 값이 유일하지 않으면 통째로 버린다.** 프로모션 문구와 정가가 섞여 서로 다른 숫자가 보이면
  하나를 고르지 않고 502 로 실패한다. 틀린 가격을 조용히 내보내느니 사람이 보는 편이 싸다.
- **나라를 섞지 않는다.** iCloud+ 세계 가격표에서는 대한민국 블록만 읽고, 못 찾으면 다른 통화로 넘어가지 않는다.
- **호출 간격은 BE 가 지킨다.** 부르면 매번 원본을 읽는다 — 캐시도 쿨다운도 없다(무상태).
  일 단위 점검을 전제로 한 엔드포인트다. 반복 호출은 우리 UA 가 차단되는 방식으로 되갚는다.
- 실패는 `offers` 없이 502 다. 200 이면 `offers` 가 반드시 1개 이상 있다.
- **코드는 `detail.code` 에 있다.** 최상위 `code` 도 `error.code` 도 아니다 — FastAPI 가 `HTTPException`
  을 `{"detail": ...}` 로 감싸기 때문이고, §7 의 `NARRATOR-AUTH-001` 401 과 같은 모양이다. 실제 본문:

```jsonc
// 502 — 페이지를 못 읽음
{ "detail": { "code": "CATALOG-SOURCE-UNAVAILABLE",
              "message": "공식 페이지를 읽지 못했습니다. 잠시 후 다시 실행해 주세요." } }
// 502 — 읽었지만 한 상품의 월 정가가 유일하지 않음
{ "detail": { "code": "CATALOG-SOURCE-CHANGED",
              "message": "상품별 월 정가를 확인하지 못했습니다. 공식 페이지와 추출 규칙을 확인해 주세요." } }
```

  엉뚱한 자리에서 코드를 찾으면 둘이 한 코드로 뭉개진다 — `CHANGED`(사람이 봐야 함)와
  `UNAVAILABLE`(그냥 재시도)를 가르는 것이 이 엔드포인트의 유일한 운영 신호다.
- **허용 오차 0.** BE 는 우리 값과 1원이라도 다르면 변경 제안을 만든다. 요금제의 100원 오차와 다른데,
  그 행의 `official_url` 이 가리키는 **바로 그 페이지**에서 찍힌 정수를 그대로 읽어 반올림이 없기 때문이다.
  100원을 허용하면 iCloud+ 50GB 1,100원의 9%가 묻힌다 — 싼 상품일수록 못 잡는다.
- **`tierName` 은 카탈로그 `subscription_tier.name` 과 같은 문자열이다.** 2026-09-20 기준 11개 전부 일치한다.
  BE 는 (`serviceName`, `tierName`)으로 바로 붙이면 된다 — 매핑표가 필요 없다.
- **BE 는 응답의 `sourceUrl` 을 자기 `subscription_service.official_url` 과 비교한다.** 다르면 제안을
  만들지 말고 실패시킨다. URL 을 요청으로 받지 않는 대신 같은 값이 두 레포에 산다 — 그 드리프트를 잡는 자리다.
- **페이지가 바뀌면 조용히 틀리지 않고 멈춘다.** 추출 규칙은 페이지 문구에 맞춰 둔 것이라 언젠가 깨진다.
  깨지면 `CATALOG-SOURCE-CHANGED` 가 나고, 카탈로그는 사람이 고칠 때까지 예전 값 그대로다(fail-soft).

> **원본 반영 완료(2026-09-20).** `BE_main/docs/architecture.md` §2 에 "구독 공식가 조회(D-60)" 절이
> 사람 승인을 받아 들어갔다. 이 절은 그 사본이며 대조해 어긋나는 데가 없음을 확인했다.


## 9. POST /operations/plans/promotions/check (2026-09-21 · 사본 선반영)

알뜰폰 **기간 한정 특가**를 공식 상품 페이지에서 읽어 원문 근거와 함께 돌려준다.
CSV `mobile_plan_promo` 13행이 2026-09-16 수집분으로 멈춰 있었고 `regular_price` 가 전부 비어 있었다.
그 칸을 매일 채우는 자리다. §8 과 경계가 같다 — **읽고 인용만 한다.**

> 2026-09-21: BE 가 13행을 **한 번 수동으로** 채웠다(v83). 배치 연결은 아직이라, 특가가 바뀌면 다시 어긋난다.

```jsonc
// 요청 — URL 이 아니라 상품 번호다. 1~30개, 양의 정수
{ "productIds": [7752, 7744] }
```

```jsonc
// 200
{
  "checkedAt": "2026-09-21T02:11:48.512Z",
  "promotions": [
    { "productId": 7752,
      "carrier": "A모바일(에넥스텔레콤)",          // CSV `mobile_plan_promo.carrier` 와 같은 표기
      "planName": "[Npay 5천] 10GB/100분 (6개월)",  // 같은 표기
      "network": "LGU+",                            // SKT | KT | LGU+
      "promoMonths": 6,
      "regularPrice": 13200,                        // **N개월 이후**의 월 요금. 오를 수도 내릴 수도 있다
      "sourceUrl": "https://www.mvnohub.kr/product/products/7752.do",
      "sourceHash": "3f9c…",
      "evidence": "월 19,800 원 6개월 이후 13,200 원/월" }
  ],
  "failures": [ { "productId": 9999, "code": "CATALOG-SOURCE-UNAVAILABLE" } ]
}
```

### 규칙

- **URL 을 요청으로 받지 않는다.** 상품 번호(정수)만 받아 고정 템플릿에 끼운다. 리다이렉트도 안 따른다(§8 과 동일).
- **목록을 훑지 않는다.** 출처 사이트에 봇 차단 시스템(TRACER)이 붙어 있어, 매일 100여 장을 도는
  발견형 크롤링은 만들지 않았다. **BE 가 이미 아는 상품 번호를 다시 확인하는 갱신 전용**이다.
  새 특가를 찾는 일은 사람이 한다 — 카탈로그 검수 절차가 원래 그렇다.
- **한 번의 호출이 여러 장을 읽으므로 간격은 이 서버가 지킨다**(1.2초). §8 과 다른 점이다.
  30개면 약 36초가 걸리니 BE 읽기 제한을 그보다 길게 잡는다.
- **`regularPrice` 는 "N개월 이후 B원/월" 의 B 다. 이름과 달리 "정가" 가 아니다.**
  13건 중 5건은 B 가 지금 금액보다 **싸다**(장기할인·약정형 — 특가가 끝나는 게 아니라 나중에 내려간다).
  그래서 이 값을 쓰는 문구는 **방향을 단정하면 안 된다.** "특가가 끝나요" 가 아니라
  "N개월 뒤에는 월 M원으로 바뀌어요" 다(BE 가 2026-09-21 에 그렇게 고쳤다).
- **페이지 머리말의 앞 숫자(`월 19,800 원`)는 싣지 않는다.** 확인해 보니 그 값은 그 페이지의
  `월 납부총액` 이고 **`mobile_plan.base_price` 와 13건 모두 일치한다** — BE 가 이미 아는 값이라 중복이다.
  처음엔 "정가인지 특가인지 몰라서" 안 실었는데, 알고 나서도 안 실을 이유가 생겼다.
- **상품명의 숫자는 더더욱 싣지 않는다.** 7743 은 이름이 "12개월간 10원!" 인데 같은 페이지의
  결제 정보는 `월 납부총액 27,500원`, 요금 문구는 `12개월 이후 4,800원/월` 이다.
  **한 페이지 안에서 세 숫자가 서로 다르다.** 이름은 판매 문구이고 값이 아니다.
- **그 상품 자신의 머리말만 읽는다.** 페이지에 비교 위젯이 있어 **다른 요금제**의 가격 줄이 같이 실린다
  (13개 중 3개가 그랬다). "알뜰폰 허브 소개" 바로 뒤 200자 안의 줄만 쓴다.
- **실패는 행 단위다.** 한 상품을 못 읽어도 나머지는 돌아온다. `failures` 에 번호와 사유가 실린다.
  **BE 는 `failures` 에 오른 번호의 기존 행을 건드리지 않는다** — 못 읽은 것과 특가가 끝난 것은 다르다.
  행이 사라진 것으로 보고 지우면 멀쩡한 특가가 조용히 없어진다. 배치 연결 시 이 자리에 테스트를 둔다.
- 2026-09-21 기준 CSV 13행 전부 성공했고 `promoMonths`·`carrier`·`planName` 이 CSV 와 한 건도 어긋나지 않았다.

> **원본 미반영.** 이 절은 아직 `BE_main/docs/architecture.md` 에 없다. 새 엔드포인트라 기존 계약을
> 건드리지는 않지만 원본에도 같은 절이 들어가야 한다 — 크로스 레포 규칙대로 사람 승인 뒤에 옮긴다.
