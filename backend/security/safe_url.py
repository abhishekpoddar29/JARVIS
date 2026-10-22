from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from config import get_settings


_LOCAL_NAMES = {
    "localhost",
    "localhost.localdomain",
    "host.docker.internal",
    "gateway.docker.internal",
}

_BLOCKED_IPS = {
    ipaddress.ip_address("169.254.169.254"),
}


class UnsafeUrlError(ValueError):
    pass


def validate_public_http_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("URL must start with http:// or https://.")
    if not parsed.hostname:
        raise UnsafeUrlError("URL must include a hostname.")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL must not include embedded credentials.")

    host = parsed.hostname.strip().lower().rstrip(".")
    if host in _LOCAL_NAMES or host.endswith(".localhost"):
        raise UnsafeUrlError("Localhost URLs are not allowed.")

    settings = get_settings()
    if not settings.scraper_block_private_networks:
        return value

    try:
        addresses = socket.getaddrinfo(host, parsed.port or None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve URL hostname: {host}.") from exc

    for address in addresses:
        ip_text = address[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise UnsafeUrlError(f"Invalid resolved IP address: {ip_text}.") from exc

        if ip in _BLOCKED_IPS:
            raise UnsafeUrlError("Metadata service URLs are not allowed.")
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeUrlError("Private, local, or reserved network URLs are not allowed.")

    return value
