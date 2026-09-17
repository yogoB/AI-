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


# BREAKDOWN 으로 규칙이 만드는 사유. 모델이 없거나 죽었을 때 이 값이 나간다.
RULE_REASONS = [
    "“선택약정 25% 할인”으로 월 13,750원이 빠져요.",
    "그래서 지금보다 월 17,700원 덜 내세요.",
]


def narrate(request, reasons=..., side_effect=None):
    """추천 사유만 모델이 만든다. 금액 문장은 고정 문구다."""
    result = {} if reasons is ... else {"reasons": reasons}
    with patch("app.llm.client.complete", side_effect=side_effect, return_value=result), \
            TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        return api.post("/narrate", json=request)


def test_contract_example_preserves_backend_amounts():
    response = narrate(BREAKDOWN, reasons=["선택약정 25% 할인으로 월 13,750원이 빠져요."])
    assert response.status_code == 200
    assert response.json()["reasons"] == ["선택약정 25% 할인으로 월 13,750원이 빠져요."]
    assert response.json()["message"] == (
        "“SKT 5G 슬림+”의 실제 내시는 금액은 월 71,300원이에요. "
        "아무 할인 없이 정가로 내는 금액은 월 89,000원이에요. "
        "월 17,700원 절약할 수 있어요. "
        "추가로 가족 결합 여부 정보를 알려주시면 더 정확해져요."
    )


def test_backend_cost_result_metadata_is_accepted_without_changing_message():
    # BE_main의 CostResult / MissingInput 레코드가 실제로 전달하는 추가 필드.
    request = deepcopy(BREAKDOWN)
    request.update({"planId": 42, "annualSavings": 212400})
    request["missingInputs"][0]["howToFind"] = "통신사 마이페이지 > 결합 상품"
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        baseline = api.post("/narrate", json=BREAKDOWN)
        response = api.post("/narrate", json=request)
    assert response.status_code == 200
    # 금액 문장은 그대로다. planId 는 어디에도 쓰지 않고, annualSavings 는 사유에서만 쓴다.
    assert response.json()["message"] == baseline.json()["message"]
    assert "212,400원" in " ".join(response.json()["reasons"])


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


def test_reason_quoting_an_invented_number_is_dropped():
    # 절대원칙 #2: 백엔드가 주지 않은 숫자는 사유 문장에도 넣지 못한다.
    response = narrate(BREAKDOWN, reasons=[
        "선택약정 25% 할인으로 월 13,750원이 빠져요.",
        "1년이면 212,400원을 아껴요.",
        "기본료가 49,900원이라 더 저렴해요.",
    ])
    assert response.status_code == 200
    assert response.json()["reasons"] == ["선택약정 25% 할인으로 월 13,750원이 빠져요."]


def test_plain_numbers_are_not_treated_as_amounts():
    # 금액 가드는 금액만 본다. 단위 표현까지 막으면 쓸 수 있는 문장이 남지 않는다.
    response = narrate(BREAKDOWN, reasons=["1년 내내 선택약정 25% 할인이 유지돼요."])
    assert response.json()["reasons"] == ["1년 내내 선택약정 25% 할인이 유지돼요."]


def test_annual_savings_becomes_quotable_once_the_backend_sends_it():
    request = deepcopy(BREAKDOWN) | {"annualSavings": 212400}
    response = narrate(request, reasons=["1년이면 212,400원을 아껴요."])
    assert response.json()["reasons"] == ["1년이면 212,400원을 아껴요."]


def test_model_failure_falls_back_to_rule_reasons():
    # 모델 키가 없거나 모델이 죽어도 "왜 추천됐나"가 비지 않는다. 표현만 투박해진다.
    from app.llm import client

    response = narrate(BREAKDOWN, side_effect=client.LLMError("down"))
    assert response.status_code == 200
    assert response.json()["reasons"] == RULE_REASONS
    assert "71,300원" in response.json()["message"]


def test_malformed_model_output_is_discarded():
    response = narrate(BREAKDOWN, reasons=["줄바꿈이\n들어간 사유"])
    assert response.status_code == 200
    assert "줄바꿈이" not in " ".join(response.json()["reasons"])
    assert response.json()["reasons"] == RULE_REASONS


