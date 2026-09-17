# 백엔드 계약

> **원본: `BE_main/docs/architecture.md`.** 이 파일은 사본이다.
> 원본이 바뀌면 같은 날 맞춘다. 여기서 먼저 바꾸지 않는다.

## 1. 흐름

```
사용자 발화
  → [BE_main] POST /api/v1/chat/messages
  → [AI] POST /parse            자연어 → 파라미터
  → [BE_main] 내부 recommend 호출 (필터 경로와 동일 로직)
  → [AI] POST /narrate          결과 → 한국어 설명
  → 사용자
```

**AI 서버는 백엔드를 호출하지 않는다.** 백엔드가 AI 서버를 호출한다. 단방향이다.
프론트는 BE_main API만 호출하며 AI 서버에 직접 요청하지 않는다.
BE 원본 §2에 맞춰 두 서버가 비밀 환경 변수 `AI_INTERNAL_TOKEN`을 공유한다.
BE가 `Authorization: Bearer <AI_INTERNAL_TOKEN>`을 생성하며 프론트의 사용자 인증 헤더는 전달하지 않는다.
`/parse`·`/narrate`·`/ocr`·`/catalog/candidates`는 토큰 누락·불일치 시 401, 서버 토큰 미설정·잘못된 설정 시 503
(`detail.code: AI-AUTH-001`)으로 차단하고 모델을 호출하지 않는다. `/health`는 토큰 없이 사용한다.
프론트 참고 명세는 BE의 `docs/BE_API.md`에서 관리한다. AI의 실제 네트워크 접근 제한은 배포 시 적용한다.

## 회원 인증 경계 (2026-09-10, BE 원본 §3 반영)

비회원 추천·계산기·카탈로그·단일 발화 챗봇은 계속 공개한다. 회원의 개인 데이터는 BE 내부 userId로 관리한다.
BE는 자체 가입·로그인과 Google OIDC를 제공하고 `/api/v1/me`에서 현재 회원만 조회한다.
회원 인증은 15분 HttpOnly JWT 쿠키 + 브라우저 확인 쿠키 + DB 발급 지문이며 회원 변경 요청은 CSRF가 필요하다.
자체 가입은 이메일 검증 토큰이 필요하고 비밀번호 재설정·로그인 세션 조회/폐기가 추가됐다. 가입 메일 요청만으로 계정은 생성되지 않으며 재설정은 CSRF 필수·자동 로그인 없음이다.
미존재·Google 전용 계정의 재설정 링크는 사용 불가. 회원 JWT는 절대 15분·유휴 5분, 세션 회수와 자격 증명 버전 검사를 적용한다.
상세는 BE `docs/auth.md`. 어느 것도 AI 요청/응답 필드에 영향 없다.
Google 로그인 시작/콜백은 `/oauth2/authorization/google`, `/login/oauth2/code/google`이다.
BE의 `/api/v1/auth/signup`, `/login`, `/logout`, `/logout-all`, `/csrf`, `/google/link`, `/password` 상세는
`BE_main/docs/auth.md`를 따른다. 계정은 이메일만으로 자동 병합하지 않고 기존 비밀번호·Google 재확인 후 연결한다.
Google 전용 회원은 동일 Google sub 재확인으로 자체 비밀번호를 추가한다.
**이 회원 쿠키·JWT·비밀번호·Google 토큰은 AI로 전달하지 않는다.** AI는 기존 AI_INTERNAL_TOKEN만 검증한다.
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

## 2. POST /parse

요청
```json
{ "text": "데이터 20기가 정도 쓰고 넷플릭스 보고 싶어요" }
```

응답
```json
{
  "required": { "monthlyDataGb": 20, "wantedServiceIds": [1] },
  "optional": { "currentCarrier": "SKT", "networkType": "5G",
                "contractType": null, "hasFamilyBundle": null },
  "confidence": 0.92,
  "clarifyingQuestion": null
}
```

- `confidence < 0.7` 이면 `clarifyingQuestion`을 채우고 나머지는 `null`로 둔다.
- 추출 못 한 optional 필드는 **`null`로 둔다.** 기본값을 지어내지 않는다.
- 현재 BE 구현(`RecommendationRequest.Required`, `RecommendationService.recommend`)에 맞춰
  `monthlyDataGb`는 1~2147483647의 정수, `wantedServiceIds`는 최소 1개로 검증한다.
