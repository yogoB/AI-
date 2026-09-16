"""공식 자료에서 카탈로그 후보 행을 뽑아 검수용 CSV를 만든다.

수동 배치다. 추천 요청 경로에서는 절대 호출하지 않는다.

**모델은 파서 자리이지 출처 자리가 아니다.** 행마다 원문 인용을 함께 받아 그 인용이
자료 안에 실제로 있는지 대조하고, 없으면 그 행을 버린다. 단위 환산·무제한 표기·ID 매핑처럼
숫자를 만드는 일은 전부 이 스크립트가 한다(절대 원칙 1·2).

결과는 검수 대기 후보다. 사람이 확인하기 전에는 추천·최저가·알림 계산에 쓰지 않는다
(`docs/catalog-data-policy.md`). 이 스크립트는 기준 CSV를 고치지 않는다.

사용:
    uv run --env-file .env python -m scripts.extract_catalog mobile_plan \
        --source-url https://www.example.co.kr/plans 자료.html
"""

import argparse
import asyncio
import base64
import csv
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Literal, Self

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.llm import client
from app.ocr import image_content
from scripts.estimate_token_cost import PRICES, count_tokens
from scripts.verify_catalog_prices import ROOT, read_rows, revision_dir

PROMPT = (Path(__file__).resolve().parents[1] / "app" / "prompts" / "extract.txt").read_text(
    encoding="utf-8"
)
# 카탈로그 CSV가 "무제한"을 적는 방식. 계산이 아니라 약속된 표기다.
UNLIMITED = 999999
# 한 자료에서 행을 여러 개 뽑으면 응답이 길어진다. 사용자 요청 경로보다 넉넉히 기다린다.
TIMEOUT_SECONDS = 180.0
MAX_OUTPUT_TOKENS = 8192
# 한 번에 보낼 수 있는 자료 길이. 넘으면 잘라서 보내지 않고 거절한다 —
# 조용히 자르면 뒷부분 상품이 통째로 빠진 것을 아무도 모른다.
MAX_TEXT_CHARS = 200_000
TEXT_SUFFIXES = {".html", ".htm", ".txt", ".md", ".csv", ".json"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

MOBILE_PLAN_FIELDS = ["carrier", "plan_name", "network_type", "base_price", "data_mb",
                      "voice_min", "sms_cnt", "contract_discount_12m", "contract_discount_24m",
                      "age_limit", "source_url", "collected_at"]
PLAN_BENEFIT_FIELDS = ["carrier", "plan_name", "service_id", "tier_id", "benefit_type",
                       "discount_value", "is_exclusive", "exclusive_group", "valid_from",
                       "valid_to", "source_url", "collected_at"]
REVIEW_FIELDS = ["자료", "데이터셋", "상태", "사유", "식별", "금액", "인용문", "출처URL"]

Name = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")]
Text = Annotated[str, Field(min_length=1, max_length=300, pattern=r"^[^\r\n]+$")]
# 인용문은 금액이 어느 상품의 것인지 사람이 알아볼 만큼 길어야 한다.
Quote = Annotated[str, Field(min_length=8, max_length=500)]
Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class MobilePlanRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    carrier: Name
    planName: Name
    networkType: Literal["5G", "LTE", "3G"]
    basePrice: int = Field(gt=0, le=10_000_000)
    dataText: Text
    voiceText: Text | None = None
    smsText: Text | None = None
    # ALL·다이렉트·미표기(NULL)만 추천 후보가 된다(G-18). 모르는 것을 ALL로 적으면
    # 자격이 필요한 상품이 전체 사용자에게 추천된다. 그래서 UNKNOWN을 따로 둔다.
    ageLimit: Literal["ALL", "다이렉트", "키즈", "청소년", "청년", "시니어", "복지",
                      "군인", "외국인", "태블릿/웨어러블", "UNKNOWN"]
    sourceQuote: Quote


class PlanBenefitRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    carrier: Name
    planName: Name
    serviceName: Name
    tierName: Name | None = None
    benefitType: Literal["FREE", "FIXED_DISCOUNT", "RATE_DISCOUNT", "BUNDLE_INCLUDED"]
    discountValue: int | None = Field(default=None, ge=0)
    validFrom: Day | None = None
    validTo: Day | None = None
    sourceQuote: Quote

    @model_validator(mode="after")
    def enforce_discount_shape(self) -> Self:
        # db/migration/V1 의 plan_benefit CHECK 과 같은 규칙이다. 여기서 막으면 적재에서 안 터진다.
        if self.benefitType in ("FREE", "BUNDLE_INCLUDED"):
            if self.discountValue is not None:
                raise ValueError("FREE and BUNDLE_INCLUDED carry no discount value")
        elif self.discountValue is None or self.discountValue <= 0:
            raise ValueError("Discount benefits need a positive value")
        elif self.benefitType == "RATE_DISCOUNT" and self.discountValue > 100:
            raise ValueError("A rate discount is a percentage")
        return self


class MobilePlanRows(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rows: list[MobilePlanRow] = Field(max_length=300)


class PlanBenefitRows(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rows: list[PlanBenefitRow] = Field(max_length=300)


ROWS = {"mobile_plan": MobilePlanRows, "plan_benefit": PlanBenefitRows}


@dataclass(frozen=True)
class Material:
    """모델에 보낼 내용과, 인용문을 대조할 원문. 이미지·PDF는 대조할 글자가 없어 text가 None이다."""

    path: Path
    content: list[dict]
    text: str | None


@dataclass(frozen=True)
class Checked:
    status: str
    reason: str
    row: dict[str, str] | None


class Stripper(HTMLParser):
    """태그를 걷어낸 본문. 모델에 보내는 문자열과 대조하는 문자열이 같아야 한다."""

    SKIP = {"script", "style", "noscript", "head", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self.skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)


def strip_html(raw: str) -> str:
    stripper = Stripper()
    stripper.feed(raw)
    stripper.close()
    return " ".join(stripper.parts)


def normalize(text: str) -> str:
    """줄바꿈·들여쓰기는 자료를 옮기는 과정에서 달라진다. 공백 하나로 맞춘 뒤 비교한다."""
    return " ".join(text.split())


def shows_amount(quote: str, amount: int | None) -> bool:
    """인용문 안에 그 금액이 실제로 보이는가. 5,000이 15,000 안에서 우연히 잡히지 않게 경계를 본다."""
    if amount is None:
        return True
    return any(
        re.search(rf"(?<![\d,]){re.escape(form)}(?![\d,])", quote)
        for form in {str(amount), f"{amount:,}"}
    )


def to_mb(text: str) -> int | None:
    """'110GB' → 112640. 환산은 코드가 한다(절대 원칙 1). 판단할 수 없으면 None."""
    # 단위 뒤에 글자가 이어지면 단위가 아니다: "5Mbps"의 Mb 를 5MB 로 읽지 않는다.
    match = re.search(r"(\d+(?:\.\d+)?)\s*(GB|기가|MB)(?![A-Za-z])", text, re.IGNORECASE)
    if match:
        size = float(match.group(1))
        return int(size * 1024) if match.group(2).upper() in ("GB", "기가") else int(size)
    return UNLIMITED if "무제한" in text else None


def to_count(text: str | None) -> int | None:
    """'300분' → 300. '무제한'이 함께 적힌 표기는 무제한으로 본다. 통화·문자는 빈칸을 허용한다."""
    if not text:
        return None
    if "무제한" in text or "기본제공" in text:
        return UNLIMITED
    match = re.search(r"(\d[\d,]*)", text)
    return int(match.group(1).replace(",", "")) if match else None


def key(name: str) -> str:
    """공백·대소문자를 무시한 대조 키. 프론트 matches()와 같은 기준이다."""
    return re.sub(r"\s+", "", name).lower()


def quote_status(quote: str, amount: int | None, source: str | None) -> tuple[str, str]:
    """이 도구의 핵심. 모델이 옮겼다고 주장한 문장이 자료에 실제로 있는지 본다.

    이미지·PDF는 글자로 대조할 수 없어 '검수필요'다 — 통과가 아니라 사람이 봐야 한다는 뜻이다.
    한계: 인용문이 자료에 있고 금액이 그 안에 있다는 것까지만 보증한다.
    그 금액이 정말 그 상품의 것인지는 검수자가 판단한다.
    """
    if not shows_amount(quote, amount):
        return "폐기", "금액이 인용문에 없음"
    if source is None:
        return "검수필요", "이미지·PDF는 인용문을 기계로 대조할 수 없음"
    if normalize(quote) not in normalize(source):
        return "폐기", "인용문이 자료에 없음"
    return "채택", ""


def check_mobile_plan(row: MobilePlanRow, material: Material, source_url: str,
                      collected_at: str) -> Checked:
    status, reason = quote_status(row.sourceQuote, row.basePrice, material.text)
    if status == "폐기":
        return Checked(status, reason, None)
    if row.ageLimit == "UNKNOWN":
        # 빈 age_limit 은 '누구나'로 읽힌다. 모르는 채로 넣으면 자격 상품이 전체에 추천된다.
        return Checked("폐기", "가입 대상 미확인", None)
    data_mb = to_mb(row.dataText)
    if data_mb is None:
        # data_mb 는 적재에서 NOT NULL 이고 후보 조회의 기준이다. 비워서 내보낼 수 없다.
        return Checked("폐기", f"데이터 제공량을 숫자로 읽지 못함: {row.dataText}", None)
    return Checked(status, reason, {
        "carrier": row.carrier, "plan_name": row.planName, "network_type": row.networkType,
        "base_price": str(row.basePrice), "data_mb": str(data_mb),
        "voice_min": blank(to_count(row.voiceText)), "sms_cnt": blank(to_count(row.smsText)),
        # 약정 할인액은 기준 CSV 1,706행 전부가 비어 있다. 모델에 묻지 않고 비운다.
        "contract_discount_12m": "", "contract_discount_24m": "",
        "age_limit": row.ageLimit, "source_url": source_url, "collected_at": collected_at,
    })


def check_plan_benefit(row: PlanBenefitRow, material: Material, source_url: str,
                       collected_at: str, catalog: "Catalog") -> Checked:
    status, reason = quote_status(row.sourceQuote, row.discountValue, material.text)
    if status == "폐기":
        return Checked(status, reason, None)
    if key(f"{row.carrier}|{row.planName}") not in catalog.plans:
        return Checked("폐기", f"카탈로그에 없는 요금제: {row.carrier} {row.planName}", None)
    service_id = catalog.services.get(key(row.serviceName))
    if service_id is None:
        return Checked("폐기", f"카탈로그에 없는 서비스: {row.serviceName}", None)

    tier_id = ""
    benefit_type = row.benefitType
    if row.tierName:
        found = catalog.tiers.get((service_id, key(row.tierName)))
        if found is None:
            return Checked("폐기", f"카탈로그에 없는 등급: {row.serviceName} {row.tierName}", None)
        tier_id = found
    elif benefit_type == "FREE":
        # 어느 등급을 주는지 모르는데 FREE 로 넣으면 프리미엄까지 0원이 되어 실제보다 싸게 추천한다.
        # catalog_benefits_from_matrix.py 와 같은 규칙으로 금액 효과를 없앤다.
        benefit_type = "BUNDLE_INCLUDED"
        reason = "등급 미상이라 BUNDLE_INCLUDED(표시만)로 낮춤"

    return Checked(status, reason, {
        "carrier": row.carrier, "plan_name": row.planName, "service_id": service_id,
        "tier_id": tier_id, "benefit_type": benefit_type,
        "discount_value": blank(row.discountValue),
        "is_exclusive": "false", "exclusive_group": "",
        "valid_from": row.validFrom or "", "valid_to": row.validTo or "",
        "source_url": source_url, "collected_at": collected_at,
    })


def blank(value: int | None) -> str:
    return "" if value is None else str(value)


@dataclass(frozen=True)
class Catalog:
    """승인된 리비전의 이름 → ID. 모델이 ID를 지어내지 않도록 대조는 코드가 한다."""

    plans: set[str]
    services: dict[str, str]
    tiers: dict[tuple[str, str], str]
    hint: str


def load_catalog(revision: Path) -> Catalog:
    plans = {key(f"{row.get('carrier', '')}|{row.get('plan_name', '')}")
             for row in read_rows(revision / "mobile_plan.csv")}
    service_rows = read_rows(revision / "subscription_service.csv")
    tier_rows = read_rows(revision / "subscription_tier.csv")
    services = {key(row["name"]): row["id"] for row in service_rows}
    tiers = {(row["service_id"], key(row["name"])): row["id"] for row in tier_rows}

    grades: dict[str, list[str]] = {}
    for row in tier_rows:
        grades.setdefault(row["service_id"], []).append(row["name"])
    lines = ["카탈로그에 있는 구독 서비스와 등급이다. serviceName·tierName은 여기 있는 이름을 그대로 쓴다."]
    lines += [f"- {row['name']}: {' / '.join(grades.get(row['id'], [])) or '등급 없음'}"
              for row in service_rows]
    return Catalog(plans, services, tiers, "\n".join(lines))


def load_material(path: Path) -> Material:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = normalize(strip_html(raw) if suffix in (".html", ".htm") else raw)
        if not text:
            raise ValueError("자료에서 읽을 글자가 없다")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"자료가 너무 길다({len(text):,}자). 상품 목록 단위로 나눠서 넘겨라")
        return Material(path, [{"type": "text", "text": text}], text)
    encoded = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    if suffix in IMAGE_SUFFIXES:
        # 형식·크기·해상도 검증은 /ocr 과 같은 코드를 쓴다.
        return Material(path, [image_content(encoded)], None)
    if suffix == ".pdf":
        return Material(path, [{"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf", "data": encoded}}], None)
    raise ValueError(f"지원하지 않는 형식: {suffix or '확장자 없음'}")


def blocks(material: Material, dataset: str, hint: str) -> list[dict]:
    instruction = f"위 자료에서 {dataset} 데이터셋의 행을 뽑아라."
    if hint:
        instruction = f"{instruction}\n\n{hint}"
    return [*material.content, {"type": "text", "text": instruction}]


async def extract(material: Material, dataset: str, hint: str) -> list[BaseModel]:
    result = await client.complete(
        PROMPT, blocks(material, dataset, hint), ROWS[dataset].model_json_schema(),
        max_tokens=MAX_OUTPUT_TOKENS, timeout=TIMEOUT_SECONDS,
    )
    return ROWS[dataset].model_validate(result).rows


def review_row(material: Material, dataset: str, checked: Checked, identity: str,
               amount: str, quote: str, source_url: str) -> dict[str, str]:
    return {"자료": material.path.name, "데이터셋": dataset, "상태": checked.status,
            "사유": checked.reason, "식별": identity, "금액": amount,
            "인용문": normalize(quote), "출처URL": source_url}


async def run(materials: list[Material], dataset: str, catalog: Catalog, hint: str,
              source_url: str, collected_at: str,
              concurrency: int) -> tuple[list[dict], list[dict], list[dict]]:
    limit = asyncio.Semaphore(concurrency)

    async def guarded(material: Material) -> tuple[Material, list[BaseModel] | str]:
        async with limit:
            try:
                return material, await extract(material, dataset, hint)
            except client.LLMError as error:
                return material, f"모델 호출 실패: {error}"
            except (client.InvalidLLMResponse, ValidationError) as error:
                return material, f"모델 응답이 계약과 다름: {type(error).__name__}"

    accepted: list[dict] = []
    manual: list[dict] = []
    review: list[dict] = []
    for done in asyncio.as_completed([guarded(material) for material in materials]):
        material, result = await done
        if isinstance(result, str):
            review.append(review_row(material, dataset, Checked("호출실패", result, None),
                                     "", "", "-", source_url))
            print(f"  {material.path.name}: {result}", file=sys.stderr)
            continue
        lines = []
        for row in result:
            if dataset == "mobile_plan":
                checked = check_mobile_plan(row, material, source_url, collected_at)
                identity = f"{row.carrier} {row.planName}"
                amount = str(row.basePrice)
            else:
                checked = check_plan_benefit(row, material, source_url, collected_at, catalog)
                identity = f"{row.carrier} {row.planName} · {row.serviceName} {row.tierName or ''}".strip()
                amount = blank(row.discountValue)
            lines.append(review_row(material, dataset, checked, identity, amount,
                                    row.sourceQuote, source_url))
            if checked.row is not None:
                (accepted if checked.status == "채택" else manual).append(checked.row)
        review += lines
        kept = sum(1 for line in lines if line["상태"] != "폐기")
        print(f"  {material.path.name}: {len(result)}행 중 {kept}행 남김", file=sys.stderr)
    return accepted, manual, review


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


async def preflight(materials: list[Material], dataset: str, hint: str, max_usd: float) -> bool:
    """실제 추출 전에 토큰을 실측한다. count_tokens는 과금되지 않는다."""
    model = client.default_model()
    input_usd, output_usd = PRICES.get(model, PRICES["claude-sonnet-5"])
    tool = [{"name": "return_result", "input_schema": ROWS[dataset].model_json_schema()}]
    total = 0.0
    for material in materials:
        tokens = await count_tokens(model, PROMPT, blocks(material, dataset, hint), tool)
        cost = tokens * input_usd / 1e6 + MAX_OUTPUT_TOKENS * output_usd / 1e6
        total += cost
        print(f"  {material.path.name}: 입력 {tokens:,}토큰 · 최대 ${cost:.3f}", file=sys.stderr)
    print(f"{model} 기준 예상 상한 ${total:.2f} (출력을 최대치로 잡은 값)", file=sys.stderr)
    if max_usd and total > max_usd:
        print(f"예상 비용이 상한 ${max_usd:.2f}를 넘는다. 자료를 나누거나 --max-usd를 올려라",
              file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=sorted(ROWS))
    parser.add_argument("files", nargs="+", type=Path, help="공식 자료. html·txt·md·csv·pdf·이미지")
    parser.add_argument("--source-url", required=True, help="이 자료를 확인한 공식 페이지 주소")
    parser.add_argument("--out", type=Path, default=Path("catalog_extract"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--collected-at", default=date.today().isoformat())
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-usd", type=float, default=2.0,
                        help="예상 토큰 비용 상한. 넘으면 실행하지 않는다 (0이면 상한 없음)")
    parser.add_argument("--dry-run", action="store_true", help="비용만 실측하고 추출하지 않는다")
    arguments = parser.parse_args()

    if not arguments.source_url.startswith("https://"):
        print("--source-url은 https 공식 주소여야 한다", file=sys.stderr)
        return 1
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        print("ANTHROPIC_API_KEY가 설정되지 않았다", file=sys.stderr)
        return 1

    try:
        catalog = load_catalog(revision_dir(arguments.root))
    except (FileNotFoundError, KeyError, OSError) as error:
        print(f"승인된 카탈로그 리비전을 읽지 못했다: {error}", file=sys.stderr)
        return 1

    materials = []
    for path in arguments.files:
        try:
            materials.append(load_material(path))
        except (HTTPException, OSError, ValueError) as error:
            detail = error.detail if isinstance(error, HTTPException) else error
            print(f"{path.name}: 건너뜀 — {detail}", file=sys.stderr)
    if not materials:
        print("읽을 수 있는 자료가 없다", file=sys.stderr)
        return 1

    hint = catalog.hint if arguments.dataset == "plan_benefit" else ""
    print(f"{arguments.dataset}: 자료 {len(materials)}건", file=sys.stderr)
    if not asyncio.run(preflight(materials, arguments.dataset, hint, arguments.max_usd)):
        return 1
    if arguments.dry_run:
        print("모델 호출 없음. 추출하려면 --dry-run을 뺀다", file=sys.stderr)
        return 0

    accepted, manual, review = asyncio.run(run(
        materials, arguments.dataset, catalog, hint, arguments.source_url,
        arguments.collected_at, arguments.concurrency,
    ))

    fields = MOBILE_PLAN_FIELDS if arguments.dataset == "mobile_plan" else PLAN_BENEFIT_FIELDS
    out = arguments.out
    write_csv(out / f"{arguments.dataset}.csv", fields, accepted)
    write_csv(out / "review.csv", REVIEW_FIELDS, review)
    if manual:
        # 이미지·PDF 는 인용문을 대조할 글자가 없다. 파일 이름으로 그 사실을 남긴다.
        write_csv(out / f"{arguments.dataset}_manual.csv", fields, manual)

    dropped = sum(1 for line in review if line["상태"] == "폐기")
    print(f"채택 {len(accepted)}행 · 눈으로 확인 {len(manual)}행 · 폐기 {dropped}행 → {out}/",
          file=sys.stderr)
    print("검수 전에는 추천·최저가·알림 계산에 쓰지 않는다. 승인은 BE 변경 제안 절차로 한다.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
