from urllib.parse import urlsplit

from app.ssrf import Resolver, UnsafeTargetError, resolve_public_target, system_resolver

ALLOWED_SCHEMES = {"http", "https"}


class BrowserPolicyError(RuntimeError):
    pass


async def validate_browser_url(value: str, resolver: Resolver = system_resolver) -> str:
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError as exc:
        raise BrowserPolicyError("unsafe_destination") from exc
    if scheme not in ALLOWED_SCHEMES:
        raise BrowserPolicyError("unsafe_scheme")
    try:
        target, _ = await resolve_public_target(value, resolver)
    except (UnsafeTargetError, ValueError, OSError) as exc:
        raise BrowserPolicyError("unsafe_destination") from exc
    return target.url
