"""수동으로 실행하는 실제 모델 평가. 발화·이미지·응답 원문은 출력하거나 저장하지 않는다."""

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path

from fastapi import HTTPException

from app.ocr import MAX_IMAGE_BYTES, OcrRequest, OcrResponse, image_content, ocr
from app.parse import ParseRequest, ParseResponse, parse


def read_image(image: Path) -> str:
    if image.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds upload limit")
    return base64.b64encode(image.read_bytes()).decode("ascii")


def validate_cases(cases: list[dict], directory: Path) -> None:
    if not isinstance(cases, list) or not cases:
        raise ValueError("Expected a nonempty case list")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Expected a case object")
        label = case.get("id")
        if not isinstance(label, str) or not label.strip() or not label.isprintable() or label in seen:
            raise ValueError("Expected a unique printable case ID")
        seen.add(label)
        kind = case.get("kind")
        if kind not in ("parse", "ocr"):
            raise ValueError("Unknown case kind")
        input_field = "text" if kind == "parse" else "image"
        if set(case) != {"id", "kind", input_field, "expected"}:
            raise ValueError("Unexpected case fields")
        expected = case["expected"]
        response_model = ParseResponse if kind == "parse" else OcrResponse
        if not isinstance(expected, dict) or not expected:
            raise ValueError("Expected output fields are required")
        # 기대값의 타입·필드만 검증한다. 평가에는 사용자가 적은 원래 기대값을 쓴다.
        response_model.model_validate({"confidence": 0.0, **expected})
        if kind == "parse":
            ParseRequest.model_validate({"text": case["text"]})
        else:
            image_content(read_image(directory / case["image"]))


async def evaluate(cases: list[dict], directory: Path) -> int:
    failures = 0
    for case in cases:
        try:
            if case["kind"] == "parse":
                result = await parse(ParseRequest(text=case["text"]))
            elif case["kind"] == "ocr":
                encoded = read_image(directory / case["image"])
                result = await ocr(OcrRequest(image=encoded))
            else:
                raise ValueError("Unknown case kind")
            actual = result.model_dump()
            passed = all(key in actual and actual[key] == value for key, value in case["expected"].items())
            print(f'{"PASS" if passed else "FAIL"} {case["id"]}')
            failures += not passed
        except HTTPException as error:
            print(f'FAIL {case["id"]}: HTTP {error.status_code}')
            if error.status_code == 503:
                print("모델 호출 불가: 나머지 평가를 중단합니다.")
                return 2
            failures += 1
        except (OSError, ValueError, KeyError):
            print(f'FAIL {case["id"]}: 평가 데이터 또는 추출값 오류')
            failures += 1
    print(f"{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).resolve().parents[1] / "evaluations/parse.json")
    parser.add_argument("--dry-run", action="store_true", help="모델 호출 없이 평가 자료와 이미지 검증")
    args = parser.parse_args()
    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        validate_cases(cases, args.cases.parent)
    except (OSError, ValueError, KeyError, TypeError, HTTPException):
        parser.error("평가 JSON의 필드·기대값·중복 ID와 이미지 파일의 경로·형식·크기를 확인해 주세요. 모델 호출 없음.")
    if args.dry_run:
        print(f"{len(cases)} cases validated; 모델 호출 없음")
        return 0
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        print("ANTHROPIC_API_KEY를 로컬 환경에 설정해 주세요. 모델 호출 없음.")
        return 2
    return asyncio.run(evaluate(cases, args.cases.parent))


if __name__ == "__main__":
    raise SystemExit(main())
