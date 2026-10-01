import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.detections import router as detections_router
from app.narrate import router as narrate_router
from app.switch_timing import router as switch_timing_router
from app.plan_promotions import router as plan_promotions_router
from app.subscription_check import router as subscription_check_router


def token_is_set(token: str) -> bool:
    return bool(token) and all(33 <= ord(character) <= 126 for character in token)


async def require_backend(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))],
) -> None:
    token = os.getenv("NARRATOR_INTERNAL_TOKEN", "")
    if not token_is_set(token):
        raise HTTPException(status_code=503, detail={"code": "NARRATOR-AUTH-001"})
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
        raise HTTPException(status_code=401, detail={"code": "NARRATOR-AUTH-001"},
                            headers={"WWW-Authenticate": "Bearer"})


# BE 의 본문 상한(2MB, G-89 b)과 같은 값. BE 가 보내는 가장 큰 /narrate(설명 300줄·안내 100건)도 수백 KB 다.
# 더 낮으면 413 → BE 는 그것을 장애로 삼켜 설명이 조용히 사라진다.
MAX_BODY_BYTES = 2_000_000
TOO_LARGE = b'{"detail":{"code":"NARRATOR-BODY-413"}}'


class BodyLimit:
    """FastAPI 는 라우터의 토큰 검사보다 **본문 파싱이 먼저**다. 토큰 없는 호출자도 큰 본문을 메모리에 올릴 수 있어
    앱 맨 앞에서 끊는다 — 길이를 밝히면 읽기 전에, 밝히지 않으면(chunked) 읽는 도중에."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return await self.refuse(send)
        seen = 0
        refused = False

        async def counted_receive():
            # 넘는 순간 직접 413 을 보내고 앱에는 연결이 끊긴 것으로 알린다. 예외로 올리면 FastAPI 가
            # 본문 파싱 오류로 잡아 400 으로 바꾼다.
            nonlocal seen, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BODY_BYTES:
                    refused = True
                    await self.refuse(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not refused:
                await send(message)

        await self.app(scope, counted_receive, guarded_send)

    @staticmethod
    async def refuse(send):
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(TOO_LARGE)).encode())]})
        await send({"type": "http.response.body", "body": TOO_LARGE})



# 문서 경로(/docs·/openapi.json)는 토큰 없이 열렸다. 쓰는 사람이 없고 계약 원본은 docs/contract.md 다.
app = FastAPI(title="요고비 운영 자동화 · 내레이터",
              description="규칙 기반 설명과 공식 출처 가격 확인. 계산·승인·저장은 백엔드가 담당한다.",
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(BodyLimit)
for router in (narrate_router, detections_router, switch_timing_router,
               subscription_check_router, plan_promotions_router):
    app.include_router(router, dependencies=[Depends(require_backend)])


@app.get("/health")
async def health() -> dict[str, str]:
    # 토큰이 없으면 모든 요청이 503 이다. 그런데 여기만 200 이면 빠진 배포가 건강해 보이고 BE 는 조용히 물러난다.
    if not token_is_set(os.getenv("NARRATOR_INTERNAL_TOKEN", "")):
        raise HTTPException(status_code=503, detail={"code": "NARRATOR-AUTH-001"})
    return {"status": "ok"}
