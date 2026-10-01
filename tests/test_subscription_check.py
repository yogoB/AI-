import re

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
# 2026-10-01 실제 표 모양: 머리행 아래 국가별 행. 미국 행이 먼저 와도 대한민국 행만 읽어야 한다.
ICLOUD = ('<p>월별 가격</p><table><tr><th>국가(통화)</th><th>50GB</th><th>200GB</th><th>2TB</th><th>6TB</th><th>12TB</th></tr>'
          '<tr><td>미국(미국 달러)</td><td>99원</td><td>299원</td><td>999원</td><td>2,999원</td><td>5,999원</td></tr>'
          '<tr><td>대한민국 5 (원)</td><td>1,100원</td><td>2,200원</td><td>3,300원</td><td>4,400원</td><td>5,500원</td></tr>'
          '<tr><td>싱가포르(싱가포르 달러)</td><td>1.48달러</td></tr></table>')


def test_only_regular_monthly_prices_from_the_right_region_are_extracted():
    assert [o.price for o in check.extract_offers('Spotify', SPOTIFY)] == [1100, 2200, 3300, 4400]
    assert [o.price for o in check.extract_offers('Apple Music', APPLE)] == [1100, 2200]
    assert [o.price for o in check.extract_offers('iCloud+', ICLOUD)] == [1100, 2200, 3300, 4400, 5500]
    for text in (APPLE.replace('/월', '/년'), APPLE + '<p>개인 ₩9,999/월</p>', '<p>개인 무료</p>'):
        with pytest.raises(ValueError):
            check.extract_offers('Apple Music', text)
    with pytest.raises(ValueError):
        check.extract_offers('iCloud+', ICLOUD.replace('대한민국 5 (원)', '다른나라 5 (원)'))
    # 열 순서가 바뀌면 위치로 읽은 값이 엉뚱한 등급에 붙는다 — 읽지 않고 사람에게 넘긴다.
    with pytest.raises(ValueError):
        check.extract_offers('iCloud+', ICLOUD.replace('<th>50GB</th><th>200GB</th>', '<th>200GB</th><th>50GB</th>'))


def test_a_label_never_borrows_the_next_cards_price():
    # 크레마클럽 X FLO 99 카드의 가격이 사라지면 뒤 카드 가격을 집지 말고 실패해야 한다.
    card = '<p>{}요금제 월 {}원 eBook 무제한</p>'
    page = ''.join(card.format(n, p) for n, p in (('스탠다드 55', '5,500'), ('프리미엄 77', '7,700'), ('크레마클럽 X FLO 99', '9,900')))
    assert [o.price for o in check.extract_offers('크레마클럽', page)] == [5500, 7700, 9900]
    with pytest.raises(ValueError):
        check.extract_offers('크레마클럽', page.replace('월 9,900원', ''))
    bugs = '<p>모바일 무제한 듣기 알뜰하게 모바일 기기에서만 재생 30일 7,590원</p><p>자동결제 8,690원</p>'
    assert not re.search(check.SOURCES['벅스'][2]['모바일 무제한 듣기'], bugs)


def test_endpoint_is_internal_and_returns_source_evidence_without_saving(monkeypatch):
    async def fetch(url):
        assert url == check.SOURCES['Apple Music'][0]
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
            asyncio.run(check.fetch_page(check.SOURCES['Apple Music'][0]))


def test_a_promotional_or_struck_price_is_never_read_as_the_regular_price():
    """바뀐 페이지가 오류 없이 **틀린 값**을 내면 운영자는 그 제안을 믿고 승인한다(원문이 붙어 권위 있어 보인다).
    이름표와 가격 사이에 첫 달·할인·특가·정상가가 끼면 읽지 않고 실패한다. 취소선 가격은 읽지 않는다."""
    rule = check.SOURCES['멜론'][2]['스트리밍']
    label = '스트리밍클럽 정기결제 이용권'
    for changed in (f'{label} 첫 달 100원, 이후 10,900원',
                    f'{label} 3개월 50% 할인 5,450원',
                    f'{label} 특가 7,900원'):
        assert not re.search(rule, changed), changed
    page = PageText_of(f'<p>{label} <s>정상가 12,000원</s> 10,900원</p>')
    assert re.search(rule, page).group(1) == '10,900'


def PageText_of(html):
    parser = check.PageText()
    parser.feed(html)
    return re.sub(r"\s+", " ", " ".join(parser.parts))
