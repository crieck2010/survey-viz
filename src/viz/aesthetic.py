"""viz.aesthetic: copy the *color mood* of a reference image into a spec.

Given a reel URL (Instagram / TikTok / YouTube / direct video or image
link) or raw image bytes (e.g. a screenshot upload), this module
extracts the reference's **color mood** — overall brightness and
dominant hues — and suggests the two aesthetic knobs survey-viz
understands: the ``"reel-dark"`` / ``"light"`` style and a curated
colormap.

What this honestly is and is not:

* IS: a deterministic color analysis. The reference's public thumbnail
  (``og:image`` / ``twitter:image``) is fetched — downloading the
  actual reel video is deliberately out of scope (social platforms put
  it behind login walls and rate limits, so a feature built on it
  would be fragile by design).
* IS NOT: a full aesthetic clone. Fonts, title layout, transitions,
  pacing, and grading curves cannot be recovered from a thumbnail and
  are not attempted. The returned :class:`AestheticProfile` carries
  ``notes`` explaining exactly what was measured so a UI can show the
  user what "copied" means.

The hue -> colormap table is a documented heuristic, not color
science: warm thumbnails suggest ``"inferno"`` / ``"hot"``,
blue/green ones ``"viridis"`` / ``"Blues"`` / ``"Greens"``, and so on.
Near-grayscale thumbnails suggest no colormap at all (``None``) — the
variable default is kept rather than guessed.

Resource bounds (safe for UI and batch use):

* network fetch: 15 s timeout, 5 MB body cap, ``http``/``https`` only;
* analysis: the image is downsampled to at most 256 px on the long
  edge before any pixel math, so memory is bounded regardless of
  input size;
* no global state — :func:`analyze_image` is a pure function of its
  bytes, safe to call from threads.

Dependencies: stdlib ``urllib`` for fetching, Pillow + numpy for
analysis (both already required through matplotlib — no new
dependencies).
"""

from __future__ import annotations

import colorsys
import io
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from .render import CURATED_CMAPS

__all__ = ["AestheticError", "AestheticProfile", "fetch_image_bytes",
           "analyze_image", "suggest_aesthetic",
           "STYLE_THRESHOLD", "FETCH_TIMEOUT", "MAX_IMAGE_BYTES"]

#: Brightness below this -> "reel-dark", at/above -> "light".
STYLE_THRESHOLD = 0.45
#: Seconds for any single HTTP request in :func:`fetch_image_bytes`.
FETCH_TIMEOUT = 15.0
#: Largest image body accepted (bytes) — thumbnails are kilobytes.
MAX_IMAGE_BYTES = 5_000_000
#: Long edge, in px, the analysis downsamples to (bounds memory).
_ANALYZE_SIZE = 256
#: Distinct colors kept for the palette.
_PALETTE_COLORS = 5
#: Saturation below this counts as grayscale (no dominant hue).
_GRAY_SATURATION = 0.15

_USER_AGENT = ("reel-studio aesthetic analyzer "
               "(github.com/crieck2010/reel-studio)")

# <meta property="og:image" content="..."> — both attribute orders,
# plus the twitter:image fallback.
_OG_IMAGE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']'
    r'[^>]+content=["\']([^"\']+)["\']', re.IGNORECASE)
_OG_IMAGE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\']'
    r'[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']',
    re.IGNORECASE)


class AestheticError(ValueError):
    """The reference could not be fetched or analyzed (honest reason)."""


@dataclass
class AestheticProfile:
    """The measured color mood of one reference image.

    Attributes:
        style: ``"reel-dark"`` or ``"light"`` (VizSpec styles).
        colormap: suggested curated colormap, or ``None`` when the
            thumbnail is near-grayscale (keep the variable default).
        palette: up to 5 dominant colors as ``"#rrggbb"`` hex strings,
            most dominant first — for the UI to show what was found.
        brightness: mean relative luminance, 0..1.
        dominant_hue: degrees 0..360 of the most dominant color, or
            ``None`` for near-grayscale thumbnails.
        source: the URL analyzed, or ``"upload"`` for raw bytes.
        notes: human-readable account of what was measured and why
            each suggestion was made.
    """
    style: str
    colormap: Optional[str]
    palette: List[str]
    brightness: float
    dominant_hue: Optional[float]
    source: str
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "style": self.style,
            "colormap": self.colormap,
            "palette": list(self.palette),
            "brightness": self.brightness,
            "dominant_hue": self.dominant_hue,
            "source": self.source,
            "notes": list(self.notes),
        }


