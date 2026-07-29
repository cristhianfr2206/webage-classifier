import pytest

from app.normalization import NormalizationError, normalize_domain, normalize_url


def test_domain_normalization_supports_idna_and_public_suffixes() -> None:
    assert normalize_domain("BÜCHER.de.") == "xn--bcher-kva.de"
    target = normalize_url("https://news.example.co.uk/path#fragment")
    assert target.host == "news.example.co.uk"
    assert target.registrable_domain == "example.co.uk"
    assert target.url == "https://news.example.co.uk/path"


@pytest.mark.parametrize(
    "value",
    ["ftp://example.com", "https://user:password@example.com", "javascript:alert(1)"],
)
def test_url_normalization_rejects_unsafe_schemes_and_credentials(value: str) -> None:
    with pytest.raises(NormalizationError):
        normalize_url(value)


def test_domain_normalization_rejects_ips_and_nonregistrable_names() -> None:
    with pytest.raises(NormalizationError):
        normalize_domain("127.0.0.1")
    with pytest.raises(NormalizationError):
        normalize_domain("localhost")
