import asyncio
import json
import os
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

AUTH = {"Authorization": "Bearer test-backend-only-token"}
REQUEST = {"query": "5G 슬림", "productType": "MOBILE_PLAN"}
CANDIDATE = {
    "productType": "MOBILE_PLAN",
    "provider": "SK텔레콤",
    "productName": "5G 슬림",
    "monthlyPriceWon": 55000,
    "networkType": "5G",
    "dataAllowanceText": "월 15GB",
    "benefits": [],
    "eligibilityText": None,
    "saleStatus": "AVAILABLE",
    "promotionStartDate": None,
    "promotionEndDate": None,
    "sourceUrl": "https://www.tworld.co.kr/product/5g-slim",
}
SOURCE_BLOCK = {
    "type": "web_search_tool_result",
    "content": [{
        "type": "web_search_result",
        "url": CANDIDATE["sourceUrl"],
        "title": "5G 슬림 | T world",
        "page_age": "2026-09-15",
    }],
}
SOURCE = {
    "url": CANDIDATE["sourceUrl"],
    "title": "5G 슬림 | T world",
    "pageAge": "2026-09-15",
}


def test_catalog_candidate_uses_domain_limited_search_and_returns_source():
    def respond(request):
        body = json.loads(request.content)
        assert body["messages"] == [{
            "role": "user", "content": "상품 종류: MOBILE_PLAN\n찾을 상품: 5G 슬림",
        }]
        assert body["tools"][0] == {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
            "allowed_domains": ["tworld.co.kr", "kt.com"],
        }
        assert body["tools"][1]["name"] == "return_result"
        assert "tool_choice" not in body
        return httpx.Response(200, json={
            "stop_reason": "tool_use",
            "content": [
                SOURCE_BLOCK,
                {"type": "tool_use", "name": "return_result", "input": {
                    "candidate": CANDIDATE, "confidence": 0.91,
                }},
            ],
        })

    with (
        patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "test-key",
            "MODEL_NAME": "test-model",
            "CATALOG_ALLOWED_DOMAINS": "tworld.co.kr,kt.com,tworld.co.kr",
        }),
        patch("app.llm.client.AsyncClient", side_effect=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kw
        )),
        TestClient(app, headers=AUTH) as api,
    ):
        response = api.post("/catalog/candidates", json=REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANDIDATE_FOUND"
    assert body["candidate"] == CANDIDATE
    assert body["sources"] == [SOURCE]
    assert body["checkedAt"].endswith("Z")


@pytest.mark.parametrize(("search_content", "expected_status", "code"), [
    ({"type": "web_search_tool_result_error", "error_code": "unavailable"}, 503, "AI-LLM-001"),
    (None, 502, "AI-CATALOG-001"),
])
def test_catalog_search_failure_is_not_reported_as_not_found(search_content, expected_status, code):
    content = [{"type": "tool_use", "name": "return_result", "input": {
        "candidate": None, "confidence": 0.9,
    }}]
    if search_content is not None:
        content.insert(0, {"type": "web_search_tool_result", "content": search_content})

    def respond(request):
        return httpx.Response(200, json={"stop_reason": "tool_use", "content": content})

    with (
        patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "test-key", "CATALOG_ALLOWED_DOMAINS": "tworld.co.kr",
        }),
        patch("app.llm.client.AsyncClient", side_effect=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kw
        )),
        TestClient(app, headers=AUTH) as api,
    ):
        response = api.post("/catalog/candidates", json=REQUEST)
    assert response.status_code == expected_status
    assert response.json() == {"detail": {"code": code}}


@pytest.mark.parametrize(("result", "expected_status"), [
    ({"candidate": None, "confidence": 0.9}, "NOT_FOUND"),
    ({"candidate": CANDIDATE, "confidence": 0.69}, "NEEDS_INPUT"),
])
def test_catalog_candidate_handles_missing_or_uncertain_result(result, expected_status):
    with (
        patch.dict(os.environ, {"CATALOG_ALLOWED_DOMAINS": "tworld.co.kr"}),
        patch("app.llm.client.search", return_value=(result, [SOURCE])),
        TestClient(app, headers=AUTH) as api,
    ):
        response = api.post("/catalog/candidates", json=REQUEST)
    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    assert response.json()["candidate"] is None
    if expected_status == "NEEDS_INPUT":
        assert response.json()["clarifyingQuestion"]


