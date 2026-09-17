import re
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field


router = APIRouter()
NUMBER = re.compile(r"\d[\d,]*")
# 금액으로 읽히는 형태만 검사한다: "원" 앞, 천 단위 구분, 네 자리 이상.
# "1년", "25%", "3위" 같은 일반 수는 금액이 아니므로 통과시킨다.
# ponytail: 금액 아닌 허위 서술은 프롬프트로 막는다. 사실 검증이 필요해지면 그때 규칙을 늘린다.
AMOUNT = re.compile(r"\d[\d,]*(?=\s*원)|\d{1,3}(?:,\d{3})+|\d{4,}")
# BE `CostCalculator`가 제휴 혜택이 적용된 줄에 붙이는 표식. 프론트 `results.js`도 같은 값을 본다.
BENEFIT_NOTE = "제휴 혜택 적용"
# 출처별 한 줄 안내. 우리가 계산한 값이 아닌 것만 밝힌다. 아는 값에만 붙이고 모르는 값은 조용히 둔다.
SOURCE_NOTES = {
    "ESTIMATED": "추정치예요",
    "USER_PROVIDED": "적어 주신 금액이에요",
}
# 사유의 근거로 쓸 수 있는 출처. 추정치는 확정된 할인처럼 읽혀 제외한다.
# 모르는 출처도 제외한다 — 얼마나 믿을 값인지 모르면서 "이래서 추천됐다"고 말하지 않는다.
GROUNDABLE = {"OFFICIAL", "DERIVED", "USER_PROVIDED"}
Reason = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[^\r\n]+$")]
Label = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")]
Notice = Annotated[str, Field(min_length=1, max_length=300, pattern=r"^[^\r\n]+$")]
# BE `RecommendationService.missingInputs`가 실제로 내보내는 값. 사용자에게 보여줄 한국어 이름이다.
# 여기 없는 값이 오면 그 항목만 문장에서 빠진다 — 안내 하나 때문에 금액 설명 전체를 막지 않는다.
FIELD_LABELS = {
    "monthlyDataGb": "월 데이터 사용량",
    "wantedServiceIds": "이용하고 싶은 구독 서비스",
    "currentCarrier": "현재 통신사",
    "networkType": "현재 망 종류",
    "contractType": "약정 유형",
    "hasFamilyBundle": "가족 결합 여부",
    "ageLimit": "가입 자격",
}


class BreakdownItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    label: Label
    amount: int
    # Literal 로 묶지 않는다. BE `Provenance` 에 값이 늘 때마다 422 가 났고
    # (`USER_PROVIDED` 가 실제로 그랬다 — 가족결합 사용자 전원의 설명이 사라졌다),
    # BE 는 그것을 장애로 삼켜 아무도 알아채지 못했다. `MissingInput.field` 와 같은 사고다.
    # **값은 너그럽게, 구조는 엄격하게**: 모르는 출처는 문장을 붙이지 않고 근거로도 쓰지 않는다.
    provenance: str = Field(min_length=1, max_length=40, pattern=r"^[A-Z][A-Z0-9_]*$")
    note: str | None = Field(default=None, max_length=1000)


