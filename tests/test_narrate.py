from copy import deepcopy

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
    "정가보다 월 17,700원 덜 내세요.",
]


def narrate(request):
    """금액 문장도 사유도 규칙이 만든다. 같은 입력이면 같은 출력이다."""
    with TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        return api.post("/narrate", json=request)


def test_contract_example_preserves_backend_amounts():
    response = narrate(BREAKDOWN)
    assert response.status_code == 200
    assert response.json()["reasons"] == RULE_REASONS
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


def test_the_amount_guard_drops_a_line_quoting_an_unknown_amount():
    """규칙은 BE 값만 쓰지만 가드는 그대로 둔다 — 문장을 늘릴 때 잘못 들어오는 금액을 여기서 막는다."""
    from app.narrate import NarrateRequest, known_numbers, quotes_known_amounts_only

    known = known_numbers(NarrateRequest(**BREAKDOWN))
    assert quotes_known_amounts_only("선택약정 25% 할인으로 월 13,750원이 빠져요.", known)
    assert not quotes_known_amounts_only("1년이면 212,400원을 아껴요.", known)   # 아직 안 받은 값
    assert not quotes_known_amounts_only("기본료가 49,900원이라 더 저렴해요.", known)


def test_plain_numbers_are_not_treated_as_amounts():
    # 금액 가드는 금액만 본다. "1년"·"25%" 까지 막으면 쓸 수 있는 문장이 남지 않는다.
    from app.narrate import NarrateRequest, known_numbers, quotes_known_amounts_only

    known = known_numbers(NarrateRequest(**BREAKDOWN))
    assert quotes_known_amounts_only("1년 내내 선택약정 25% 할인이 유지돼요.", known)


def test_annual_savings_is_quoted_once_the_backend_sends_it():
    request = deepcopy(BREAKDOWN) | {"annualSavings": 212400}
    reasons = narrate(request).json()["reasons"]
    assert "1년이면 212,400원 덜 내세요." in " ".join(reasons)


def test_the_same_request_always_gets_the_same_sentences():
    """재현되지 않는 설명은 금액 서비스에서 신뢰를 깎는다. 규칙이라 두 번 물어도 같다."""
    assert narrate(BREAKDOWN).json() == narrate(BREAKDOWN).json()


def test_hypothetical_savings_from_missing_inputs_cannot_be_quoted():
    # missingInputs의 "가족 결합 시 최대 11,000원"은 아직 반영되지 않은 금액이다.
    # 사유가 그대로 인용하면 이미 받는 할인처럼 읽힌다.
    request = deepcopy(BREAKDOWN)
    request["missingInputs"] = [{"field": "hasFamilyBundle",
                                 "impact": "가족 결합 시 최대 11,000원 추가 절감 가능"}]
    response = narrate(request)
    assert "11,000" not in " ".join(response.json()["reasons"])
    assert "가족 결합 여부" in response.json()["message"]


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
    response = narrate(payload)
    assert response.status_code == 200
    assert response.json()["reasons"] == ["정가보다 월 17,700원, 1년이면 212,400원 덜 내세요."]


@pytest.mark.parametrize("field", ["monthlySavings", "annualSavings"])
def test_a_worse_combination_still_gets_an_explanation(field):
    # 절감액이 음수여도 422가 아니다. BE는 baseline보다 비싼 조합도 설명을 요구한다.
    request = deepcopy(BREAKDOWN) | {field: -10000}
    assert narrate(request).status_code == 200


def rules_only(request):
    """규칙이 만든 사유. 이 서버에는 다른 공급자가 없다."""
    return narrate(request).json()["reasons"]


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
    assert reasons[-1] == "정가보다 월 17,700원 덜 내세요."


def test_no_saving_means_no_saving_sentence():
    request = deepcopy(BREAKDOWN) | {"monthlySavings": 0}
    reasons = rules_only(request)
    assert all("덜 내세요" not in reason for reason in reasons)


def test_a_label_too_long_to_read_is_left_out_rather_than_cut():
    request = deepcopy(BREAKDOWN)
    request["breakdown"] = [{"label": "긴" * 100, "amount": -13750, "provenance": "DERIVED"}]
    reasons = rules_only(request)
    assert reasons == ["정가보다 월 17,700원 덜 내세요."]


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
    response = narrate(request)
    assert response.status_code == 200
    assert response.json()["reasons"] == RULE_REASONS
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
    assert "정가보다 월 17,700원, 1년이면 212,400원 덜 내세요." in rules_only(request)


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


