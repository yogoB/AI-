import json
import os
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

REQUIRED = {"monthlyDataGb": 20, "wantedServiceIds": [1]}
UNKNOWN_OPTIONAL = {
    "currentCarrier": None,
    "networkType": None,
    "contractType": None,
    "hasFamilyBundle": None,
}
GOOD_RESULT = {"required": REQUIRED, "confidence": 0.92}
QUESTION = "월 데이터 사용량과 이용하고 싶은 구독 서비스를 알려주시겠어요?"
TEXT = "데이터 20기가 정도 쓰고 넷플릭스 보고 싶어요"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (GOOD_RESULT, {**GOOD_RESULT, "optional": UNKNOWN_OPTIONAL, "clarifyingQuestion": None}),
        (
            {**GOOD_RESULT, "confidence": 0.7, "optional": {"hasFamilyBundle": False}},
            {**GOOD_RESULT, "confidence": 0.7,
             "optional": {**UNKNOWN_OPTIONAL, "hasFamilyBundle": False}, "clarifyingQuestion": None},
        ),
        (
            {**GOOD_RESULT, "confidence": 0.699, "optional": {"currentCarrier": "SKT"}},
            {"required": None, "optional": None, "confidence": 0.699, "clarifyingQuestion": QUESTION},
        ),
        (
            {"confidence": 0, "clarifyingQuestion": "월 99,000원 요금제로 바꾸실래요?"},
            {"required": None, "optional": None, "confidence": 0,
             "clarifyingQuestion": QUESTION},
        ),
        (
            {"required": {"monthlyDataGb": 0, "wantedServiceIds": []}, "confidence": 1},
            {"required": {"monthlyDataGb": 0, "wantedServiceIds": []}, "optional": UNKNOWN_OPTIONAL,
             "confidence": 1, "clarifyingQuestion": None},
        ),
    ],
)
def test_parse_contract(result, expected):
    def respond(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["messages"] == [{"role": "user", "content": TEXT}]
        assert "금액을 계산하거나 만들어내지 않는다" in body["system"]
        assert body["tool_choice"] == {"type": "tool", "name": "return_result"}
        assert "confidence" in body["tools"][0]["input_schema"]["required"]
        return httpx.Response(200, json={
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": "return_result", "input": result}],
        })

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_NAME": "test-model"}),
        patch("app.llm.client.AsyncClient", side_effect=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kw
        )),
        TestClient(app) as api,
    ):
        response = api.post("/parse", json={"text": TEXT})
    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.parametrize("result", [
    {**GOOD_RESULT, "confidence": -0.1},
    {**GOOD_RESULT, "confidence": 1.1},
    {**GOOD_RESULT, "confidence": "0.92"},
    {**GOOD_RESULT, "confidence": True},
    {"confidence": 0.9},
    {**GOOD_RESULT, "clarifyingQuestion": "데이터는 얼마나 쓰시나요?"},
    {**GOOD_RESULT, "required": {"monthlyDataGb": -1, "wantedServiceIds": [1]}},
    {**GOOD_RESULT, "required": {"monthlyDataGb": True, "wantedServiceIds": [1]}},
    {**GOOD_RESULT, "required": {"monthlyDataGb": 20, "wantedServiceIds": [7]}},
    {**GOOD_RESULT, "required": {"monthlyDataGb": 20, "wantedServiceIds": [True]}},
    {**GOOD_RESULT, "required": {"monthlyDataGb": 20}},
    {**GOOD_RESULT, "optional": {"hasFamilyBundle": "false"}},
    {**GOOD_RESULT, "optional": {"networkType": "FIVE_G"}},
    {**GOOD_RESULT, "optional": {"contractType": "UNKNOWN"}},
    {**GOOD_RESULT, "monthlyTotal": 50000},
])
def test_invalid_extraction_returns_clarification(result):
    with (
        patch("app.llm.client.complete", return_value=result),
        TestClient(app) as api,
    ):
        response = api.post("/parse", json={"text": TEXT})
    assert response.status_code == 502
    assert response.json() == {"detail": {
        "code": "AI-PARSE-001", "clarifyingQuestion": QUESTION,
    }}


@pytest.mark.parametrize("body", [
    {}, {"text": ""}, {"text": " \n\t"}, {"text": None}, {"text": 20},
    {"text": "가" * 4001}, {"text": TEXT, "history": ["이전 발화"]},
])
def test_invalid_request_never_calls_model(body):
    with patch("app.llm.client.complete") as complete, TestClient(app) as api:
        response = api.post("/parse", json=body)
        complete.assert_not_called()
    assert response.status_code == 422


@pytest.mark.parametrize(("payload", "status", "expected_status", "code"), [
    ({"error": "secret upstream error"}, 401, 503, "AI-LLM-001"),
    ({"error": "secret upstream error"}, 429, 503, "AI-LLM-001"),
    ({"error": "secret upstream error"}, 500, 503, "AI-LLM-001"),
    ({"stop_reason": "max_tokens", "content": []}, 200, 502, "AI-PARSE-001"),
    ({"stop_reason": "refusal", "content": []}, 200, 502, "AI-PARSE-001"),
    ({"stop_reason": "tool_use", "content": []}, 200, 502, "AI-PARSE-001"),
    ({"stop_reason": "tool_use", "content": [None]}, 200, 502, "AI-PARSE-001"),
    ([], 200, 502, "AI-PARSE-001"),
    ("not JSON", 200, 502, "AI-PARSE-001"),
])
def test_upstream_failure_is_safe(payload, status, expected_status, code):
    def respond(request):
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        patch("app.llm.client.AsyncClient", side_effect=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kw
        )),
        TestClient(app) as api,
    ):
        response = api.post("/parse", json={"text": TEXT})
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == code
    if code == "AI-PARSE-001":
        assert response.json()["detail"]["clarifyingQuestion"] == QUESTION
    assert "secret" not in response.text
    assert "test-key" not in response.text


def test_timeout_and_missing_key():
    def timeout(request):
        raise httpx.ReadTimeout("secret upstream error", request=request)

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        patch("app.llm.client.AsyncClient", side_effect=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(timeout), **kw
        )),
        TestClient(app) as api,
    ):
        response = api.post("/parse", json={"text": TEXT})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "AI-LLM-001"}}

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}),
        patch("app.llm.client.AsyncClient") as external_client,
        TestClient(app) as api,
    ):
        response = api.post("/parse", json={"text": TEXT})
        external_client.assert_not_called()
        assert api.get("/health").status_code == 200
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "AI-LLM-001"}}
