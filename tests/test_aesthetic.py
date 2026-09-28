"""Tests for viz.aesthetic (reference-image color-mood analysis).

Network is fully stubbed: ``urllib.request.urlopen`` is monkeypatched
with fake responses, so the fetch tests run offline.
"""

from __future__ import annotations

import io

import pytest

from viz.aesthetic import (AestheticError, analyze_image, fetch_image_bytes,
                           suggest_aesthetic)


def _png(color, size=(64, 48)):
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Analysis of synthetic images
# ---------------------------------------------------------------------------

def test_dark_warm_image_suggests_dark_inferno():
    profile = analyze_image(_png((150, 30, 20)))
    assert profile.style == "reel-dark"
    assert profile.colormap == "inferno"
    assert profile.brightness < 0.45
    assert profile.dominant_hue is not None
    assert len(profile.palette) >= 1
    assert all(p.startswith("#") and len(p) == 7 for p in profile.palette)


def test_light_cool_image_suggests_light_blues():
    profile = analyze_image(_png((170, 205, 235)))
    assert profile.style == "light"
    assert profile.colormap == "Blues"
    assert 200.0 <= (profile.dominant_hue or 0) <= 230.0


def test_grayscale_suggests_no_colormap():
    profile = analyze_image(_png((128, 128, 128)))
    assert profile.colormap is None
    assert profile.dominant_hue is None
    assert any("grayscale" in note for note in profile.notes)


def test_garbage_bytes_raise_honestly():
    with pytest.raises(AestheticError):
        analyze_image(b"this is not an image")


def test_suggest_from_bytes_marks_upload_source():
    profile = suggest_aesthetic(_png((150, 30, 20)))
    assert profile.source == "upload"


def test_suggest_rejects_bad_type():
    with pytest.raises(AestheticError):
        suggest_aesthetic(12345)


def test_profile_to_dict_round_trip():
    profile = analyze_image(_png((150, 30, 20)))
    d = profile.to_dict()
    assert d["style"] == "reel-dark" and d["colormap"] == "inferno"
    assert isinstance(d["palette"], list) and isinstance(d["notes"], list)


# ---------------------------------------------------------------------------
# Fetch (stubbed network)
# ---------------------------------------------------------------------------

class _FakeHeaders:
    def __init__(self, content_type):
        self._content_type = content_type

    def get_content_type(self):
        return self._content_type


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self.headers = _FakeHeaders(content_type)

    def read(self, n: int = -1) -> bytes:
        chunk, self._body = self._body[:n], self._body[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_urlopen(monkeypatch, routes):
    """routes: url -> (body, content_type)."""
    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url not in routes:
            raise IOError(f"unexpected URL {url}")
        body, content_type = routes[url]
        return _FakeResponse(body, content_type)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_fetch_direct_image(monkeypatch):
    data = _png((10, 20, 30))
    _stub_urlopen(monkeypatch, {
        "https://example.com/frame.png": (data, "image/png")})
    assert fetch_image_bytes("https://example.com/frame.png") == data


def test_fetch_page_uses_og_image(monkeypatch):
    thumb = _png((10, 20, 30))
    html = (b'<html><head><meta property="og:image" '
            b'content="https://cdn.example.com/t.jpg"></head></html>')
    _stub_urlopen(monkeypatch, {
        "https://social.example.com/reel/123": (html, "text/html"),
        "https://cdn.example.com/t.jpg": (thumb, "image/jpeg")})
    assert fetch_image_bytes(
        "https://social.example.com/reel/123") == thumb


def test_fetch_page_without_thumbnail_raises(monkeypatch):
    _stub_urlopen(monkeypatch, {
        "https://social.example.com/reel/123":
            (b"<html><head></head></html>", "text/html")})
    with pytest.raises(AestheticError) as excinfo:
        fetch_image_bytes("https://social.example.com/reel/123")
    assert "og:image" in str(excinfo.value)


def test_fetch_rejects_non_http_scheme():
    with pytest.raises(AestheticError):
        fetch_image_bytes("ftp://example.com/frame.png")


def test_fetch_rejects_oversize_body(monkeypatch):
    big = b"\x89PNG" + b"x" * (6_000_000)
    _stub_urlopen(monkeypatch, {
        "https://example.com/big.png": (big, "image/png")})
    with pytest.raises(AestheticError) as excinfo:
        fetch_image_bytes("https://example.com/big.png")
    assert "cap" in str(excinfo.value)


def test_suggest_from_url_end_to_end(monkeypatch):
    thumb = _png((150, 30, 20))
    html = (b'<html><head><meta property="og:image" '
            b'content="/t.jpg"></head></html>')
    _stub_urlopen(monkeypatch, {
        "https://social.example.com/reel/9": (html, "text/html"),
        "https://social.example.com/t.jpg": (thumb, "image/jpeg")})
    profile = suggest_aesthetic("https://social.example.com/reel/9")
    assert profile.style == "reel-dark"
    assert profile.colormap == "inferno"
    assert profile.source == "https://social.example.com/reel/9"
