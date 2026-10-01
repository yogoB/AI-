"""본문 상한(BE G-89 b 와 같은 이유). FastAPI 는 라우터의 토큰 검사보다 **본문 파싱이 먼저**라
토큰 없는 호출자도 큰 본문을 메모리에 올리게 할 수 있었다. 앱 맨 앞에서 끊는다."""

from fastapi.testclient import TestClient

from app.main import MAX_BODY_BYTES, app


def test_a_declared_oversized_body_is_refused_before_auth_or_parsing():
    with TestClient(app) as api:
        response = api.post("/narrate", content=b"x" * (MAX_BODY_BYTES + 1),
                            headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": {"code": "NARRATOR-BODY-413"}}


def test_a_streamed_body_is_cut_when_it_crosses_the_limit():
    def chunks():
        for _ in range(MAX_BODY_BYTES // 65536 + 2):
            yield b"x" * 65536

    with TestClient(app) as api:
        response = api.post("/narrate", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_a_normal_body_still_reaches_the_route():
    with TestClient(app) as api:
        # 토큰이 없으니 401 — 본문 상한이 정상 요청을 막지 않는다는 뜻이다.
        assert api.post("/narrate", json={}).status_code == 401
