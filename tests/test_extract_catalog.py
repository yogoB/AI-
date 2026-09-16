"""추출 결과의 대조 규칙. 실제 모델을 호출하지 않는다.

여기서 지키는 것은 하나다: 모델이 옮겼다고 주장한 문장이 자료에 실제로 있어야 하고,
그 행의 금액이 그 문장 안에 보여야 한다. 숫자를 만드는 일은 코드가 한다.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.extract_catalog import (
    MOBILE_PLAN_FIELDS,
    PLAN_BENEFIT_FIELDS,
    Catalog,
    Material,
    MobilePlanRow,
    PlanBenefitRow,
    check_mobile_plan,
    check_plan_benefit,
    key,
    shows_amount,
    strip_html,
    to_count,
    to_mb,
)

SOURCE = ("요금제 안내 베스트 Max 월정액 129,000원 5G 데이터 무제한 "
          "음성통화 집/이동전화 무제한 가입 대상 제한 없음 "
          "넷플릭스 스탠다드 제공 제휴 할인 5,000원 프로모션 15,000원 상당")
CATALOG = Catalog(
    plans={key("SKT|베스트 Max")},
    services={key("넷플릭스"): "1"},
    tiers={("1", key("스탠다드")): "2"},
    hint="",
)


def material(text: str = SOURCE) -> Material:
    return Material(Path("plans.html"), [{"type": "text", "text": text}], text)


def screenshot() -> Material:
    return Material(Path("shot.png"), [{"type": "image"}], None)


def plan(**changes) -> MobilePlanRow:
    values = {
        "carrier": "SKT", "planName": "베스트 Max", "networkType": "5G", "basePrice": 129000,
        "dataText": "데이터 무제한", "voiceText": "집/이동전화 무제한", "smsText": None,
        "ageLimit": "ALL", "sourceQuote": "베스트 Max 월정액 129,000원",
    }
    return MobilePlanRow(**(values | changes))


def benefit(**changes) -> PlanBenefitRow:
    values = {
        "carrier": "SKT", "planName": "베스트 Max", "serviceName": "넷플릭스",
        "tierName": "스탠다드", "benefitType": "BUNDLE_INCLUDED",
        "sourceQuote": "넷플릭스 스탠다드 제공",
    }
    return PlanBenefitRow(**(values | changes))


def test_a_quote_that_is_not_in_the_material_is_dropped():
    # 자료에 없는 문장을 근거로 내세운 행이 곧 지어낸 행이다.
    # 금액은 맞게 옮겼지만 그 문장이 자료에 없다 — 표를 잘못 읽었거나 지어낸 것이다.
    checked = check_mobile_plan(plan(sourceQuote="슈퍼 프리미엄 월정액 129,000원"),
                                material(), "https://x.kr", "2026-09-17")
    assert checked.status == "폐기"
    assert checked.reason == "인용문이 자료에 없음"
    assert checked.row is None


def test_a_quote_without_its_own_price_is_dropped():
    # 인용은 진짜인데 금액이 그 안에 없으면 금액의 근거가 없는 것이다.
    checked = check_mobile_plan(plan(basePrice=55000, sourceQuote="요금제 안내 베스트 Max"),
                                material(), "https://x.kr", "2026-09-17")
    assert (checked.status, checked.reason) == ("폐기", "금액이 인용문에 없음")


def test_line_breaks_between_the_material_and_the_quote_do_not_reject_a_real_row():
    checked = check_mobile_plan(plan(sourceQuote="베스트   Max\t월정액 129,000원"),
                                material(), "https://x.kr", "2026-09-17")
    assert checked.status == "채택"


def test_a_price_is_not_matched_inside_a_bigger_number():
    # "15,000원" 안의 "5,000"을 5,000원의 근거로 인정하면 대조가 통과 도장이 된다.
    assert shows_amount("제휴 할인 5,000원", 5000)
    assert not shows_amount("프로모션 15,000원 상당", 5000)
    assert not shows_amount("월 129,000원", 29000)


def test_data_allowance_is_converted_by_the_script_not_the_model():
    # 모델은 "110GB"를 그대로 옮기고, ×1024는 코드가 한다(절대 원칙 1).
    assert to_mb("110GB+최대 5Mbps") == 112640
    assert to_mb("데이터 무제한") == 999999
    assert to_mb("확인 필요") is None


def test_throughput_after_exhaustion_is_not_read_as_an_allowance():
    # "5Mbps"의 Mb 를 5MB 로 읽으면 무제한 요금제가 5MB 요금제가 된다.
    assert to_mb("소진 후 최대 5Mbps") is None
    assert to_count("월 300분") == 300
    assert to_count("집/이동전화 무제한") == 999999


def test_a_plan_whose_data_allowance_can_not_be_read_is_dropped():
    # data_mb 는 적재에서 비울 수 없고 후보 조회의 기준이다.
    checked = check_mobile_plan(plan(dataText="문의"), material(), "https://x.kr", "2026-09-17")
    assert checked.status == "폐기"
    assert "데이터 제공량" in checked.reason


def test_unknown_eligibility_is_dropped_rather_than_opened_to_everyone():
    # 빈 age_limit 은 '누구나'로 읽힌다(G-18). 모르는 것을 통과시키면 자격 상품이 전체에 추천된다.
    checked = check_mobile_plan(plan(ageLimit="UNKNOWN"), material(), "https://x.kr", "2026-09-17")
    assert (checked.status, checked.reason) == ("폐기", "가입 대상 미확인")


def test_an_accepted_plan_keeps_the_seed_csv_column_order():
    checked = check_mobile_plan(plan(), material(), "https://x.kr", "2026-09-17")
    assert checked.status == "채택"
    assert list(checked.row) == MOBILE_PLAN_FIELDS
    assert checked.row["data_mb"] == "999999"
    assert checked.row["source_url"] == "https://x.kr"
    # 약정 할인액은 기준 CSV 전체가 비어 있다. 모델에 묻지 않고 비운다.
    assert checked.row["contract_discount_12m"] == ""


def test_an_image_can_not_be_matched_and_is_marked_for_human_review():
    # 스크린샷에는 대조할 글자가 없다. 통과가 아니라 사람이 봐야 한다는 표시다.
    checked = check_mobile_plan(plan(), screenshot(), "https://x.kr", "2026-09-17")
    assert checked.status == "검수필요"
    assert checked.row is not None


def test_a_free_benefit_without_a_tier_loses_its_money_effect():
    # 어느 등급인지 모르는데 FREE 로 넣으면 프리미엄까지 0원이 되어 실제보다 싸게 추천한다.
    checked = check_plan_benefit(benefit(tierName=None, benefitType="FREE"), material(),
                                 "https://x.kr", "2026-09-17", CATALOG)
    assert checked.status == "채택"
    assert checked.row["benefit_type"] == "BUNDLE_INCLUDED"
    assert checked.row["tier_id"] == "" and checked.row["discount_value"] == ""


def test_a_known_tier_is_resolved_to_its_catalog_id():
    checked = check_plan_benefit(benefit(), material(), "https://x.kr", "2026-09-17", CATALOG)
    assert list(checked.row) == PLAN_BENEFIT_FIELDS
    assert (checked.row["service_id"], checked.row["tier_id"]) == ("1", "2")


@pytest.mark.parametrize(("changes", "expected"), [
    ({"serviceName": "쿠팡플레이"}, "카탈로그에 없는 서비스"),
    ({"tierName": "메가프리미엄"}, "카탈로그에 없는 등급"),
    ({"planName": "없는 요금제"}, "카탈로그에 없는 요금제"),
])
def test_names_the_catalog_does_not_have_are_dropped_instead_of_invented(changes, expected):
    # 모델이 ID를 지어내지 못하게 이름만 받고 대조는 코드가 한다.
    checked = check_plan_benefit(benefit(**changes), material(), "https://x.kr",
                                 "2026-09-17", CATALOG)
    assert checked.status == "폐기"
    assert expected in checked.reason


def test_a_bundle_benefit_can_not_carry_a_discount_value():
    # db/migration/V1 의 plan_benefit CHECK 과 같은 규칙. 적재 전에 막는다.
    with pytest.raises(ValidationError):
        benefit(benefitType="BUNDLE_INCLUDED", discountValue=5000)
    with pytest.raises(ValidationError):
        benefit(benefitType="FIXED_DISCOUNT", discountValue=None)
    with pytest.raises(ValidationError):
        benefit(benefitType="RATE_DISCOUNT", discountValue=250)


def test_script_and_style_text_never_reaches_the_model():
    # 모델이 보는 문자열과 대조하는 문자열이 같아야 하므로 정리는 한 곳에서만 한다.
    raw = "<html><head><title>t</title></head><body><script>var a='55,000원'</script>" \
          "<p>월정액 129,000원</p><style>.a{}</style></body></html>"
    text = strip_html(raw)
    assert "55,000원" not in text
    assert "월정액 129,000원" in text
