"""URL → material ingest guards (Stage 56). CI-safe - no external network:
the scheme check and the SSRF check both run before any HTTP request, and
``localhost`` resolves to a loopback IP locally."""

import pytest

from src.app_services import (
    _assert_public_host,
    _fetch_url_text,
    _fetch_youtube_text,
    _safe_material_filename,
    _youtube_video_id,
)


def test_safe_material_filename_strips_illegal_chars():
    name = _safe_material_filename('Статья: раздел/тема*?"<>|')
    for bad in '/\\:*?"<>|':
        assert bad not in name
    assert name


def test_safe_material_filename_falls_back_when_empty():
    assert _safe_material_filename("   ") == "Веб-страница"


def test_fetch_url_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        _fetch_url_text("ftp://example.com/file.txt")


def test_fetch_url_blocks_internal_network():
    # SSRF guard: localhost → loopback IP must be rejected before any HTTP call.
    with pytest.raises(ValueError):
        _fetch_url_text("http://localhost/")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # classic cloud-metadata SSRF target
        "http://[::1]/",  # IPv6 loopback
        "http://[fd00::1]/",  # IPv6 unique-local (private)
    ],
)
def test_assert_public_host_blocks_internal(url):
    # IP literals + localhost resolve without external DNS - CI-safe.
    with pytest.raises(ValueError):
        _assert_public_host(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "http://youtube.com/watch?v=dQw4w9WgXcQ&t=10s",
    ],
)
def test_youtube_video_id_extraction(url):
    assert _youtube_video_id(url) == "dQw4w9WgXcQ"


def test_youtube_video_id_none_for_non_youtube():
    assert _youtube_video_id("https://example.com/watch?v=abc") is None


def test_fetch_youtube_rejects_non_youtube_before_network():
    # No video id → ValueError before any import/network (CI-safe).
    with pytest.raises(ValueError):
        _fetch_youtube_text("https://example.com/article")
