"""등록된 공식 페이지의 월 정가를 읽는다. 저장·대조·승인은 BE가 맡는다."""

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()
ServiceName = Literal["Spotify", "Apple Music", "iCloud+"]
# URL을 요청으로 받지 않는다. 리다이렉트도 따르지 않아 내부망으로의 우회를 막는다.
SOURCES = {
    "Spotify": "https://www.spotify.com/kr-ko/premium/",
    "Apple Music": "https://www.apple.com/kr/apple-music/",
    "iCloud+": "https://support.apple.com/ko-kr/108047",
}
MAX_BYTES = 1_000_000
PRICE = r"([1-9]\d{0,2}(?:,\d{3})*|[1-9]\d*)"


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    serviceName: ServiceName


class Offer(BaseModel):
    tierName: str
    price: int = Field(gt=0)
    currency: Literal["KRW"] = "KRW"
    billingPeriod: Literal["MONTH"] = "MONTH"
    evidence: str = Field(min_length=1, max_length=500)


class CheckResponse(BaseModel):
    serviceName: ServiceName
    sourceUrl: str
    checkedAt: datetime
    sourceHash: str
    offers: list[Offer] = Field(min_length=1, max_length=10)


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def extract_offers(service: ServiceName, html: str) -> list[Offer]:
    page = PageText()
    page.feed(html)
    text = re.sub(r"\s+", " ", " ".join(page.parts))
    if service == "Spotify":
        # FAQ는 프로모션 카드와 달리 대한민국의 월 정가를 명시한다.
        section = re.search(r"대한민국의 Spotify Premium 가격은.*?(?:입니다\.)", text)
        patterns = {
            "Premium Basic": rf"Premium 베이직 요금제는 ₩{PRICE}\(매월 기준\)",
            "Premium Individual": rf"Premium 개인 요금제는 ₩{PRICE}\(매월 기준\)",
            "Premium Duo": rf"Premium 듀오 요금제는 ₩{PRICE}\(매월 기준\)",
            "Premium Student": rf"Premium 학생 요금제는 ₩{PRICE}\(매월 기준\)",
        }
        text = section.group() if section else ""
    elif service == "Apple Music":
        patterns = {name: rf"{name} ₩{PRICE}/월" for name in ("개인", "가족")}
    else:
        # 세계 가격표에서 대한민국 블록만 읽는다. 다른 국가·통화로 폴백하지 않는다.
        section = re.search(r"대한민국\(원\).*?(?=싱가포르)", text)
        text = section.group() if section and "월별 가격" in text else ""
        patterns = {name: rf"(?<![\w]){name}\s*:\s*{PRICE}원" for name in ("50GB", "200GB", "2TB", "6TB", "12TB")}

    offers = []
    for name, pattern in patterns.items():
        matches = list(re.finditer(pattern, text))
        # 구조가 바뀌었거나 한 상품에 서로 다른 값이 보이면 사람 확인이 필요하다.
        if not matches or len({m.group(1) for m in matches}) != 1:
            raise ValueError("상품별 월 정가를 유일하게 확인하지 못했습니다. 공식 페이지를 확인해 주세요.")
        match = matches[0]
        offers.append(Offer(tierName=name, price=int(match.group(1).replace(",", "")), evidence=match.group()))
    return offers


async def fetch_page(url: str) -> bytes:
    async with asyncio.timeout(15):
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", url, headers={"Accept-Language": "ko-KR", "User-Agent": "Yogobi-Catalog-Check/1.0"}) as response:
                response.raise_for_status()
                if "text/html" not in response.headers.get("content-type", "").lower():
                    raise ValueError("공식 페이지가 HTML 문서를 반환하지 않았습니다.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError("공식 페이지가 허용 크기를 초과했습니다.")
                return bytes(body)


@router.post("/operations/subscriptions/check", response_model=CheckResponse)
async def check_subscription(request: CheckRequest) -> CheckResponse:
    url = SOURCES[request.serviceName]
    try:
        body = await fetch_page(url)
        offers = extract_offers(request.serviceName, body.decode("utf-8", errors="strict"))
    except (httpx.HTTPError, TimeoutError) as error:
        raise HTTPException(502, detail={"code": "CATALOG-SOURCE-UNAVAILABLE", "message": "공식 페이지를 읽지 못했습니다. 잠시 후 다시 실행해 주세요."}) from error
    except (ValueError, UnicodeError) as error:
        raise HTTPException(502, detail={"code": "CATALOG-SOURCE-CHANGED", "message": "상품별 월 정가를 확인하지 못했습니다. 공식 페이지와 추출 규칙을 확인해 주세요."}) from error
    return CheckResponse(serviceName=request.serviceName, sourceUrl=url, checkedAt=datetime.now(timezone.utc),
                         sourceHash=hashlib.sha256(body).hexdigest(), offers=offers)