def test_notices_join_backend_guidance_without_rewriting_it():
    """`impact`·`howToFind`는 BE가 쓴 문장이다. 고쳐 쓰면 "최대 11,000원" 같은 구체성이 뭉개진다."""
    request = deepcopy(BREAKDOWN)
    request["missingInputs"] = [
        {"field": "hasFamilyBundle", "impact": "가족 결합 시 최대 11,000원 추가 절감 가능",
         "howToFind": "통신사 마이페이지 > 결합 상품"},
        {"field": "networkType", "impact": "망 종류를 알면 더 정확해져요"},   # howToFind 없음
    ]
    notices = narrate(request).json()["notices"]
    assert notices == ["가족 결합 시 최대 11,000원 추가 절감 가능 — 통신사 마이페이지 > 결합 상품",
                       "망 종류를 알면 더 정확해져요"]


def test_a_notice_too_long_to_read_is_dropped_rather_than_cut():
    # 잘린 안내는 오해를 만든다. 통째로 빼는 편이 낫다.
    request = deepcopy(BREAKDOWN)
    request["missingInputs"] = [{"field": "hasFamilyBundle", "impact": "가" * 301}]
    assert narrate(request).json()["notices"] == []


def test_no_missing_inputs_means_no_notices():
    request = deepcopy(BREAKDOWN) | {"missingInputs": []}
    assert narrate(request).json()["notices"] == []


def notices_of(missing_inputs):
    request = deepcopy(BREAKDOWN) | {"missingInputs": missing_inputs}
    response = narrate(request)
    assert response.status_code == 200, response.text[:200]
    return response.json()["notices"]


def test_a_line_break_in_backend_guidance_does_not_take_down_the_explanation():
    """`impact`·`howToFind` 는 BE 의 자유 서술이라 줄바꿈이 섞일 수 있다.

    그대로 두면 Notice 패턴에 걸려 응답이 500 이 되고, BE 는 그것을 장애로 삼켜
    message·reasons 까지 전부 사라진다. 안내 한 줄 때문에 설명 전체를 잃지 않는다.
    """
    notices = notices_of([{"field": "contractType", "impact": "앞줄\n뒷줄",
                           "howToFind": "가운데\r\n줄바꿈"}])
    assert notices == ["앞줄 뒷줄 — 가운데 줄바꿈"]


def test_the_notice_limits_are_the_same_numbers_the_backend_enforces():
    """BE `NarratorClient.lines(notices, 10, 300)` 와 같은 값이어야 한다.

    여기서 한 줄이라도 넘겨 보내면 BE 는 관대하게 자르지 않고 **설명 전체를 폐기한다.**
    """
    assert notices_of([{"field": "contractType", "impact": "가" * 300}]) == ["가" * 300]
    assert notices_of([{"field": "contractType", "impact": "가" * 301}]) == []

    many = [{"field": "contractType", "impact": f"안내 {n}"} for n in range(12)]
    assert len(notices_of(many)) == 10


def narrate_breakdown(breakdown, **changes):
    request = deepcopy(BREAKDOWN) | {"breakdown": breakdown} | changes
    return narrate(request)


def test_a_user_provided_amount_does_not_take_down_the_explanation():
    """BE `Provenance` 는 4값인데 여기 Literal 은 3값이었다 — `USER_PROVIDED` 가 빠져 있었다.

    가족결합 할인 줄이 그 출처로 나가므로, 가족결합을 적어 넣은 **모든** 사용자의 추천에서
    422 가 났고 BE 가 장애로 삼켜 설명·사유·안내가 전부 사라졌다. 운영에서 재현한 사고다.
    """
    response = narrate_breakdown([
        {"label": "기본료", "amount": 17000, "provenance": "OFFICIAL"},
        {"label": "가족결합 할인", "amount": -5000, "provenance": "USER_PROVIDED"},
    ])
    assert response.status_code == 200
    body = response.json()
    # 우리가 계산한 값이 아니라는 것을 밝힌다. 추정치와는 다른 문장이다.
    assert '“가족결합 할인”(-5,000원) 항목은 적어 주신 금액이에요.' in body["message"]
    # 사용자가 적어 준 확정 금액이라 사유의 근거로 쓸 수 있다. 추정치와 다른 점이다.
    assert '“가족결합 할인”으로 월 5,000원이 빠져요.' in body["reasons"]


