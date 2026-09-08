import os

from httpx import AsyncClient, HTTPError, Timeout


class LLMError(RuntimeError):
    pass


class InvalidLLMResponse(ValueError):
    pass


async def complete(system_prompt: str, text: str, schema: dict) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY is not configured")

    try:
        async with AsyncClient(timeout=Timeout(20.0, connect=5.0)) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                json={
                    "model": os.getenv("MODEL_NAME") or "claude-sonnet-4-6",
                    "max_tokens": 1024,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": text}],
                    "tools": [{"name": "return_result", "input_schema": schema}],
                    "tool_choice": {"type": "tool", "name": "return_result"},
                },
            )
            response.raise_for_status()
    except HTTPError:
        raise LLMError("Model request failed") from None

    try:
        message = response.json()
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