@pytest.mark.parametrize("candidate", [
    {**CANDIDATE, "sourceUrl": "https://example.com/fake"},
    {**CANDIDATE, "sourceUrl": "https://fake.tworld.co.kr.example.com/fake"},
    {**CANDIDATE, "productType": "SUBSCRIPTION", "networkType": None, "dataAllowanceText": None},
])
def test_catalog_candidate_rejects_unproven_or_wrong_type_result(candidate):
    sources = [{"url": candidate["sourceUrl"], "title": "source", "pageAge": None}]
    with (
        patch.dict(os.environ, {"CATALOG_ALLOWED_DOMAINS": "tworld.co.kr"}),
        patch("app.llm.client.search", return_value=({
            "candidate": candidate, "confidence": 0.9,
        }, sources)),
        TestClient(app, headers=AUTH) as api,
    ):
        response = api.post("/catalog/candidates", json=REQUEST)
    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "AI-CATALOG-001"}}


def test_catalog_candidate_rejects_unallowed_search_source():
    with (
        patch.dict(os.environ, {"CATALOG_ALLOWED_DOMAINS": "tworld.co.kr"}),
        patch("app.llm.client.search", return_value=({
            "candidate": CANDIDATE, "confidence": 0.9,
        }, [SOURCE, {
            "url": "https://example.com/untrusted", "title": "untrusted", "pageAge": None,
        }])),
        TestClient(app, headers=AUTH) as api,
    ):
        response = api.post("/catalog/candidates", json=REQUEST)
    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "AI-CATALOG-001"}}


@pytest.mark.parametrize("domains", ["", "https://tworld.co.kr", "*.tworld.co.kr", "localhost"])
def test_catalog_configuration_failure_never_calls_model(domains):
    with (
        patch.dict(os.environ, {"CATALOG_ALLOWED_DOMAINS": domains}),
        patch("app.llm.client.search") as search,
        TestClient(app, headers=AUTH) as api,
    ):
        response = api.post("/catalog/candidates", json=REQUEST)
        search.assert_not_called()
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "AI-CATALOG-001"}}


@pytest.mark.parametrize("body", [
    {},
    {"query": "x", "productType": "MOBILE_PLAN"},
    {"query": "5G 슬림", "productType": "UNKNOWN"},
    {"query": "5G 슬림", "productType": "MOBILE_PLAN", "allowedDomains": ["example.com"]},
])
def test_invalid_catalog_request_never_calls_model(body):
    with patch("app.llm.client.search") as search, TestClient(app, headers=AUTH) as api:
        response = api.post("/catalog/candidates", json=body)
        search.assert_not_called()
    assert response.status_code == 422


def test_the_same_product_is_searched_only_once(monkeypatch):
    monkeypatch.setenv("CATALOG_ALLOWED_DOMAINS", "tworld.co.kr")
    # 같은 상품을 여러 사용자가 물어도 검색은 한 번이다. 검색은 1,000회당 $10다.
    from unittest.mock import AsyncMock

    from app.catalog import CatalogSearchRequest, find_candidate

    stub = AsyncMock(return_value=(
        {"candidate": None, "confidence": 0.9},
        [{"url": "https://www.tworld.co.kr/p", "title": "요금제", "pageAge": None}],
    ))
    with patch("app.llm.client.search", stub):
        request = CatalogSearchRequest(query="SKT 베스트 Max", productType="MOBILE_PLAN")
        first = asyncio.run(find_candidate(request))
        second = asyncio.run(find_candidate(request))
    assert first.status == second.status == "NOT_FOUND"
    assert first.checkedAt == second.checkedAt      # 재사용해도 확인 시각을 새로 찍지 않는다
    assert stub.await_count == 1


def test_the_search_budget_stops_further_searches():
    from app.llm import client

    monkey = client.search_limit()
    client._searches_used = monkey
    with pytest.raises(client.LLMBudgetExceeded):
        asyncio.run(client.search("p", "q", {}, ["tworld.co.kr"], max_uses=1))
