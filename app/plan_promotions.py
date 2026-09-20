"""알뜰폰 기간 한정 특가를 공식 상품 페이지에서 읽는다. 저장·대조·승인은 BE가 맡는다(D-60과 같은 경계).

**URL을 요청으로 받지 않는다.** 상품 번호(정수)만 받아 고정 템플릿에 끼운다 — 내부망 우회 입력을 만들지 않는다.
**목록을 훑지 않는다.** 출처 사이트에 봇 차단 시스템이 붙어 있어, 매일 104개 상세 페이지를 도는 발견형
크롤링은 만들지 않았다. BE가 이미 들고 있는 상품 번호를 다시 확인하는 갱신 전용이다.
새 특가를 찾는 일은 사람이 한다 — 카탈로그 검수 절차가 원래 그렇다.
"""

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.subscription_check import PageText, fetch_page

router = APIRouter()

SOURCE_TEMPLATE = "https://www.mvnohub.kr/product/products/{}.do"
NETWORKS = ("LGU+", "SKT", "KT")          # 긴 것부터 — "SKT"가 "KT"에 먹히지 않게
REQUEST_GAP_SECONDS = 1.2                 # 한 번의 호출이 여러 장을 읽는다. 간격은 여기서 지킨다.
HEADER = "알뜰폰 허브 소개 "
HEADER_SPAN = 200   # 머리말과 가격 줄 사이 최대 거리. 이보다 멀면 다른 상품 이야기다.
# "월 19,800 원 6개월 이후 13,200 원/월" — 뒤의 값만 쓴다(아래 주석 참고).
PRICE_BLOCK = re.compile(r"월\s*([\d,]+)\s*원\s*(\d{1,2})개월\s*이후\s*([\d,]+)\s*원/월")


class PromotionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    productIds: list[Annotated[int, Field(ge=1, le=99_999_999)]] = Field(min_length=1, max_length=30)


class Promotion(BaseModel):
    productId: int
    carrier: str = Field(min_length=1, max_length=100)
    planName: str = Field(min_length=1, max_length=200)
    network: Literal["SKT", "KT", "LGU+"]
    promoMonths: int = Field(ge=1, le=60)
    # 특가 종료 후 월 요금. 페이지가 "N개월 이후 B원/월"이라고 적은 값 그대로다.
    # 앞의 "월 A원"은 싣지 않는다 — 어떤 값인지 페이지가 말하지 않는다.
    # 상품명이 "12개월간 990원 특가"인데 A가 31,900원이라 특가액도 정가도 아니다. 모르면 안 옮긴다.
    regularPrice: int = Field(gt=0)
    sourceUrl: str
    sourceHash: str
    evidence: str = Field(min_length=1, max_length=500)


class Failure(BaseModel):
    productId: int
    # BE 는 이 번호의 기존 행을 **건드리지 않는다.** 못 읽은 것과 특가가 끝난 것은 다르다.
    code: Literal["CATALOG-SOURCE-UNAVAILABLE", "CATALOG-SOURCE-CHANGED"]


class PromotionsResponse(BaseModel):
    checkedAt: datetime
    promotions: list[Promotion] = Field(default_factory=list, max_length=30)
    failures: list[Failure] = Field(default_factory=list, max_length=30)


def extract_promotion(product_id: int, html: str) -> Promotion:
    page = PageText()
    page.feed(html)
    text = re.sub(r"\s+", " ", " ".join(page.parts))

    # 가격 줄은 페이지에 여러 번 나온다 — 상품 머리말, 공유 팝업, 그리고 **비교 위젯의 다른 상품들**.
    # 값이 하나로 모이길 기대하면 안 된다(위젯에 다른 요금제가 실린 페이지가 13개 중 3개였다).
    # 대신 **위치로 고정한다**: "알뜰폰 허브 소개" 바로 뒤에 오는 그 상품 자신의 머리말만 읽는다.
    # ("알뜰폰 허브 소개"는 상단 메뉴에도 있어서, 가격 줄 직전의 마지막 것을 쓴다.)
    match = None
    for candidate in PRICE_BLOCK.finditer(text):
        head = text[: candidate.start()]
        if HEADER not in head:
            continue
        if candidate.start() - (head.rindex(HEADER) + len(HEADER)) <= HEADER_SPAN:
            match = candidate
            break
    if match is None:
        raise ValueError("상품 머리말에서 특가 기간과 종료 후 요금을 찾지 못했습니다.")

    head = text[: match.start()]
    head = head[head.rindex(HEADER) + len(HEADER):].strip()

    # 머리말은 "{요금제명} {망} {통신사}" 다. 망 표기를 경계로 가른다.
    for network in NETWORKS:
        marker = f" {network} "
        if marker in head:
            plan_name, _, carrier = head.partition(marker)
            break
    else:
        raise ValueError("망 표기를 찾지 못했습니다.")

    plan_name, carrier = plan_name.strip(), carrier.strip()
    if not plan_name or not carrier:
        raise ValueError("요금제명 또는 통신사를 읽지 못했습니다.")

    return Promotion(
        productId=product_id,
        carrier=carrier,
        planName=plan_name,
        network=network,
        promoMonths=int(match.group(2)),
        regularPrice=int(match.group(3).replace(",", "")),
        sourceUrl=SOURCE_TEMPLATE.format(product_id),
        sourceHash=hashlib.sha256(html.encode()).hexdigest(),
        evidence=match.group().strip(),
    )


@router.post("/operations/plans/promotions/check", response_model=PromotionsResponse)
async def check_promotions(request: PromotionsRequest) -> PromotionsResponse:
    promotions: list[Promotion] = []
    failures: list[Failure] = []

    for index, product_id in enumerate(dict.fromkeys(request.productIds)):
        if index:
            await asyncio.sleep(REQUEST_GAP_SECONDS)
        try:
            body = await fetch_page(SOURCE_TEMPLATE.format(product_id))
        except (httpx.HTTPError, TimeoutError, ValueError):
            failures.append(Failure(productId=product_id, code="CATALOG-SOURCE-UNAVAILABLE"))
            continue
        try:
            promotions.append(extract_promotion(product_id, body.decode("utf-8", errors="strict")))
        except (ValueError, UnicodeError):
            failures.append(Failure(productId=product_id, code="CATALOG-SOURCE-CHANGED"))

    return PromotionsResponse(checkedAt=datetime.now(timezone.utc), promotions=promotions, failures=failures)
