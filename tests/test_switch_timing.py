"""변경 시점 문구. 판정·개월·금액은 BE 가 준 값을 표기만 한다."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"Authorization": "Bearer test-backend-only-token"}
BASE = {"status": "SWITCH_NOW", "paybackMonths": 3, "remainingContractMonths": 0,
        "monthlySavings": 12000, "switchingCost": 33000, "expiryDate": None}


def explain(**over):
    with TestClient(app, headers=HEADERS) as api:
        return api.post("/narrate/switch-timing", json=BASE | over)


def test_switching_now_states_the_cost_and_how_long_to_recover_it():
    body = explain().json()
    assert body["headline"] == "지금이 최적 실행 시점"
    assert "전환비용 33,000원을 3개월이면 회수해요" in body["note"]


def test_no_switching_cost_says_so_instead_of_zero_months():
    """"0개월이면 회수해요"는 읽히지 않는다. 비용이 없으면 없다고 말한다."""
    note = explain(switchingCost=0, paybackMonths=0).json()["note"]
    # BE 는 전환비용을 **모르면** 0 을 보낸다 — 없다고 단정하지 않는다(2026-09-27).
    assert "위약금·할부금이 없다면 지금 옮기는 게 바로 이득이에요" in note
    assert "0개월" not in note


def test_waiting_names_the_expiry_day_when_the_user_gave_one():
    note = explain(status="WAIT_UNTIL_EXPIRY", remainingContractMonths=8,
                   expiryDate="2099-12-03").json()["note"]
    assert "약정 만료일 2099년 12월 3일까지 기다리는 게 이득이에요." in note


def test_waiting_without_a_date_falls_back_to_the_remaining_months():
    """날짜를 모르면 "언제까지"를 말할 수 없다. 남은 개월로 대신한다."""
    note = explain(status="WAIT_UNTIL_EXPIRY", remainingContractMonths=8).json()["note"]
    assert "약정이 8개월 남아" in note
    assert "만료일" not in note


def test_no_benefit_does_not_promise_a_saving():
    body = explain(status="NO_BENEFIT", paybackMonths=None, monthlySavings=0).json()
    assert body["headline"] == "절감 없음 · 참고용 일정"
    assert "옮겨도 절감이 없어요" in body["note"]


@pytest.mark.parametrize("bad", [
    {"status": "MAYBE_LATER"},
    {"switchingCost": -1},
    {"remainingContractMonths": -1},
    {"expiryDate": "2026-02-31"},      # 달력에 없는 날
    {"expiryDate": "2026/12/03"},
])
def test_a_value_backend_would_never_send_is_rejected(bad):
    assert explain(**bad).status_code == 422


def test_the_endpoint_needs_the_backend_token():
    with TestClient(app) as api:
        assert api.post("/narrate/switch-timing", json=BASE).status_code == 401


def test_switch_now_without_payback_months_is_rejected():
    """계약 밖 조합("None개월이면 회수해요")은 문장을 만들지 않고 422 다."""
    assert explain(paybackMonths=None).status_code == 422


def test_a_past_expiry_date_is_not_offered_as_something_to_wait_for():
    """BE 는 날짜 형식만 본다. 이미 지난 날까지 "기다리라"고 하면 따를 수 없는 안내다."""
    note = explain(status="WAIT_UNTIL_EXPIRY", remainingContractMonths=6,
                   expiryDate="2025-01-31").json()["note"]
    assert "2025년" not in note
    assert "약정이 6개월 남아" in note


def test_switch_now_after_the_contract_ended_does_not_show_zero_months_left():
    note = explain(switchingCost=200000, paybackMonths=40, remainingContractMonths=0).json()["note"]
    assert "약정 잔여 0개월" not in note
    assert "전환비용 200,000원을 40개월이면 회수해요." in note
