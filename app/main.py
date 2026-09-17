import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.narrate import router as narrate_router


async def require_backend(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))],
) -> None:
    token = os.getenv("NARRATOR_INTERNAL_TOKEN", "")
    if not token or any(not 33 <= ord(character) <= 126 for character in token):
        raise HTTPException(status_code=503, detail={"code": "NARRATOR-AUTH-001"})
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
        raise HTTPException(status_code=401, detail={"code": "NARRATOR-AUTH-001"},
                            headers={"WWW-Authenticate": "Bearer"})


app = FastAPI(title="요고비 내레이터",
              description="백엔드가 계산한 금액을 한국어 문장으로 바꾼다. 숫자를 만들지 않는다.")
for router in (narrate_router,):
    app.include_router(router, dependencies=[Depends(require_backend)])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
