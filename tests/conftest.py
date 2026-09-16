import pytest

from app.catalog import CANDIDATE_CACHE
from app.llm import client
from app.narrate import REASON_CACHE


@pytest.fixture(autouse=True)
def clear_process_state():
    # 캐시와 검색 카운터는 프로세스 전역이다. 테스트끼리 새지 않게 비우고 시작한다.
    REASON_CACHE.clear()
    CANDIDATE_CACHE.clear()
    client._searches_used = 0


@pytest.fixture(autouse=True)
def backend_token(monkeypatch):
    # 실제 비밀값과 무관한 테스트 전용 토큰. HTTP 인증 검증 자체는 생략하지 않는다.
    monkeypatch.setenv("AI_INTERNAL_TOKEN", "test-backend-only-token")
