import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize("path", ["/narrate"])
def test_internal_endpoints_reject_unauthorized_callers_before_processing(path, monkeypatch):
    with TestClient(app) as api:
        for authorization in (None, "Bearer wrong", "Basic test-backend-only-token"):
            headers = {} if authorization is None else {"Authorization": authorization}
            response = api.post(path, json={}, headers=headers)
            assert response.status_code == 401
            assert response.json() == {"detail": {"code": "NARRATOR-AUTH-001"}}
        for token in ("", " ", "invalid\ntoken"):
            monkeypatch.setenv("NARRATOR_INTERNAL_TOKEN", token)
            assert api.post(path, json={}).status_code == 503
        monkeypatch.delenv("NARRATOR_INTERNAL_TOKEN")
        assert api.post(path, json={}).status_code == 503
        assert api.get("/health").status_code == 200


def test_openapi_marks_only_internal_operations_as_bearer_protected():
    schema = app.openapi()
    for path in ("/narrate",):
        assert schema["paths"][path]["post"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/health"]["get"]
