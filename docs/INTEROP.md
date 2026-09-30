# INTEROP — how survey-viz plugs into the suite

survey-viz is a middle link in a three-repo pipeline. All coupling is
duck-typed and one-directional; no repo imports another.

```
survey-currents (fetch)  --->  survey-viz (parse + render)  --->  survey-animate (encode)
   GLSEA adapter (v0.2.0+)        VizSpec + frames/manifest        --frames-dir / manifest
```

## survey-currents → survey-viz (data in)

survey-currents fetches field data; survey-viz renders it. The handoff is a
duck-typed **field** object — anything with:

- `.times` — sequence of dates/datetimes/ISO strings
- `.lats`, `.lons` — 1D coordinate arrays
- grid access — a 3D `(ntime, nlat, nlon)` attribute named `values`, `data`,
  `sst`, or `grids`, **or** a per-index method `grid(i)` / `frame(i)` /
  `at(i)` / `get_frame(i)`

…or a plain dict `{"times", "lats", "lons", "values"}` (numpy arrays or lists).

**Non-gridded field shapes.** Some sources are not scalar grids:
- **Storm tracks** (`source="ibtracs"`): an IBTrACS `StormField`
  (or its `to_dict()` form) renders as track polylines.
- **Streamgages** (`source="usgs"`): a USGS `GageField` (or its
  `to_dict()` form) renders as gage markers + hydrograph panel —
  never a scalar grid. See docs/STREAMFLOW.md; the model is shared
  with survey-flood.

survey-viz **never imports survey-currents**. A `GlseaField`-shaped object
satisfies the protocol as-is. If the field's coordinates extend beyond the
spec bbox, the map panel clips to the spec bbox via axis limits.

The **series** (time-series panel) is likewise duck-typed: an object with
`.dates`/`.values` (or `.timestamps`/`.values`), a `{"dates", "values"}`
dict, or `None` — `None` still renders, with a placeholder note in the chart
panel.

Fetchability: SST is fetchable everywhere the gazetteer names. Use
`viz.sources.resolve_source(spec)` — it returns `"glsea"` for SST over
the 5 Great Lakes, `"oisst"` for SST anywhere else, `"mur"` when the
spec pins high resolution, and `""` when no adapter exists (non-SST
variables still raise the honest "no adapter yet" refusal). Then
`viz.sources.fetch_for_source(source)` lazily imports the matching
survey-currents callable (`fetch_glsea_sst` / `fetch_oisst` /
`fetch_mur`) — survey-viz itself never imports survey-currents.
(`viz.gazetteer.is_fetchable(key)` remains as the region-key-only
check, kept for backward compatibility.)

Typical glue (in the caller, not in survey-viz):

```python
from viz import parse_description, render_viz, resolve_source, fetch_for_source
# survey-currents is imported lazily by fetch_for_source, not by survey-viz

spec = parse_description("North Atlantic sea surface temperature over the past 5 years")
source = resolve_source(spec)          # "oisst"
fetch = fetch_for_source(source)       # currents.sst_global.fetch_oisst
field = fetch(spec.bbox, spec.start, spec.end, stride_days=30)
frames, manifest = render_viz(spec, field, None, out_dir="frames")
```

## survey-timescales → survey-viz (smart default time windows)

survey-timescales is the suite's **"when"** engine. survey-viz never
imports it at module load (the suite peer pattern: lazy import,
duck-typed, one-directional, never a hard dependency — install with
`pip install survey-viz[timescales]` for the smart defaults).

In `viz.parser.parse_description`, when `_parse_time` finds no time
phrase, the old hard-coded past-1-year fallback is replaced by
`suggest_window(variable, region=region_key, today=today)`:

