from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_docs_are_not_served():
    """문서 경로는 토큰 없이 열렸다. 쓰는 사람이 없으니 닫는다(계약은 docs/contract.md 가 원본)."""
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404



def test_health_is_not_ok_without_the_backend_token(monkeypatch):
    """토큰이 빠진 배포는 모든 요청이 503 인데 /health 만 200 이라 배포가 건강해 보였고, BE 는 조용히 물러났다."""
    monkeypatch.setenv("NARRATOR_INTERNAL_TOKEN", "")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 503
