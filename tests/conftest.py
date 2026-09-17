import pytest



@pytest.fixture(autouse=True)
def backend_token(monkeypatch):
    # 실제 비밀값과 무관한 테스트 전용 토큰. HTTP 인증 검증 자체는 생략하지 않는다.
    monkeypatch.setenv("NARRATOR_INTERNAL_TOKEN", "test-backend-only-token")
