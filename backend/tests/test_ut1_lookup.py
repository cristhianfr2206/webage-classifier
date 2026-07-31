from pathlib import Path

from app.ut1_lookup import load_ut1_lookup, lookup_domain

FIXTURE = Path(__file__).parent / "fixtures" / "ut1_categories.csv"


def test_fixture_uses_production_normalizer_and_skips_nonregistrable_rows() -> None:
    lookup = load_ut1_lookup(FIXTURE)
    assert lookup == {
        "netflix.com": "audio-video",
        "facebook.com": "social_networks",
        "pinterest.com": "social_networks",
    }


def test_exact_and_parent_domain_matches_are_distinguished() -> None:
    lookup = load_ut1_lookup(FIXTURE)
    exact = lookup_domain("NETFLIX.com.", lookup)
    parent = lookup_domain("www.facebook.com", lookup)
    assert exact.matched and exact.source_category == "audio-video"
    assert exact.normalized_domain == "netflix.com"
    assert exact.match_type == "exact"
    assert parent.matched and parent.source_category == "social_networks"
    assert parent.normalized_domain == "www.facebook.com"
    assert parent.match_type == "parent-domain"


def test_unknown_is_not_safe_or_matched() -> None:
    result = lookup_domain("unknown-example.test", load_ut1_lookup(FIXTURE))
    assert result.matched is False
    assert result.source_category is None
    assert result.match_type == "unknown"
