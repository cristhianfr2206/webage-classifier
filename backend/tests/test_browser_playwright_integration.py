import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from app.browser_inspector import BrowserInspectionError, BrowserInspector
from app.browser_policy import validate_browser_url
from app.config import Settings, get_settings
from app.ssrf import Resolver
from tests.browser_fixture import FixtureServer

pytestmark = pytest.mark.browser_integration


def chromium_processes() -> set[int]:
    processes: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().decode(errors="ignore").lower()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if "chromium" in command or "chrome-linux" in command:
            processes.add(int(entry.name))
    return processes


async def wait_for_processes(expected: set[int], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while chromium_processes() != expected:
            await asyncio.sleep(0.05)


@pytest.fixture
def browser_fixture() -> Iterator[FixtureServer]:
    server = FixtureServer().start()
    try:
        yield server
    finally:
        server.close()


def configured(**updates: object) -> Settings:
    return get_settings().model_copy(update=updates)


def fixture_validator(server: FixtureServer):
    async def validate(value: str, resolver: Resolver) -> str:
        parsed = urlsplit(value)
        allowed = urlsplit(server.origin)
        if (
            parsed.scheme == "http"
            and parsed.hostname == allowed.hostname
            and parsed.port == allowed.port
        ):
            return value
        return await validate_browser_url(value, resolver)

    return validate


def inspector(server: FixtureServer, **limits: object) -> BrowserInspector:
    return BrowserInspector(
        configured(**limits),
        url_validator=fixture_validator(server),
    )


async def test_javascript_rendered_page_is_extracted(browser_fixture: FixtureServer) -> None:
    result = await inspector(browser_fixture).inspect(browser_fixture.url("/js"))
    assert result.page.title == "Rendered title"
    assert "Meaningful dynamic content" in result.page.visible_text
    assert result.headings == ("Rendered games",)


async def test_redirect_to_prohibited_destination_fails_safely(
    browser_fixture: FixtureServer,
) -> None:
    with pytest.raises(BrowserInspectionError) as error:
        await inspector(browser_fixture).inspect(browser_fixture.url("/redirect-private"))
    assert str(error.value) == "browser_failed"
    assert "127.0.0.1" not in str(error.value)


async def test_prohibited_iframe_image_stylesheet_fetch_xhr_and_websocket_are_blocked(
    browser_fixture: FixtureServer,
) -> None:
    result = await inspector(browser_fixture).inspect(browser_fixture.url("/blocked-subresources"))
    assert result.blocked_requests >= 5
    assert "secret" not in result.page.visible_text
    assert "leak" not in result.page.visible_text


async def test_popup_is_denied_before_destination_visit(browser_fixture: FixtureServer) -> None:
    result = await inspector(browser_fixture).inspect(browser_fixture.url("/popup"))
    assert "Popup test" in result.page.visible_text
    assert browser_fixture.count("/popup-target") == 0


async def test_download_is_cancelled_without_retained_file(
    browser_fixture: FixtureServer, tmp_path: Path
) -> None:
    before = set(tmp_path.iterdir())
    result = await inspector(browser_fixture).inspect(browser_fixture.url("/download"))
    assert "Download test" in result.page.visible_text
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "ftp://127.0.0.1/file",
        "gopher://127.0.0.1/",
        "data:text/html,unsafe",
        "blob:http://127.0.0.1/unsafe",
    ],
)
async def test_unsafe_initial_navigation_is_rejected(
    browser_fixture: FixtureServer, value: str
) -> None:
    with pytest.raises(BrowserInspectionError, match="unsafe_scheme"):
        await inspector(browser_fixture).inspect(value)


async def test_unsafe_subframe_schemes_do_not_escape_inspection(
    browser_fixture: FixtureServer,
) -> None:
    result = await inspector(browser_fixture).inspect(browser_fixture.url("/unsafe-subframes"))
    assert result.page.visible_text == "Unsafe schemes"
    assert "root:" not in result.page.visible_text
    assert "unsafe" not in result.page.visible_text.lower().removeprefix("unsafe schemes")


async def test_endless_javascript_times_out_and_next_inspection_succeeds(
    browser_fixture: FixtureServer,
) -> None:
    before = chromium_processes()
    browser = inspector(browser_fixture, browser_navigation_timeout_seconds=1)
    with pytest.raises(BrowserInspectionError, match="navigation_timeout"):
        await browser.inspect(browser_fixture.url("/endless"))
    await wait_for_processes(before)
    result = await browser.inspect(browser_fixture.url("/js"))
    assert result.page.title == "Rendered title"
    await wait_for_processes(before)


async def test_active_browser_inspection_can_be_cancelled_and_releases_resources(
    browser_fixture: FixtureServer,
) -> None:
    browser = inspector(browser_fixture, browser_navigation_timeout_seconds=30)
    task = asyncio.create_task(browser.inspect(browser_fixture.url("/endless")))
    assert await asyncio.to_thread(browser_fixture.request_seen.wait, 15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = await browser.inspect(browser_fixture.url("/js"))
    assert result.page.title == "Rendered title"


async def test_browser_crash_is_normalized_and_next_browser_remains_healthy(
    browser_fixture: FixtureServer,
) -> None:
    async def crash(page: Any) -> None:
        session = await page.context.new_cdp_session(page)
        await asyncio.wait_for(session.send("Page.crash"), timeout=3)

    crashing = BrowserInspector(
        configured(),
        url_validator=fixture_validator(browser_fixture),
        post_navigation_hook=crash,
    )
    with pytest.raises(BrowserInspectionError, match="browser_failed"):
        await crashing.inspect(browser_fixture.url("/js"))
    result = await inspector(browser_fixture).inspect(browser_fixture.url("/js"))
    assert result.page.title == "Rendered title"


async def test_navigation_loop_stops_at_redirect_limit(browser_fixture: FixtureServer) -> None:
    with pytest.raises(BrowserInspectionError, match="redirect_limit"):
        await inspector(browser_fixture, browser_max_redirects=2).inspect(
            browser_fixture.url("/loop?n=0")
        )


async def test_excessive_resource_requests_hit_request_cap(
    browser_fixture: FixtureServer,
) -> None:
    with pytest.raises(BrowserInspectionError, match="request_limit"):
        await inspector(browser_fixture, browser_max_requests=5).inspect(
            browser_fixture.url("/resources")
        )


async def test_maximum_transferred_bytes_stops_inspection(
    browser_fixture: FixtureServer,
) -> None:
    with pytest.raises(BrowserInspectionError, match="byte_limit"):
        await inspector(browser_fixture, browser_max_transferred_bytes=2_000).inspect(
            browser_fixture.url("/large")
        )


async def test_rendered_values_are_truncated_to_all_configured_limits(
    browser_fixture: FixtureServer,
) -> None:
    result = await inspector(
        browser_fixture,
        browser_max_text_characters=100,
        browser_max_headings=2,
        browser_max_links=3,
        browser_max_buttons=4,
    ).inspect(browser_fixture.url("/oversized"))
    assert len(result.page.visible_text) == 100
    assert len(result.headings) == 2
    assert len(result.links) == 3
    assert len(result.buttons) == 4


async def test_xss_payloads_are_extracted_as_inert_text(browser_fixture: FixtureServer) -> None:
    result = await inspector(browser_fixture).inspect(browser_fixture.url("/xss"))
    assert "<script>titleAttack()</script>" in result.page.title
    assert "<img src=x onerror=headingAttack()>" in result.headings
    assert "<script>buttonAttack()</script>" in result.buttons
    assert "<svg onload=evidenceAttack()>" in result.page.visible_text
