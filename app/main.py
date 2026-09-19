import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.detections import router as detections_router
from app.narrate import router as narrate_router
from app.switch_timing import router as switch_timing_router
from app.subscription_check import router as subscription_check_router


async def require_backend(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))],
) -> None:
    token = os.getenv("NARRATOR_INTERNAL_TOKEN", "")
    if not token or any(not 33 <= ord(character) <= 126 for character in token):
        raise HTTPException(status_code=503, detail={"code": "NARRATOR-AUTH-001"})
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
        raise HTTPException(status_code=401, detail={"code": "NARRATOR-AUTH-001"},
                            headers={"WWW-Authenticate": "Bearer"})


app = FastAPI(title="요고비 운영 자동화 · 내레이터",
              description="규칙 기반 설명과 공식 구독 가격 확인. 계산·승인·저장은 백엔드가 담당한다.")
for router in (narrate_router, detections_router, switch_timing_router, subscription_check_router):
    app.include_router(router, dependencies=[Depends(require_backend)])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
