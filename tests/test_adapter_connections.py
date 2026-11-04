"""Connection-level adapter invariants; fixtures never fetch a real private host."""
import asyncio
import gzip
import socket

import httpcore
import httpx
import pytest

from adapters import safe_fetch
from app.fetch_security import UnsafeURLError, validate_public_url


def test_sync_adapter_revalidates_before_connection(monkeypatch):
    answers = iter(['93.184.216.34', '127.0.0.1'])
    def resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (next(answers), port))]
    monkeypatch.setattr(socket, 'getaddrinfo', resolver)
    for name in ('FETCH_PROXY_URL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.delenv(name, raising=False)
    def unexpected(*args, **kwargs):
        pytest.fail('connection must not be attempted after DNS rebind')
    monkeypatch.setattr(httpcore.SyncBackend, 'connect_tcp', unexpected)
    with pytest.raises(ValueError):
        safe_fetch.safe_requests_get('https://rebind.example/article')


def test_async_helper_does_not_trust_plain_client_transport(monkeypatch):
    answers = iter(['93.184.216.34', '127.0.0.1'])
    def resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (next(answers), port))]
    monkeypatch.setattr(socket, 'getaddrinfo', resolver)
    monkeypatch.delenv('FETCH_PROXY_URL', raising=False)
    async def run():
        with pytest.raises(ValueError):
            async with httpx.AsyncClient(trust_env=False) as client:
                await safe_fetch.safe_httpx_get(client, 'https://rebind.example/article')
    asyncio.run(run())


def test_network_validation_retains_www_hostname():
    seen = []
    def resolver(host, port, *a):
        seen.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]
    result=validate_public_url('https://www.example.com/article',resolver=resolver)
    assert seen == ['www.example.com']
    assert result.hostname == 'www.example.com'


@pytest.mark.parametrize('url', ['http://224.0.0.1/', 'http://[ff02::1]/'])
def test_multicast_is_not_a_public_web_destination(url):
    with pytest.raises(UnsafeURLError):
        validate_public_url(url)


def test_decoded_gzip_is_not_decoded_twice():
    from app.fetch_security import safe_httpx_get
    async def run():
        body=gzip.compress(b'hello world')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
                httpx.Response(200,content=body,headers={'content-encoding':'gzip'}))) as client:
            response=await safe_httpx_get(client,'https://example.com/',validator=lambda u:True)
            assert response.text == 'hello world'
    asyncio.run(run())


def test_sync_adapter_preserves_response_interface(monkeypatch):
    monkeypatch.setattr(safe_fetch, 'is_safe_url', lambda *a, **k: True)
    monkeypatch.setattr(safe_fetch, '_sync_client', lambda **kwargs:
        httpx.Client(transport=httpx.MockTransport(lambda req:
            httpx.Response(200, json={'result':'ok'}, request=req))))
    response=safe_fetch.safe_requests_get('https://example.com/article')
    assert response.ok
    assert response.json() == {'result':'ok'}
    assert response.url == 'https://example.com/article'
    response.raise_for_status()


def test_explicit_proxy_is_used_without_environment_fallback(monkeypatch):
    monkeypatch.setenv('FETCH_PROXY_URL','http://fetch-proxy-private:8888')
    monkeypatch.setenv('HTTPS_PROXY','http://unexpected-proxy:1')
    assert safe_fetch._client_options(asynchronous=True) == {
        'proxy':'http://fetch-proxy-private:8888','trust_env':False}
    assert safe_fetch._client_options(asynchronous=False) == {
        'proxy':'http://fetch-proxy-private:8888','trust_env':False}


@pytest.mark.parametrize('address', ['224.0.0.1', 'ff02::1', '172.29.0.23'])
def test_validating_proxy_refuses_nonpublic_destinations(address):
    from app.fetch_proxy import _validated_ip
    with pytest.raises(ValueError):
        _validated_ip(address)
