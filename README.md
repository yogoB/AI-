# 요고비 내레이터

Python 3.12 · FastAPI. **백엔드가 계산한 금액을 한국어 문장으로 바꾼다.** 모델을 쓰지 않는다(D-45).
**금액 계산은 하지 않는다** — 메인 백엔드(github.com/yogoB/BE_main)가 전담한다.
사용자 → BE_main → 백엔드 계산·추천 → `/narrate` → BE_main → 사용자 순서다.
대화 이력은 백엔드가 관리하며 내레이터는 세션을 저장하지 않는다.
프론트는 BE_main API만 호출한다. `/narrate`는 백엔드 전용이며,
프론트에는 내레이터 주소와 내부 토큰을 전달하지 않는다.
프론트 연동 명세는 `BE_main/docs/BE_API.md`, 서버 간 명세는 이 README와 로컬 `docs/contract.md`에서 관리한다.

```bash
uv sync
uv run fastapi dev app/main.py   # http://localhost:8000/docs
uv run pytest
```

헬스체크: `GET /health` → `200 {"status": "ok"}`. API 키 없이 실행할 수 있다.

내레이터와 BE_main의 환경 변수 `NARRATOR_INTERNAL_TOKEN`에 **동일한 임의 토큰**을 설정한다.
예를 들어 `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`로 한 번 생성해
두 서버의 로컬 `.env` 또는 배포 비밀값에 넣는다. ASCII 공백 없는 토큰을 사용한다.
백엔드는 매 요청에 `Authorization: Bearer <NARRATOR_INTERNAL_TOKEN>`을 붙인다.
사용자 로그인 토큰과 별개이며 프론트의 Authorization 헤더를 내레이터로 전달하지 않는다.

- `/narrate`: 내부 토큰 누락·불일치는 401 (`NARRATOR-AUTH-001`), 모델 호출 없음.
- 서버의 토큰 설정이 비어 있거나 잘못됐으면 503 (`NARRATOR-AUTH-001`)로 차단한다.
- `/health`는 토큰 없이 사용 가능하다. `/docs`의 Authorize에는 내부 토큰만 입력한다.

## 배포 (Fly.io · 사설 전용)

**인터넷에 노출하지 않는다.** 공개 IP 없이 flycast 하나만 두고 BE(`yogob-api`)만 부른다.

```bash
fly deploy --ha=false -a yogob-narrator
fly ips list -a yogob-narrator     # private ingress 한 줄만 나와야 한다
```