def _read_capped(response, max_bytes: int) -> bytes:
    chunks: List[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = response.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _fetch(url: str) -> Tuple[bytes, str]:
    """Return ``(body, content_type)`` for ``url`` (capped, timed)."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request,
                                     timeout=FETCH_TIMEOUT) as response:
            body = _read_capped(response, MAX_IMAGE_BYTES)
            content_type = response.headers.get_content_type()
    except AestheticError:
        raise
    except Exception as exc:
        raise AestheticError(
            f"could not fetch {url}: {exc}") from exc
    if len(body) > MAX_IMAGE_BYTES:
        raise AestheticError(
            f"image at {url} exceeds the {MAX_IMAGE_BYTES // 1_000_000} MB "
            "cap — refusing to download it")
    return body, content_type


def _thumbnail_url(page_url: str, html: bytes) -> str:
    text = html.decode("utf-8", errors="replace")
    match = _OG_IMAGE.search(text) or _OG_IMAGE_REV.search(text)
    if not match:
        raise AestheticError(
            f"no og:image / twitter:image thumbnail found at {page_url} — "
            "the page may require login, or block scrapers")
    return urllib.parse.urljoin(page_url, match.group(1).strip())


def fetch_image_bytes(url: str) -> bytes:
    """Fetch the reference image for ``url``.

    A URL that serves an image directly is downloaded as-is; any other
    page URL is scraped for its ``og:image`` / ``twitter:image``
    thumbnail (the best reliably available proxy for a social reel's
    look — the video itself sits behind login walls).

    Raises :class:`AestheticError` with the honest reason when the
    fetch fails, no thumbnail is advertised, or the body is not an
    image / exceeds the size cap.
    """
    if not isinstance(url, str) or not url.strip():
        raise AestheticError("empty URL")
    url = url.strip()
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise AestheticError(
            f"only http(s) URLs are fetched, got {scheme or 'no scheme'!r}")
    body, content_type = _fetch(url)
    if content_type.startswith("image/"):
        return body
    if content_type.startswith("text/html"):
        thumb_url = _thumbnail_url(url, body)
        thumb_body, thumb_type = _fetch(thumb_url)
        if not thumb_type.startswith("image/"):
            raise AestheticError(
                f"thumbnail at {thumb_url} is {thumb_type}, not an image")
        return thumb_body
    raise AestheticError(
        f"URL served {content_type}, not an image or a page with a "
        "thumbnail")


def _hue_to_colormap(hue_degrees: float, dark: bool) -> str:
    """Documented heuristic: dominant hue -> curated colormap."""
    table = (
        # (hue_lo, hue_hi, dark_cmap, light_cmap)
        (345.0, 15.0, "inferno", "hot"),      # red
        (15.0, 45.0, "inferno", "YlOrRd"),    # orange
        (45.0, 75.0, "plasma", "YlGn"),       # yellow-green
        (75.0, 160.0, "viridis", "Greens"),   # green
        (160.0, 200.0, "ocean", "YlGnBu"),    # cyan
        (200.0, 260.0, "viridis", "Blues"),   # blue
        (260.0, 345.0, "plasma", "Purples"),  # purple-magenta
    )
    for lo, hi, dark_cmap, light_cmap in table:
        if lo <= hi:
            hit = lo <= hue_degrees < hi
        else:  # wraps past 360 (red)
            hit = hue_degrees >= lo or hue_degrees < hi
        if hit:
            chosen = dark_cmap if dark else light_cmap
            assert chosen in CURATED_CMAPS
            return chosen
    raise AssertionError(f"hue {hue_degrees} fell off the table")  # no cover


def analyze_image(data: bytes, source: str = "upload") -> AestheticProfile:
    """Measure the color mood of raw image ``data``.

    Pure function: no network, no global state. Raises
    :class:`AestheticError` when the bytes do not decode as an image.
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow ships w/ mpl
        raise AestheticError(
            "image analysis needs Pillow (pip install pillow)") from exc
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise AestheticError(
            f"could not decode image bytes: {exc}") from exc
    img.thumbnail((_ANALYZE_SIZE, _ANALYZE_SIZE))

    import numpy as np
    pixels = np.asarray(img, dtype=np.float64) / 255.0
    luminance = (0.2126 * pixels[..., 0] + 0.7152 * pixels[..., 1]
                 + 0.0722 * pixels[..., 2])
    brightness = float(luminance.mean())
    dark = brightness < STYLE_THRESHOLD
    style = "reel-dark" if dark else "light"

    quantized = img.quantize(colors=_PALETTE_COLORS, method=2).convert("RGB")
    counts = sorted(quantized.getcolors(maxcolors=1 << 24),
                    key=lambda item: item[0], reverse=True)
    palette = [f"#{r:02x}{g:02x}{b:02x}"
               for _, (r, g, b) in counts[:_PALETTE_COLORS]]

    notes = [f"mean brightness {brightness:.2f} -> "
             f"{'dark' if dark else 'light'} style"]
    dominant_hue: Optional[float] = None
    colormap: Optional[str] = None
    if counts:
        r, g, b = [c / 255.0 for c in counts[0][1]]
        h, s, _v = colorsys.rgb_to_hsv(r, g, b)
        if s >= _GRAY_SATURATION:
            dominant_hue = round(h * 360.0, 1)
            colormap = _hue_to_colormap(dominant_hue, dark)
            notes.append(f"dominant hue {dominant_hue:.0f}° "
                         f"(saturation {s:.2f}) -> {colormap!r} colormap")
        else:
            notes.append("near-grayscale thumbnail — no colormap "
                         "suggested, keeping the variable default")

    return AestheticProfile(
        style=style, colormap=colormap, palette=palette,
        brightness=round(brightness, 4), dominant_hue=dominant_hue,
        source=source, notes=notes)


def suggest_aesthetic(source: Union[str, bytes]) -> AestheticProfile:
    """Suggest a style + colormap from a reel URL or image bytes.

    Args:
        source: an ``http(s)`` URL (reel / video / image / page with
            an ``og:image`` thumbnail) or raw image bytes (e.g. from a
            file upload).
    """
    if isinstance(source, bytes):
        return analyze_image(source, source="upload")
    if isinstance(source, str):
        return analyze_image(fetch_image_bytes(source), source=source)
    raise AestheticError(
        f"expected a URL string or image bytes, got {type(source).__name__}")
