import os
from urllib.parse import urlsplit

from httpx import AsyncClient, HTTPError, Timeout


class LLMError(RuntimeError):
    pass


class InvalidLLMResponse(ValueError):
    pass


async def _messages(body: dict) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY is not configured")

    body = {"model": os.getenv("MODEL_NAME") or "claude-sonnet-4-6", **body}
    try:
        async with AsyncClient(timeout=Timeout(20.0, connect=5.0)) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
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


async def complete(system_prompt: str, content: str | list[dict], schema: dict) -> dict:
    message = await _messages({
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": [{"role": "user", "content": content}],
        "tools": [{"name": "return_result", "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": "return_result"},
    })
    return _structured_result(message)


async def search(system_prompt: str, query: str, schema: dict, allowed_domains: list[str]) -> tuple[dict, list[dict]]:
    message = await _messages({
        "max_tokens": 2048,
        "system": system_prompt,
        "messages": [{"role": "user", "content": query}],
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
                "allowed_domains": allowed_domains,
            },
            {
                "name": "return_result",
                "description": "Return the catalog candidate in the required schema after web search.",
                "input_schema": schema,
            },
        ],
    })
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
