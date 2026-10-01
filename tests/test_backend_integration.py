"""별도로 실행한 테스트용 BE와의 HTTP 연동. 모델을 쓰지 않는다.

    YOGOBI_TEST_BACKEND_URL=http://127.0.0.1:18080 uv run pytest -q tests/test_backend_integration.py
"""

import os
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"Authorization": "Bearer test-backend-only-token"}
FILTER_REQUEST = {"required": {"monthlyDataGb": 20, "wantedServiceIds": [1]}}


@pytest.mark.skipif(not os.getenv("YOGOBI_TEST_BACKEND_URL"), reason="테스트용 BE 주소 미설정")
@pytest.mark.parametrize("current_plan", [None, "most_expensive"])
def test_backend_recommendation_amounts_survive_narration(current_plan):
    """BE 가 실제로 내레이터를 불러 만든 설명에 BE 금액이 그대로 옮겨지는지 본다.

    예전 테스트는 `CostResult` 를 통째로 이 서버에 보냈다 — BE 는 `NARRATE_FIELDS` 로 떼고 보내므로
    그건 실제 경로가 아니었고, 필드가 늘자 422 로 깨졌다(2026-10-01). 이제 BE 의
    `/recommendations/narrate` 를 부른다. BE 는 `NARRATOR_URL` 로 **띄워 둔 이 서버**를 부른다.
    """
    with httpx.Client(base_url=os.environ["YOGOBI_TEST_BACKEND_URL"], timeout=35, trust_env=False) as be:
        body = dict(FILTER_REQUEST)
        if current_plan:
            # 지금 요금제를 알면 '지금' 기준 문장(currentMonthly*·currentAnnualSavings)이 실제로 오간다.
            first = be.post("/api/v1/recommendations", json=body).json()["data"]["results"]
            body = {**body, "optional": {"currentPlanId": max(first, key=lambda r: r["monthlyTotal"])["planId"]}}
        data = be.post("/api/v1/recommendations", json=body).json()["data"]
        assert data["results"], "테스트용 BE에 20GB 이상 요금제와 넷플릭스 시드가 필요하다"
        narrated = be.post("/api/v1/recommendations/narrate", json=body)
        assert narrated.status_code == 200, narrated.text
        explained = narrated.json()["data"]
        top, current = data["results"][0], data.get("current")
        message = explained["message"]
        assert message, "BE 가 내레이터에 닿지 못했다 — NARRATOR_URL·토큰을 확인"
        assert f'실제 내시는 금액은 월 {top["monthlyTotal"]:,}원' in message
        if current is None:
            assert "지금 내시는" not in message
        elif current["monthlySavings"] > 0:
            annual = current.get("annualSavings")
            expected = ("1년 합계로는" if annual is not None and annual <= 0
                        else f'지금 내시는 월 {current["cost"]["monthlyTotal"]:,}원보다 월 {current["monthlySavings"]:,}원 덜 내요.')
            assert expected in message
        # 사유는 BE 가 준 금액만 인용한다. 새 금액이 끼어들면 여기서 걸린다.
        allowed = {abs(v) for v in (top["monthlyTotal"], top["baseline"], top["monthlySavings"],
                                    top.get("annualSavings") or 0)}
        allowed |= {abs(line["amount"]) for line in top["breakdown"]}
        for reason in explained["reasons"]:
            for found in re.findall(r"\d[\d,]*(?=\s*원)", reason):
                assert int(found.replace(",", "")) in allowed, f"{reason} — 없는 금액"


def test_direct_calls_without_the_backend_token_are_rejected():
    """프론트가 이 서버를 직접 부르지 못한다. BE만 토큰을 안다."""
    with TestClient(app) as narrator:
        assert narrator.post("/narrate", json={}).status_code == 401
        assert narrator.get("/health").status_code == 200
