# 요고비 AI 서버

Python 3.12 · FastAPI. 자연어 파싱과 결과 설명 담당.
**금액 계산은 하지 않는다** — 메인 백엔드(github.com/yogoB/BE_main)가 전담한다.
사용자 → BE_main → `/parse` → 백엔드 계산·추천 → `/narrate` → BE_main → 사용자 순서다.
대화 이력은 백엔드가 관리하며 AI 서버는 세션을 저장하지 않는다.
프론트는 BE_main API만 호출한다. `/parse`, `/narrate`, `/ocr`는 백엔드 전용이며,
프론트에는 AI 주소·내부 토큰·모델 API 키를 전달하지 않는다.
프론트 연동 명세는 `BE_main/docs/BE_API.md`, 서버 간 명세는 이 README와 로컬 `docs/contract.md`에서 관리한다.

```bash
uv sync
uv run fastapi dev app/main.py   # http://localhost:8000/docs
uv run pytest
```

헬스체크: `GET /health` → `200 {"status": "ok"}`. API 키 없이 실행할 수 있다.

AI와 BE_main의 환경 변수 `AI_INTERNAL_TOKEN`에 **동일한 임의 토큰**을 설정한다.
예를 들어 `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`로 한 번 생성해
두 서버의 로컬 `.env` 또는 배포 비밀값에 넣는다. ASCII 공백 없는 토큰을 사용한다.
백엔드는 매 요청에 `Authorization: Bearer <AI_INTERNAL_TOKEN>`을 붙인다.
사용자 로그인 토큰과 별개이며 프론트의 Authorization 헤더를 AI로 전달하지 않는다.

- `/parse`·`/narrate`·`/ocr`·`/catalog/candidates`: 내부 토큰 누락·불일치는 401 (`AI-AUTH-001`), 모델 호출 없음.
- 서버의 토큰 설정이 비어 있거나 잘못됐으면 503 (`AI-AUTH-001`)로 차단한다.
- `/health`는 토큰 없이 사용 가능하다. `/docs`의 Authorize에는 내부 토큰만 입력한다.

[FastAPI 의존성](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-in-path-operation-decorators/)으로
네 API에 같은 검증을 적용한다. 배포 시 AI는 사설 주소로 연결하거나 네트워크에서 BE의 접근만 허용한다.
현재 저장소에서 구현한 것은 요청 인증이며, 실제 배포망 설정은 별도다.

`POST /parse`는 `{"text":"데이터 20기가 정도 쓰고 넷플릭스 보고 싶어요"}`를 받는다.
`text`는 공백을 제외한 내용이 있어야 하며 최대 4,000자다.
실제 파싱은 `.env.example`을 참고해 `.env`에 `ANTHROPIC_API_KEY`를 설정한 뒤 실행한다.

```bash
uv run --env-file .env fastapi dev app/main.py
```

Claude의 [도구 입력 스키마](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)로
구조화된 응답을 받고 Pydantic으로 검증한다. 모델 호출은 `app/llm/client.py`를 거치며
프롬프트는 `app/prompts/parse.txt`에 있다. 테스트는 모델 응답을 스텁하며 실제 API를 호출하지 않는다.

- `confidence < 0.7`: HTTP 200, `required`·`optional`은 `null`, 서버의 고정 확인 질문을 반환한다.
- 추출하지 못한 선택 항목은 `null`이며 임의 기본값을 채우지 않는다.
- 추천 입력은 현재 BE 기준으로 `monthlyDataGb`가 1~2,147,483,647의 정수이고 `wantedServiceIds`가 1개 이상이어야 한다.
  소수 사용량·0·구독 없음은 값을 보정하지 않고 추천에 사용할 조건을 되묻는다.
  모델이 이런 값을 높은 confidence로 반환하더라도 검증에서 차단하고 HTTP 502 (`AI-PARSE-001`)로 확인 질문을 반환한다.
- 잘못된 요청은 HTTP 422, 키 누락·모델 호출 실패는 HTTP 503 (`detail.code: AI-LLM-001`).
- 잘못되거나 잘린 모델 응답은 HTTP 502 (`detail.code: AI-PARSE-001`, `detail.clarifyingQuestion` 포함).

계약에는 오류 코드만 정의되어 있어 HTTP 상태와 `detail` 외피는 현재 구현 기준이다.
백엔드 연동 시 이 오류 형식의 수신 처리를 확인한다.

구독 서비스 ID: 1 넷플릭스, 2 디즈니+, 3 티빙, 4 웨이브, 5 왓챠, 6 유튜브 프리미엄.

`POST /narrate`는 백엔드의 계산 결과를 다음 형태로 받는다.

