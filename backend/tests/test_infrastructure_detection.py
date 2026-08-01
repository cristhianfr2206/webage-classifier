from app.infrastructure_detection import (
    detect_infrastructure,
    evidence_only_rules,
    rules,
    safe_exclude_rules,
)


def test_high_confidence_pilot_infrastructure_is_detected_without_pipeline_integration() -> None:
    decision = detect_infrastructure(" GTLD-SERVERS.NET. ")
    assert decision is not None
    assert decision.domain == "gtld-servers.net"
    assert decision.infrastructure_type == "dns_nameserver"
    assert decision.confidence == "high"
    assert decision.confidence_tier == "safe_exclude"
    assert not decision.http_useful
    assert not decision.browser_useful
    assert decision.exclude_from_age_classification


def test_consumer_and_medium_confidence_service_domains_remain_unknown() -> None:
    for domain in (
        "google.com",
        "instagram.com",
        "ezviz7.com",
        "sharepoint.com",
        "adobe.com",
        "discord.gg",
        "gwfb.net",
    ):
        assert detect_infrastructure(domain) is None


def test_ambiguous_provider_domains_are_evidence_only() -> None:
    for domain in (
        "cloudflare.com",
        "amazonaws.com",
        "fastly.net",
        "windows.net",
        "office.net",
        "googledomains.com",
        "microsoftonline.com",
        "digicert.com",
        "sentry.io",
        "okcdn.ru",
        "fbcdn.net",
        "tiktokv.com",
    ):
        decision = detect_infrastructure(domain)
        assert decision is not None
        assert decision.confidence_tier == "evidence_only"
        assert decision.http_useful
        assert decision.browser_useful
        assert not decision.exclude_from_age_classification


def test_invalid_domain_is_not_detected() -> None:
    assert detect_infrastructure("https://127.0.0.1/private") is None
    assert detect_infrastructure("not-a-domain") is None


def test_rules_are_exact_and_do_not_add_application_categories() -> None:
    assert "adult" not in rules()
    assert "gambling" not in rules()
    assert "social_networks" not in rules()
    assert "audio-video" not in rules()
    assert detect_infrastructure("sub.cloudflare.com") is None
    assert safe_exclude_rules().isdisjoint(evidence_only_rules())
    assert safe_exclude_rules() | evidence_only_rules() == rules()
