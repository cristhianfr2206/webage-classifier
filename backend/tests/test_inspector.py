import httpx
import pytest

from app.config import Settings
from app.inspector import InspectionError, WebsiteInspector
from app.ssrf import DnsResolutionError


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite+aiosqlite://",
        "secret_key": "test-secret-key-that-is-at-least-thirty-two-bytes",
        "initial_admin_email": "admin@example.com",
        "initial_admin_password": "long-test-password",
        "allowed_origins": ["http://localhost"],
        "allowed_hosts": ["localhost"],
        "inspector_max_response_bytes": 1024,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def public_resolver(_: str, __: int) -> list[str]:
    return ["93.184.216.34"]


async def test_safe_redirect_is_revalidated_and_html_is_extracted() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["sni_hostname"] == request.url.host
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://www.example.org/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<title>Example</title><p>Visible page</p>",
        )

    result = await WebsiteInspector(
        settings(), resolver=public_resolver, transport=httpx.MockTransport(handler)
    ).inspect("https://example.com")
    assert result.final_url == "https://www.example.org/final"
    assert result.page.title == "Example"
    assert "Visible page" in result.page.visible_text


async def test_redirect_to_private_destination_is_blocked() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    with pytest.raises(InspectionError, match="unsafe_destination"):
        await WebsiteInspector(
            settings(), resolver=public_resolver, transport=httpx.MockTransport(handler)
        ).inspect("https://example.com")


async def test_response_size_and_content_type_are_enforced() -> None:
    async def large(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 1025)

    async def json(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, json={})

    with pytest.raises(InspectionError, match="response_too_large"):
        await WebsiteInspector(
            settings(), resolver=public_resolver, transport=httpx.MockTransport(large)
        ).inspect("https://example.com")
    with pytest.raises(InspectionError, match="not_html"):
        await WebsiteInspector(
            settings(), resolver=public_resolver, transport=httpx.MockTransport(json)
        ).inspect("https://example.com")


async def test_dns_failure_is_a_controlled_inspection_outcome() -> None:
    async def missing(_: str, __: int) -> list[str]:
        raise DnsResolutionError("dns_not_found", transient=False)

    with pytest.raises(InspectionError, match="dns_not_found"):
        await WebsiteInspector(settings(), resolver=missing).inspect("https://missing.example")
