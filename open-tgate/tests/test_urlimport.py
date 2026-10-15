"""Tests for the SSRF-guarded knowledge URL importer.

These cover the security-critical, network-free logic: which targets are
refused, and how HTML/text is reduced to a reviewable artifact.
"""

import pytest

from app.urlimport import (
    MAX_CHARS,
    UrlImportError,
    _extract,
    _TextExtractor,
    _validate_url,
    import_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",            # non-http scheme
        "file:///etc/passwd",            # file scheme
        "javascript:alert(1)",           # junk scheme
        "http://127.0.0.1/",             # loopback
        "http://localhost/",             # resolves to loopback
        "http://169.254.169.254/latest", # cloud metadata (link-local)
        "http://10.0.0.5/",              # private
        "http://192.168.1.1/",           # private
        "http://[::1]/",                 # loopback v6
        "http://0.0.0.0/",               # unspecified
    ],
)
def test_validate_url_refuses_unsafe_targets(url):
    with pytest.raises(UrlImportError):
        _validate_url(url)


def test_extract_strips_scripts_and_keeps_title_and_text():
    html = (
        "<html><head><title>  Support Policy  </title>"
        "<style>.x{color:red}</style></head>"
        "<body><script>steal()</script>"
        "<h1>Refunds</h1><p>Refunds take 5 days.</p>"
        "<p>Contact support.</p></body></html>"
    )
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.text()
    assert parser.title == "Support Policy"
    assert "Refunds take 5 days." in text
    assert "Contact support." in text
    assert "steal()" not in text  # script content dropped
    assert "color:red" not in text  # style content dropped


def test_extract_returns_title_and_capped_content():
    title, content = _extract("text/html", "<title>T</title><p>Hello world</p>", "https://a.example/p")
    assert title == "T"
    assert content == "Hello world"
    # Plain-text path uses the host as the title.
    title2, content2 = _extract("text/plain", "   just text   ", "https://docs.example/readme.txt")
    assert title2 == "docs.example"
    assert content2 == "just text"
    # Oversized content is capped.
    _, big = _extract("text/plain", "x" * (MAX_CHARS + 50), "https://a.example/")
    assert len(big) == MAX_CHARS


def test_extract_empty_is_rejected():
    with pytest.raises(UrlImportError):
        _extract("text/html", "<html><head></head><body></body></html>", "https://a.example/")


def test_import_url_rejects_unsafe_before_any_fetch():
    # import_url must refuse a loopback target without attempting a request.
    with pytest.raises(UrlImportError):
        import_url("http://127.0.0.1:8000/admin")