```json
{
  "planId": 42,
  "monthlyTotal": 71300, "baseline": 89000, "monthlySavings": 17700,
  "annualSavings": 212400,
  "planName": "5G 슬림+", "carrier": "SKT",
  "breakdown": [{"label": "기본료", "amount": 55000, "provenance": "OFFICIAL"}],
  "missingInputs": [{"field": "hasFamilyBundle", "impact": "가족 결합 여부 확인 필요",
                     "howToFind": "통신사 마이페이지 > 결합 상품"}]
}
```

`monthlyTotal`, `baseline`, `monthlySavings`, `planName`, `carrier`, `breakdown`이 필수이며
`missingInputs`, `planId`, `annualSavings`, `howToFind`는 생략할 수 있다.
백엔드의 `CostResult`에 상위 응답의 `missingInputs`를 합쳐 전달한다.
추천 응답의 `data.results`에서는 백엔드가 설명할 결과를 정하고, 계산기 응답에서는 `data.result`를 사용한다.
`data`, `warnings`, `accuracy` 외피나 결과 목록 전체를 `/narrate`로 보내지 않는다.
금액은 정수 원 단위로 검증하고, 천 단위 쉼표만 붙여 표시한다.
합계와 절약액이 다른 항목과 맞는지 다시 계산하지 않는다.

설명은 `app/narrate.py`의 고정 문구로 만들며 모델 API 키나 모델 호출이 필요 없다. 서버 간 내부 토큰은 필요하다.
기본 3문장에 `ESTIMATED` 항목의 이름·금액과 "추정치예요" 안내를 덧붙이고,
`missingInputs`가 있으면 필요한 정보를 마지막 문장으로 안내해 총 3~5문장으로 반환한다.
추가 입력 안내는 `/parse`의 6개 입력 필드를 지원한다. 잘못된 요청은 HTTP 422로 응답한다.
`planId`·`annualSavings`는 연동용으로 수용하며 설명에 사용하지 않는다.
자유 서술인 `note`·`impact`·`howToFind`는 설명에 복사하지 않으며, 계약에 판정 정보가 없는
미사용 혜택 제외·해지 제안도 생성하지 않는다.

추천 사유 `reasons`는 두 곳에서 나온다. 모델이 있으면 모델이 문장을 고르고,
키가 없거나 모델이 실패하면 `rule_reasons`가 같은 값으로 문장을 만든다.
두 경로 모두 같은 금액 가드를 통과하므로 요청에 없는 금액은 어느 쪽에서도 나가지 않는다.
따라서 **모델 없이도 "왜 추천됐나" 목록이 비지 않는다.** 규칙은 제휴 혜택 줄, 할인 줄, 절감액 순으로
최대 3문장을 만들고, `provenance`가 `ESTIMATED`인 줄은 근거로 쓰지 않는다.

BE는 `/narrate` 요청에 계약 필드만 싣는다(`AiGateway.NARRATE_FIELDS`).
`CostResult`에는 AI가 쓰지 않는 내부 필드가 더 있고, `extra=forbid`라 하나라도 새면 422가 된다.
BE는 그것을 장애로 삼키므로 사유가 화면에서 조용히 사라진다.

`POST /ocr`는 `{"image":"이미지 파일의 base64 문자열"}`를 받아
`monthlyDataGb`, `monthlyVoiceMin`, `monthlySmsCount`, `confidence`를 반환한다.
PNG·JPEG·WebP·GIF 정지 이미지, 디코딩 후 5MiB 이하·한 변 8,000픽셀 이하를 지원한다.
URL이나 `data:` 접두사는 받지 않는다. 이미지 구조 검증에는 Pillow를 사용한다.

