import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from app.normalization import NormalizedTarget, normalize_url


class UnsafeTargetError(ValueError):
    pass


class DnsResolutionError(OSError):
    def __init__(self, code: str, *, transient: bool) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient


Resolver = Callable[[str, int], Awaitable[list[str]]]


def is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


async def system_resolver(host: str, port: int) -> list[str]:
    # getaddrinfo is deliberately obtained from the current loop on every call;
    # no resolver or Future is cached across Celery tasks/processes.
    from app.config import get_settings

    settings = get_settings()
    for attempt in range(settings.dns_max_retries + 1):
        try:
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    host,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                ),
                timeout=settings.dns_resolution_timeout_seconds,
            )
            return sorted({record[4][0] for record in records})
        except socket.gaierror as exc:
            transient = exc.errno == socket.EAI_AGAIN
            if not transient or attempt >= settings.dns_max_retries:
                code = "dns_temporary_failure" if transient else "dns_not_found"
                raise DnsResolutionError(code, transient=transient) from exc
        except TimeoutError as exc:
            if attempt >= settings.dns_max_retries:
                raise DnsResolutionError("dns_timeout", transient=True) from exc
        await asyncio.sleep(0.1 * (attempt + 1))
    raise DnsResolutionError("dns_temporary_failure", transient=True)  # pragma: no cover


async def validate_public_target(
    value: str, resolver: Resolver = system_resolver
) -> NormalizedTarget:
    target, _ = await resolve_public_target(value, resolver)
    return target


async def resolve_public_target(
    value: str, resolver: Resolver = system_resolver
) -> tuple[NormalizedTarget, list[str]]:
    target = normalize_url(value)
    port = 443 if target.url.startswith("https://") else 80
    try:
        parsed_port = urlsplit(target.url).port
    except ValueError as exc:
        raise UnsafeTargetError("invalid destination port") from exc
    try:
        ipaddress.ip_address(target.host)
        addresses = [target.host]
    except ValueError:
        addresses = await resolver(target.host, parsed_port or port)
    if not addresses or any(not is_public_address(address) for address in addresses):
        raise UnsafeTargetError("destination is not publicly routable")
    return target, addresses
