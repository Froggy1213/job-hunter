"""Tests for URL normalization — the deduplication key."""

from database.repository import normalize_url


def test_strips_trailing_slash_and_fragment():
    assert normalize_url("https://example.com/jobs/1/#top") == "https://example.com/jobs/1"


def test_lowercases_scheme_and_host_only():
    # Host is lowercased; the path's case is preserved.
    assert (
        normalize_url("HTTPS://WWW.Wantedly.com/projects/123")
        == "https://www.wantedly.com/projects/123"
    )


def test_queryless_urls_unchanged():
    url = "https://www.wantedly.com/projects/123"
    assert normalize_url(url) == url


def test_strips_query_string():
    # Query + fragment are dropped so cosmetic variants dedup together.
    assert (
        normalize_url("https://www.wantedly.com/projects/123?ref=feed&utm=x")
        == "https://www.wantedly.com/projects/123"
    )