def test_hypothetical_savings_from_missing_inputs_cannot_be_quoted():
    # missingInputs의 "가족 결합 시 최대 11,000원"은 아직 반영되지 않은 금액이다.
    # 사유가 그대로 인용하면 이미 받는 할인처럼 읽힌다.
    request = deepcopy(BREAKDOWN)
    request["missingInputs"] = [{"field": "hasFamilyBundle",
                                 "impact": "가족 결합 시 최대 11,000원 추가 절감 가능"}]
    response = narrate(request, reasons=["가족 결합으로 월 11,000원을 더 아껴요."])
    assert "11,000" not in " ".join(response.json()["reasons"])
    assert "가족 결합 여부" in response.json()["message"]


def test_identical_requests_reuse_the_first_answer():
    from unittest.mock import AsyncMock

    stub = AsyncMock(return_value={"reasons": ["월 17,700원 덜 내세요."]})
    with patch("app.llm.client.complete", stub), \
            TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        first = api.post("/narrate", json=BREAKDOWN)
        second = api.post("/narrate", json=BREAKDOWN)
    assert first.json()["reasons"] == second.json()["reasons"] == ["월 17,700원 덜 내세요."]
    assert stub.await_count == 1


def test_a_changed_recommendation_is_explained_again():
    from unittest.mock import AsyncMock

    cheaper = deepcopy(BREAKDOWN) | {"monthlyTotal": 65000}
    stub = AsyncMock(return_value={"reasons": ["월 17,700원 덜 내세요."]})
    with patch("app.llm.client.complete", stub), \
            TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        api.post("/narrate", json=BREAKDOWN)
        api.post("/narrate", json=cheaper)
    assert stub.await_count == 2


def test_a_model_outage_is_not_cached():
    from unittest.mock import AsyncMock

    from app.llm import client

    stub = AsyncMock(side_effect=[client.LLMError("down"), {"reasons": ["월 17,700원 덜 내세요."]}])
    with patch("app.llm.client.complete", stub), \
            TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        # 장애 응답은 규칙 문장으로 대신하되 캐시에 남기지 않는다. 다음 요청은 다시 모델을 부른다.
        assert api.post("/narrate", json=BREAKDOWN).json()["reasons"] == RULE_REASONS
        assert api.post("/narrate", json=BREAKDOWN).json()["reasons"] == ["월 17,700원 덜 내세요."]
    assert stub.await_count == 2


def test_the_exact_payload_backend_sends_is_accepted():
    """BE `AiGateway.narrate`가 계약 필드만 남겨 보내는 모양(`NARRATE_FIELDS`).

    `CostResult`에는 `priceCrossCheck`처럼 AI가 쓰지 않는 필드가 더 있다. BE가 떼고 보낸다 —
    `extra=forbid`라 하나라도 새면 422가 되고, BE는 그것을 장애로 삼켜 사유가 조용히 사라진다.
    Java long은 항상 값이 있고 null이 아니다. note·howToFind만 null일 수 있다.
    """
    payload = {
        "planId": 42, "planName": "5G 슬림+", "carrier": "SKT",
        "monthlyTotal": 71300, "baseline": 89000,
        "monthlySavings": 17700, "annualSavings": 212400,
        "breakdown": [{"label": "5G 슬림+ 기본료", "amount": 55000,
                       "provenance": "OFFICIAL", "note": None}],
        "missingInputs": [{"field": "hasFamilyBundle", "impact": "가족 결합 시 절감 가능",
                           "howToFind": None}],
    }
    response = narrate(payload, reasons=["기본료가 월 55,000원이에요."])
    assert response.status_code == 200
    assert response.json()["reasons"] == ["기본료가 월 55,000원이에요."]


@pytest.mark.parametrize("field", ["monthlySavings", "annualSavings"])
def test_a_worse_combination_still_gets_an_explanation(field):
    # 절감액이 음수여도 422가 아니다. BE는 baseline보다 비싼 조합도 설명을 요구한다.
    request = deepcopy(BREAKDOWN) | {field: -10000}
    assert narrate(request, reasons=[]).status_code == 200


