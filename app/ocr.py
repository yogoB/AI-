import base64
import binascii
import warnings
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm import client

router = APIRouter()
PROMPT = (Path(__file__).parent / "prompts" / "ocr.txt").read_text(encoding="utf-8")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BASE64_LENGTH = ((MAX_IMAGE_BYTES + 2) // 3) * 4
OCR_ERROR = {
    "code": "AI-OCR-001",
    "clarifyingQuestion": "사용량 숫자가 선명한 정지 이미지를 5MiB 이하, 한 변 8,000픽셀 이하로 다시 올려주시겠어요?",
}


class OcrRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    image: str = Field(min_length=1, repr=False)


class OcrResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    monthlyDataGb: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    monthlyVoiceMin: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    monthlySmsCount: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


def image_content(encoded: str) -> dict:
    if len(encoded) > MAX_BASE64_LENGTH:
        raise HTTPException(status_code=413, detail=OCR_ERROR)
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=422, detail=OCR_ERROR) from None
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=OCR_ERROR)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP", "GIF")) as image:
                if max(image.size) > 8000:
                    raise HTTPException(status_code=413, detail=OCR_ERROR)
                if getattr(image, "is_animated", False):
                    raise ValueError("Animated images are not supported")
                media_type = Image.MIME[image.format]
                image.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(status_code=422, detail=OCR_ERROR) from None

    return {"type": "image", "source": {
        "type": "base64", "media_type": media_type, "data": encoded,
    }}


@router.post("/ocr", response_model=OcrResponse)
async def ocr(request: OcrRequest) -> OcrResponse:
    content = [image_content(request.image)]
    try:
        result = await client.complete(PROMPT, content, OcrResponse.model_json_schema())
        usage = OcrResponse.model_validate(result)
    except client.LLMError:
        raise HTTPException(status_code=503, detail={"code": "AI-LLM-001"}) from None
    except (client.InvalidLLMResponse, ValidationError):
        raise HTTPException(status_code=502, detail=OCR_ERROR) from None
    if usage.confidence < 0.7 or all(value is None for value in (
        usage.monthlyDataGb, usage.monthlyVoiceMin, usage.monthlySmsCount,
    )):
        raise HTTPException(status_code=422, detail=OCR_ERROR)
    return usage
