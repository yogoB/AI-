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
MAX_BYTES = 1_000_000
PRICE = r"([1-9]\d{0,2}(?:,\d{3})*|[1-9]\d*)"
# 이름표와 가격 사이. **다른 금액을 건너뛰지 못한다** — 이름표 뒤 첫 가격이 그 상품의 가격일 때만 붙는다.
# 첫 달·할인·특가·정상가를 지나서도 붙지 않는다 — 그 뒤 가격은 정가가 아닐 수 있고, 오류 없이 틀린 값이
# 원문과 함께 제안되면 운영자는 믿고 승인한다. 이런 페이지는 CATALOG-SOURCE-CHANGED 로 사람에게 넘긴다.
GAP = r"(?:(?!₩|\d\s?원|\d,\d|첫\s?달|할인|특가|정상가).){0,150}?"


def monthly(labels: dict[str, str], price: str) -> dict[str, str]:
    """{tierName: 페이지의 이름표 정규식} → 이름표 뒤 첫 금액이 월 정가인 패턴."""
    return {name: label + GAP + price for name, label in labels.items()}


# serviceName → (공식 URL, 읽을 구간, {tierName: 월 정가 패턴}).
# 키는 BE `subscription_service.name`, tierName 은 `subscription_tier.name` 과 글자까지 같아야 붙는다.
# URL 은 `official_url` 과 같아야 한다 — 다르면 BE 가 SUBSCRIPTION_SOURCE_DRIFT 로 건너뛴다.
# 구간이 None 이면 페이지 전체, 그룹이 있으면 그 그룹만 읽는다.
# URL을 요청으로 받지 않는다. 리다이렉트도 따르지 않아 내부망으로의 우회를 막는다.
# 넣지 않은 것: 스크립트로 가격을 그리는 페이지(넷플릭스·티빙·웨이브·유튜브 등), 1MB 넘는 페이지(디즈니+),
# 날짜가 박힌 보도자료·블로그(쿠팡플레이·YouTube Music) — 바뀔 수 없는 글을 점검하면 안심만 준다.
SOURCES: dict[str, tuple[str, str | None, dict[str, str]]] = {
    # FAQ는 프로모션 카드와 달리 대한민국의 월 정가를 명시한다.
    "Spotify": ("https://www.spotify.com/kr-ko/premium/", r"대한민국의 Spotify Premium 가격은.*?(?:입니다\.)", {
        "Premium Basic": rf"Premium 베이직 요금제는 ₩{PRICE}\(매월 기준\)",
        "Premium Individual": rf"Premium 개인 요금제는 ₩{PRICE}\(매월 기준\)",
        "Premium Duo": rf"Premium 듀오 요금제는 ₩{PRICE}\(매월 기준\)",
        "Premium Student": rf"Premium 학생 요금제는 ₩{PRICE}\(매월 기준\)",
    }),
    "Apple Music": ("https://www.apple.com/kr/apple-music/", None,
                    {name: rf"{name} ₩{PRICE}/월" for name in ("개인", "가족")}),
    # 세계 가격표에서 월별 표의 대한민국 행만 읽는다. 다른 국가·통화로 폴백하지 않는다.
    # 2026-10-01 표가 "50GB : 1,100원" 에서 열 형식으로 바뀌었다 — 머리행 "50GB 200GB 2TB 6TB 12TB" 아래
    # "대한민국 5 (원) 1,100원 4,400원 …"(5 는 각주). **머리행 순서를 구간 조건으로 건다** — 열이 바뀌면
    # 위치로 읽은 값이 다른 등급에 붙으므로, 그때는 구간을 못 찾아 사람에게 넘긴다.
    "iCloud+": ("https://support.apple.com/ko-kr/108047",
                r"월별 가격.*?국가\(통화\) 50GB 200GB 2TB 6TB 12TB .*?(대한민국 (?:\d )?\(원\) .*?)(?=싱가포르)",
                {name: rf"^대한민국 (?:\d )?\(원\) (?:(?:{PRICE[1:-1]})원 ){{{i}}}{PRICE}원"
                 for i, name in enumerate(("50GB", "200GB", "2TB", "6TB", "12TB"))}),
    "멜론": ("https://www.melon.com/buy/pamphlet/all.htm", None, monthly({
        "스트리밍 플러스": r"프리미엄 스트리밍 플러스 정기결제 이용권",
        "Hi-Fi 스트리밍": r"Hi-Fi스트리밍클럽 정기결제 이용권",
        "스트리밍": r"(?<!Fi)(?<!모바일 )스트리밍클럽 정기결제 이용권",
        "모바일 스트리밍": r"모바일 스트리밍클럽 정기결제 이용권",
        "MP3 10 플러스": r"MP3 10 플러스 정기결제 이용권",
        "MP3 30 플러스": r"MP3 30 플러스 정기결제 이용권",
        "MP3 10": r"MP3 10 정기결제 이용권",
        "MP3 30": r"MP3 30 정기결제 이용권",
        "300회 듣기": r"300회 듣기 정기결제 이용권",
        "익스트리밍 플러스": r"멜론 익스트리밍 플러스 SK텔레콤",
        "익스트리밍": r"멜론 익스트리밍 SK텔레콤",
    }, rf"{PRICE}원")),
    # 크루는 페이지에 가격이 없다.
    "벅스": ("https://music.bugs.co.kr/pay/public", None, monthly({
        "무제한 듣기": r"(?<!Premium )(?<!모바일 )무제한 듣기 모든 기기에서",
        "모바일 무제한 듣기": r"모바일 무제한 듣기 알뜰하게",
        "Premium 무제한 듣기": r"Premium 무제한 듣기 모든 기기 재생과",
        "무제한 듣기 + 오프라인": r"무제한 듣기\+오프라인 재생 오프라인에서도",
    }, rf"자동결제 {PRICE}원")),
    "크레마클럽": ("https://cremaclub.yes24.com/BookClub/Guide", None,
                  {name: rf"{name}요금제 월 {PRICE}원" for name in ("스탠다드 55", "프리미엄 77", "크레마클럽 X FLO 99")}),
    # 'sam2 첫 이용'·'sam무제한 첫 이용'은 첫 달 특가 카드라 정가 자리가 모호해 읽지 않는다. 같은 정가를 위 두 등급이 본다.
    "교보 sam": ("https://sam.kyobobook.co.kr/dig/sam/pssbuy", None, monthly({
        "sam2": r"sam2 프리미엄 나만의",
        "sam3": r"sam3 프리미엄 나만의",
        "sam12": r"sam12 프리미엄 나만의",
        "sam무제한": r"sam무제한 무제한 무제한으로",
        "과학": r"sam스페셜\[동아사이언스\]",
        "Why": r"sam스페셜\[Why\?시리즈\]",
        "판타지": r"sam스페셜\[판타지무협\]",
        "세계문학": r"sam스페셜\[세계문학\]",
    }, rf"월 {PRICE}원")),
    "윌라": ("https://www.welaaa.com/", None,
             {name: rf"월 {PRICE}원 \({name} 멤버십\)" for name in ("베이직", "패밀리")}),
    # 연 결제가 먼저 나온다. 그 뒤 첫 월 가격을 읽는다.
    "Microsoft 365": ("https://www.microsoft.com/ko-kr/microsoft-365/basic", None, monthly(
        {name: rf"Microsoft 365 {name} ₩[\d,]+ /년" for name in ("Basic", "Personal", "Family", "Premium")},
        rf"₩{PRICE} /월")),
    "Notion": ("https://www.notion.com/ko/pricing", None,
               {name: rf"{name} ₩{PRICE} 1인/월" for name in ("플러스", "비즈니스")}),
    "Google One": ("https://one.google.com/about/plans?hl=ko", None, {
        "Basic 100GB": rf"Basic \(100GB\) ₩{PRICE}/월",
        "Google AI Plus 2TB": rf"Google AI Plus \(2TB\) ₩{PRICE}/월",
    }),
    "스토리텔": ("https://www.storytel.com/kr/subscriptions", None, {
        "언리미티드": rf"스토리텔 언리미티드{GAP}{PRICE} 원 /월",
        "패밀리": rf"본인 \+ 1 가족 구성원 2 개 계정 {PRICE} 원 /월",
    }),
    "애플TV+": ("https://tv.apple.com/kr", None, {"Apple TV": rf"무료 체험 후 월 ₩{PRICE}입니다"}),
    "리디셀렉트": ("https://ridihelp.ridibooks.com/support/solutions/articles/154000208053", None,
                  {"리디셀렉트": rf"월 {PRICE}원\(\$[\d,.]+\)을 정기결제"}),
    "카카오 이모티콘 플러스": ("https://cs.kakao.com/helps_html/1073203543", None,
                        {"이모티콘 플러스": rf"월 {PRICE}원에 이모티콘플러스 상품을 구독"}),
}
ServiceName = Literal[tuple(SOURCES)]

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
    offers: list[Offer] = Field(min_length=1, max_length=20)