def rules_only(request):
    """모델이 없을 때 나가는 사유. 키 미설정도 LLMError 로 들어오는 같은 경로다."""
    from app.llm import client

    return narrate(request, side_effect=client.LLMError("no key")).json()["reasons"]


def test_a_field_backend_must_strip_is_rejected_rather_than_ignored():
    """`priceCrossCheck`는 AI가 쓰지 않는 BE 내부 필드다. 받아 넘기지 않고 거부한다.

    거부가 곧 표류 감지다. BE가 계약 밖 필드를 보내기 시작하면 여기서 422가 난다.
    """
    request = deepcopy(BREAKDOWN) | {"priceCrossCheck": {"status": "UNVERIFIED"}}
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        assert api.post("/narrate", json=request).status_code == 422


def test_an_included_subscription_is_explained_without_inventing_its_list_price():
    # 요금제가 무료로 주는 구독은 금액이 0이다. 원래 가격은 요청에 없으므로 문장에 넣지 않는다.
    request = deepcopy(BREAKDOWN)
    request["breakdown"].append({"label": "넷플릭스 스탠다드", "amount": 0,
                                 "provenance": "OFFICIAL", "note": "제휴 혜택 적용"})
    reasons = rules_only(request)
    assert reasons[0] == "“넷플릭스 스탠다드” 구독을 요금제가 무료로 제공해요."


def test_a_discounted_subscription_quotes_the_amount_backend_calculated():
    request = deepcopy(BREAKDOWN)
    request["breakdown"].append({"label": "티빙 스탠다드", "amount": 9500,
                                 "provenance": "OFFICIAL", "note": "제휴 혜택 적용"})
    reasons = rules_only(request)
    assert reasons[0] == "“티빙 스탠다드” 구독을 제휴 혜택으로 월 9,500원에 이용해요."


def test_an_estimated_line_is_never_used_as_a_reason():
    # 추정치는 message가 따로 안내한다. 근거로 쓰면 확정된 할인처럼 읽힌다.
    request = deepcopy(BREAKDOWN)
    request["breakdown"].append({"label": "가족 결합 할인", "amount": -11000,
                                 "provenance": "ESTIMATED"})
    reasons = rules_only(request)
    assert all("11,000" not in reason for reason in reasons)
    assert "가족 결합 할인" not in " ".join(reasons)


def test_reasons_lead_with_the_cause_and_close_with_the_saving():
    request = deepcopy(BREAKDOWN)
    request["breakdown"].append({"label": "넷플릭스 스탠다드", "amount": 0,
                                 "provenance": "OFFICIAL", "note": "제휴 혜택 적용"})
    reasons = rules_only(request)
    assert len(reasons) == 3
    assert "구독을" in reasons[0] and "빠져요" in reasons[1]
    assert reasons[-1] == "그래서 지금보다 월 17,700원 덜 내세요."


def test_no_saving_means_no_saving_sentence():
    request = deepcopy(BREAKDOWN) | {"monthlySavings": 0}
    reasons = rules_only(request)
    assert all("덜 내세요" not in reason for reason in reasons)


def test_a_label_too_long_to_read_is_left_out_rather_than_cut():
    request = deepcopy(BREAKDOWN)
    request["breakdown"] = [{"label": "긴" * 100, "amount": -13750, "provenance": "DERIVED"}]
    reasons = rules_only(request)
    assert reasons == ["그래서 지금보다 월 17,700원 덜 내세요."]


@pytest.mark.parametrize(("label", "expected"), [
    ("선택약정 25% 할인", "으로"), ("웨이브", "로"), ("서울결합", "으로"),
    ("가족결합", "으로"), ("Netflix", "으로"), ("우리집 결합할", "로"),
])
def test_the_korean_particle_follows_the_label(label, expected):
    from app.narrate import connective

    assert connective(label) == expected


