"""합친 카탈로그 CSV의 가격을 공식 출처와 대조해 불일치 리포트를 만든다.

수동 배치다. 추천 요청 경로에서는 절대 호출하지 않는다.
결과는 검수 대기 목록일 뿐이며, 사람이 확인하기 전에는 추천·최저가·알림 계산에 쓰지 않는다
(`docs/catalog-data-policy.md`). 이 스크립트는 CSV를 고치지 않는다.
"""

import argparse
import asyncio
import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from app.catalog import CatalogSearchRequest, CatalogSearchResponse, find_candidate

ROOT = Path(__file__).resolve().parents[2]
SEARCHES_PER_ROW = 1
USD_PER_SEARCH = 0.01  # 웹 검색 $10 / 1,000회. 토큰과 별개로 과금된다.
REPORT_FIELDS = [
    "구분", "상품ID", "사업자", "상품명", "CSV가격", "확인가격", "차액",
    "판정", "confidence", "출처URL", "확인시각", "비고",
]


@dataclass(frozen=True)
class Target:
    product_type: str
    label: str
    product_id: str
    provider: str
    name: str
    csv_price: int


def to_won(value: str) -> int | None:
    digits = "".join(character for character in (value or "") if character.isdigit())
    return int(digits) if digits else None


def mobile_targets(rows: list[dict[str, str]], include_excluded: bool) -> list[Target]:
    targets = []
    for row in rows:
        if not include_excluded and row.get("추천판정") == "추천 제외":
            continue
        price = to_won(row.get("월정액(원)", ""))
        name = (row.get("요금제명") or "").strip()
        provider = (row.get("브랜드") or "").strip()
        if price is None or not name or not provider:
            continue
        targets.append(Target("MOBILE_PLAN", "통신", (row.get("공식상품ID") or "").strip(),
                              provider, name, price))
    return targets


def subscription_targets(rows: list[dict[str, str]], include_excluded: bool) -> list[Target]:
    targets = []
    for row in rows:
        # 원화 표시가 아닌 행은 원 단위 CSV 값과 대조할 수 없다. 환산하지 않는다.
        if row.get("currency") != "KRW":
            continue
        price = to_won(row.get("regular_price", ""))
        name = (row.get("plan_name") or "").strip()
        provider = (row.get("service") or "").strip()
        if price is None or not name or not provider:
            continue
        targets.append(Target("SUBSCRIPTION", row.get("구분", "구독"), (row.get("plan_id") or "").strip(),
                              provider, name, price))
    return targets


def judge(target: Target, response: CatalogSearchResponse) -> dict[str, str]:
    report = {
        "구분": target.label, "상품ID": target.product_id, "사업자": target.provider,
        "상품명": target.name, "CSV가격": str(target.csv_price), "확인가격": "", "차액": "",
        "판정": response.status, "confidence": f"{response.confidence:.2f}",
        "출처URL": "", "확인시각": response.checkedAt.isoformat(), "비고": "",
    }
    if response.status != "CANDIDATE_FOUND" or response.candidate is None:
        report["비고"] = response.clarifyingQuestion or "공식 출처에서 확인하지 못함"
        return report
    found = response.candidate.monthlyPriceWon
    report["확인가격"] = str(found)
    report["차액"] = str(found - target.csv_price)
    report["출처URL"] = response.candidate.sourceUrl
    report["판정"] = "일치" if found == target.csv_price else "불일치"
    if response.candidate.saleStatus == "ENDED":
        report["비고"] = "공식 출처에서 판매 종료로 표시됨"
    return report