# 취소선(<s>·<del>·<strike>)은 "더 이상 아닌 가격"이다. 읽지 않는다.
HIDDEN_TAGS = ("script", "style", "noscript", "s", "del", "strike")


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in HIDDEN_TAGS:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in HIDDEN_TAGS:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def extract_offers(service: ServiceName, html: str) -> list[Offer]:
    page = PageText()
    page.feed(html)
    text = re.sub(r"\s+", " ", " ".join(page.parts))
    _, section, patterns = SOURCES[service]
    if section:
        found = re.search(section, text)
        text = found.group(found.lastindex or 0) if found else ""
    offers = []
    for name, pattern in patterns.items():
        matches = list(re.finditer(pattern, text))
        # 구조가 바뀌었거나 한 상품에 서로 다른 값이 보이면 사람 확인이 필요하다.
        if not matches or len({m.group(1) for m in matches}) != 1:
            raise ValueError("상품별 월 정가를 유일하게 확인하지 못했습니다. 공식 페이지를 확인해 주세요.")
        match = matches[0]
        offers.append(Offer(tierName=name, price=int(match.group(1).replace(",", "")), evidence=match.group()))
    return offers


PAGE_TIMEOUT_SECONDS = 15


async def fetch_page(url: str) -> bytes:
    async with asyncio.timeout(PAGE_TIMEOUT_SECONDS):
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
    url = SOURCES[request.serviceName][0]
    try:
        body = await fetch_page(url)
        offers = extract_offers(request.serviceName, body.decode("utf-8", errors="strict"))
    except (httpx.HTTPError, TimeoutError) as error:
        raise HTTPException(502, detail={"code": "CATALOG-SOURCE-UNAVAILABLE", "message": "공식 페이지를 읽지 못했습니다. 잠시 후 다시 실행해 주세요."}) from error
    except (ValueError, UnicodeError) as error:
        raise HTTPException(502, detail={"code": "CATALOG-SOURCE-CHANGED", "message": "상품별 월 정가를 확인하지 못했습니다. 공식 페이지와 추출 규칙을 확인해 주세요."}) from error
    return CheckResponse(serviceName=request.serviceName, sourceUrl=url, checkedAt=datetime.now(timezone.utc),
                         sourceHash=hashlib.sha256(body).hexdigest(), offers=offers)
