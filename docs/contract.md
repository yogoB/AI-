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
  "monthlyTotal": 71300, "baseline": 89000, "monthlySavings": 17700,
  "planName": "5G 슬림+", "carrier": "SKT",
  "breakdown": [
    { "label": "5G 슬림+ 기본료", "amount": 55000, "provenance": "OFFICIAL" },
    { "label": "선택약정 25% 할인", "amount": -13750, "provenance": "DERIVED" },
    { "label": "넷플릭스 스탠다드", "amount": 13500, "provenance": "OFFICIAL",
      "note": "제휴 혜택으로 4,000원 할인 적용" }
  ],
  "missingInputs": [ { "field": "hasFamilyBundle", "impact": "..." } ]
}
```

응답
```json
{ "message": "SKT 5G 슬림+로 바꾸시면 실제 내시는 금액이 월 71,300원이에요. ..." }
```

### 규칙

- **`breakdown`에 없는 금액을 문장에 넣지 않는다.** 합계를 다시 계산하지도 않는다.
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

## 5. 사용자 대면 문구

표현이 흔들리면 아마추어처럼 보인다. 아래를 고정해서 쓴다.

| 개념 | 쓸 표현 | 쓰지 말 것 |
|---|---|---|
| `monthlyTotal` | "실제 내시는 금액" | "총비용", "실질비용" |
| `monthlySavings` | "월 N원 절약" | "N원 이득", "세이브" |
| `ESTIMATED` | "추정치예요" | "약", "~쯤" |
| 미사용 혜택 | "안 쓰시는 혜택은 계산에서 뺐어요" | "무가치", "쓸모없는" |
| 해지 제안 | "정리해 보실래요?" | "해지하세요", "낭비 중입니다" |

## 6. 에러

백엔드가 이 코드로 받는다.

| 코드 | 상황 |
|---|---|
| `AI-PARSE-001` | 의도 파악 실패 → `clarifyingQuestion` 필수 |
| `AI-LLM-001` | 모델 호출 실패 → 백엔드가 필터 경로로 폴백 |
| `AI-OCR-001` | 이미지 판독 실패 |
