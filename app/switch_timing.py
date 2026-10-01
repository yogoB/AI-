"""변경 시점 판정을 사람 문장으로 바꾼다(D-47).

판정(`status`)과 회수개월은 BE `SwitchTiming` 이 계산한 값이다. 여기서는 표기만 한다 —
금액도 개월도 만들지 않는다(절대 원칙 2).

만료일은 사용자가 화면에 적은 날짜라 BE 도 모른다. 받은 날짜를 그대로 한국어로 적기만 하고,
없으면 그 문장을 빼는 쪽을 택한다 — 날짜 없이 "만료일까지 기다리세요"는 언제까지인지 알려주지 못한다.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

router = APIRouter()
Text = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")]

# BE `SwitchTiming.Status`. 제목은 화면 배지에, 문장은 안내 줄에 쓴다.
HEADLINES = {
    "SWITCH_NOW": "지금이 최적 실행 시점",
    "WAIT_UNTIL_EXPIRY": "약정 만료 후가 이득",
    "NO_BENEFIT": "절감 없음 · 참고용 일정",
}
# 월 절감은 양수인데 특가가 끝나 회수하지 못하는 NO_BENEFIT 의 배지.
NEVER_RECOUPED = "전환비용 회수 불가 · 참고용 일정"


class SwitchTimingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    status: Literal["SWITCH_NOW", "WAIT_UNTIL_EXPIRY", "NO_BENEFIT"]
    # NO_BENEFIT 이면 회수할 것이 없어 null 이다(BE `SwitchTiming.Result`).
    paybackMonths: int | None = Field(default=None, ge=0)
    remainingContractMonths: int = Field(ge=0)
    monthlySavings: int
    switchingCost: int = Field(ge=0)
    # 사용자가 적은 약정 만료일. `YYYY-MM-DD`. 모르면 없다.
    expiryDate: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def payback_needed_unless_no_benefit(self) -> "SwitchTimingRequest":
        # 계약 밖 조합이면 "None개월이면 회수해요" 같은 문장을 만드느니 422 다.
        if self.status != "NO_BENEFIT" and self.paybackMonths is None:
            raise ValueError("paybackMonths 는 NO_BENEFIT 이 아니면 필요하다")
        return self

    @field_validator("expiryDate")
    @classmethod
    def validate_day(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)   # 2026-02-31 같은 값을 여기서 막는다
        return value


class SwitchTimingResponse(BaseModel):
    headline: Text
    note: str = Field(max_length=300)


def today_in_korea() -> date:
    # 머신 시계는 UTC 다. 한국 자정~09시에는 UTC 날짜가 하루 늦다. tzdata 없이 고정 +9.
    return datetime.now(timezone(timedelta(hours=9))).date()


def korean_day(value: str) -> str:
    year, month, day = value.split("-")
    return f"{int(year)}년 {int(month)}월 {int(day)}일"


def note_for(request: SwitchTimingRequest) -> str:
    if request.status == "NO_BENEFIT":
        if request.monthlySavings < 0:
            # 더 내는 것은 더 낸다고 말한다. BE 가 준 값의 부호만 뗀다.
            return f"옮기면 월 {abs(request.monthlySavings):,}원 더 내요. 아래 일정은 참고용이에요."
        if request.monthlySavings > 0:
            # 매달은 싸도 특가가 끝나 전환비용을 영영 회수하지 못하는 경우다(BE `SwitchTiming`, 2026-10-01).
            # "절감이 없어요"는 화면의 월 절감과 어긋난다. 처음엔 싸다는 것과 회수 못 한다는 것을 둘 다 말한다.
            return (f"처음엔 월 {request.monthlySavings:,}원 덜 내지만, 전환비용을 회수하지 못해요. "
                    "아래 일정은 참고용이에요.")
        return "지금 조건에서는 옮겨도 절감이 없어요. 아래 일정은 참고용이에요."

    if request.status == "WAIT_UNTIL_EXPIRY":
        # BE 는 날짜 형식만 본다. 이미 지난 날까지 기다리라고 하면 따를 수 없으니 남은 개월로 말한다.
        if not request.expiryDate or date.fromisoformat(request.expiryDate) <= today_in_korea():
            # 날짜를 모르면 "언제까지"를 말할 수 없다. 남은 개월로 대신한다.
            return (f"약정이 {request.remainingContractMonths}개월 남아 지금 옮기면 손해예요. "
                    "만료 후에 옮기는 게 이득이에요.")
        return (f"약정 만료일 {korean_day(request.expiryDate)}까지 기다리는 게 이득이에요. "
                "그날에 맞춰 일정을 잡았어요.")

    if request.switchingCost == 0:
        # BE 는 전환비용을 **모르면** 0 을 보낸다(화면이 묻지 않는다). 없다고 단정하지 않는다.
        return "위약금·할부금이 없다면 지금 옮기는 게 바로 이득이에요. 오늘 기준으로 일정을 잡았어요."
    payback = "바로" if request.paybackMonths == 0 else f"{request.paybackMonths}개월이면"
    remaining = (f"(약정 잔여 {request.remainingContractMonths}개월)"
                 if request.remainingContractMonths > 0 else "")
    return f"지금 옮기는 게 이득이에요. 전환비용 {request.switchingCost:,}원을 {payback} 회수해요{remaining}."


@router.post("/narrate/switch-timing", response_model=SwitchTimingResponse)
async def switch_timing(request: SwitchTimingRequest) -> SwitchTimingResponse:
    headline = HEADLINES[request.status]
    if request.status == "NO_BENEFIT" and request.monthlySavings > 0:
        # 매달은 싼데 "절감 없음" 배지는 아래 문장과 싸운다. 없는 것은 절감이 아니라 회수다.
        headline = NEVER_RECOUPED
    return SwitchTimingResponse(headline=headline, note=note_for(request))
