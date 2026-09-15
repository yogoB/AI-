from pathlib import Path
from typing import Annotated, Literal, Self

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.llm import client

router = APIRouter()
PROMPT = (Path(__file__).parent / "prompts" / "parse.txt").read_text(encoding="utf-8")
CLARIFYING_QUESTION = "추천에 사용할 월 데이터 용량을 1GB 이상의 정수로, 원하는 구독 서비스를 1개 이상 알려주시겠어요?"


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=4000)


class RequiredInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # BE RecommendationRequest의 Java Integer 범위. 소수나 0을 보정하지 않는다.
    monthlyDataGb: int = Field(ge=1, le=2147483647)
    wantedServiceIds: list[Annotated[int, Field(ge=1, le=6)]] = Field(min_length=1, max_length=6)


class OptionalInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    currentCarrier: str | None = Field(default=None, min_length=1, max_length=100)
    networkType: Literal["5G", "LTE", "3G"] | None = None
    contractType: Literal["NONE", "SELECTIVE_25", "DEVICE_SUBSIDY"] | None = None
    hasFamilyBundle: bool | None = None


class ParseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    required: RequiredInputs | None = None
    optional: OptionalInputs | None = None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    clarifyingQuestion: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def enforce_confidence(self) -> Self:
        if self.confidence < 0.7:
            self.required = None
            self.optional = None
            # ponytail: 공통 질문을 사용한다. 세부 질문이 필요해지면 서버에서 문구를 선택한다.
            self.clarifyingQuestion = CLARIFYING_QUESTION
        elif self.required is None or self.clarifyingQuestion is not None:
            raise ValueError("Confident extraction requires complete, unambiguous inputs")
        else:
            self.optional = self.optional or OptionalInputs()
            self.clarifyingQuestion = None
        return self


@router.post("/parse", response_model=ParseResponse)
async def parse(request: ParseRequest) -> ParseResponse:
    try:
        result = await client.complete(PROMPT, request.text, ParseResponse.model_json_schema())
        return ParseResponse.model_validate(result)
    except client.LLMError:
        raise HTTPException(status_code=503, detail={"code": "AI-LLM-001"}) from None
    except (client.InvalidLLMResponse, ValidationError):
        raise HTTPException(
            status_code=502,
            detail={"code": "AI-PARSE-001", "clarifyingQuestion": CLARIFYING_QUESTION},
        ) from None