def test_an_unknown_provenance_degrades_instead_of_422():
    """BE 가 출처를 또 늘려도 설명이 사라지지 않는다. 같은 사고가 세 번째라 값을 열거로 묶지 않는다.

    모르는 출처는 문장을 붙이지 않고 근거로도 쓰지 않는다 — 얼마나 믿을 값인지 모르면서
    "이래서 추천됐다"고 말하지 않는다.
    """
    response = narrate_breakdown([
        {"label": "기본료", "amount": 20000, "provenance": "OFFICIAL"},
        {"label": "알 수 없는 할인", "amount": -5000, "provenance": "PARTNER_QUOTED"},
    ])
    assert response.status_code == 200
    body = response.json()
    assert "알 수 없는 할인" not in body["message"]
    assert all("알 수 없는 할인" not in reason for reason in body["reasons"])


def test_an_estimate_is_still_told_apart_from_a_user_number():
    body = narrate_breakdown([
        {"label": "기본료", "amount": 20000, "provenance": "OFFICIAL"},
        {"label": "가족결합 할인", "amount": -5000, "provenance": "USER_PROVIDED"},
        {"label": "제휴 할인", "amount": -2000, "provenance": "ESTIMATED"},
    ]).json()
    assert "항목은 추정치예요." in body["message"]
    assert "항목은 적어 주신 금액이에요." in body["message"]
    # 추정치는 근거로 쓰지 않는다 — 확정된 할인처럼 읽힌다.
    assert all("제휴 할인" not in reason for reason in body["reasons"])


def test_a_malformed_provenance_is_still_rejected():
    # 값은 너그럽게, 구조는 엄격하게. 출처 자리에 아무 문자열이나 오는 것은 계약 위반이다.
    for bad in ("official", "USER PROVIDED", "", "1ST"):
        response = narrate_breakdown([{"label": "기본료", "amount": 1, "provenance": bad}])
        assert response.status_code == 422, bad


def test_an_informational_notice_is_not_turned_into_a_question():
    """BE 는 `missingInputs` 로 "더 알려달라"와 "이렇게 처리했다"를 **같이** 보낸다.

    `familyBundleDiscountKrw` 가 실제로 양쪽에 쓰인다 — 할인액을 안 줬을 때는 요청이지만,
    다른 통신사 요금제가 추천됐을 때는 "적어 주신 11,000원은 SKT 에만 반영했어요"라는 통보다.
    후자를 "추가로 알려주시면 더 정확해져요"로 바꾸면 **이미 답한 것을 다시 묻는다.**
    안내 원문은 notices 가 그대로 나르므로 문장에서는 빠지는 것이 맞다.
    """
    request = deepcopy(BREAKDOWN) | {"missingInputs": [
        {"field": "familyBundleDiscountKrw",
         "impact": "가족결합 할인 11,000원은 SKT 요금제에만 반영했어요",
         "howToFind": "지금 통신사 안에서 바꾸면 할인은 그대로예요"},
        {"field": "networkType", "impact": "망 종류를 지정하면 후보를 더 정확히 좁혀요"},
    ]}
    body = narrate(request).json()
    assert "추가로 현재 망 종류 정보를 알려주시면 더 정확해져요." in body["message"]
    assert "가족결합" not in body["message"]
    # 빠지는 것은 문장뿐이다 — 안내는 두 줄 그대로 화면에 간다.
    assert len(body["notices"]) == 2
    assert any("11,000원은 SKT" in notice for notice in body["notices"])


def narrate_with_current(current, **changes):
    request = deepcopy(BREAKDOWN) | changes
    if current is not None:
        request["currentMonthlyTotal"] = current
    return narrate(request).json()


def test_without_the_current_bill_the_sentences_stay_list_price_based():
    # 지금 내는 금액을 모르면 기준은 정가뿐이다. 기존 동작 그대로여야 한다.
    body = narrate_with_current(None)
    assert "아무 할인 없이 정가로 내는 금액은 월 89,000원이에요." in body["message"]
    assert "월 17,700원 절약할 수 있어요." in body["message"]
    assert "지금 내시는" not in body["message"]


