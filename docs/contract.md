# 백엔드 계약

> **원본: `BE_main/docs/architecture.md`.** 이 파일은 사본이다.
> 원본이 바뀌면 같은 날 맞춘다. 여기서 먼저 바꾸지 않는다.

## 1. 흐름

```
사용자 발화
  → [BE_main] POST /api/v1/chat/messages
  → [BE_main] 내부 recommend 호출 (필터 경로와 동일 로직)
  → [내레이터] POST /narrate          결과 → 한국어 설명
  → 사용자
```

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
- `message`는 LLM을 쓰지 않는 고정 템플릿이다. 모델이 금액 문장을 만들지 못한다.
- `reasons`는 모델이 만들지만, 요청에 없는 금액이 섞인 줄은 서버가 버린다(`app/narrate.py`의 `AMOUNT` 가드).
  근거는 `breakdown` 항목뿐이므로 미사용 혜택은 사유에도 등장하지 않는다.
- **모델 키가 없거나 모델이 실패하면 `rule_reasons`가 같은 값으로 문장을 만든다**(D-38, 사용자 승인 2026-09-17).
  제휴 혜택 줄 → 할인 줄 → 절감액(월·연 한 문장) → 후보 수 순으로 최대 3문장이며
  `ESTIMATED` 줄은 근거로 쓰지 않는다. 후보 수 문장은 자리가 남을 때만 들어가 실제 혜택을 밀어내지 않는다.
  모델 문장과 같은 금액 가드를 통과한다 — 생성 방식이 달라도 나가는 규칙은 하나다.
  근거가 없으면 빈 배열도 가능하고 `message`는 어느 경우에도 정상이다.
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

**이 서버가 내는 코드는 위 하나뿐이다.** 모델을 호출하지 않으므로 모델 실패 코드가 없다(D-45).
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
  현재 응답 형태와 한 발화 처리 범위는 양쪽 README에 기록했다. 사용자별 이력 저장·조건 병합은 미연결이다.
- 2026-09-16: 카탈로그 후보 조회(`/catalog/candidates`)를 §5로 더했다.
- **2026-09-18: 그 절과 `/parse`·`/ocr` 절을 지웠다.** D-45 로 엔드포인트 자체가 사라졌다.
  이 서버의 라우터는 `/narrate`·`/narrate/detections`·`/health` 셋뿐이다(`app/main.py`).
  BE 의 카탈로그 자동 검토(D-29)가 아직 `/catalog/candidates` 를 부르고 있다면 **항상 실패한다** —
  그쪽 코드가 실제로 무엇을 호출하는지 확인이 필요하다.


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
