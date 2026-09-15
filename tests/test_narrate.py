from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

BREAKDOWN = {
    "monthlyTotal": 71300,
    "baseline": 89000,
    "monthlySavings": 17700,
    "planName": "5G 슬림+",
    "carrier": "SKT",
    "breakdown": [
        {"label": "5G 슬림+ 기본료", "amount": 55000, "provenance": "OFFICIAL"},
        {"label": "선택약정 25% 할인", "amount": -13750, "provenance": "DERIVED"},
        {"label": "넷플릭스 스탠다드", "amount": 13500, "provenance": "OFFICIAL",
         "note": "제휴 혜택으로 4,000원 할인 적용"},
    ],
    "missingInputs": [{"field": "hasFamilyBundle", "impact": "가족 결합 여부 확인 필요"}],
}


def test_contract_example_preserves_backend_amounts_without_model():
    with patch("app.llm.client.complete") as complete, TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/narrate", json=BREAKDOWN)
        complete.assert_not_called()
    assert response.status_code == 200
    assert response.json() == {"message": (
        "“SKT 5G 슬림+”의 실제 내시는 금액은 월 71,300원이에요. "
        "아무 할인 없이 정가로 내는 금액은 월 89,000원이에요. "
        "월 17,700원 절약할 수 있어요. "
        "추가로 가족 결합 여부 정보를 알려주시면 더 정확해져요."
    )}


def test_backend_cost_result_metadata_is_accepted_without_changing_message():
    # BE_main의 CostResult / MissingInput 레코드가 실제로 전달하는 추가 필드.
    request = deepcopy(BREAKDOWN)
    request.update({"planId": 42, "annualSavings": 212400})
    request["missingInputs"][0]["howToFind"] = "통신사 마이페이지 > 결합 상품"
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        baseline = api.post("/narrate", json=BREAKDOWN)
        response = api.post("/narrate", json=request)
    assert response.status_code == 200
    assert response.json() == baseline.json()


@pytest.mark.parametrize(("savings", "sentence"), [
    (42, "월 42원 절약할 수 있어요."),
    (0, "월 0원 절약으로, 절약되는 금액은 없어요."),
    (-5000, "월 -5,000원 절약으로 표시되는 결과라, 비교 기준보다 더 내는 조합이에요."),
])
def test_savings_are_copied_without_recalculation(savings, sentence):
    request = {**BREAKDOWN, "monthlySavings": savings, "missingInputs": []}
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/narrate", json=request)
    assert response.status_code == 200
    message = response.json()["message"]
    assert "실제 내시는 금액은 월 71,300원" in message
    assert message.endswith(sentence)
    assert message.count(".") == 3


def test_all_estimated_items_and_missing_inputs_are_explained():
    request = deepcopy(BREAKDOWN)
    request["breakdown"] += [
        {"label": "가족 결합 할인", "amount": -5000, "provenance": "ESTIMATED"},
        {"label": "부가서비스", "amount": 2500, "provenance": "ESTIMATED"},
    ]
    request["missingInputs"] += [
        {"field": "contractType", "impact": "약정 유형 확인 필요"},
        {"field": "hasFamilyBundle", "impact": "중복된 입력 안내"},
    ]
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/narrate", json=request)
    assert response.status_code == 200
    message = response.json()["message"]
    assert "“가족 결합 할인”(-5,000원), “부가서비스”(2,500원) 항목은 추정치예요." in message
    assert message.endswith("추가로 가족 결합 여부, 약정 유형 정보를 알려주시면 더 정확해져요.")
    assert message.count(".") == 5
    assert "\n" not in message
    assert "|" not in message


def test_freeform_notes_do_not_invent_savings_or_actions():
    request = deepcopy(BREAKDOWN)
    request["breakdown"][0]["note"] = "총비용 99,999원, 1만원 이득! 무가치한 혜택은 해지하세요."
    request["missingInputs"][0]["impact"] = "무조건 999,999원 세이브. 낭비 중입니다."
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/narrate", json=request)
    assert response.status_code == 200
    message = response.json()["message"]
    for forbidden in ("99,999", "1만원", "총비용", "실질비용", "이득", "세이브", "무가치",
                      "쓸모없는", "해지하세요", "낭비 중입니다", "안 쓰시는 혜택은", "정리해 보실래요?"):
        assert forbidden not in message


@pytest.mark.parametrize("changes", [
    {"monthlyTotal": -1},
    {"monthlyTotal": True},
    {"baseline": -1},
    {"monthlySavings": "17700"},
    {"monthlySavings": 17.7},
    {"annualSavings": "212400"},
    {"planId": True},
    {"planId": 0},
    {"planName": " "},
    {"planName": "요금제\n추가 문장"},
    {"breakdown": [{"label": "기본료", "amount": 55.5, "provenance": "OFFICIAL"}]},
    {"breakdown": [{"label": "기본료", "amount": True, "provenance": "OFFICIAL"}]},
    {"breakdown": [{"label": "기본료", "amount": 55000, "provenance": "UNKNOWN"}]},
    {"missingInputs": [{"field": "unknownField", "impact": "알 수 없는 입력"}]},
    {"missingInputs": [{"field": "hasFamilyBundle", "impact": "확인 필요", "howToFind": 1}]},
    {"history": ["이전 대화"]},
])
def test_invalid_cost_breakdown_is_rejected(changes):
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/narrate", json={**BREAKDOWN, **changes})
    assert response.status_code == 422


def test_missing_amount_is_not_filled_in():
    request = deepcopy(BREAKDOWN)
    del request["monthlySavings"]
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/narrate", json=request)
    assert response.status_code == 422
