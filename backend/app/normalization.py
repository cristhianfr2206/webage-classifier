import ipaddress
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

import idna
import tldextract

_extract = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    include_psl_private_domains=True,
)


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedTarget:
    url: str
    host: str
    registrable_domain: str


def normalize_domain(value: str) -> str:
    candidate = value.strip().rstrip(".").lower()
    if not candidate or len(candidate) > 253:
        raise NormalizationError("invalid domain length")
    if "://" in candidate or any(char in candidate for char in "/?#@"):
        raise NormalizationError("expected a domain, not a URL")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise NormalizationError("IP addresses are not Tranco domains")
    try:
        ascii_domain = idna.encode(candidate, uts46=True, std3_rules=True).decode("ascii")
    except idna.IDNAError as exc:
        raise NormalizationError("invalid internationalized domain") from exc
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(not label or len(label) > 63 for label in labels):
        raise NormalizationError("domain must have a valid public suffix")
    if not registrable_domain(ascii_domain):
        raise NormalizationError("domain is not registrable")
    return ascii_domain


def registrable_domain(host: str) -> str:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        result = _extract(host)
        return str(result.registered_domain)
    return host


def normalize_url(value: str) -> NormalizedTarget:
    raw = value.strip()
    if not raw:
        raise NormalizationError("URL is required")
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise NormalizationError("invalid URL") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise NormalizationError("only HTTP and HTTPS are permitted")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise NormalizationError("URL credentials and missing hosts are not permitted")
    host = parsed.hostname.rstrip(".").lower()
    try:
        address = ipaddress.ip_address(host)
        ascii_host = address.compressed
        rendered_host = f"[{ascii_host}]" if address.version == 6 else ascii_host
    except ValueError:
        try:
            ascii_host = idna.encode(host, uts46=True, std3_rules=True).decode("ascii")
        except idna.IDNAError as exc:
            raise NormalizationError("invalid internationalized host") from exc
        rendered_host = ascii_host
    if len(ascii_host) > 253:
        raise NormalizationError("host is too long")
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
    path = parsed.path or "/"
    normalized = SplitResult(parsed.scheme.lower(), netloc, path, parsed.query, "")
    return NormalizedTarget(
        url=urlunsplit(normalized),
        host=ascii_host,
        registrable_domain=registrable_domain(ascii_host),
    )
