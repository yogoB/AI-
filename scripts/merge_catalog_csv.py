"""흩어진 카탈로그 CSV를 통신·구독 두 축으로 합쳐 정렬한다.

원본은 지우지 않는다. 결과는 작업 루트에 쓰며 어느 레포에도 커밋하지 않는다.
합친 결과는 아직 검수본이 아니다. `docs/catalog-data-policy.md`의 검수 절차를 그대로 따른다.
"""

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 우선순위 순서다. 같은 상품이 여러 파일에 있으면 앞선 파일의 행을 남긴다.
MOBILE_SOURCES = [
    "통신사_요금제_매트릭스_추천대상_2026-09-16.csv",
    "통신사_요금제_매트릭스_추천대상_2026-09-14.csv",
    "통신사_요금제_매트릭스_2026-09-14.csv",
]
SUBSCRIPTION_DIR = "subscriptions_csv_only_handoff_20260915_v2"
SUBSCRIPTION_SOURCES = {
    "normalized_ott.csv": ("OTT", "ott_plan_id"),
    "normalized_music.csv": ("음악", "music_plan_id"),
    "normalized_ai.csv": ("AI", "ai_plan_id"),
    "normalized_ebook.csv": ("전자책", "ebook_plan_id"),
    "normalized_cloud.csv": ("클라우드", "cloud_plan_id"),
}


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def to_won(value: str) -> int:
    """정렬용. 빈 값·비숫자는 맨 뒤로 보낸다."""
    digits = "".join(character for character in value if character.isdigit())
    return int(digits) if digits else sys.maxsize


def merge_mobile(root: Path) -> tuple[list[str], list[dict[str, str]]]:
    header: list[str] = ["행번호"]
    merged: dict[tuple[str, ...], dict[str, str]] = {}
    for name in MOBILE_SOURCES:
        path = root / name
        if not path.exists():
            print(f"건너뜀(없음): {name}", file=sys.stderr)
            continue
        fields, rows = read_rows(path)
        header += [field for field in fields if field not in header]
        for row in rows:
            # 컬럼설명 CSV의 안내대로 공식상품ID를 기준으로 맞추고, 없으면 브랜드+요금제명을 쓴다.
            product_id = (row.get("공식상품ID") or "").strip()
            key = (product_id,) if product_id else (
                (row.get("브랜드") or "").strip(), (row.get("요금제명") or "").strip(),
                (row.get("월정액(원)") or "").strip())
            if key not in merged:
                merged[key] = {**row, "출처파일": name}

    header = [field for field in header if field != "행번호"] + ["출처파일"]
    rows = sorted(merged.values(), key=lambda row: (
        row.get("통신사구분", ""), row.get("브랜드", ""),
        to_won(row.get("월정액(원)", "")), row.get("요금제명", ""),
    ))
    for number, row in enumerate(rows, start=1):
        row["행번호"] = str(number)
    return ["행번호", *header], rows


def merge_subscriptions(root: Path) -> tuple[list[str], list[dict[str, str]]]:
    header = ["plan_id", "구분"]
    rows: list[dict[str, str]] = []
    for name, (category, pk) in SUBSCRIPTION_SOURCES.items():
        path = root / SUBSCRIPTION_DIR / name
        if not path.exists():
            print(f"건너뜀(없음): {name}", file=sys.stderr)
            continue
        fields, source_rows = read_rows(path)
        # PK 이름만 표마다 다르다. plan_id 하나로 통일하고 구분 컬럼으로 출처 축을 남긴다.
        header += [field for field in fields if field != pk and field not in header]
        header += ["출처파일"] if "출처파일" not in header else []
        for row in source_rows:
            rows.append({**row, "plan_id": row.get(pk, ""), "구분": category, "출처파일": name})

    rows.sort(key=lambda row: (
        row["구분"], row.get("service", ""),
        to_won(row.get("regular_price", "")), row.get("plan_name", ""),
    ))
    return header, rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="CSV가 있는 작업 루트")
    arguments = parser.parse_args()

    for label, (header, rows), out in (
        ("통신", merge_mobile(arguments.root), "catalog_mobile_plans.csv"),
        ("구독", merge_subscriptions(arguments.root), "catalog_subscriptions.csv"),
    ):
        if not rows:
            print(f"{label}: 원본을 찾지 못했다", file=sys.stderr)
            return 1
        write_rows(arguments.root / out, header, rows)
        print(f"{label}: {len(rows)}행 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