class MissingInput(BaseModel):
    """추가 입력 안내 한 건. **보조 정보다.**

    `field`를 Literal로 묶었더니 BE가 `ageLimit` 안내를 더한 날부터 422가 났고,
    BE는 그것을 장애로 삼켜 금액 설명과 사유가 통째로 사라졌다(D-38과 같은 사고).
    구조는 계속 엄격하게 본다(`extra=forbid`) — 모르는 **필드**는 여전히 거부한다.
    다만 모르는 **값**은 그 항목만 문장에서 빠뜨린다. 안내 하나가 설명 전체를 막지 않는다.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    field: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
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
    # 이 조합이 몇 개의 후보 중에서 뽑혔는지. BE 가 정렬한 후보 전체 수다.
    # 기준 카탈로그 1,706개 중 1,645개는 제휴 혜택도 약정할인도 없어 절감액이 0이다.
    # 그런 요금제는 "왜 추천됐나"에 쓸 근거가 이 값 하나뿐이다.
    candidateCount: int | None = Field(default=None, ge=1)


class NarrateResponse(BaseModel):
    message: str
    reasons: list[Reason] = Field(default_factory=list, max_length=3)
    # 결과 화면의 ⓘ 안내 줄. 화면이 조립하던 것을 여기로 모은다(D-46) —
    # 같은 값으로 두 곳에서 문장을 만들면 표현이 갈라진다(UX_POLICY 규칙 6).
    notices: list[Notice] = Field(default_factory=list, max_length=10)


def known_numbers(request: "NarrateRequest") -> set[int]:
    """이번 추천에 실제로 반영된 금액만. 라벨·노트 안의 숫자도 같은 계산의 일부라 인용을 허용한다.

    missingInputs는 제외한다. "가족 결합 시 최대 11,000원 추가 절감 가능"은 아직 반영되지 않은
    가정이라, 사유 문장이 그대로 인용하면 이미 받는 할인처럼 읽힌다.
    """
    numbers = {
        abs(value)
        for value in (request.monthlyTotal, request.baseline, request.monthlySavings,
                      request.annualSavings, request.candidateCount,
                      *(item.amount for item in request.breakdown))
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


def connective(word: str) -> str:
    """'~으로' / '~로'. 받침이 없거나 ㄹ 받침이면 '로'다. 한글이 아닌 끝 글자는 '으로'로 둔다."""
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return "으로"
    return "로" if (ord(last) - 0xAC00) % 28 in (0, 8) else "으로"


def rule_reasons(request: "NarrateRequest") -> list[str]:
    """사유는 규칙이 만든다. BE가 준 값만 인용하므로 금액을 만들지 않는다(절대 원칙 2).

    같은 입력이면 같은 문장이 나온다 — 재현되지 않는 설명은 금액 서비스에서 신뢰를 깎는다.
    breakdown에 있는 항목만 읽으므로 미사용 혜택은 근거에 등장하지 않는다(절대 원칙 1).
    추정치 줄은 근거로 쓰지 않는다. message가 이미 추정치임을 따로 안내한다.
    ponytail: 기본료가 '낮다'는 판단은 비교 대상이 없어 만들지 않는다. 절감액 문장이 그 결론을 대신한다.
    """
    benefits, discounts = [], []
    for item in request.breakdown:
        if item.provenance not in GROUNDABLE:
            continue
        if item.note == BENEFIT_NOTE:
            benefits.append(
                f"“{item.label}” 구독을 요금제가 무료로 제공해요." if item.amount == 0
                else f"“{item.label}” 구독을 제휴 혜택으로 월 {item.amount:,}원에 이용해요."
            )
        elif item.amount < 0:
            discounts.append(
                f"“{item.label}”{connective(item.label)} 월 {abs(item.amount):,}원이 빠져요."
            )
    candidates = benefits + discounts
    if request.monthlySavings > 0:
        # 월·연을 한 문장에 담는다. 자리는 3개뿐이라 두 줄로 쪼개면 다른 근거가 밀린다.
        annual = request.annualSavings
        candidates.append(
            f"그래서 지금보다 월 {request.monthlySavings:,}원, 1년이면 {annual:,}원 덜 내세요."
            if annual and annual > 0
            else f"그래서 지금보다 월 {request.monthlySavings:,}원 덜 내세요."
        )
    if request.candidateCount and request.candidateCount > 1:
        # 혜택도 할인도 없는 요금제(기준 카탈로그의 96%)에는 이 문장이 유일한 근거다.
        # 절감액이 0이어도 "조건에 맞는 것 중 가장 싸다"는 사실은 남는다.
        candidates.append(f"조건에 맞는 조합 {request.candidateCount:,}개 중 가장 싼 선택이에요.")
    # 나가기 전 마지막 관문: BE가 준 금액 외의 금액이 섞인 줄은 버린다.
    known = known_numbers(request)
    return [reason for reason in candidates
            if len(reason) <= 80 and quotes_known_amounts_only(reason, known)][:3]


def notices_for(request: "NarrateRequest") -> list[str]:
    """화면 상단 ⓘ 안내. BE 가 준 문장을 그대로 잇는다 — 여기서 새로 쓰지 않는다.

    `impact`·`howToFind`는 BE 가 쓴 자유 서술이다. 내레이터가 고쳐 쓰면 "최대 11,000원"
    같은 구체적인 안내가 뭉개진다. 잇기만 하고, 잇는 규칙만 한 곳에서 정한다.

    "결과 없음" 안내는 여기서 만들지 않는다 — BE 는 결과가 있을 때만 내레이터를 부른다.
    그 경우 화면이 직접 말해야 한다.
    """
    lines = []
    for missing in request.missingInputs:
        # BE 의 자유 서술이라 줄바꿈이 섞일 수 있다. 그대로 두면 Notice 패턴에 걸려 응답이
        # 통째로 500 이 되고 BE 는 그것을 장애로 삼켜 **설명 전체가 사라진다.**
        # 공백으로 눌러 한 줄로 만든다 — 내용은 그대로이고 잘리지도 않는다.
        impact = " ".join(missing.impact.split())
        if not impact:
            continue
        how = " ".join((missing.howToFind or "").split())
        lines.append(f"{impact} — {how}" if how else impact)
    # 300·10 은 BE `NarratorClient.lines(notices, 10, 300)` 와 같은 값이다.
    # 하나라도 넘으면 BE 가 설명 전체를 폐기하므로 여기서 먼저 맞춘다.
    # 넘는 줄은 자르지 않고 통째로 뺀다 — 잘린 안내는 오해를 만든다.
    return [line for line in lines if len(line) <= 300][:10]


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

    # 우리가 계산하지 않은 값은 그렇다고 밝힌다. 출처가 여럿이면 각각 한 문장이다.
    for source, note in SOURCE_NOTES.items():
        labelled = [f'“{item.label}”({item.amount:,}원)'
                    for item in request.breakdown if item.provenance == source]
        if labelled:
            sentences.append(f"{', '.join(labelled)} 항목은 {note}.")
    known_fields = [FIELD_LABELS[item.field] for item in request.missingInputs
                    if item.field in FIELD_LABELS]
    if known_fields:
        fields = ", ".join(dict.fromkeys(known_fields))
        sentences.append(f"추가로 {fields} 정보를 알려주시면 더 정확해져요.")

    return NarrateResponse(message=" ".join(sentences), reasons=rule_reasons(request),
                           notices=notices_for(request))
