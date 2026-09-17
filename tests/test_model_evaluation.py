import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
import pytest

from app.ocr import OcrResponse
from scripts.evaluate_model import evaluate, main


def test_evaluation_detects_mismatch_without_printing_user_data(tmp_path, capsys):
    image = tmp_path / "private-screen.png"
    with Image.new("RGB", (2, 2), "white") as source:
        source.save(image)
    cases = [{"id": "sample", "kind": "ocr", "image": image.name,
              "expected": {"monthlyDataGb": 20.0}}]
    result = OcrResponse.model_validate({"confidence": 0.9, "monthlyDataGb": 20.0})
    with patch("scripts.evaluate_model.ocr", return_value=result):
        assert asyncio.run(evaluate(cases, tmp_path)) == 0
        cases[0]["expected"] = {"monthlyDataGb": 7.5}
        assert asyncio.run(evaluate(cases, tmp_path)) == 1
    # 첫 오류에서 멈춘다 — 실패가 이어질 때 남은 사례로 모델을 더 부르지 않는다.
    with patch("scripts.evaluate_model.ocr", side_effect=HTTPException(status_code=503)) as ocr_call:
        assert asyncio.run(evaluate(cases * 2, tmp_path)) == 2
        ocr_call.assert_called_once()
    output = capsys.readouterr().out
    assert "PASS sample" in output and "FAIL sample" in output
    assert image.name not in output


@pytest.mark.parametrize("image_state", ["missing", "corrupt", "oversize"])
def test_dry_run_rejects_invalid_ocr_image_without_model_call(tmp_path, capsys, image_state):
    image = tmp_path / "private-screen.png"
    if image_state == "corrupt":
        image.write_bytes(b"private non-image data")
    elif image_state == "oversize":
        with image.open("wb") as source:
            source.truncate(5 * 1024 * 1024 + 1)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{
        "id": "screen", "kind": "ocr", "image": image.name,
        "expected": {"monthlyDataGb": 18.4},
    }]))
    with (
        patch("sys.argv", ["evaluate_model", "--dry-run", "--cases", str(cases)]),
        patch("app.llm.client.complete") as complete,
    ):
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
        complete.assert_not_called()
    assert image.name not in capsys.readouterr().err


@pytest.mark.parametrize("invalid", [
    {"id": "valid"},
    {"expected": {"unknownField": 20}},
    {"expected": {"monthlyDataGb": True}},
])
def test_all_cases_are_validated_before_first_model_call(tmp_path, capsys, invalid):
    image = tmp_path / "private-screen.png"
    with Image.new("RGB", (2, 2), "white") as source:
        source.save(image)
    valid = {"id": "valid", "kind": "ocr", "image": image.name,
             "expected": {"monthlyDataGb": 18.4}}
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([valid, {**valid, "id": "invalid", **invalid}]))
    with (
        patch("sys.argv", ["evaluate_model", "--cases", str(cases)]),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-only"}),
        patch("app.llm.client.complete") as complete,
    ):
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
        complete.assert_not_called()
    assert valid["image"] not in capsys.readouterr().err


def test_ocr_evaluation_reads_image_preserves_null_and_zero_and_detects_errors(tmp_path, capsys):
    image = tmp_path / "private-screen.png"
    with Image.new("RGB", (2, 2), "white") as source:
        source.save(image)
    cases = [{"id": "screen", "kind": "ocr", "image": image.name,
              "expected": {"monthlyDataGb": 18.4, "monthlyVoiceMin": None, "monthlySmsCount": 0}}]
    result = {**cases[0]["expected"], "confidence": 0.9}
    case_file = tmp_path / "cases.json"
    case_file.write_text(json.dumps(cases))
    with (
        patch("sys.argv", ["evaluate_model", "--dry-run", "--cases", str(case_file)]),
        patch("app.llm.client.complete") as complete,
    ):
        assert main() == 0
        complete.assert_not_called()
    with patch("app.llm.client.complete", return_value=result) as complete:
        assert asyncio.run(evaluate(cases, tmp_path)) == 0
        content = complete.call_args.args[1]
        assert content[0]["source"]["media_type"] == "image/png"
        complete.return_value = {**result, "monthlyDataGb": 18.5}
        assert asyncio.run(evaluate(cases, tmp_path)) == 1
        complete.return_value = {**result, "confidence": 0.2}
        assert asyncio.run(evaluate(cases, tmp_path)) == 1
    output = capsys.readouterr().out
    assert "PASS screen" in output and "FAIL screen: HTTP 422" in output
    assert "private-screen.png" not in output and content[0]["source"]["data"] not in output
