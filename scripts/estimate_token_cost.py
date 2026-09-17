"""$20 크레딧으로 각 경로를 몇 번 부를 수 있는지 계산한다.

토큰 수는 `count_tokens`로 실측한다. 이 엔드포인트는 과금되지 않으며 모델을 실행하지 않는다.
가격은 2026-09-16 기준 공식 표(platform.claude.com/docs/en/about-claude/pricing)를 옮긴 값이다.
가격이 바뀌면 PRICES와 USD_PER_SEARCH를 고쳐야 한다.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.catalog import CatalogModelResult
from app.llm import client
from app.narrate import PROMPT as NARRATE_PROMPT
from app.narrate import NarrateRequest, ReasonResult

PROMPTS = Path(__file__).resolve().parents[1] / "app" / "prompts"
USD_PER_SEARCH = 0.01  # 웹 검색 $10 / 1,000회. 토큰과 별개다.
BUDGET_USD = 20.0
# 백만 토큰당 입력 / 출력 요금.
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SAMPLE_NARRATE = NarrateRequest(
    monthlyTotal=71300, baseline=89000, monthlySavings=17700, annualSavings=212400,
    planId=42, planName="5G 슬림+", carrier="SKT",
    breakdown=[
        {"label": "5G 슬림+ 기본료", "amount": 55000, "provenance": "OFFICIAL"},
        {"label": "선택약정 25% 할인", "amount": -13750, "provenance": "DERIVED"},
        {"label": "넷플릭스 스탠다드", "amount": 13500, "provenance": "OFFICIAL",
         "note": "제휴 혜택으로 4,000원 할인 적용"},
    ],
    missingInputs=[{"field": "hasFamilyBundle", "impact": "가족 결합 시 최대 11,000원 추가 절감 가능",
                    "howToFind": "통신사 마이페이지 > 결합 상품"}],
)


async def count_tokens(model: str, system: str, content: str | list[dict],
                       tools: list[dict]) -> int:
    body = {"model": model, "system": system, "tools": tools,
            "messages": [{"role": "user", "content": content}]}
    result = await client._messages(body, path="/v1/messages/count_tokens")
    return int(result["input_tokens"])


def tool(schema: dict) -> dict:
    return {"name": "return_result", "input_schema": schema}


async def report(model: str) -> None:
    input_usd, output_usd = PRICES[model]
    # 실제 출력 길이의 보수적 상한. 사유 3줄·파싱 결과 모두 이보다 짧다.
    routes = [
        ("/narrate 사유", NARRATE_PROMPT, SAMPLE_NARRATE.model_dump_json(),
         [tool(ReasonResult.model_json_schema())], 200, 0),
        ("/catalog 후보 1건", (PROMPTS / "catalog.txt").read_text(encoding="utf-8"),
         "상품 종류: MOBILE_PLAN\n찾을 상품: SKT 베스트 Max",
         [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1,
           "allowed_domains": ["tworld.co.kr"]}, tool(CatalogModelResult.model_json_schema())],
         500, 1),
    ]

    print(f"\n=== {model}  (입력 ${input_usd}/MTok · 출력 ${output_usd}/MTok) ===")
    print(f"{'경로':<20}{'입력':>8}{'출력상한':>9}{'1회 비용':>12}{'$20으로':>12}")
    for label, system, content, tools, max_output, searches in routes:
        tokens = await count_tokens(model, system, content, tools)
        # 검색 결과 본문이 입력 토큰으로 되돌아온다. 검색 1회당 대략 2,500토큰으로 잡는다.
        tokens += searches * 2500
        cost = tokens * input_usd / 1e6 + max_output * output_usd / 1e6 + searches * USD_PER_SEARCH
        print(f"{label:<20}{tokens:>8,}{max_output:>9,}{'$' + format(cost, '.4f'):>12}"
              f"{int(BUDGET_USD / cost):>10,}회")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="*", default=["claude-sonnet-4-6", "claude-sonnet-5",
                                                        "claude-haiku-4-5"])
    arguments = parser.parse_args()
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        print("ANTHROPIC_API_KEY가 필요하다. count_tokens는 과금되지 않는다.")
        return 1
    for model in arguments.models:
        await report(model)
    print(f"\n웹 검색은 토큰과 별개로 1,000회당 $10다. 카탈로그 전수 검증 2,038행 × 1회 = "
          f"${2038 * USD_PER_SEARCH:.2f} (검색 요금만).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
