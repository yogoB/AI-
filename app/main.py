import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.catalog import router as catalog_router
from app.narrate import router as narrate_router
from app.ocr import router as ocr_router
from app.parse import router as parse_router


async def require_backend(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))],
) -> None:
    token = os.getenv("AI_INTERNAL_TOKEN", "")
    if not token or any(not 33 <= ord(character) <= 126 for character in token):
        raise HTTPException(status_code=503, detail={"code": "AI-AUTH-001"})
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode()):
        raise HTTPException(status_code=401, detail={"code": "AI-AUTH-001"},
                            headers={"WWW-Authenticate": "Bearer"})


app = FastAPI(title="요고비 AI 서버", description="백엔드 전용 내부 API. 프론트는 BE_main을 호출합니다.")
for router in (parse_router, narrate_router, ocr_router, catalog_router):
    app.include_router(router, dependencies=[Depends(require_backend)])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