def test_a_new_missing_input_field_does_not_take_down_the_whole_explanation():
    """BE 가 안내 종류를 늘려도 금액 설명과 사유는 그대로 나간다.

    `ageLimit` 이 실제로 그랬다 — Literal 로 묶어 둔 탓에 422 가 났고 BE 가 장애로 삼켰다.
    구조는 계속 엄격하게 본다. 모르는 **값**만 그 항목을 문장에서 빠뜨린다.
    """
    request = deepcopy(BREAKDOWN)
    request["missingInputs"] = [
        {"field": "ageLimit", "impact": "가입 자격이 필요한 요금제 43건은 뺐어요",
         "howToFind": "자격이 있다면 통신사에서 더 싼 요금제를 찾을 수 있어요"},
        {"field": "hasFamilyBundle", "impact": "가족 결합 시 결합할인이 추가로 반영돼요"},
        {"field": "notInventedYet", "impact": "아직 없는 안내"},
    ]
    response = narrate(request, reasons=["월 17,700원 덜 내세요."])
    assert response.status_code == 200
    assert response.json()["reasons"] == ["월 17,700원 덜 내세요."]
    message = response.json()["message"]
    # 아는 값만 문장에 넣는다. 모르는 값은 조용히 빠진다 — 없는 이름을 지어내지 않는다.
    assert "추가로 가입 자격, 가족 결합 여부 정보를 알려주시면 더 정확해져요." in message
    assert "notInventedYet" not in message


def test_a_structural_change_is_still_rejected():
    # 값은 너그럽게, 구조는 엄격하게. 모르는 필드가 늘면 여기서 잡힌다.
    request = deepcopy(BREAKDOWN)
    request["missingInputs"] = [{"field": "ageLimit", "impact": "안내", "severity": "high"}]
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        assert api.post("/narrate", json=request).status_code == 422


def test_a_plan_with_no_benefit_and_no_discount_still_gets_a_reason():
    """기준 카탈로그 1,706개 중 1,645개가 이 경우다 — 혜택도 할인도 없어 절감액이 0이다.

    순위 근거가 없으면 사유 섹션이 대부분의 추천에서 통째로 사라진다.
    """
    request = deepcopy(BREAKDOWN) | {
        "monthlySavings": 0, "annualSavings": 0, "candidateCount": 127,
        "breakdown": [{"label": "유심 7GB 기본료", "amount": 38000, "provenance": "OFFICIAL"}],
    }
    assert rules_only(request) == ["조건에 맞는 조합 127개 중 가장 싼 선택이에요."]


def test_the_saving_sentence_carries_the_year_in_the_same_line():
    # 자리가 3개뿐이라 월·연을 두 줄로 쪼개면 다른 근거가 밀린다.
    request = deepcopy(BREAKDOWN) | {"annualSavings": 212400}
    assert "그래서 지금보다 월 17,700원, 1년이면 212,400원 덜 내세요." in rules_only(request)


def test_the_ranking_reason_never_pushes_out_a_real_benefit():
    request = deepcopy(BREAKDOWN) | {"candidateCount": 127}
    request["breakdown"].append({"label": "넷플릭스 스탠다드", "amount": 0,
                                 "provenance": "OFFICIAL", "note": "제휴 혜택 적용"})
    reasons = rules_only(request)
    assert len(reasons) == 3
    assert "구독을" in reasons[0]
    assert all("가장 싼 선택" not in reason for reason in reasons)


def test_a_single_candidate_is_not_described_as_the_cheapest_of_many():
    # 후보가 하나뿐이면 비교한 것이 없다. 비교했다고 말하지 않는다.
    request = deepcopy(BREAKDOWN) | {"monthlySavings": 0, "candidateCount": 1,
                                     "breakdown": [{"label": "기본료", "amount": 38000,
                                                    "provenance": "OFFICIAL"}]}
    assert rules_only(request) == []


def test_a_four_digit_candidate_count_survives_the_amount_guard():
    # "1,706개"는 금액처럼 보인다. 요청에 있는 값이라 통과해야 한다.
    request = deepcopy(BREAKDOWN) | {"monthlySavings": 0, "candidateCount": 1706,
                                     "breakdown": [{"label": "기본료", "amount": 38000,
                                                    "provenance": "OFFICIAL"}]}
    assert rules_only(request) == ["조건에 맞는 조합 1,706개 중 가장 싼 선택이에요."]
