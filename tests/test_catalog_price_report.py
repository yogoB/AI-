"""가격 불일치 배치의 판정 규칙. 실제 모델이나 웹 검색을 호출하지 않는다."""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.catalog import CatalogCandidate, CatalogSearchResponse
from scripts.verify_catalog_prices import Target, check, judge, mobile_targets, subscription_targets

TARGET = Target("MOBILE_PLAN", "통신", "NA00009818", "SKT", "베스트 Max", 129000)


def found(price: int, sale_status: str = "AVAILABLE") -> CatalogSearchResponse:
    return CatalogSearchResponse(
        status="CANDIDATE_FOUND", confidence=0.9, sources=[], checkedAt=datetime.now(UTC),
        candidate=CatalogCandidate(
            productType="MOBILE_PLAN", provider="SKT", productName="베스트 Max",
            monthlyPriceWon=price, saleStatus=sale_status,
            sourceUrl="https://m.tworld.co.kr/product",
        ),
    )


def test_same_price_is_not_reported_as_a_mismatch():
    report = judge(TARGET, found(129000))
    assert report["판정"] == "일치"
    assert report["차액"] == "0"


def test_price_gap_is_reported_with_its_source():
    report = judge(TARGET, found(139000))
    assert report["판정"] == "불일치"
    assert report["차액"] == "10000"
    assert report["출처URL"] == "https://m.tworld.co.kr/product"


def test_ended_product_is_flagged_even_when_the_price_matches():
    report = judge(TARGET, found(129000, sale_status="ENDED"))
    assert report["판정"] == "일치"
    assert report["비고"] == "공식 출처에서 판매 종료로 표시됨"


def test_not_found_never_produces_a_price():
    response = CatalogSearchResponse(status="NOT_FOUND", candidate=None, confidence=0.9,
                                     sources=[], checkedAt=datetime.now(UTC))
    report = judge(TARGET, response)
    assert report["확인가격"] == "" and report["차액"] == ""
    assert report["판정"] == "NOT_FOUND"


@pytest.mark.parametrize(("status", "expected"), [(502, "출처거부"), (503, "호출실패")])
def test_rejected_source_is_kept_apart_from_a_price_mismatch(monkeypatch, status, expected):
    async def reject(request, max_uses=3):
        raise HTTPException(status_code=status, detail={"code": "AI-CATALOG-001"})

    monkeypatch.setattr("scripts.verify_catalog_prices.find_candidate", reject)
    report = asyncio.run(check(TARGET))
    assert report["판정"] == expected
    assert report["확인가격"] == "" and report["차액"] == ""


def test_excluded_and_non_krw_rows_are_left_out():
    plans = [
        {"추천판정": "추천 가능", "브랜드": "SKT", "요금제명": "A", "월정액(원)": "55,000"},
        {"추천판정": "추천 제외", "브랜드": "SKT", "요금제명": "B", "월정액(원)": "55000"},
        {"추천판정": "추천 가능", "브랜드": "SKT", "요금제명": "C", "월정액(원)": ""},
    ]
    assert [target.name for target in mobile_targets(plans, include_excluded=False)] == ["A"]
    assert [target.name for target in mobile_targets(plans, include_excluded=True)] == ["A", "B"]
    assert mobile_targets(plans, include_excluded=False)[0].csv_price == 55000

    subscriptions = [
        {"currency": "KRW", "service": "넷플릭스", "plan_name": "스탠다드", "regular_price": "13500", "구분": "OTT"},
        {"currency": "USD", "service": "ChatGPT", "plan_name": "Plus", "regular_price": "20", "구분": "AI"},
    ]
    assert [target.name for target in subscription_targets(subscriptions, False)] == ["스탠다드"]


def test_the_public_endpoint_does_not_let_callers_raise_the_search_budget():
    # 검색은 1,000회당 $10다. 도메인과 같은 이유로 호출자가 횟수를 정하지 못한다.
    import inspect

    from app.catalog import catalog_candidate

    assert list(inspect.signature(catalog_candidate).parameters) == ["request"]


def test_verification_spends_one_search_per_row():
    from scripts.verify_catalog_prices import SEARCHES_PER_ROW

    assert SEARCHES_PER_ROW == 1


def test_a_run_over_the_spend_ceiling_refuses_to_start(monkeypatch, capsys):
    # 검색비는 되돌릴 수 없다. 시작 전에 막는 것이 유일한 기회다.
    from scripts.verify_catalog_prices import main

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("CATALOG_ALLOWED_DOMAINS", "tworld.co.kr")
    monkeypatch.setattr("sys.argv", ["verify", "통신", "--limit", "1000", "--max-usd", "1"])
    assert main() == 1
    assert "상한 $1.00를 넘는다" in capsys.readouterr().err
