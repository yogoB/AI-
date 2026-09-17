# syntax=docker/dockerfile:1
# 잠금 파일 그대로 설치한다 — 로컬과 배포의 의존성이 갈리지 않게.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# 의존성을 먼저 받는다. 소스만 바뀌면 이 레이어는 캐시된다.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 런타임에는 uv 가 필요 없다. 가상환경과 앱 소스만 옮긴다.
FROM python:3.12-slim
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=build /app/.venv /app/.venv
# 프롬프트는 앱 실행에 필요하다(app/prompts/*.txt). 스크립트·테스트는 넣지 않는다.
COPY app ./app
EXPOSE 8000
# 요청당 하는 일은 외부 HTTP 대기뿐이라 워커를 늘릴 이유가 없다. 동시성은 asyncio 가 맡는다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
