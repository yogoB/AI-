"""중복 결제 탐지 설명. 금액은 BE 가 준 값을 표기만 한다."""

from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"Authorization": "Bearer test-backend-only-token"}
FINDING = {"rule": "BENEFIT_OVERLAP", "targetName": "넷플릭스", "wastedAmount": 13500,
           "provenance": "DERIVED"}


def explain(findings):
    with TestClient(app, headers=HEADERS) as api:
        return api.post("/narrate/detections", json={"findings": findings})


def test_a_known_rule_gets_its_title_and_next_step():
    line = explain([FINDING]).json()["lines"][0]
    assert line["title"] == "요금제에 포함된 구독을 따로 결제 중"
    assert line["target"] == "넷플릭스"
    assert line["amount"] == "월 13,500원"
    assert "개별 결제를 해지하면" in line["how"]


def test_an_estimated_amount_is_not_stated_as_certain():
    """등급을 밝히지 않는 요금제는 상한으로 잡은 값이다. 단정하면 사용자가 그 금액을 기대한다."""
    line = explain([FINDING | {"provenance": "ESTIMATED"}]).json()["lines"][0]
    assert line["amount"] == "최대 월 13,500원"


def test_an_unknown_rule_is_shown_rather_than_dropped():
    """BE 가 규칙을 늘려도 화면에서 조용히 사라지지 않는다 — 문구만 비워 둔다."""
    line = explain([FINDING | {"rule": "NOT_INVENTED_YET"}]).json()["lines"][0]
    assert line["title"] == "NOT_INVENTED_YET"
    assert line["how"] == ""
    assert line["amount"] == "월 13,500원"


def test_the_summary_leads_with_the_conclusion():
    body = explain([FINDING, FINDING | {"rule": "TIER_DUPLICATE"}]).json()
    assert body["summary"].startswith("겹치는 결제 2건을 찾았어요.")
    # 합계 금액을 말하지 않는다 — 더하는 순간 계산이 되고, 계산은 BE 몫이다.
    assert "27,000" not in body["summary"]


def test_nothing_found_says_so_plainly():
    body = explain([]).json()
    assert body["lines"] == []
    assert body["summary"] == "중복으로 새는 금액이 없어요."


def test_a_negative_amount_is_rejected_rather_than_shown():
    assert explain([FINDING | {"wastedAmount": -1}]).status_code == 422


def test_the_endpoint_needs_the_backend_token():
    with TestClient(app) as api:
        assert api.post("/narrate/detections", json={"findings": []}).status_code == 401
