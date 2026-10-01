# Aesthetic presets (mapped.earth look)

`render_viz(..., preset=...)` renders through the **survey-aesthetics**
peer engine (v0.2.0+) instead of the standard matplotlib data map. The
four presets reproduce the visual system of mapped.earth reference
reels: chrome-free full-bleed frames, editorial typography, custom
legends, and an explicit statement of what is encoded.

## The presets

| preset | look | variables | data layer |
|---|---|---|---|
| `dark_flow` | LIC flow streaks on black; hue = scalar, brightness = speed | `currents`, `wind` | `lic_texture` |
| `dark_glow` | additive event glow with bloom on black | `earthquakes`, `storm-tracks` | `glow_from_grid` over splatted disks |
| `paper_prism` | 3D prism extrusion on warm paper; height = value | any continuous gridded variable (`tp` is the reference case) | `prism_frame` |
| `dark_strands` | advected hair-like particle strands on black; color = temperature | `currents`, `wind` | `render_strands` over survey-flow trails |

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

### dark_strands details

The warming.watch look: thousands of hair-like strands advected
through the vector field, colored by temperature, on the black void
with no basemap drawn — geography emerges from the data itself.

* `currents` (CurrentField): strands follow the current vectors;
  color = water temperature in °F (the honesty line reads
  `COLOR = WATER TEMPERATURE`).
* `wind` (Era5Field): strands follow the 10 m wind vectors; color =
  2 m air temperature (the honesty line reads
  `COLOR = AIR TEMPERATURE`). Wind needs an `air_temperature` scalar
  on the field — without it the preset fails fast instead of coloring
  by wind speed.
* Per data timestep: 3000 particles are seeded (fixed seed `7` —
  recombed, so the render is deterministic), spun up for 12 hourly
  advection steps on that timestep's field, and their 12-step trails
  rendered with head-to-tail alpha fade. `strand_count` (default 3000)
  and `strand_linewidth` (default 1.4 pt) are tunable.
* `vmin`/`vmax` are reel-wide and rounded like `dark_flow`.
* For `currents` the strands are clipped to an ocean mask (NaN =
  land in the field), feathered 3 px — with no basemap drawn, the
  lake/coastline shape is drawn by the strands stopping at the shore.
  An all-land field is an honest `ValueError`, not an empty reel.
  For `wind` the strands are clipped to the Natural Earth land
  polygons (rasterized once per reel via `viz.underlay.fetch_landmask`,
  feathered 3 px) — the continent emerges from the strands, the
  warming.watch look. Pass `landmask=False` to disable; when the land
  layer is unavailable the render falls back to unclipped strands and
  records the fallback in the manifest.
* Legends: timeline scrubber with mono timestamp, gradient bar with
  min/max, north arrow, the honesty line.

```python
frames, manifest = render_viz(
    spec, field, None, out_dir="frames", preset="dark_strands",
    strand_count=4000, strand_linewidth=1.2,
)
```

## Fixed scales (no flicker)

`vmin`/`vmax`/`speed_max` are computed once from the full dataset and
reused for every frame. Explicit `spec.vmin`/`vmax` always win over the
data range. Data-derived scales are rounded to one decimal for clean
legend labels.

## Basemap styles

`basemap=` chooses what geography is drawn under the data layer
(preset-path only; `None` keeps the preset's bundled style). Unknown
names fail fast listing the choices.

| style | draws |
|---|---|
| `void_black` | hairline coastlines on the black void (the `dark_flow`/`dark_glow` default) |
| `no_basemap` | nothing — geography emerges from the data mask (the `dark_strands` default) |
| `subtle_land` | faint landmass fill under hairline coastlines (needs a landmask; falls back to coastlines-only when none is available) |

```python
frames, manifest = render_viz(
    spec, field, None, out_dir="frames", preset="dark_flow",
    basemap="no_basemap",   # pure data on the void
)
```

The manifest records the resolved style. `basemap` without `preset`
is a `ValueError` (it has no meaning on the legacy renderer).

## Furniture layout

Titles, subtitles, legends, dials, and the honesty line are placed
by the engine's collision-aware placer (`place_furniture`), not at
fixed coordinates: each item carries a preferred rect, a priority,
and fallback positions, and the placer fits them without overlap
(measured from real glyph extents on a probe canvas). The placement
runs once per reel; per-frame draw calls reuse the same rects. Long
titles shrink to fit instead of running off the canvas edge.

Place labels avoid the placed furniture rects (via the engine's
`place_labels(..., obstacles=...)`), then the engine's greedy
decluttering drops what still cannot fit. The manifest records every
placed rect, so a render's layout is fully auditable.

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

## Place labels

`place_labels=True` (default when a preset is active) auto-fetches up
to `max_labels` (default 8, `min_population` default 0) places for the
reel's north-up bbox from the **survey-gazetteer** peer engine (Natural
Earth 1:10m populated places, stdlib-only, deterministic). The fetch
happens ONCE per reel and the same labels ride every frame; they are
drawn upright with the rest of the furniture on the final canvas,
after rotation, via the engine's `place_labels` — which owns the final
on-canvas decluttering (greedy, priority-ordered; labels that fit
nowhere are dropped, never overlapping furniture).