async def check(target: Target) -> dict[str, str]:
    request = CatalogSearchRequest(
        query=f"{target.provider} {target.name}", productType=target.product_type
    )
    try:
        # 검증은 이미 상품명을 안다. 가격 한 줄만 확인하면 되므로 검색 1회면 된다.
        return judge(target, await find_candidate(request, max_uses=SEARCHES_PER_ROW))
    except HTTPException as error:
        code = (error.detail or {}).get("code", "") if isinstance(error.detail, dict) else ""
        # 502는 출처가 허용 도메인·실제 검색 결과에 없다는 뜻이다. 가격 불일치와 구분해 남긴다.
        return {
            "구분": target.label, "상품ID": target.product_id, "사업자": target.provider,
            "상품명": target.name, "CSV가격": str(target.csv_price), "확인가격": "", "차액": "",
            "판정": "출처거부" if error.status_code == 502 else "호출실패",
            "confidence": "", "출처URL": "", "확인시각": "",
            "비고": f"HTTP {error.status_code} {code}".strip(),
        }


async def run(targets: list[Target], concurrency: int) -> list[dict[str, str]]:
    limit = asyncio.Semaphore(concurrency)

    async def guarded(target: Target) -> dict[str, str]:
        async with limit:
            return await check(target)

    reports = []
    for done in asyncio.as_completed([guarded(target) for target in targets]):
        report = await done
        reports.append(report)
        print(f"  [{len(reports)}/{len(targets)}] {report['사업자']} {report['상품명']} → {report['판정']}",
              file=sys.stderr)
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("axis", choices=["통신", "구독"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--limit", type=int, default=20,
                        help="검사할 행 수. 0이면 전체 (행마다 웹 검색을 호출한다)")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--include-excluded", action="store_true", help="추천 제외 행도 검사")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="대상만 세고 모델을 호출하지 않는다")
    parser.add_argument("--max-usd", type=float, default=5.0,
                        help="예상 검색비 상한. 넘으면 실행하지 않는다 (0이면 상한 없음)")
    arguments = parser.parse_args()

    for variable in () if arguments.dry_run else ("ANTHROPIC_API_KEY", "CATALOG_ALLOWED_DOMAINS"):
        if not os.getenv(variable, "").strip():
            print(f"{variable}가 설정되지 않았다", file=sys.stderr)
            return 1

    mobile = arguments.axis == "통신"
    source = arguments.root / ("catalog_mobile_plans.csv" if mobile else "catalog_subscriptions.csv")
    if not source.exists():
        print(f"{source.name}이 없다. 먼저 merge_catalog_csv.py를 실행한다", file=sys.stderr)
        return 1

    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    build = mobile_targets if mobile else subscription_targets
    targets = build(rows, arguments.include_excluded)

    total = len(targets)
    if arguments.limit:
        targets = targets[:arguments.limit]
    searches = len(targets) * SEARCHES_PER_ROW
    print(f"{source.name}: 대상 {total}행 중 {len(targets)}행 검사"
          f"{f' (나머지 {total - len(targets)}행 건너뜀)' if total > len(targets) else ''}",
          file=sys.stderr)
    projected = searches * USD_PER_SEARCH
    print(f"검색 {searches}회 = 약 ${projected:.2f} (토큰 비용 별도)", file=sys.stderr)
    if arguments.max_usd and projected > arguments.max_usd:
        # 검색비는 토큰과 별개로 나가고 되돌릴 수 없다. 시작 전에 막는다.
        print(f"예상 검색비가 상한 ${arguments.max_usd:.2f}를 넘는다. "
              f"--limit을 줄이거나 --max-usd를 올려라", file=sys.stderr)
        return 1

    if arguments.dry_run:
        for target in targets[:5]:
            print(f"  질의 예시: {target.provider} {target.name} (CSV {target.csv_price:,}원)", file=sys.stderr)
        print(f"모델 호출 없음. {len(targets)}행을 검사하려면 --dry-run을 뺀다", file=sys.stderr)
        return 0

    reports = asyncio.run(run(targets, arguments.concurrency))
    reports.sort(key=lambda report: (report["판정"] != "불일치", report["구분"], report["사업자"]))

    out = arguments.out or arguments.root / f"catalog_price_report_{arguments.axis}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(reports)

    mismatched = sum(1 for report in reports if report["판정"] == "불일치")
    print(f"불일치 {mismatched}건 / 검사 {len(reports)}건 → {out.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
