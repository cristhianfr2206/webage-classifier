"""Read-only prototype for identifying obvious infrastructure hostnames.

This module is intentionally not imported by the classification or queue
pipelines.  The rules are exact-domain matches only; no parent-domain
inference is performed, which avoids classifying an entire consumer domain
family as infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.normalization import NormalizationError, normalize_domain


@dataclass(frozen=True)
class InfrastructureDecision:
    domain: str
    infrastructure_type: str
    confidence: str
    confidence_tier: str
    evidence: str
    http_useful: bool
    browser_useful: bool
    exclude_from_age_classification: bool


# These are deliberately conservative, exact matches derived from the pilot
# review.  Medium-confidence service/platform domains are intentionally absent.
_SAFE_EXCLUDE = frozenset(
    {
        "gtld-servers.net",
        "root-servers.net",
        "apple-dns.net",
        "trafficmanager.net",
        "cloudflare-dns.com",
        "googletagmanager.com",
        "google-analytics.com",
        "doubleclick.net",
        "googlesyndication.com",
        "appsflyersdk.com",
        "windowsupdate.com",
        "ntp.org",
        "gstatic.com",
        "googlevideo.com",
        "ytimg.com",
        "cdninstagram.com",
        "tiktokcdn.com",
        "aaplimg.com",
        "googleusercontent.com",
    }
)

_RULES: dict[str, tuple[str, str, str]] = {
    # DNS, nameserver, registry, and time infrastructure.
    "gtld-servers.net": ("dns_nameserver", "high", "authoritative TLD nameserver hostname"),
    "domaincontrol.com": ("dns_nameserver", "high", "registrar/DNS service hostname"),
    "apple-dns.net": ("dns_nameserver", "high", "DNS service hostname"),
    "trafficmanager.net": ("dns_nameserver", "high", "managed DNS/load-balancing hostname"),
    "ripn.net": ("dns_nameserver", "high", "registry/DNS infrastructure hostname"),
    "googledomains.com": ("dns_nameserver", "high", "domain/DNS service hostname"),
    "cloudflare-dns.com": ("dns_nameserver", "high", "DNS service hostname"),
    "nic.ru": ("dns_nameserver", "high", "registry/registrar service hostname"),
    "root-servers.net": ("dns_nameserver", "high", "root nameserver hostname"),
    "ntp.org": ("time_service", "high", "network time service hostname"),
    # CDN, cloud delivery, and static assets.
    "cloudflare.com": ("cdn_delivery", "high", "CDN/security provider hostname"),
    "gstatic.com": ("static_asset_host", "high", "Google static asset hostname"),
    "amazonaws.com": ("cloud_hosting", "high", "cloud hosting provider hostname"),
    "fbcdn.net": ("static_asset_host", "high", "Facebook CDN hostname"),
    "googlevideo.com": ("cdn_delivery", "high", "Google media delivery hostname"),
    "fastly.net": ("cdn_delivery", "high", "CDN provider hostname"),
    "googleusercontent.com": ("cloud_hosting", "high", "Google hosted-content hostname"),
    "aaplimg.com": ("static_asset_host", "high", "Apple static asset hostname"),
    "office.net": ("static_asset_host", "high", "Microsoft service/static asset hostname"),
    "cloudflare.net": ("cdn_delivery", "high", "CDN/security provider hostname"),
    "cdninstagram.com": ("static_asset_host", "high", "Instagram CDN hostname"),
    "tiktokcdn.com": ("static_asset_host", "high", "TikTok CDN hostname"),
    "akam.net": ("cdn_delivery", "high", "CDN provider hostname"),
    "pv-cdn.net": ("cdn_delivery", "high", "CDN provider hostname"),
    "tiktokv.com": ("static_asset_host", "high", "TikTok delivery hostname"),
    "ytimg.com": ("static_asset_host", "high", "YouTube static asset hostname"),
    "okcdn.ru": ("cdn_delivery", "high", "CDN provider hostname"),
    # Analytics and advertising infrastructure.
    "googletagmanager.com": ("analytics_advertising", "high", "tag-management hostname"),
    "appsflyersdk.com": ("analytics_advertising", "high", "mobile attribution SDK hostname"),
    "doubleclick.net": ("analytics_advertising", "high", "advertising delivery hostname"),
    "googlesyndication.com": ("analytics_advertising", "high", "advertising delivery hostname"),
    "google-analytics.com": ("analytics_advertising", "high", "analytics collection hostname"),
    # Updates, identity, security, and telemetry.
    "windowsupdate.com": ("software_update", "high", "software update hostname"),
    "gvt1.com": ("software_update", "high", "browser/software delivery hostname"),
    "gvt2.com": ("software_update", "high", "browser/software delivery hostname"),
    "microsoftonline.com": ("identity_service", "high", "Microsoft identity service hostname"),
    "windows.net": ("cloud_service", "high", "Microsoft service infrastructure hostname"),
    "digicert.com": ("certificate_security", "high", "certificate/security service hostname"),
    "sentry.io": ("observability", "high", "application telemetry service hostname"),
    "whatsapp.net": (
        "messaging_infrastructure",
        "high",
        "messaging service infrastructure hostname",
    ),
}


def detect_infrastructure(domain: str) -> InfrastructureDecision | None:
    """Return a conservative exact-match decision, or ``None``.

    Invalid domains and all non-listed domains remain unknown.  In particular,
    this prototype does not classify medium-confidence service domains or infer
    a result from a parent domain.
    """

    try:
        normalized = normalize_domain(domain)
    except NormalizationError:
        return None
    rule = _RULES.get(normalized)
    if rule is None:
        return None
    infrastructure_type, confidence, evidence = rule
    confidence_tier = "safe_exclude" if normalized in _SAFE_EXCLUDE else "evidence_only"
    return InfrastructureDecision(
        domain=normalized,
        infrastructure_type=infrastructure_type,
        confidence=confidence,
        confidence_tier=confidence_tier,
        evidence=evidence,
        http_useful=confidence_tier != "safe_exclude",
        browser_useful=confidence_tier != "safe_exclude",
        exclude_from_age_classification=confidence_tier == "safe_exclude",
    )


def rules() -> frozenset[str]:
    """Expose the exact rule keys for deterministic tests and audit output."""

    return frozenset(_RULES)


def safe_exclude_rules() -> frozenset[str]:
    return _SAFE_EXCLUDE


def evidence_only_rules() -> frozenset[str]:
    return frozenset(_RULES).difference(_SAFE_EXCLUDE)
