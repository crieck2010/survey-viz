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

## Schema stability

- `survey-viz.frame-manifest/1.0` is frozen for the 0.1.x line. New fields
  may be added; existing fields will not change meaning.
- `VizSpec` validation is strict on read: unknown layouts/styles/cadences
  raise `ValueError` rather than silently degrading.
