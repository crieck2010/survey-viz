# Aesthetic presets (mapped.earth look)

`render_viz(..., preset=...)` renders through the **survey-aesthetics**
peer engine (v0.1.0+) instead of the standard matplotlib data map. The
three presets reproduce the visual system of mapped.earth reference
reels: chrome-free full-bleed frames, editorial typography, custom
legends, and an explicit statement of what is encoded.

## The presets

| preset | look | variables | data layer |
|---|---|---|---|
| `dark_flow` | LIC flow streaks on black; hue = scalar, brightness = speed | `currents`, `wind` | `lic_texture` |
| `dark_glow` | additive event glow with bloom on black | `earthquakes`, `storm-tracks` | `glow_from_grid` over splatted disks |
| `paper_prism` | 3D prism extrusion on warm paper; height = value | any continuous gridded variable (`tp` is the reference case) | `prism_frame` |

A preset on a variable without the data it needs is a `ValueError`
with the reason — never a silently wrong picture.

### dark_flow details

* `currents` (CurrentField): hue = water temperature in °F (converted
  from the field's °C), brightness = current speed. When the field
  carries no temperature, hue falls back to speed and the manifest says
  so.
* `wind` (Era5Field): hue = wind speed from `u10`/`v10`.
* Fixed LIC seed per reel (`7`); a varying seed would shimmer.
* Legends: timeline scrubber with mono timestamp, gradient bar with
  min/max, north arrow, `BRIGHTNESS = SPEED` honesty line.

### dark_glow details

* Frames are cumulative: every event with time ≤ frame date.
* Brightness encodes magnitude (quakes) or max sustained wind in kt
  (storms) — linear, stated in the encoding line
  (`BRIGHTNESS = MAGNITUDE` / `BRIGHTNESS = MAX SUSTAINED WIND (KT)`).
* Sparse events are splatted as small disks before the engine's blur
  passes (single-pixel spikes would be attenuated to invisibility —
  see "Honest limits" below).
* Legends: running cumulative counter, circular date dial.

### paper_prism details

* Grids larger than 44×60 are NaN-aware block-mean downsampled (the
  engine's documented cost limit); the rendered grid size is recorded
  in the manifest.
* `vmin` is `0.0` for precipitation-like data (data floor otherwise);
  `vmax` is the reel-wide data max. Both are fixed for the whole reel.
* Legends: vertical scale bar, date dial, the preset's honesty line
  ("Height is millimetres per day. Nothing else is encoded.").

## Fixed scales (no flicker)

`vmin`/`vmax`/`speed_max` are computed once from the full dataset and
reused for every frame. Explicit `spec.vmin`/`vmax` always win over the
data range. Data-derived scales are rounded to one decimal for clean
legend labels.

## Frame rotation

`rotation=None` (default) renders north-up. `rotation="auto"` uses the
engine's `optimal_rotation` on the spec bbox — for a region much wider
than tall on the vertical canvas this returns ~90° (the Lake Ontario
case: the lake's long axis runs up the frame and the zoom goes much
deeper). A numeric value is degrees counter-clockwise.

The map is rendered north-up on an enlarged canvas, rotated with
`rotate_frame_fill`, and center-cropped to exactly 1080×1920. Titles,
legends, and furniture are drawn **after** rotation, unrotated, on the
final frame; the north arrow rotates honestly with the map. `rotation`
requires `preset` — it has no meaning on the legacy path (rotating it
would rotate its burned-in titles too) and fails fast without one.

## Editorial title block

* Title: always `spec.title` — explicit user titles are never rewritten.
* Subtitle: the explicit `subtitle` parameter, else the auto time-window
  label ("16–23 September 2026", "1–5 September 2026", ...).
* Serif display title (DejaVu Serif), letterspaced subtitle,
  monospace readouts — the engine's type system, DejaVu families only
  (no system-font lottery, so survey-cache fingerprints stay stable).

## Watermark (opt-in)

`watermark=None` (default) draws no brand furniture at all.
`watermark="<handle>"` burns the standard furniture via the engine:
brand bottom-right, © line, data-source line bottom-left, and the
encoding honesty line centered above them.

## Custom legends

Each preset draws its own legends instead of the default colorbar:
gradient bar (dark_flow), vertical scale bar (paper_prism), timeline
scrubber (dark_flow), cumulative counter + date dial (dark_glow).
`encoding_line=False` hides the honesty line.

## Interop

* The peer is optional: `pip install 'survey-viz[aesthetics]'`
  (pinned to the GitHub release — the package is not on PyPI).
  Without it, `preset=...` raises a `RuntimeError` with the install
  command; the legacy renderer never imports the peer.
* The manifest keeps the `survey-viz.frame-manifest/1.0` schema and
  records `preset`, `rotation_deg`, `watermark`, `subtitle`,
  `vmin`/`vmax`/`speed_max`, and the combined engine string, so
  survey-animate and survey-cache treat preset renders like any other.
* `story_captions` and `canvas` (platform safe zones) are legacy-path
  features and fail fast with a preset — the preset has its own
  editorial system and layout grammar.
* A user `cmap` overrides the preset's curated colormap (recorded in
  the manifest); the preset default is kept otherwise.

## Honest limits

* LIC costs ~seconds per frame at 1080×1920 on fine grids (the kernel
  trades streak length for speed); prism grids are capped at ~60×44.
* Byte-determinism is matplotlib-version-pinned (same rule as the
  legacy renderer).
* The engine v0.1.0 top-level namespace omits `draw_north_arrow` and
  `close` even though its own `docs/API.md` promises them; this module
  resolves both from their submodules without modifying the release.
* The engine's glow blur is designed for dense gridded energy: sparse
  events are splatted as disks first (see above), so dot size is fixed
  in pixels rather than data-scaled.
* No automatic place labels yet — the reference reels' city dots need
  a label gazetteer this repo does not have (future work).
* Prism tick labels are exact data fractions (e.g. 15.375), not snapped
  to round numbers — precise, not pretty.