- BE 의 `Optional` 에는 `familyLineCount`·`familyBundleDiscountKrw` 두 필드가 더 있다(2026-09-17).
  **`/parse` 는 이 둘도 만들지 않는다** — 자연어에서 "가족결합 할인 얼마" 를 뽑지 않는다. 화면 입력 전용이다.
  `familyBundleDiscountKrw` 는 사용자가 적어 준 금액이라 `USER_PROVIDED` 이고, 선택약정 25% 뒤에 정액으로 빠진다.
- BE 의 `Required` 에는 `wantedTierIds`(선택)가 하나 더 있지만 **`/parse` 는 그것을 만들지 않는다.**
  자연어는 "넷플릭스" 까지지 "넷플릭스 프리미엄" 을 가리지 않기 때문이다. BE 가 `null` 로 채워 보내고
  서버가 대표 등급(스탠다드 우선)을 고른다. 이 응답 스키마는 그대로다 — 필드를 추가하지 않는다.
- 소수 사용량·0·명시적인 구독 없음은 임의 보정 없이 추천용 조건을 되묻는다.
  높은 confidence로 범위 밖 값이 오면 HTTP 502, `detail.code: AI-PARSE-001`과 고정 확인 질문을 반환한다.
- 확인 질문: "추천에 사용할 월 데이터 용량을 1GB 이상의 정수로, 원하는 구독 서비스를 1개 이상 알려주시겠어요?"
- `wantedServiceIds`는 아래 고정 ID를 쓴다.

| ID | 서비스 | ID | 서비스 |
|---|---|---|---|
| 1 | 넷플릭스 | 4 | 웨이브 |
| 2 | 디즈니+ | 5 | 왓챠 |
| 3 | 티빙 | 6 | 유튜브 프리미엄 |

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
    { "label": "넷플릭스 스탠다드", "amount": 13500, "provenance": "OFFICIAL",
      "note": "제휴 혜택으로 4,000원 할인 적용" }
  ],
  "missingInputs": [ { "field": "hasFamilyBundle", "impact": "...",
                       "howToFind": "통신사 마이페이지 > 결합 상품" } ],
  "candidateCount": 127
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
- `provenance`가 `ESTIMATED`인 항목은 "추정치예요"를 붙인다.
- `missingInputs`가 있으면 마지막에 무엇을 더 알려주면 정확해지는지 한 문장 덧붙인다.
- 3~5문장. 표나 목록을 만들지 않는다. 화면이 이미 보여준다.

## 4. POST /ocr (선택 기능)

통신사 앱 사용량 화면 스크린샷 → 사용량 추출.

```json
// 응답
{ "monthlyDataGb": 18.4, "monthlyVoiceMin": 120, "monthlySmsCount": 30,
  "confidence": 0.8 }
```

추출값은 백엔드에서 **`ESTIMATED`로 처리되고 사용자 확인을 거친다.**
읽히지 않은 항목은 `null`로 둔다. 0으로 채우지 않는다.
OCR의 소수 사용량·0은 그대로 보존한다. 추천 API가 요구하는 정수 조건과 원하는 구독 서비스는
백엔드가 별도로 사용자 확인을 받아야 하며, OCR 값을 자동으로 반올림하거나 추천에 바로 넘기지 않는다.

## 5. POST /catalog/candidates

카탈로그에 없는 상품 1건을 공개 출처에서 찾아 **보고한다**(BE 원본 D-29의 2차 더블체크).
사용자 요청 경로가 아니라 **운영자의 카탈로그 변경 승인 절차에서만** 호출된다.

```json
// 요청
{ "query": "KT 베이직21GB Y덤", "productType": "MOBILE_PLAN" }

// 응답
{ "status": "CANDIDATE_FOUND",
  "candidate": {
    "productType": "MOBILE_PLAN", "provider": "KT", "productName": "베이직21GB Y덤",
    "monthlyPriceWon": 58000, "networkType": "5G", "dataAllowanceText": "42GB + 1Mbps 속도제어",
    "benefits": [], "eligibilityText": "만 34세 이하", "saleStatus": "AVAILABLE",
    "promotionStartDate": null, "promotionEndDate": null,
    "sourceUrl": "https://product.kt.com/..."
  },
  "confidence": 0.86,
  "sources": [{ "url": "https://product.kt.com/...", "title": "요금제 상세", "pageAge": null }],
  "checkedAt": "2026-09-16T12:40:00Z",
  "clarifyingQuestion": null }
```

