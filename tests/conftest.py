import pytest

from app.narrate import REASON_CACHE


@pytest.fixture(autouse=True)
def clear_reason_cache():
    # 사유 캐시는 프로세스 전역이다. 테스트끼리 결과가 새지 않게 비우고 시작한다.
    REASON_CACHE.clear()


@pytest.fixture(autouse=True)
def backend_token(monkeypatch):
    # 실제 비밀값과 무관한 테스트 전용 토큰. HTTP 인증 검증 자체는 생략하지 않는다.
    monkeypatch.setenv("AI_INTERNAL_TOKEN", "test-backend-only-token")