[Claude 이미지 입력 API](https://platform.claude.com/docs/en/build-with-claude/vision)를
`app/llm/client.py`로 호출하며, 프롬프트는 `app/prompts/ocr.txt`에 있다.
원본 이미지와 추출값은 파일·세션에 저장하지 않는다.
읽히지 않은 값은 `null`로 반환하며, 백엔드가 `ESTIMATED`로 표시하고 사용자 확인을 받아야 한다.
confidence가 0.7 미만이거나 전부 읽지 못하면 HTTP 422로 재업로드를 요청한다.
이미지 형식·손상 오류는 422, 크기 초과는 413, 잘못된 모델 응답은 502이며,
`detail.code: AI-OCR-001`과 `detail.clarifyingQuestion`을 포함한다.
모델 호출 실패는 HTTP 503 (`detail.code: AI-LLM-001`)이다.
실제 이미지의 OCR 정확도는 별도 검증이 필요하다.

`POST /catalog/candidates`는 검수 CSV에서 찾지 못한 상품명과 종류를 받는다.

```json
{"query":"5G 슬림","productType":"MOBILE_PLAN"}
```

AI는 `CATALOG_ALLOWED_DOMAINS`에 설정한 공식 도메인만 웹 검색하고, 상품명·사업자·공식 월 가격·조건·출처를
구조화한다. 모델 응답의 `sourceUrl`이 실제 검색 결과와 허용 도메인에 모두 포함돼야 `CANDIDATE_FOUND`로 반환한다.
낮은 confidence는 `NEEDS_INPUT`, 검색 결과가 없으면 `NOT_FOUND`다. 잘못된 모델 결과는 502
`AI-CATALOG-001`, 도메인 설정 오류는 503 `AI-CATALOG-001`, 모델 호출 실패는 503 `AI-LLM-001`이다.

이 응답은 검수 후보일 뿐이다. AI 서버는 저장·승인·추천·금액 계산을 하지 않는다. 백엔드는 검수 전 후보를
최저가·절감액·알림 계산에서 제외하고, 검수 완료 데이터만 기준 CSV에 편입해야 한다.

`/ocr`는 화면에 있는 소수 사용량·0을 그대로 보존한다. 이 결과를 추천에 바로 넘기지 않고,
백엔드가 사용자에게 추천용 정수 조건과 원하는 구독 서비스를 확인해야 한다.
OCR 업로드·확인 화면의 백엔드 연결은 아직 구현하지 않았으며, 연결 시 다음 순서를 따른다.

1. 추출값을 `ESTIMATED`(추정치)로 표시한다. `18.4`·`0`은 그대로 보여주고, `null`은 미판독으로 표시한다.
2. 사용자에게 추천에 사용할 양의 정수 GB와 구독 서비스를 직접 선택하게 한다. 소수 사용량을 자동 반올림하지 않는다.
3. 사용자가 **19GB·넷플릭스**를 선택했다면 기존 `POST /api/v1/recommendations`에 아래 요청을 보낸다.
   음성·문자 사용량은 현재 추천 요청에 포함하지 않는다.

```json
{"required":{"monthlyDataGb":19,"wantedServiceIds":[1]}}
```

`tests/test_backend_integration.py`는 `/parse`의 결과를 실제 BE 추천 API에 보내고,
반환된 각 결과를 `/narrate`에 전달해 금액이 그대로 표시되는지 검증한다.
첫 번째 테스트는 AI 엔드포인트를 TestClient로 실행하고 모델 응답만 고정한다.
20GB 이상 요금제와 넷플릭스 시드가 있는 테스트용 BE를 별도로 실행한 뒤 다음과 같이 실행한다.
BE의 `dev` 프로파일 더미 시드를 사용할 수 있다.

```bash
YOGOBI_TEST_BACKEND_URL=http://127.0.0.1:18080 uv run pytest -q tests/test_backend_integration.py
```

테스트 BE의 `AI_SERVER_URL=http://127.0.0.1:18000`을 설정하면 같은 파일에서 챗봇 게이트웨이의
실제 HTTP 왕복도 검증한다. 테스트가 18000 포트에 AI 서버를 띄우고 모델 응답만 스텁한다.
테스트 BE에는 `AI_INTERNAL_TOKEN=test-backend-only-token`을 설정한다. 이 값은 테스트 전용이며 운영에 사용하지 않는다.
테스트는 AI 직접 호출 차단, 두 서버의 토큰 불일치 폴백, 프론트 토큰을 AI에 전달하지 않는 경로도 확인한다.
포트를 바꾸려면 테스트의 `YOGOBI_TEST_AI_PORT`와 BE의 `AI_SERVER_URL`을 함께 맞춘다.
주소를 설정하지 않은 기본 테스트에서는 연동 2건을 건너뛴다.
2026-09-09 검증: 별도 PostgreSQL 17·BE 복사본·개발 시드로 전체 97개 통과.
내부 토큰 적용 후 같은 방식으로 108개 통과. 기본 실행은 106개 통과·연동 2개 건너뜀.

BE의 `POST /api/v1/chat/messages`는 `{"text":"..."}`를 받아 AI 파싱·기존 추천·AI 설명을 연결한다.
응답의 `data.status`는 `RECOMMENDED`, `NEEDS_INPUT`, `FILTER_FALLBACK`이며,
`data.message`에 안내, `data.recommendation`에 기존 추천 결과 또는 `null`이 들어간다.
AI 장애는 필터 입력으로 안내하고, 설명만 실패하면 추천 결과를 보존한다.
현재는 한 발화 단위이며 사용자별 이력 저장·추가 답변의 조건 병합은 후속 작업이다.

실제 모델 품질은 자동 테스트와 별도로 수동 평가한다. 아래 명령은 기본 발화 9건을 모델에 보낸다.

```bash
uv run python -m scripts.evaluate_model --dry-run
uv run --env-file .env python -m scripts.evaluate_model
```

케이스는 `evaluations/parse.json`에 있다. 실행 결과에는 케이스 ID와 통과 여부만 표시하며,
키 미설정 시 호출하지 않고 종료한다. 호출 장애(503) 시 나머지 평가를 중단한다.
실행 전에 전체 케이스의 필드·기대값 타입·중복 ID와 이미지의 존재·형식·크기를 검증한다.
잘못된 자료가 하나라도 있으면 모델 호출 없이 종료한다. `--dry-run`은 이 검증만 수행한다.
현재 API 키가 없어 실제 모델의 평가 점수는 아직 없다.

개인 발화와 OCR 이미지는 Git에서 제외되는 `evaluations/local/`에 둔다.
OCR 평가 JSON은 다음 형태로 작성하고 `--cases evaluations/local/ocr.json`을 지정한다.
이미지 경로는 평가 JSON 파일 기준이며, 실제 화면에서 확인한 기대값을 적는다.

```json
[
  {"id":"usage-screen", "kind":"ocr", "image":"usage.png",
   "expected":{"monthlyDataGb":18.4,"monthlyVoiceMin":120,"monthlySmsCount":30}}
]
```

공식 자료에서 카탈로그 후보 행을 뽑아 검수용 CSV를 만드는 수동 배치가 있다.
추천 요청 경로에서는 호출하지 않으며, 기준 CSV를 고치지 않는다.

```bash
uv run --env-file .env python -m scripts.extract_catalog mobile_plan \
    --source-url https://www.example.co.kr/plans 자료.html
uv run --env-file .env python -m scripts.extract_catalog plan_benefit \
    --source-url https://www.example.co.kr/plans 화면.png --dry-run
```

입력은 HTML·텍스트·CSV·PDF·이미지다. HTML은 태그를 걷어낸 본문만 보내며 `<script>`·`<style>` 안의
글자는 보내지 않는다. `--source-url`은 운영자가 확인한 공식 주소이며 모델이 만들지 않는다.
자바스크립트로 그리는 화면은 내려받은 HTML에 값이 없으므로 PDF나 스크린샷으로 넘긴다.

모델은 행마다 `sourceQuote`를 함께 반환한다. 스크립트는 그 인용이 보낸 자료 안에 실제로 있는지,
그 행의 금액이 인용 안에 보이는지 대조하고 어긋나면 그 행을 버린다.
단위 환산(`110GB` → `112640`), 무제한 표기(`999999`), 서비스·등급 이름 → ID 매핑은 스크립트가 한다.
모델은 숫자를 만들지 않는다.

- `mobile_plan.csv` 또는 `plan_benefit.csv`: 기계 대조를 통과한 행. 시드 CSV와 열 순서가 같다.
- `*_manual.csv`: 이미지·PDF에서 나온 행. 대조할 글자가 없어 사람이 화면과 나란히 봐야 한다.
- `review.csv`: 채택·검수필요·폐기 전부와 사유·인용문.

가입 대상을 확인할 수 없는 요금제(`UNKNOWN`)는 버린다. 빈 `age_limit`은 누구나 가입 가능으로 읽혀
자격이 필요한 상품이 전체 사용자에게 추천된다. 등급을 모르는 무료 제공은
`BUNDLE_INCLUDED`(표시만)로 낮춰 금액 효과를 없앤다.

`--dry-run`은 `count_tokens`로 입력 토큰을 실측하고 예상 비용만 출력한다(과금 없음).
기본 상한은 `--max-usd 2`이며 넘으면 실행하지 않는다. 웹 검색을 쓰지 않으므로 토큰 외 비용은 없다.
결과는 검수 대기 후보다. 승인은 BE의 카탈로그 변경 제안 절차로 한다.

README 이외 Markdown(`AGENTS.md`, `docs/` 포함)은 로컬에서 관리한다.
앱 실행에 필요한 프롬프트는 `.txt`로 저장소에 포함한다.
`.env.*`, 키·인증서·자격 증명 파일, 업로드 이미지 폴더, 로그·DB·백업 파일은 Git에서 제외한다.
실제 값을 넣지 않은 `.env.example`만 공유한다. `.gitignore`는 코드에 직접 쓴 비밀값이나
기존 커밋 이력까지 제거하지 않으므로 커밋할 변경사항을 확인해야 한다.
