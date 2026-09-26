# ARCHITECTURE

## What it is

`survey-viz` is a pure-Python **engine** (no UI-framework imports anywhere
under `src/viz/`) that turns a plain-English description of a map
visualization into a vertical (1080×1920) social-media-ready frame sequence.

```
description --[parser]--> VizSpec --[render]--> frames/*.png + manifest.json
                                ^                     |
                                |                     v
                          (JSON round-trip)    survey-animate encodes reels
```

Zero AI, zero network in the engine. `matplotlib`/`numpy` are **lazy
imports**: `import viz` works without them; only `render_viz` requires them
and raises an informative error otherwise.

## Module map

| module | responsibility |
|---|---|
| `viz/spec.py` | `VizSpec` dataclass: title, region_key, bbox, variable, start/end, cadence, layout (`reel-vertical`), style (`reel-dark`/`light`), optional `vmin`/`vmax`. `to_dict`/`from_dict` + strict validation. |
| `viz/gazetteer.py` | YAML gazetteer loader + `find_region` (case-insensitive, longest match). `is_fetchable(key)` reports the 5 Great Lakes as the only regions with a fetch adapter today. |
| `viz/parser.py` | `parse_description(text) -> VizSpec`. Deterministic, offline, stdlib-only (regex + gazetteer). Raises `UnparseableDescription` with a helpful message. |
| `viz/render.py` | `render_viz(spec, field, series, out_dir, layout, style) -> (frames, manifest_path)`. Duck-typed inputs, fixed colormap scale, burned-in timestamps, playhead-synced series panel. |
| `viz/cli.py` | Thin CLI: `viz parse`, `viz demo`, `viz render`. |

## Key design decisions

1. **Duck-typed peers, never hard imports.** `render_viz` accepts any field
   object with `.times`/`.lats`/`.lons` plus 3D grid access (attribute
   `values`/`data`/`sst`/`grids`, or per-index method `grid`/`frame`/`at`/
   `get_frame`), and plain dicts/numpy bundles. A `GlseaField` from
   survey-currents just works — without importing survey-currents. Same for
   series (`.dates`/`.values`, dict, or `None` → placeholder panel).

2. **Fixed colormap, no flicker.** `vmin`/`vmax` come from the spec when set;
   otherwise they are computed once from the full data range (`nanmin`/
   `nanmax`) and frozen for every frame.

3. **Cadence bucketing.** `monthly` picks the timestep closest to the 15th of
   each month; `yearly` the one closest to Jul 1; `daily` takes all timesteps.
   Everything is capped at `MAX_FRAMES = 240` with even subsampling.

4. **Gazetteer location.** Canonical file is `data/regions.yaml` at the repo
   root; it is a symlink to `src/viz/data/regions.yaml`, which is the copy
   shipped inside the wheel via `package-data`. The loader also honors
   `SURVEY_VIZ_REGIONS` as an override.

5. **Honest fetchability.** Only the 5 Great Lakes have a data adapter today
   (GLSEA in survey-currents). `is_fetchable()` + per-entry `notes` say so
   plainly; the CLI prints a note when you parse a non-fetchable region.

6. **Manifest as contract.** `manifest.json` carries `schema:
   "survey-viz.frame-manifest/1.0"`, the full spec dict, frame paths,
   timestamps, and render params — a plain PNG directory + manifest that
   survey-animate consumes via its `--frames-dir`/manifest path.

## What's deliberately out of scope (v0.1.0)

- Fetching data (survey-currents' job).
- Encoding video (survey-animate's job).
- Coastlines/basemap (no cartopy — fully offline).
- Layouts beyond `reel-vertical`; styles beyond `reel-dark`/`light`.