- Season language in the description ("fire season", "hurricane
  season", "melt season", ...) requests `intent="season"`; variables
  with no `season` mode fall back to the engine's default mode.
- **Explicit user dates always win** (engine precedence rule #1):
  the engine is never consulted when a time phrase was parsed.
- `variable == "tp"` passes `source=` (the pinned source, or `"era5"`
  for the regional default) so the engine disambiguates ERA5 vs IMERG.
- Peer absent (`ImportError`), unknown variable
  (`UnknownVariableError`), or time-invariant underlay
  (`StaticVariableError` for `bathymetry`/`elevation`) → the old
  past-1-year default; the parser works exactly as before.

The engine's `reason` string is recorded as
`VizSpec.timescale_reason` ("" when the peer was not consulted) and
rides into the frame manifest via `VizSpec.to_dict()` — the window
decision is run provenance, not hidden metadata.

## survey-aesthetics → survey-viz (mapped.earth preset rendering)

survey-aesthetics is the suite's **"look"** engine. survey-viz never
imports it at module load (the suite peer pattern: lazy import inside
the preset branch of `render_viz`, duck-typed, one-directional, never
a hard dependency — install with `pip install "survey-viz[aesthetics]"`,
pinned to the GitHub release since the package is not on PyPI).

`render_viz(..., preset="dark_flow"/"dark_glow"/"paper_prism")` routes
to `viz.aesthetic_render.render_preset_viz`, which follows the exact
call pattern in the engine's `docs/API.md`:

- LIC streaks (`lic_texture`) for vector variables (`currents` with
  u/v/temperature, `wind` with ERA5 u10/v10); fixed reel-wide
  `vmin`/`vmax`/`speed_max` and a fixed LIC seed — never per-frame.
- Event glow (`glow_from_grid` over disk-splatted events) for
  `earthquakes`/`storm-tracks`; cumulative frames.
- Prism extrusion (`prism_frame`) for gridded variables (grids > 44×60
  are NaN-aware downsampled first).
- Rotated framing (`optimal_rotation` / `rotate_frame_fill`):
  the north-up map renders on an enlarged canvas, rotates, and
  center-crops to exactly 1080×1920; titles, legends, and furniture
  are drawn **after** rotation, unrotated.
- Coastlines consume survey-viz's existing underlay segment format
  (`[{"lons": [...], "lats": [...]}]`) — no adapter needed.

The manifest keeps the `survey-viz.frame-manifest/1.0` schema and
records `preset`, `rotation_deg`, `watermark`, `subtitle`, and the
fixed scales, so survey-animate and survey-cache treat preset renders
like any other frame batch.

Two v0.1.0 engine gaps are worked around consumer-side (the release
is never patched): `draw_north_arrow` and `close` are missing from
the engine's top-level namespace despite its `docs/API.md` promising
them — `viz.aesthetic_render._engine()` resolves both from their
submodules. Sparse events are splatted as disks before the engine's
blur passes (single-pixel spikes attenuate to invisibility).

## survey-viz → survey-animate (frames out)

`render_viz` writes a plain directory of PNGs plus `manifest.json`:

```json
{
  "schema": "survey-viz.frame-manifest/1.0",
  "spec": { ... VizSpec.to_dict() ... },
  "frames": ["/abs/path/frame_0001.png", "..."],
  "timestamps": ["2021-09-15", "..."],
  "render": {"layout": "reel-vertical", "style": "reel-dark", "vmin": 0.0,
             "vmax": 25.0, "cmap": "turbo", "n_frames": 60,
             "engine": "survey-viz 0.1.0", "created": "..."}
}
```

survey-animate consumes this via its `--frames-dir` / manifest path, exactly
as it does for survey-flow's `FrameManifest`: sorted `frame_*.png` files,
one frame per timestamp, burned-in dates already on the frames.

```bash
viz demo --out-dir demo-frames
# survey-animate encodes:
animate --frames-dir demo-frames --out reel.mp4   # (survey-animate CLI)
```

## A future app layer → survey-viz (descriptions in)

The deterministic parser is the default path for structured-ish input.
A future app (web UI, chat bot) can:

1. Call `parse_description` first — free, offline, deterministic.
2. On `UnparseableDescription`, either show the message's examples or fall
   back to an LLM that is instructed to emit a `VizSpec` JSON (validated by
   `VizSpec.from_dict`, which rejects anything malformed).
3. Accept a `title=` override for user-edited titles.

The parser's failure message already documents this upgrade path, so the
app layer has a contract to build against.

### Refinements and aesthetic copying (v0.16.0)

Two new engine entry points keep the app layer thin:

- `refine_spec(spec, instruction) -> RefineResult` — plain-language
  edits to an existing spec. `RefineResult.applied` is a list of
  `SpecChange(field, old, new, reason)` the UI renders verbatim;
  `RefineResult.unparsed` carries anything not understood (shown, never
  silently dropped); `RefineResult.cmap` is the requested colormap
  override for the caller's `render_viz(..., cmap=...)` call — it is
  deliberately *not* a `VizSpec` field, so the spec schema stays
  stable. Pure function: no I/O, no network, no global state — safe
  for threads, batch jobs, and future server endpoints.
- `suggest_aesthetic(url_or_bytes) -> AestheticProfile` — color-mood
  analysis of a reference reel URL or uploaded image bytes. The page's
  `og:image`/`twitter:image` thumbnail is fetched (15 s timeout, 5 MB
  cap, http(s) only; the video itself is never downloaded) and
  downsampled to 256 px before analysis, so memory and latency are
  bounded. `AestheticProfile` is plain data (`to_dict()`-serializable)
  with `style`, `colormap` (None for near-grayscale), `palette` hex
  swatches, `brightness`, `dominant_hue`, and `notes` explaining each
  suggestion. Failures raise `AestheticError` with the honest reason.

### Motion refinements and story captions (v0.17.0)

- `RefineResult.motion` — camera-motion intents parsed from the same
  plain-language instruction ("add a slow zoom in", "pan left during
  the video", "no camera motion"). A partial dict
  (`zoom`/`zoom_speed`, `pan`/`pan_speed`, `smooth`/`smooth_steps`) the
  caller merges over its encode-time motion settings (survey-animate's
  `MotionSpec`); never a `VizSpec` field. Thread-safe and pure like the
  rest of `refine_spec`.
- `render_viz(..., story_captions=True)` — data-driven caption events
  from `viz.insights` (stdlib + numpy only), burned in as lower-third
  chips and recorded at `manifest["render"]["story_captions"]`.
  Categorical renderers accept the flag and record it as not-applied.
  Scales with frame count (one pass over the grids); caption
  computation is O(frames) scalars after the render loop.

### Platform canvases from survey-layout (v0.18.0)

`render_viz(..., canvas=None)` accepts a platform canvas from the new
**survey-layout** engine (github.com/crieck2010/survey-layout) —
platform aspect ratios with measured safe zones:

- TikTok / Instagram Reels / YouTube Shorts — 1080×1920, 9:16
- X portrait — 1080×1350, 4:5
- Square — 1080×1080, 1:1
- Widescreen — 1920×1080, 16:9
- Legacy — the historical 1080×1920 geometry (== `canvas=None`)

The handoff is a plain dict (duck-typed — survey-viz never imports
survey-layout):

```python
from viz import render_viz
from layout import build_canvas, to_viz_canvas   # survey-layout, in the caller

canvas = to_viz_canvas(build_canvas("tiktok", flavor="quake"))
# {"platform": "tiktok", "flavor": "quake", "width": 1080, "height": 1920,
#  "regions": {"title": [...], "map": [...], "ranking": [...],
#              "chart": [...], "caption": [...], "footer": [...]},
#  "unsafe": [[0.0, 0.0, 1.0, 0.1875]]}

frames, manifest = render_viz(spec, field, series,
                              out_dir="frames", canvas=canvas)
```

Rules:

- Frame dimensions follow `canvas["width"/"height"]`; every region
  becomes a Matplotlib axes fraction, so arbitrary platform shapes
  work with no engine change.
- All four render paths (continuous, storm tracks, streamgages,
  earthquakes) accept canvases; the earthquake flavor adds the
  `ranking` region (derived between map and chart when absent).
- Malformed canvases raise `ValueError` before any frame renders.
- `canvas=None` (default) is pixel-equivalent to the historical
  layout — legacy behavior is unchanged.
- Manifest records `render.canvas` with platform/flavor/width/height/
  region names for provenance.
- Region rectangles come from survey-layout's `validate_canvas()`:
  inside the canvas, clear of platform chrome, and collision-free
  (except the intentionally floating caption chip). survey-viz
  trusts but re-validates shape on entry (`_resolve_canvas`).
- Dataset selection stays where it belongs: survey-viz's
  `parse_description` picks the dataset/combination; survey-layout
  consumes the resulting render plan (flavor = `"quake"` when the
  parsed variable is earthquakes, `"standard"` otherwise).

## Schema stability

- `survey-viz.frame-manifest/1.0` is frozen for the 0.1.x line. New fields
  may be added; existing fields will not change meaning.
- `VizSpec` validation is strict on read: unknown layouts/styles/cadences
  raise `ValueError` rather than silently degrading.
