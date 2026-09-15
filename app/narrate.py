from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()
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

    return NarrateResponse(message=" ".join(sentences))
