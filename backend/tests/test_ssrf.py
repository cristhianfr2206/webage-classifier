import asyncio
import socket

import pytest

from app.ssrf import (
    DnsResolutionError,
    UnsafeTargetError,
    is_public_address,
    system_resolver,
    validate_public_target,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104 - explicit SSRF rejection fixture
        "224.0.0.1",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "2001:db8::1",
    ],
)
def test_non_public_addresses_are_rejected(address: str) -> None:
    assert not is_public_address(address)


def test_public_ipv4_and_ipv6_are_allowed() -> None:
    assert is_public_address("93.184.216.34")
    assert is_public_address("2606:2800:220:1:248:1893:25c8:1946")


async def test_every_dns_answer_must_be_public() -> None:
    async def mixed_resolver(_: str, __: int) -> list[str]:
        return ["93.184.216.34", "127.0.0.1"]

    with pytest.raises(UnsafeTargetError):
        await validate_public_target("https://example.com", mixed_resolver)


async def test_empty_dns_results_are_rejected() -> None:
    async def empty_resolver(_: str, __: int) -> list[str]:
        return []

    with pytest.raises(UnsafeTargetError):
        await validate_public_target("https://example.com", empty_resolver)


async def test_transient_dns_failure_is_retried_within_current_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    calls = 0

    async def resolve(*_: object, **__: object) -> list[tuple[object, ...]]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise socket.gaierror(socket.EAI_AGAIN, "temporary")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", resolve)
    assert await system_resolver("example.com", 443) == ["93.184.216.34"]
    assert calls == 3


async def test_permanent_dns_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    calls = 0

    async def resolve(*_: object, **__: object) -> list[tuple[object, ...]]:
        nonlocal calls
        calls += 1
        raise socket.gaierror(socket.EAI_NONAME, "not found")

    monkeypatch.setattr(loop, "getaddrinfo", resolve)
    with pytest.raises(DnsResolutionError, match="dns_not_found") as error:
        await system_resolver("missing.example", 443)
    assert error.value.transient is False
    assert calls == 1