```python
frames, manifest = render_viz(
    spec, field, None, out_dir="frames", preset="paper_prism",
    place_labels=True,      # auto via survey-gazetteer (default)
    max_labels=6,           # fewer, larger places
    min_population=50000,
)
# or explicit labels (win over auto, used verbatim):
frames, manifest = render_viz(
    spec, field, None, out_dir="frames", preset="paper_prism",
    place_labels=[{"x": -87.9, "y": 43.0, "text": "Milwaukee",
                   "priority": 10}],
)
# or off:
frames, manifest = render_viz(
    spec, field, None, out_dir="frames", preset="paper_prism",
    place_labels=False,
)
```

Explicit label dicts need numeric `x`/`y` (lon/lat degrees), a
non-empty string `text`, and must be JSON-serializable — they land
verbatim in the frame manifest, which is what the survey-cache
fingerprint hashes. `place_labels`/`max_labels`/`min_population` are
preset-path options and fail fast without `preset` (like
`rotation`/`watermark`/`subtitle`).

## Custom legends

Each preset draws its own legends instead of the default colorbar:
gradient bar (dark_flow), vertical scale bar (paper_prism), timeline
scrubber (dark_flow), cumulative counter + date dial (dark_glow).
`encoding_line=False` hides the honesty line.

## Interop

* The peers are optional: `pip install 'survey-viz[aesthetics]'`
  (pinned to the GitHub releases — the packages are not on PyPI).
  Without survey-aesthetics, `preset=...` raises a `RuntimeError` with
  the install command; without survey-gazetteer, `place_labels=True`
  (the preset-path default) raises the same kind of honest error, and
  `place_labels=False` / explicit lists never import the peer. The
  legacy renderer never imports either peer.
* The manifest keeps the `survey-viz.frame-manifest/1.0` schema and
  records `preset`, `rotation_deg`, `watermark`, `subtitle`,
  `vmin`/`vmax`/`speed_max`, the combined engine string, and a
  `place_labels` block (`mode` auto/explicit/off, `max_labels`,
  `min_population`, `kinds`, gazetteer version, and the
  JSON-serializable label dicts), so survey-animate and survey-cache
  treat preset renders like any other — and the cache fingerprint can
  hash the label inputs.
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
* The engine v0.1.0 top-level namespace omits `draw_north_arrow`,
  `close`, and `letterspace` even though its own `docs/API.md`
  promises them; this module resolves them from their submodules
  (or stdlib) without modifying the release. v0.2.0 is now the
  pinned engine.
* Strand rendering needs the **survey-flow** peer (now in the
  `aesthetics` extra, pinned to its GitHub release) for
  `VectorField`/`ParticleSet`; without it `preset="dark_strands"`
  raises the honest peer-missing error. The other three presets never
  import it.
* Wind strands are clipped to the Natural Earth land polygons
  (`viz.underlay.fetch_landmask`, 110m/50m by region width, feathered
  3 px); when the land layer is unavailable the reel falls back to
  unclipped strands and the manifest records
  `"land (unavailable — unclipped)"`. `no_basemap` + wind with
  `landmask=False` draws strands over land as well as sea. Currents
  are masked by the field's own NaN (land) — which is why the preset
  requires a current field with a real land mask.
* Strand cost is ~1–2 s/frame at 3000 particles (12 spinup steps +
  trail rasterization); the 4000-particle reference is ~1.3 s/frame
  on the engine's benchmark.
* The engine's glow blur is designed for dense gridded energy: sparse
  events are splatted as disks first (see above), so dot size is fixed
  in pixels rather than data-scaled.
* Automatic place labels come from the survey-gazetteer peer's
  Natural Earth 1:10m populated places (cities/towns only — no
  physical features, admin regions, or POIs); empty regions simply get
  no labels. The engine's greedy decluttering drops labels that cannot
  fit without overlap.
* Prism tick labels are exact data fractions (e.g. 15.375), not snapped
  to round numbers — precise, not pretty.
