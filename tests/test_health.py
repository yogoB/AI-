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

