import os
from urllib.parse import urlsplit

from httpx import AsyncClient, HTTPError, Timeout


class LLMError(RuntimeError):
    pass


class InvalidLLMResponse(ValueError):
    pass


class LLMBudgetExceeded(LLMError):
    """웹 검색 예산 소진. LLMError를 상속해 기존 호출부가 그대로 503으로 흡수한다."""


def search_limit() -> int:
    return int(os.getenv("SEARCH_LIMIT") or 300)


_searches_used = 0


def searches_used() -> int:
    return _searches_used


def default_model() -> str:
    return os.getenv("MODEL_NAME") or "claude-sonnet-5"


async def _messages(body: dict, path: str = "/v1/messages") -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY is not configured")

    body = {"model": default_model(), **body}
    try:
        async with AsyncClient(timeout=Timeout(20.0, connect=5.0)) as client:
            response = await client.post(
                f"https://api.anthropic.com{path}",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                json=body,
            )
            response.raise_for_status()
    except HTTPError:
        raise LLMError("Model request failed") from None

    try:
        return response.json()
    except ValueError:
        raise InvalidLLMResponse("Invalid model response") from None


def _structured_result(message: dict) -> dict:
    try:
        if message["stop_reason"] != "tool_use":
            raise ValueError("Incomplete model response")
        results = [
            block["input"]
            for block in message["content"]
            if block["type"] == "tool_use" and block["name"] == "return_result"
        ]
        if len(results) != 1 or not isinstance(results[0], dict):
            raise ValueError("Expected one structured result")
        return results[0]
    except (ValueError, KeyError, TypeError):
        raise InvalidLLMResponse("Invalid model response") from None


async def complete(system_prompt: str, content: str | list[dict], schema: dict,
                   max_tokens: int = 1024, model: str | None = None) -> dict:
    message = await _messages({
        **({"model": model} if model else {}),
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": content}],
        "tools": [{"name": "return_result", "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": "return_result"},
    })
    return _structured_result(message)


def _search_body(system_prompt: str, query: str, schema: dict, allowed_domains: list[str],
                 max_uses: int) -> dict:
    return {
        "max_tokens": 2048,
        "system": system_prompt,
        "messages": [{"role": "user", "content": query}],
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_uses,
                "allowed_domains": allowed_domains,
            },
            {
                "name": "return_result",
                "description": "Return the catalog candidate in the required schema after web search.",
                "input_schema": schema,
            },
        ],
    }


def _actual_searches(message: dict, fallback: int) -> int:
    """응답이 보고한 실제 검색 횟수. 형태가 예상과 다르면 보수적으로 예약분을 유지한다."""
    try:
        used = message["usage"]["server_tool_use"]["web_search_requests"]
    except (KeyError, TypeError):
        return fallback
    return used if isinstance(used, int) and 0 <= used <= fallback else fallback


async def search(system_prompt: str, query: str, schema: dict, allowed_domains: list[str],
                 max_uses: int = 3) -> tuple[dict, list[dict]]:
    """max_uses는 곧 돈이다 — 웹 검색은 1,000회당 $10로 토큰과 별개로 과금된다.
    모르는 상품을 찾을 때는 3회가 필요하지만, 아는 상품의 가격만 확인할 때는 1회로 충분하다.
    """
    global _searches_used
    # 검색은 1,000회당 $10다. 한 번 새면 크레딧이 조용히 사라지므로 호출 전에 막는다.
    # 최악의 경우(max_uses회 전부 사용)를 먼저 예약해 상한을 절대 넘지 않게 하고,
    # 응답이 오면 모델이 실제로 쓴 횟수로 정정한다. 예약만 하고 두면 3회 잡고 1회만 쓴 경우
    # 예산이 3배 빨리 마른다.
    # ponytail: 프로세스 수명 기준 카운터다. 재시작하면 0으로 돌아간다.
    #   여러 대로 늘리거나 일자별 상한이 필요해지면 공용 저장소로 옮긴다.
    if _searches_used + max_uses > search_limit():
        raise LLMBudgetExceeded(
            f"Web search budget exhausted ({_searches_used}/{search_limit()})"
        )
    _searches_used += max_uses

    try:
        message = await _messages(_search_body(system_prompt, query, schema, allowed_domains, max_uses))
    except BaseException:
        # 검색이 실패하면 과금되지 않는다. 예약을 그대로 돌려준다.
        _searches_used -= max_uses
        raise
    _searches_used += _actual_searches(message, max_uses) - max_uses

    result = _structured_result(message)

    sources: list[dict] = []
    searched = False
    try:
        for block in message["content"]:
            if block.get("type") != "web_search_tool_result":
                continue
            searched = True
            if isinstance(block.get("content"), dict):
                raise LLMError("Web search failed")
            if not isinstance(block.get("content"), list):
                raise ValueError("Invalid web search result")
            for item in block["content"]:
                if item.get("type") != "web_search_result":
                    continue
                url = item.get("url")
                title = item.get("title")
                if isinstance(url, str) and isinstance(title, str) and urlsplit(url).hostname:
                    sources.append({"url": url, "title": title, "pageAge": item.get("page_age")})
        if not searched:
            raise ValueError("Web search was not used")
    except LLMError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError):
        raise InvalidLLMResponse("Invalid web search response") from None
    return result, sources
