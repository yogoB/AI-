"""중복 결제 탐지 결과를 사람 문장으로 바꾼다(D-46).

규칙 문구가 화면에 하드코딩돼 있으면 탐지 규칙이 늘 때마다 화면을 고쳐야 한다.
문구를 여기로 모아 두면 BE 가 새 규칙을 내보내도 화면은 그대로 둔다.

**금액을 만들지 않는다.** `wastedAmount` 는 BE 가 계산한 값이고 여기서는 표기만 한다.
모르는 규칙은 지어내지 않는다 — 제목 자리에 규칙 코드를 그대로 두고 설명을 비운다.
그래야 새 규칙이 화면에서 조용히 사라지지 않는다.
"""

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()
Text = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")]

# BE `DetectionRule` 의 값. 제목은 "무엇이 일어나고 있나", how 는 "그래서 무엇을 하면 되나"다.
# 해지·변경은 사용자가 각 서비스에서 직접 한다 — 우리는 금액만 알려준다.
RULES = {
    "BENEFIT_OVERLAP": (
        "요금제에 포함된 구독을 따로 결제 중",
        "요금제 혜택으로 이미 제공돼요. 개별 결제를 해지하면 그만큼 줄어요.",
    ),
    "TIER_DUPLICATE": (
        "같은 서비스를 두 등급으로 결제 중",
        "더 비싼 등급 하나만 남기면 나머지가 줄어요.",
    ),
    "BUNDLE_OVERLAP": (
        "묶음 상품이 더 싼 조합",
        "개별 결제 합계가 묶음 상품보다 비싸요.",
    ),
}


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    rule: Text
    # BE 가 카탈로그에서 찾아 넘긴 이름. 내레이터는 카탈로그를 모른다.
    targetName: Text
    wastedAmount: int = Field(ge=0)
    provenance: Text


class DetectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    findings: list[Finding] = Field(max_length=50)


class Explained(BaseModel):
    title: Text
    target: Text
    amount: Text
    how: str = Field(max_length=200)


class DetectionsResponse(BaseModel):
    lines: list[Explained] = Field(default_factory=list, max_length=50)
    summary: str = Field(max_length=300)


def summarize(findings: list[Finding]) -> str:
    """결론 먼저(UX_POLICY 규칙 1). 합계는 BE 가 준 값을 더하지 않고 건수만 말한다 —
    금액 합산은 계산이고, 계산은 이 서버가 하지 않는다(절대 원칙 2)."""
    if not findings:
        return "중복으로 새는 금액이 없어요."
    return (f"겹치는 결제 {len(findings)}건을 찾았어요. "
            "해지·변경은 각 서비스에서 직접 해주세요 — 요고비는 금액만 알려드려요.")


@router.post("/narrate/detections", response_model=DetectionsResponse)
async def detections(request: DetectionsRequest) -> DetectionsResponse:
    lines = []
    for finding in request.findings:
        title, how = RULES.get(finding.rule, (finding.rule, ""))
        amount = f"월 {finding.wastedAmount:,}원"
        # ESTIMATED 는 요금제가 등급을 밝히지 않아 상한으로 잡은 값이다. 단정하지 않는다.
        if finding.provenance == "ESTIMATED":
            amount = f"최대 {amount}"
        lines.append(Explained(title=title, target=finding.targetName, amount=amount, how=how))
    return DetectionsResponse(lines=lines, summary=summarize(request.findings))
