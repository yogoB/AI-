import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.llm import client

router = APIRouter()
PROMPT = (Path(__file__).parent / "prompts" / "catalog.txt").read_text(encoding="utf-8")
DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
CLARIFYING_QUESTION = "사업자와 정확한 상품명을 함께 알려주시겠어요?"
ShortText = Annotated[str, Field(min_length=1, max_length=500, pattern=r"^[^\r\n]+$")]


class CatalogSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    query: str = Field(min_length=2, max_length=300)
    productType: Literal["MOBILE_PLAN", "SUBSCRIPTION"]


class CatalogCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    productType: Literal["MOBILE_PLAN", "SUBSCRIPTION"]
    provider: ShortText
    productName: ShortText
    monthlyPriceWon: int = Field(ge=0, le=9223372036854775807)
    networkType: Literal["5G", "LTE", "3G"] | None = None
    dataAllowanceText: str | None = Field(default=None, min_length=1, max_length=200)
    benefits: list[ShortText] = Field(default_factory=list, max_length=30)
    eligibilityText: str | None = Field(default=None, min_length=1, max_length=1000)
    saleStatus: Literal["AVAILABLE", "ENDED", "UNKNOWN"]
    promotionStartDate: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    promotionEndDate: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    sourceUrl: str = Field(min_length=1, max_length=2000)

    @field_validator("promotionStartDate", "promotionEndDate")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is not None:
            datetime.strptime(value, "%Y-%m-%d")
        return value

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> Self:
        if self.productType == "SUBSCRIPTION" and (
            self.networkType is not None or self.dataAllowanceText is not None
        ):
            raise ValueError("Subscription candidates cannot contain mobile network fields")
        return self


class CatalogModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    candidate: CatalogCandidate | None = None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class CatalogSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: str = Field(min_length=1, max_length=2000)
    title: ShortText
    pageAge: str | None = Field(default=None, max_length=100)


class CatalogSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["CANDIDATE_FOUND", "NOT_FOUND", "NEEDS_INPUT"]
    candidate: CatalogCandidate | None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    sources: list[CatalogSource] = Field(max_length=100)
    checkedAt: datetime
    clarifyingQuestion: str | None = None


def allowed_domains() -> list[str]:
    domains = [value.strip().lower().rstrip(".") for value in os.getenv(
        "CATALOG_ALLOWED_DOMAINS", ""
    ).split(",") if value.strip()]
    if not domains or len(domains) > 30 or any(not DOMAIN.fullmatch(domain) for domain in domains):
        raise ValueError("CATALOG_ALLOWED_DOMAINS is not configured correctly")
    return list(dict.fromkeys(domains))


def _allowed_url(url: str, domains: list[str]) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and any(host == domain or host.endswith(f".{domain}") for domain in domains)


@router.post("/catalog/candidates", response_model=CatalogSearchResponse)
async def catalog_candidate(request: CatalogSearchRequest) -> CatalogSearchResponse:
    try:
        domains = allowed_domains()
    except ValueError:
        raise HTTPException(status_code=503, detail={"code": "AI-CATALOG-001"}) from None

    query = f"상품 종류: {request.productType}\n찾을 상품: {request.query}"
    try:
        raw, raw_sources = await client.search(
            PROMPT, query, CatalogModelResult.model_json_schema(), domains
        )
        result = CatalogModelResult.model_validate(raw)
        sources = [CatalogSource.model_validate(source) for source in raw_sources]
    except client.LLMError:
        raise HTTPException(status_code=503, detail={"code": "AI-LLM-001"}) from None
    except (client.InvalidLLMResponse, ValidationError):
        raise HTTPException(status_code=502, detail={"code": "AI-CATALOG-001"}) from None

    if any(not _allowed_url(source.url, domains) for source in sources):
        raise HTTPException(status_code=502, detail={"code": "AI-CATALOG-001"})

    checked_at = datetime.now(UTC)
    if result.confidence < 0.7:
        return CatalogSearchResponse(
            status="NEEDS_INPUT", candidate=None, confidence=result.confidence,
            sources=sources, checkedAt=checked_at, clarifyingQuestion=CLARIFYING_QUESTION,
        )
    if result.candidate is None:
        return CatalogSearchResponse(
            status="NOT_FOUND", candidate=None, confidence=result.confidence,
            sources=sources, checkedAt=checked_at,
        )

    source_urls = {source.url for source in sources}
    candidate = result.candidate
    if (
        candidate.productType != request.productType
        or candidate.sourceUrl not in source_urls
        or not _allowed_url(candidate.sourceUrl, domains)
    ):
        raise HTTPException(status_code=502, detail={"code": "AI-CATALOG-001"})
    return CatalogSearchResponse(
        status="CANDIDATE_FOUND", candidate=candidate, confidence=result.confidence,
        sources=sources, checkedAt=checked_at,
    )
