import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.cache import Lru
from app.llm import client

router = APIRouter()
PROMPT = (Path(__file__).parent / "prompts" / "narrate.txt").read_text(encoding="utf-8")
NUMBER = re.compile(r"\d[\d,]*")
# 금액으로 읽히는 형태만 검사한다: "원" 앞, 천 단위 구분, 네 자리 이상.
# "1년", "25%", "3위" 같은 일반 수는 금액이 아니므로 통과시킨다.
# ponytail: 금액 아닌 허위 서술은 프롬프트로 막는다. 사실 검증이 필요해지면 그때 규칙을 늘린다.
AMOUNT = re.compile(r"\d[\d,]*(?=\s*원)|\d{1,3}(?:,\d{3})+|\d{4,}")
REASON_CACHE = Lru(limit=256)
Reason = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[^\r\n]+$")]
Label = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")]
FIELD_LABELS = {
    "monthlyDataGb": "월 데이터 사용량",
    "wantedServiceIds": "이용하고 싶은 구독 서비스",
    "currentCarrier": "현재 통신사",
    "networkType": "현재 망 종류",
    "contractType": "약정 유형",
    "hasFamilyBundle": "가족 결합 여부",
}


class BreakdownItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    label: Label
    amount: int
    provenance: Literal["OFFICIAL", "DERIVED", "ESTIMATED"]
    note: str | None = Field(default=None, max_length=1000)


class MissingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: Literal[
        "monthlyDataGb", "wantedServiceIds", "currentCarrier",
        "networkType", "contractType", "hasFamilyBundle",
    ]
    impact: str = Field(max_length=1000)
    howToFind: str | None = Field(default=None, max_length=1000)


class NarrateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    monthlyTotal: int = Field(ge=0)
    baseline: int = Field(ge=0)
    monthlySavings: int
    annualSavings: int | None = None
    planId: int | None = Field(default=None, ge=1)
    planName: Label
    carrier: Label
    breakdown: list[BreakdownItem] = Field(max_length=100)
    missingInputs: list[MissingInput] = Field(default_factory=list, max_length=100)


class NarrateResponse(BaseModel):
    message: str
    reasons: list[Reason] = Field(default_factory=list, max_length=3)


class ReasonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    reasons: list[Reason] = Field(max_length=3)


def known_numbers(request: "NarrateRequest") -> set[int]:
    """이번 추천에 실제로 반영된 금액만. 라벨·노트 안의 숫자도 같은 계산의 일부라 인용을 허용한다.

    missingInputs는 제외한다. "가족 결합 시 최대 11,000원 추가 절감 가능"은 아직 반영되지 않은
    가정이라, 사유 문장이 그대로 인용하면 이미 받는 할인처럼 읽힌다.
    """
    numbers = {
        abs(value)
        for value in (request.monthlyTotal, request.baseline, request.monthlySavings,
                      request.annualSavings, *(item.amount for item in request.breakdown))
        if value is not None
    }
    texts = [request.planName, request.carrier]
    texts += [item.label for item in request.breakdown]
    texts += [item.note for item in request.breakdown if item.note]
    for text in texts:
        numbers |= {int(found.replace(",", "")) for found in NUMBER.findall(text)}
    return numbers


def quotes_known_amounts_only(reason: str, known: set[int]) -> bool:
    return all(int(found.replace(",", "")) in known for found in AMOUNT.findall(reason))


async def curate_reasons(request: "NarrateRequest") -> list[str]:
    """같은 추천이면 같은 사유다. 결과 화면의 인라인 수정은 1순위가 그대로인 경우가 많아
    재호출이 잦다. 요청 내용만으로 키를 만들어 모델 왕복을 건너뛴다.
    대화 이력이나 사용자 식별자를 보관하지 않으므로 무상태 원칙(절대 원칙 3)은 유지된다.
    ponytail: 프로세스 안의 단순 LRU다. 서버를 여러 대로 늘리면 적중률만 떨어지고 정확도는 그대로다.
    """
    key = request.model_dump_json()
    if (cached := REASON_CACHE.get(key)) is not None:
        return cached
    try:
        raw = await client.complete(PROMPT, key, ReasonResult.model_json_schema(), max_tokens=512)
        reasons = ReasonResult.model_validate(raw).reasons
    except (client.LLMError, client.InvalidLLMResponse, ValidationError):
        # 사유는 보조 정보다. 모델이 실패해도 금액 설명까지 막지 않는다.
        # 일시적 장애를 캐시에 남기지 않는다. 다음 요청은 다시 모델을 부른다.
        return []
    known = known_numbers(request)
    curated = [reason for reason in reasons if quotes_known_amounts_only(reason, known)]
    REASON_CACHE.put(key, curated)
    return curated


@router.post("/narrate", response_model=NarrateResponse)
async def narrate(request: NarrateRequest) -> NarrateResponse:
    # ponytail: 고정 문구로 금액 생성을 막는다. 설명 종류가 늘면 검증된 문구를 추가한다.
    sentences = [
        f'“{request.carrier} {request.planName}”의 실제 내시는 금액은 월 {request.monthlyTotal:,}원이에요.',
        f"아무 할인 없이 정가로 내는 금액은 월 {request.baseline:,}원이에요.",
    ]
    if request.monthlySavings > 0:
        sentences.append(f"월 {request.monthlySavings:,}원 절약할 수 있어요.")
    elif request.monthlySavings == 0:
        sentences.append(f"월 {request.monthlySavings:,}원 절약으로, 절약되는 금액은 없어요.")
    else:
        sentences.append(
            f"월 {request.monthlySavings:,}원 절약으로 표시되는 결과라, 비교 기준보다 더 내는 조합이에요."
        )

    estimated = [
        f'“{item.label}”({item.amount:,}원)'
        for item in request.breakdown
        if item.provenance == "ESTIMATED"
    ]
    if estimated:
        sentences.append(f"{', '.join(estimated)} 항목은 추정치예요.")
    if request.missingInputs:
        fields = ", ".join(dict.fromkeys(FIELD_LABELS[item.field] for item in request.missingInputs))
        sentences.append(f"추가로 {fields} 정보를 알려주시면 더 정확해져요.")

    return NarrateResponse(message=" ".join(sentences), reasons=await curate_reasons(request))
