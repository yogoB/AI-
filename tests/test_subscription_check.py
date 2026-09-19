import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import subscription_check as check

AUTH = {"Authorization": "Bearer test-backend-only-token"}
SPOTIFY = """<script>Premium 개인 요금제는 ₩1(매월 기준)</script><p>
대한민국의 Spotify Premium 가격은 선택한 Premium 요금제에 따라 다릅니다.
Premium 베이직 요금제는 ₩1,100(매월 기준), Premium 개인 요금제는 ₩2,200(매월 기준),
Premium 듀오 요금제는 ₩3,300(매월 기준), Premium 학생 요금제는 ₩4,400(매월 기준)입니다.</p>
<p>무료 체험 ₩0</p>"""
APPLE = '<h3>개인</h3><p>₩1,100/월, 첫 달 무료</p><h3>가족</h3><p>₩2,200/월</p>'
ICLOUD = '<p>월별 가격</p>미국 50GB : 99원 대한민국(원) 5 ' + ' '.join(
    f'{name} : {price}원' for name, price in zip(('50GB', '200GB', '2TB', '6TB', '12TB'), ('1,100', '2,200', '3,300', '4,400', '5,500'))) + ' 싱가포르 50GB : 999원'


def test_only_regular_monthly_prices_from_the_right_region_are_extracted():
    assert [o.price for o in check.extract_offers('Spotify', SPOTIFY)] == [1100, 2200, 3300, 4400]
    assert [o.price for o in check.extract_offers('Apple Music', APPLE)] == [1100, 2200]
    assert [o.price for o in check.extract_offers('iCloud+', ICLOUD)] == [1100, 2200, 3300, 4400, 5500]
    for text in (APPLE.replace('/월', '/년'), APPLE + '<p>개인 ₩9,999/월</p>', '<p>개인 무료</p>'):
        with pytest.raises(ValueError):
            check.extract_offers('Apple Music', text)
    with pytest.raises(ValueError):
        check.extract_offers('iCloud+', ICLOUD.replace('대한민국(원)', '다른나라(원)'))


def test_endpoint_is_internal_and_returns_source_evidence_without_saving(monkeypatch):
    async def fetch(url):
        assert url == check.SOURCES['Apple Music']
        return APPLE.encode()
    monkeypatch.setattr(check, 'fetch_page', fetch)
    with TestClient(app) as client:
        assert client.post('/operations/subscriptions/check', json={'serviceName': 'Apple Music'}).status_code == 401
        for body in ({'serviceName': 'other'}, {'serviceName': 'Apple Music', 'url': 'http://localhost'}):
            assert client.post('/operations/subscriptions/check', json=body, headers=AUTH).status_code == 422
        r = client.post('/operations/subscriptions/check', json={'serviceName': 'Apple Music'}, headers=AUTH)
        assert r.status_code == 200
        assert r.json()['offers'][0] == {'tierName': '개인', 'price': 1100, 'currency': 'KRW', 'billingPeriod': 'MONTH', 'evidence': '개인 ₩1,100/월'}
        assert len(r.json()['sourceHash']) == 64
        assert r.json()['checkedAt']


def test_source_failure_never_returns_a_price(monkeypatch):
    async def unavailable(url):
        raise httpx.ConnectError('private transport detail')
    monkeypatch.setattr(check, 'fetch_page', unavailable)
    with TestClient(app) as client:
        r = client.post('/operations/subscriptions/check', json={'serviceName': 'Apple Music'}, headers=AUTH)
        assert r.status_code == 502
        assert 'private transport' not in r.text
        assert 'offers' not in r.json()


def test_fetch_rejects_redirect_non_html_and_oversized_pages(monkeypatch):
    import asyncio
    real_client = httpx.AsyncClient
    for response in (httpx.Response(302, headers={'location': 'http://127.0.0.1'}),
                     httpx.Response(200, headers={'content-type': 'application/json'}, text='{}'),
                     httpx.Response(200, headers={'content-type': 'text/html'}, content=b'x' * (check.MAX_BYTES + 1))):
        transport = httpx.MockTransport(lambda request: response)
        monkeypatch.setattr(check.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
        with pytest.raises((httpx.HTTPError, ValueError)):
            asyncio.run(check.fetch_page(check.SOURCES['Apple Music']))
