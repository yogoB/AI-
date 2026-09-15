"""별도로 실행한 테스트용 BE와의 HTTP 연동. 실제 모델 호출은 하지 않는다."""

import os
import socket
import threading
import time
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.main import app
from app.llm.client import LLMError


@pytest.mark.skipif(not os.getenv("YOGOBI_TEST_BACKEND_URL"), reason="테스트용 BE 주소 미설정")
def test_parse_to_backend_recommendation_to_narrate():
    with (
        patch("app.llm.client.complete", return_value={
            "required": {"monthlyDataGb": 20, "wantedServiceIds": [1]},
            "confidence": 0.92,
        }) as complete,
        TestClient(app, headers={"Authorization": "Bearer test-backend-only-token"}) as ai,
        httpx.Client(base_url=os.environ["YOGOBI_TEST_BACKEND_URL"], timeout=10, trust_env=False) as be,
    ):
        parsed = ai.post("/parse", json={"text": "데이터 20기가 쓰고 넷플릭스 보고 싶어요"})
        assert parsed.status_code == 200
        inputs = parsed.json()
        assert type(inputs["required"]["monthlyDataGb"]) is int

        recommended = be.post("/api/v1/recommendations", json={
            "required": inputs["required"], "optional": inputs["optional"],
        })
        assert recommended.status_code == 200, recommended.text
        data = recommended.json()["data"]
        assert data["results"], "테스트용 BE에 20GB 이상 요금제와 넷플릭스 시드가 필요합니다."

        for cost in data["results"]:
            narrated = ai.post("/narrate", json={**cost, "missingInputs": data["missingInputs"]})
            assert narrated.status_code == 200, narrated.text
            message = narrated.json()["message"]
            assert f'실제 내시는 금액은 월 {cost["monthlyTotal"]:,}원' in message
            assert f'정가로 내는 금액은 월 {cost["baseline"]:,}원' in message
            assert f'월 {cost["monthlySavings"]:,}원 절약' in message
            assert cost["planName"] in message
        complete.assert_called_once()


@pytest.mark.skipif(not os.getenv("YOGOBI_TEST_BACKEND_URL"), reason="테스트용 BE 주소 미설정")
def test_backend_chat_gateway_calls_real_ai_http_and_falls_back():
    # 테스트 BE의 AI_SERVER_URL은 이 서버를 가리켜야 한다. 실제 모델만 스텁한다.
    with (
        socket.socket() as listener,
        patch("app.llm.client.complete", return_value={
            "required": {"monthlyDataGb": 20, "wantedServiceIds": [1]}, "confidence": 0.92,
        }) as complete,
        httpx.Client(base_url=os.environ["YOGOBI_TEST_BACKEND_URL"], timeout=35, trust_env=False) as be,
    ):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", int(os.getenv("YOGOBI_TEST_AI_PORT", "18000"))))
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning", access_log=False))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "테스트용 AI HTTP 서버 기동 실패"

            text = {"text": "데이터 20기가 쓰고 넷플릭스 보고 싶어요"}
            response = be.post("/api/v1/chat/messages", json=text,
                               headers={"Authorization": "Bearer frontend-user-token"})
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["status"] == "RECOMMENDED"
            best = data["recommendation"]["results"][0]
            assert f'월 {best["monthlyTotal"]:,}원' in data["message"]
            complete.assert_called_once()

            # 직접 AI 호출과 서로 다른 서버 토큰은 모델 호출 없이 거절한다.
            ai_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            with httpx.Client(base_url=ai_url, trust_env=False) as direct:
                assert direct.post("/parse", json=text).status_code == 401
            with patch.dict(os.environ, {"AI_INTERNAL_TOKEN": "different-test-token"}):
                rejected = be.post("/api/v1/chat/messages", json=text)
                assert rejected.status_code == 200
                assert rejected.json()["data"]["status"] == "FILTER_FALLBACK"
                assert rejected.json()["warnings"][0]["code"] == "YGB-EXT-001"
                assert "test-token" not in rejected.text
            complete.assert_called_once()

            complete.return_value = {"confidence": 0.2}
            response = be.post("/api/v1/chat/messages", json=text)
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "NEEDS_INPUT"
            assert response.json()["data"]["recommendation"] is None

            complete.side_effect = LLMError("private upstream error")
            response = be.post("/api/v1/chat/messages", json=text)
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "FILTER_FALLBACK"
            assert response.json()["warnings"][0]["code"] == "YGB-EXT-001"
            assert "private" not in response.text

            filtered = be.post("/api/v1/recommendations", json={
                "required": {"monthlyDataGb": 20, "wantedServiceIds": [1]},
            })
            assert filtered.status_code == 200
            assert filtered.json()["data"] == data["recommendation"]
            assert complete.call_count == 3
        finally:
            server.should_exit = True
            worker.join(timeout=5)
        assert not worker.is_alive(), "테스트용 AI HTTP 서버 종료 실패"
