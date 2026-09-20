import httpx
import pytest
from fastapi.testclient import TestClient

from app import plan_promotions as promo
from app.main import app

AUTH = {"Authorization": "Bearer test-backend-only-token"}
# 실제 페이지 모양 그대로(2026-09-21 확인). 머리말과 공유 팝업에 같은 가격 줄이 두 번 나온다.
HEAD = "알뜰폰 허브 소개 "
PAGE = (f"<p>메뉴 고객 리뷰 {HEAD}[Npay 5천] 10GB/100분 (6개월) LGU+ A모바일(에넥스텔레콤) "
        "월 19,800 원 6개월 이후 13,200 원/월 지금 가입하기 LTE</p>"
        "<p>공유하기 월 19,800 원 6개월 이후 13,200 원/월 ( 0.0 /5.0) 데이터 10GB</p>")
SPACED = (f"<p>{HEAD}티플 5GB(5GB/300분)_12개월할인 KT KCT (티플러스) "
          "월 27,500 원 12개월 이후 4,800 원/월 지금 가입하기</p>")


def test_reads_months_and_post_promo_price_but_never_the_ambiguous_headline():
    offer = promo.extract_promotion(7752, PAGE)
    assert (offer.carrier, offer.network) == ("A모바일(에넥스텔레콤)", "LGU+")
    assert offer.planName == "[Npay 5천] 10GB/100분 (6개월)"
    assert (offer.promoMonths, offer.regularPrice) == (6, 13200)
    # 앞의 "월 19,800 원"은 무엇인지 페이지가 말하지 않는다. 어디에도 실리지 않아야 한다.
    assert 19800 not in offer.model_dump().values()
    assert offer.evidence == "월 19,800 원 6개월 이후 13,200 원/월"
    assert offer.sourceUrl.endswith("/7752.do") and len(offer.sourceHash) == 64
    # 통신사 이름에 공백이 있어도 망 표기를 경계로 갈린다.
    assert promo.extract_promotion(7179, SPACED).carrier == "KCT (티플러스)"


def test_only_the_products_own_header_is_read_never_the_comparison_widget():
    """페이지에는 비교 위젯이 있어 **다른 요금제**의 가격 줄이 같이 실린다(13개 중 3개가 그랬다).
    머리말 바로 뒤의 줄만 읽고, 멀리 있는 줄은 남의 상품으로 본다."""
    widget = PAGE + "<p>비교 요금제 닫기 A 갓성비 10GB LGU+ A모바일 LTE 월 110 원 6개월 이후 99,900 원/월</p>"
    assert promo.extract_promotion(7752, widget).regularPrice == 13200

    # 머리말이 없고 위젯 줄만 있으면 읽지 않는다 — 남의 상품 값을 우리 행에 넣지 않는다.
    only_widget = "<p>비교 요금제 A 갓성비 LGU+ A모바일 LTE 월 110 원 6개월 이후 99,900 원/월</p>"
    with pytest.raises(ValueError):
        promo.extract_promotion(7752, only_widget)

    # 머리말은 있는데 가격 줄이 멀리 떨어져 있으면(구조가 바뀐 것) 버린다.
    far = f"<p>{HEAD}어떤 요금제 LGU+ A모바일 " + "설명 " * 90 + "월 110 원 6개월 이후 99,900 원/월</p>"
    with pytest.raises(ValueError):
        promo.extract_promotion(7752, far)


def test_a_changed_page_never_yields_a_number():
    for broken in (PAGE.replace(HEAD, "다른 머리말 "),      # 머리말 표기가 사라짐
                   PAGE.replace(" LGU+ ", " 알수없는망 "),   # 망 표기가 사라짐
                   PAGE.replace("6개월 이후 13,200 원/월", "특가 종료"),   # 가격 줄 형식이 바뀜
                   "<p>가격 안내가 없습니다</p>"):
        with pytest.raises(ValueError):
            promo.extract_promotion(7752, broken)


def test_one_dead_product_does_not_drop_the_other_rows(monkeypatch):
    async def fetch(url):
        if url.endswith("/7752.do"):
            return PAGE.encode()
        if url.endswith("/7179.do"):
            return b"<p>\xed\x8e\x98\xec\x9d\xb4\xec\xa7\x80 \xea\xb0\x9c\xed\x8e\xb8</p>"
        raise httpx.ConnectError("private transport detail")

    monkeypatch.setattr(promo, "fetch_page", fetch)
    monkeypatch.setattr(promo, "REQUEST_GAP_SECONDS", 0)
    with TestClient(app) as client:
        body = {"productIds": [7752, 7179, 9999]}
        assert client.post("/operations/plans/promotions/check", json=body).status_code == 401
        response = client.post("/operations/plans/promotions/check", json=body, headers=AUTH)
        assert response.status_code == 200
        payload = response.json()
        assert [p["productId"] for p in payload["promotions"]] == [7752]
        # 못 읽은 것과 특가가 끝난 것은 다르다. BE 가 기존 행을 지우지 않도록 사유를 갈라 준다.
        assert payload["failures"] == [{"productId": 7179, "code": "CATALOG-SOURCE-CHANGED"},
                                       {"productId": 9999, "code": "CATALOG-SOURCE-UNAVAILABLE"}]
        assert "private transport" not in response.text


def test_url_is_never_taken_from_the_request():
    with TestClient(app) as client:
        for body in ({"productIds": []}, {"productIds": list(range(31))},
                     {"productIds": [0]}, {"productIds": ["7752"]},
                     {"productIds": [7752], "url": "http://127.0.0.1"}):
            assert client.post("/operations/plans/promotions/check",
                               json=body, headers=AUTH).status_code == 422