def test_the_current_bill_becomes_the_yardstick_when_the_backend_sends_it():
    """화면 히어로가 "지금보다 얼마"를 말하는데 문장은 "정가 대비 절약 없음"이라고 말하던 모순.

    라이트 모드 실사용에서 실제로 본 값이다 — 현재 72,500원, 추천 21,490원인데
    절감액(정가 대비)은 0이라 "절약되는 금액은 없어요"가 나갔다.
    """
    body = narrate_with_current(72500, monthlyTotal=21490, baseline=21490,
                                monthlySavings=0, annualSavings=0)
    assert "지금 내시는 월 72,500원보다 월 51,010원 덜 내요." in body["message"]
    # 지금을 아는 순간 정가 문장 두 개는 빠진다. 같은 화면에서 두 기준이 싸우지 않게.
    assert "정가로 내는 금액은" not in body["message"]
    assert "절약되는 금액은 없어요" not in body["message"]


def test_the_same_amount_is_said_plainly():
    body = narrate_with_current(71300)
    assert "지금 내시는 금액과 같아요." in body["message"]


def test_a_pricier_combination_says_so_and_says_why():
    # 원하는 데이터·구독을 다 담으면 지금보다 비쌀 수 있다. 감추지 않는다.
    body = narrate_with_current(60000)
    assert "지금보다 월 11,300원 더 내는 조합이에요" in body["message"]
    assert "원하시는 데이터·구독을 다 담으면" in body["message"]


def test_the_gap_is_quotable_so_a_reason_using_it_is_not_discarded():
    # known_numbers 에 없으면 금액 가드가 그 문장을 버린다.
    from app.narrate import NarrateRequest, current_savings, known_numbers

    request = NarrateRequest.model_validate(deepcopy(BREAKDOWN) | {"currentMonthlyTotal": 90000})
    assert current_savings(request) == 18700
    known = known_numbers(request)
    assert 90000 in known and 18700 in known


def test_the_reason_says_list_price_not_now():
    """사유의 절감액은 baseline 기준이다. message 가 '지금'을 말하기 시작하면 두 수가 충돌한다.

    화면의 '정가 기준' 열과 같은 말을 쓴다.
    """
    body = narrate_with_current(90000)
    assert any("정가보다 월 17,700원" in reason for reason in body["reasons"])
    assert all("지금보다" not in reason for reason in body["reasons"])


def test_the_backend_supplied_saving_is_copied_not_recomputed():
    """BE 가 `current.monthlySavings` 를 보내면 내레이터는 빼지 않고 옮기기만 한다(절대 원칙 1).

    운영에서 확인한 값을 그대로 쓴다 — 현재 70,390원, 지금보다 17,100원 덜 냄.
    """
    body = narrate_with_current(70390, monthlyTotal=53290, currentMonthlySavings=17100)
    assert "지금 내시는 월 70,390원보다 월 17,100원 덜 내요." in body["message"]


def test_two_numbers_that_do_not_subtract_are_not_shown_side_by_side():
    """BE 가 보낸 절감액이 두 금액의 차와 안 맞으면 '지금' 기준으로 말하지 않는다.

    서로 빼지지 않는 두 수를 한 화면에 나란히 놓느니 정가 기준으로 물러나는 편이 낫다.
    422 로 설명 전체를 죽이지도 않는다 — 오늘만 그 사고를 세 번 봤다.
    """
    body = narrate_with_current(90000, currentMonthlySavings=999)
    assert "지금 내시는" not in body["message"]
    assert "아무 할인 없이 정가로 내는 금액은 월 89,000원이에요." in body["message"]
    assert body["reasons"]  # 설명 자체는 살아 있다


def test_a_negative_supplied_saving_means_the_combination_costs_more():
    body = narrate_with_current(60000, currentMonthlySavings=-11300)
    assert "지금보다 월 11,300원 더 내는 조합이에요" in body["message"]


def test_the_supplied_saving_is_quotable():
    from app.narrate import NarrateRequest, current_savings, known_numbers

    request = NarrateRequest.model_validate(
        deepcopy(BREAKDOWN) | {"currentMonthlyTotal": 90000, "currentMonthlySavings": 18700})
    assert current_savings(request) == 18700
    assert 18700 in known_numbers(request)
