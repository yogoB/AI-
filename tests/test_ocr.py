import base64
import json
import os
from io import BytesIO
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.llm.client import InvalidLLMResponse, LLMError
from app.main import app
from app.ocr import MAX_BASE64_LENGTH, MAX_IMAGE_BYTES


def encoded_image(image_format="PNG", size=(2, 2)):
    buffer = BytesIO()
    with Image.new("RGB", size, "white") as image:
        image.save(buffer, format=image_format)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.parametrize(("image_format", "media_type"), [
    ("PNG", "image/png"), ("JPEG", "image/jpeg"),
    ("WEBP", "image/webp"), ("GIF", "image/gif"),
])
def test_ocr_sends_validated_image_through_shared_client(image_format, media_type):
    encoded = encoded_image(image_format)
    result = {"monthlyDataGb": 18.4, "monthlyVoiceMin": 120, "monthlySmsCount": 30, "confidence": 0.8}

    def respond(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        body = json.loads(request.content)
        assert body["messages"] == [{"role": "user", "content": [{
            "type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded},
        }]}]
        assert "읽히지 않거나 가려진 항목은 null" in body["system"]
        return httpx.Response(200, json={
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": "return_result", "input": result}],
        })

    with (
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        patch("app.llm.client.AsyncClient", side_effect=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kw
        )),
        TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api,
    ):
        response = api.post("/ocr", json={"image": encoded})
    assert response.status_code == 200
    assert response.json() == result


def test_ocr_preserves_visible_zero_and_leaves_unread_fields_null():
    with (
        patch("app.llm.client.complete", return_value={"monthlySmsCount": 0, "confidence": 0.7}),
        TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api,
    ):
        response = api.post("/ocr", json={"image": encoded_image()})
    assert response.status_code == 200
    assert response.json() == {
        "monthlyDataGb": None, "monthlyVoiceMin": None, "monthlySmsCount": 0, "confidence": 0.7,
    }


@pytest.mark.parametrize(("result", "status"), [
    ({"monthlyDataGb": 18.4, "confidence": 0.699}, 422),
    ({"confidence": 0.9}, 422),
    ({"monthlyDataGb": -1, "confidence": 0.9}, 502),
    ({"monthlyDataGb": float("nan"), "confidence": 0.9}, 502),
    ({"monthlyDataGb": float("inf"), "confidence": 0.9}, 502),
    ({"monthlyDataGb": "18.4", "confidence": 0.9}, 502),
    ({"monthlyVoiceMin": True, "confidence": 0.9}, 502),
    ({"monthlySmsCount": 1.5, "confidence": 0.9}, 502),
    ({"monthlySmsCount": 30, "confidence": 1.1}, 502),
    ({"monthlySmsCount": 30, "confidence": True}, 502),
    ({"monthlyTotal": 50000, "confidence": 0.9}, 502),
    ([], 502),
])
def test_unreadable_or_invalid_output_returns_question(result, status):
    with patch("app.llm.client.complete", return_value=result), TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/ocr", json={"image": encoded_image()})
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == "AI-OCR-001"
    assert detail["clarifyingQuestion"]
    assert "monthlyDataGb" not in detail


@pytest.mark.parametrize("encoded", [
    "not base64!", "https://example.com/screenshot.png",
    "data:image/png;base64,AAAA", "개인정보",
    base64.b64encode(b"not an image").decode(),
])
def test_invalid_image_never_calls_model_or_echoes_image(encoded):
    with patch("app.llm.client.complete") as complete, TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/ocr", json={"image": encoded})
        complete.assert_not_called()
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "AI-OCR-001"
    assert encoded not in response.text


@pytest.mark.parametrize("encoded", [
    "A" * (MAX_BASE64_LENGTH + 1),
    base64.b64encode(b"\0" * (MAX_IMAGE_BYTES + 1)).decode(),
])
def test_oversized_encoded_or_decoded_image_is_rejected(encoded):
    with patch("app.llm.client.complete") as complete, TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/ocr", json={"image": encoded})
        complete.assert_not_called()
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "AI-OCR-001"


def test_corrupt_unsupported_animated_and_large_images_are_rejected():
    animation = BytesIO()
    with Image.new("RGB", (2, 2), "red") as first, Image.new("RGB", (2, 2), "blue") as second:
        first.save(animation, format="GIF", save_all=True, append_images=[second])
    corrupt_png = base64.b64decode(encoded_image())[:24]
    cases = [
        (encoded_image("BMP"), 422),
        (base64.b64encode(animation.getvalue()).decode(), 422),
        (base64.b64encode(corrupt_png).decode(), 422),
        (encoded_image(size=(8001, 1)), 413),
    ]
    with patch("app.llm.client.complete") as complete, TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        for encoded, status in cases:
            response = api.post("/ocr", json={"image": encoded})
            assert response.status_code == status
        complete.assert_not_called()


@pytest.mark.parametrize(("error", "status", "code"), [
    (LLMError("secret model error"), 503, "AI-LLM-001"),
    (InvalidLLMResponse("secret response"), 502, "AI-OCR-001"),
])
def test_model_errors_are_redacted(error, status, code):
    with patch("app.llm.client.complete", side_effect=error), TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as api:
        response = api.post("/ocr", json={"image": encoded_image()})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "secret" not in response.text
