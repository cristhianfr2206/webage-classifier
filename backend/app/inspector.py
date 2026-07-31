from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.config import Settings
from app.extraction import ExtractedPage, extract_page
from app.ssrf import (
    DnsResolutionError,
    Resolver,
    UnsafeTargetError,
    resolve_public_target,
    system_resolver,
)


class InspectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class InspectionResult:
    final_url: str
    page: ExtractedPage


class WebsiteInspector:
    def __init__(
        self,
        settings: Settings,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.transport = transport

    async def inspect(self, value: str) -> InspectionResult:
        current = value
        timeout = httpx.Timeout(
            self.settings.inspector_response_timeout_seconds,
            connect=self.settings.inspector_connect_timeout_seconds,
            pool=self.settings.inspector_connect_timeout_seconds,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            cookies=None,
            headers={"User-Agent": "WebAgeInspector/0.2", "Accept": "text/html"},
            transport=self.transport,
        ) as client:
            for redirect_count in range(self.settings.inspector_max_redirects + 1):
                try:
                    target, addresses = await resolve_public_target(current, self.resolver)
                except DnsResolutionError as exc:
                    raise InspectionError(exc.code) from exc
                except (UnsafeTargetError, ValueError) as exc:
                    raise InspectionError("unsafe_destination") from exc
                parsed = urlsplit(target.url)
                address = addresses[0]
                bracketed = f"[{address}]" if ":" in address else address
                connect_netloc = f"{bracketed}:{parsed.port}" if parsed.port else bracketed
                connect_url = urlunsplit(
                    (parsed.scheme, connect_netloc, parsed.path, parsed.query, parsed.fragment)
                )
                if self.transport is not None:
                    connect_url = target.url
                request_headers = {"Host": parsed.netloc}
                try:
                    async with client.stream(
                        "GET",
                        connect_url,
                        headers=request_headers,
                        extensions={"sni_hostname": target.host},
                    ) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if (
                                not location
                                or redirect_count >= self.settings.inspector_max_redirects
                            ):
                                raise InspectionError("redirect_limit")
                            current = urljoin(target.url, location)
                            continue
                        if response.status_code >= 400:
                            if (
                                response.status_code in {408, 425, 429}
                                or response.status_code >= 500
                            ):
                                raise InspectionError("fetch_failed")
                            raise InspectionError("fetch_rejected")
                        content_type = (
                            response.headers.get("content-type", "").split(";", 1)[0].lower()
                        )
                        if content_type not in {"text/html", "application/xhtml+xml"}:
                            raise InspectionError("not_html")
                        content_length = response.headers.get("content-length")
                        if content_length is not None:
                            try:
                                announced_size = int(content_length)
                            except ValueError as exc:
                                raise InspectionError("invalid_content_length") from exc
                            if announced_size > self.settings.inspector_max_response_bytes:
                                raise InspectionError("response_too_large")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.settings.inspector_max_response_bytes:
                                raise InspectionError("response_too_large")
                        encoding = response.encoding or "utf-8"
                        html = bytes(body).decode(encoding, errors="replace")
                        return InspectionResult(
                            final_url=target.url,
                            page=extract_page(html, self.settings.inspector_max_text_characters),
                        )
                except InspectionError:
                    raise
                except (httpx.HTTPError, TimeoutError) as exc:
                    raise InspectionError("fetch_failed") from exc
        raise InspectionError("redirect_limit")
