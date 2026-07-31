import asyncio
import importlib.metadata
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from app.artifacts import ArtifactStore
from app.browser_policy import BrowserPolicyError, validate_browser_url
from app.config import Settings
from app.extraction import ExtractedPage
from app.ssrf import Resolver, system_resolver

UrlValidator = Callable[[str, Resolver], Awaitable[str]]
PostNavigationHook = Callable[[Any], Awaitable[None]]


class BrowserInspectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserResult:
    final_url: str
    page: ExtractedPage
    headings: tuple[str, ...]
    links: tuple[str, ...]
    buttons: tuple[str, ...]
    request_count: int
    transferred_bytes: int
    blocked_requests: int
    duration_ms: int
    artifact_id: str | None
    browser_version: str
    playwright_version: str


class BrowserInspector:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Resolver = system_resolver,
        capture_screenshot: bool = False,
        url_validator: UrlValidator = validate_browser_url,
        post_navigation_hook: PostNavigationHook | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.capture_screenshot = capture_screenshot and settings.browser_screenshot_enabled
        self.url_validator = url_validator
        self.post_navigation_hook = post_navigation_hook

    async def inspect(self, value: str) -> BrowserResult:
        try:
            target = await self.url_validator(value, self.resolver)
        except BrowserPolicyError as exc:
            raise BrowserInspectionError(str(exc)) from exc
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserInspectionError("browser_unavailable") from exc

        started = time.monotonic()
        request_count = 0
        transferred_bytes = 0
        blocked_requests = 0
        redirects = 0
        context: Any = None
        browser: Any = None
        page: Any = None
        cdp: Any = None
        playwright: Any = None
        termination_code: str | None = None
        main_frame_navigations = 0
        stop_task: asyncio.Task[None] | None = None

        def stop_with(code: str) -> None:
            nonlocal stop_task, termination_code
            if termination_code is None:
                termination_code = code
                stop_task = asyncio.create_task(stop_page())

        async def route_request(route: Any, request: Any) -> None:
            nonlocal blocked_requests, main_frame_navigations, request_count, termination_code
            request_count += 1
            if request_count > self.settings.browser_max_requests:
                blocked_requests += 1
                await route.abort("blockedbyclient")
                return
            if (
                page is not None
                and request.is_navigation_request()
                and request.frame == page.main_frame
            ):
                main_frame_navigations += 1
                if main_frame_navigations - 1 > self.settings.browser_max_redirects:
                    termination_code = "redirect_limit"
                    await route.abort("blockedbyclient")
                    return
            try:
                await self.url_validator(request.url, self.resolver)
            except BrowserPolicyError:
                blocked_requests += 1
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        async def stop_page() -> None:
            if page is not None and not page.is_closed():
                await page.close()

        def account_request(event: dict[str, Any]) -> None:
            nonlocal main_frame_navigations
            if event.get("type") == "Document" and event.get("redirectResponse") is not None:
                main_frame_navigations += 1
                if main_frame_navigations - 1 > self.settings.browser_max_redirects:
                    stop_with("redirect_limit")

        def account_data_received(event: dict[str, Any]) -> None:
            nonlocal transferred_bytes
            transferred_bytes += int(event.get("encodedDataLength", 0))
            if transferred_bytes > self.settings.browser_max_transferred_bytes:
                stop_with("byte_limit")

        def account_response(response: Any) -> None:
            value = response.headers.get("content-length", "0")
            if value.isdigit() and int(value) > (
                self.settings.browser_max_transferred_bytes - transferred_bytes
            ):
                stop_with("byte_limit")

        async def reject_popup(popup: Any) -> None:
            nonlocal blocked_requests
            blocked_requests += 1
            await popup.close()

        async def reject_download(download: Any) -> None:
            nonlocal blocked_requests
            blocked_requests += 1
            await download.cancel()

        async def reject_websocket(websocket: Any) -> None:
            nonlocal blocked_requests
            blocked_requests += 1
            await websocket.close()

        async def reject_dialog(dialog: Any) -> None:
            nonlocal blocked_requests
            blocked_requests += 1
            await dialog.dismiss()

        def navigation(_: Any) -> None:
            nonlocal redirects, termination_code
            redirects += 1
            if redirects - 1 > self.settings.browser_max_redirects:
                stop_with("redirect_limit")

        try:
            playwright = await async_playwright().start()
            if True:
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-extensions",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-default-apps",
                        "--disable-sync",
                        "--no-first-run",
                    ],
                )
                context = await browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    viewport={
                        "width": self.settings.browser_screenshot_width,
                        "height": self.settings.browser_screenshot_height,
                    },
                )
                await context.add_init_script(
                    "Object.defineProperty(window, 'open', {value: () => null, writable: false});"
                )
                await context.route("**/*", route_request)
                if hasattr(context, "route_web_socket"):
                    await context.route_web_socket("**/*", reject_websocket)
                page = await context.new_page()
                cdp = await context.new_cdp_session(page)
                await cdp.send("Network.enable")
                cdp.on("Network.requestWillBeSent", account_request)
                cdp.on("Network.dataReceived", account_data_received)
                context.on("page", reject_popup)
                page.on("popup", reject_popup)
                page.on("download", reject_download)
                page.on("dialog", reject_dialog)
                page.on("response", account_response)

                page.on("framenavigated", navigation)
                try:
                    await asyncio.wait_for(
                        page.goto(
                            target,
                            wait_until="domcontentloaded",
                            timeout=self.settings.browser_navigation_timeout_seconds * 1000,
                        ),
                        timeout=self.settings.browser_navigation_timeout_seconds + 2,
                    )
                except (TimeoutError, PlaywrightTimeoutError) as exc:
                    termination_code = "navigation_timeout"
                    raise BrowserInspectionError("navigation_timeout") from exc
                if redirects - 1 > self.settings.browser_max_redirects:
                    raise BrowserInspectionError("redirect_limit")
                if request_count > self.settings.browser_max_requests:
                    raise BrowserInspectionError("request_limit")
                if transferred_bytes > self.settings.browser_max_transferred_bytes:
                    raise BrowserInspectionError("byte_limit")
                final_url = await self.url_validator(page.url, self.resolver)
                if self.post_navigation_hook is not None:
                    await self.post_navigation_hook(page)
                title = (await page.title())[:500]
                text = (
                    await page.locator("body").inner_text(timeout=2000)
                    if await page.locator("body").count()
                    else ""
                )
                text = " ".join(text.split())[: self.settings.browser_max_text_characters]
                headings = tuple(
                    value[:500]
                    for value in await page.locator("h1,h2,h3,h4,h5,h6").all_inner_texts()
                )[: self.settings.browser_max_headings]
                links = tuple(value[:500] for value in await page.locator("a").all_inner_texts())[
                    : self.settings.browser_max_links
                ]
                buttons = tuple(
                    value[:500]
                    for value in await page.locator("button,[role=button]").all_inner_texts()
                )[: self.settings.browser_max_buttons]
                artifact_id = None
                if self.capture_screenshot:
                    image = await page.screenshot(
                        type="png",
                        full_page=False,
                        animations="disabled",
                        caret="hide",
                    )
                    artifact_id = (
                        ArtifactStore(
                            self.settings.browser_artifact_root,
                            self.settings.browser_screenshot_max_bytes,
                        )
                        .put_png(image)
                        .artifact_id
                    )
                return BrowserResult(
                    final_url=final_url,
                    page=ExtractedPage(title=title, description="", visible_text=text),
                    headings=headings,
                    links=links,
                    buttons=buttons,
                    request_count=request_count,
                    transferred_bytes=transferred_bytes,
                    blocked_requests=blocked_requests,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    artifact_id=artifact_id,
                    browser_version=browser.version,
                    playwright_version=importlib.metadata.version("playwright"),
                )
        except BrowserPolicyError as exc:
            raise BrowserInspectionError(str(exc)) from exc
        except BrowserInspectionError:
            raise
        except Exception as exc:
            if "ERR_TOO_MANY_REDIRECTS" in str(exc):
                raise BrowserInspectionError("redirect_limit") from exc
            if termination_code is not None:
                raise BrowserInspectionError(termination_code) from exc
            raise BrowserInspectionError("browser_failed") from exc
        finally:
            if stop_task is not None:
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await stop_task
            # Stop producing callbacks before tearing down their Playwright
            # targets. Closing the browser first causes pending page/context
            # handlers to cascade TargetClosedError exceptions after a limit
            # or timeout has already terminated the inspection.
            if cdp is not None:
                with suppress(Exception):
                    cdp.remove_listener("Network.requestWillBeSent", account_request)
                    cdp.remove_listener("Network.dataReceived", account_data_received)
            if page is not None:
                with suppress(Exception):
                    page.remove_listener("popup", reject_popup)
                    page.remove_listener("download", reject_download)
                    page.remove_listener("dialog", reject_dialog)
                    page.remove_listener("response", account_response)
                    page.remove_listener("framenavigated", navigation)
            if context is not None:
                with suppress(Exception):
                    context.remove_listener("page", reject_popup)
            if context is not None:
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await context.unroute_all(behavior="ignoreErrors")
            if cdp is not None:
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await cdp.detach()
            if page is not None and not page.is_closed():
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await page.close()
            if context is not None:
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await context.close()
            if browser is not None and browser.is_connected():
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await browser.close()
            if playwright is not None:
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await playwright.stop()
