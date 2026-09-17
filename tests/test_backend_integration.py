"""별도로 실행한 테스트용 BE와의 HTTP 연동. 모델을 쓰지 않는다.

    YOGOBI_TEST_BACKEND_URL=http://127.0.0.1:18080 uv run pytest -q tests/test_backend_integration.py
"""

import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"Authorization": "Bearer test-backend-only-token"}
FILTER_REQUEST = {"required": {"monthlyDataGb": 20, "wantedServiceIds": [1]}}


@pytest.mark.skipif(not os.getenv("YOGOBI_TEST_BACKEND_URL"), reason="테스트용 BE 주소 미설정")
def test_backend_recommendation_amounts_survive_narration():
    """BE가 만든 금액이 설명 문장에 그대로 옮겨지는지 본다 — 이 서버의 존재 이유가 그것뿐이다."""
    with (
        httpx.Client(base_url=os.environ["YOGOBI_TEST_BACKEND_URL"], timeout=35, trust_env=False) as be,
        TestClient(app, headers=HEADERS) as narrator,
    ):
        response = be.post("/api/v1/recommendations", json=FILTER_REQUEST)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["results"], "테스트용 BE에 20GB 이상 요금제와 넷플릭스 시드가 필요하다"

        for cost in data["results"]:
            narrated = narrator.post("/narrate", json={**cost, "missingInputs": data["missingInputs"]})
            assert narrated.status_code == 200, narrated.text
            message = narrated.json()["message"]
            assert f'실제 내시는 금액은 월 {cost["monthlyTotal"]:,}원' in message
            assert f'정가로 내는 금액은 월 {cost["baseline"]:,}원' in message
            assert f'월 {cost["monthlySavings"]:,}원 절약' in message
            assert cost["planName"] in message
            # 사유는 BE가 준 금액만 인용한다. 새 금액이 끼어들면 여기서 걸린다.
            allowed = {abs(v) for v in (cost["monthlyTotal"], cost["baseline"], cost["monthlySavings"],
                                        cost.get("annualSavings") or 0)}
            allowed |= {abs(line["amount"]) for line in cost["breakdown"]}
            for reason in narrated.json()["reasons"]:
                for found in __import__("re").findall(r"\d[\d,]*(?=\s*원)", reason):
                    assert int(found.replace(",", "")) in allowed, f"{reason} — 없는 금액"


def test_direct_calls_without_the_backend_token_are_rejected():
    """프론트가 이 서버를 직접 부르지 못한다. BE만 토큰을 안다."""
    with TestClient(app) as narrator:
        assert narrator.post("/narrate", json={}).status_code == 401
        assert narrator.get("/health").status_code == 200
