import socket

import pytest

from config import get_settings
from security.safe_url import UnsafeUrlError, validate_public_http_url


@pytest.fixture(autouse=True)
def reset_settings_cache(monkeypatch):
    monkeypatch.delenv("SCRAPER_BLOCK_PRIVATE_NETWORKS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _addrinfo(ip_address: str):
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (ip_address, 443),
        )
    ]


def test_validate_public_http_url_accepts_public_http(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _addrinfo("93.184.216.34"),
    )

    assert validate_public_http_url("https://example.com/report") == (
        "https://example.com/report"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://user:pass@example.com",
        "http://localhost:8000",
        "http://service.localhost",
    ],
)
def test_validate_public_http_url_blocks_unsafe_shapes(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_http_url(url)


@pytest.mark.parametrize(
    "ip_address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.1.1",
        "192.168.1.10",
        "169.254.169.254",
        "::1",
    ],
)
def test_validate_public_http_url_blocks_private_resolutions(
    monkeypatch,
    ip_address,
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _addrinfo(ip_address),
    )

    with pytest.raises(UnsafeUrlError):
        validate_public_http_url("https://example.com")
