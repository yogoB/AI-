from fastapi import FastAPI

from app.parse import router as parse_router

app = FastAPI(title="요고비 AI 서버")
app.include_router(parse_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