`query`는 2~300자, `productType`은 `MOBILE_PLAN` · `SUBSCRIPTION` 둘뿐이다. 정의되지 않은 필드는 거절한다.

`status` 세 값의 뜻:

| 값 | 뜻 | `candidate` |
|---|---|---|
| `CANDIDATE_FOUND` | 찾았다 | 객체 |
| `NOT_FOUND` | 찾지 못했다 | `null` |
| `NEEDS_INPUT` | 확신이 부족하다(`confidence < 0.7`) | `null`, `clarifyingQuestion` 동반 |

### 규칙

**AI는 숫자를 만들지 않는다**(절대 원칙 2). 공개된 출처를 찾아 그대로 보고할 뿐이고,
그 값이 맞는지 판정하는 규칙은 **BE가 갖는다** — BE는 자기 값과 `monthlyPriceWon`을
100원 이내 차이까지 같은 값으로 보고 `VERIFIED` / `MISMATCH`(승인 차단) / `UNVERIFIED`를 정한다.

**`confidence` 게이트는 AI 쪽 책임이다.** BE는 이 값을 판정에 쓰지 않고 검토 메모에만 남긴다.
확신이 0.7 미만이면 후보를 올리지 말고 `NEEDS_INPUT`으로 돌려준다 — 애매한 후보를 넘기면
BE는 그것을 "확인된 금액"으로 취급한다.

`sourceUrl`은 **`sources`에 실제로 있는 URL이어야 하고**, 허용 도메인(`CATALOG_ALLOWED_DOMAINS`)
안이어야 한다. 모델이 지어낸 출처를 그대로 싣지 않기 위한 것이며, 어기면 502로 거절한다.
응답의 `productType`이 요청과 다른 경우도 같다.

찾지 못한 것은 `NOT_FOUND`로 돌려준다. **추측으로 채우지 않는다** — BE는 확인 못 한 것을
"틀렸다"가 아니라 "확인 못 했다"로 처리하고 막지 않는다(사용자 제보로 보완, D-18).

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
| `AI-PARSE-001` | 의도 파악 실패 → `clarifyingQuestion` 필수 |
| `AI-LLM-001` | 모델 호출 실패 → 백엔드가 필터 경로로 폴백 |
| `AI-OCR-001` | 이미지 판독 실패 |
| `AI-CATALOG-001` | 상품 조회 실패. 503=허용 도메인 미설정, 502=응답이 계약과 어긋남(출처 밖 URL·productType 불일치 등) |

## 연동 확인 사항 (2026-09-08)

- BE `RecommendationRequest.Required.monthlyDataGb`는 `Integer`이며 추천 서비스는 양수만 허용한다.
- BE 추천 서비스는 `wantedServiceIds`가 비어 있으면 거절한다.
- `/parse`는 위 BE 범위에 맞췄다. 소수·0·구독 없음의 추천 자체를 지원하려면 BE 계약 변경이 먼저다.
- 테스트용 실제 BE와 `/parse` 결과 전달 → 추천 → `/narrate` 금액 보존을 검증했다.
  AI는 TestClient, 모델만 고정 응답이며 BE·PostgreSQL은 실제 프로세스다.
- 2026-09-09: BE 단일 발화 게이트웨이의 실제 HTTP 왕복·되묻기·필터 폴백 검증 완료(모델만 스텁).
  현재 응답 형태와 한 발화 처리 범위는 양쪽 README에 기록했다. 사용자별 이력 저장·조건 병합은 미연결이다.
- 2026-09-16: BE 원본 §3에 카탈로그 변경 제안·승인 2단계(D-28)와 자동 검토(D-29)가 들어가 §5를 더했다.
  `/catalog/candidates` 구현은 그 전부터 있었고(`app/catalog.py`) 사본에만 빠져 있었다 — 코드 변경은 없다.
  BE가 읽는 필드는 `status`·`candidate.monthlyPriceWon`·`confidence`·`candidate.sourceUrl` 넷이다.
  나머지 필드는 BE가 현재 쓰지 않으나, 운영자가 승인 화면에서 볼 근거라 응답에서 빼지 않는다.
