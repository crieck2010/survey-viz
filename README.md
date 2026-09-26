# survey-viz

Turn a plain-English description of a map visualization into a vertical
(1080×1920) social-media-ready frame sequence.

Three stages, **zero AI, zero network** in the engine:

1. **Parse** — deterministic offline description → spec (`viz parse`)
2. **Spec** — `VizSpec`: the validated contract between parser, renderer, and peers
3. **Render** — spec + data → PNG frames + `manifest.json` (`viz demo`, `viz render`)

Peers are optional and duck-typed: `survey-currents` feeds it field data,
`survey-animate` encodes its frames into MP4/GIF/WebM reels. Never hard
imports, never hard dependencies.

## Install

```bash
pip install survey-viz            # parser + spec + gazetteer (stdlib + pyyaml)
pip install "survey-viz[render]"  # + numpy/matplotlib for rendering
```

Or from source:

```bash
git clone https://github.com/crieck2010/survey-viz
cd survey-viz
pip install -e ".[render,test]"
```

## Quickstart

```bash
# Parse a description into a VizSpec (prints JSON)
viz parse "Lake Superior surface temperature over the past 5 years"

# Render a small synthetic vertical reel, fully offline
viz demo --out-dir viz-demo-frames

# Render from your own data
viz parse "Gulf of Mexico currents, 2015 to 2020" > spec.json
viz render --spec spec.json --field-npz field.npz --series-csv series.csv --out-dir frames
# then: survey-animate --frames-dir frames   (encodes the reel)
```

`field.npz` must contain arrays `times` (ISO date strings), `lats`, `lons`,
and `values` shaped `(ntime, nlat, nlon)`. `series.csv` has `date,value`
columns (optional — the chart panel shows a placeholder note without it).

## API overview

```python
from viz import parse_description, render_viz, VizSpec

spec = parse_description("Chlorophyll in Puget Sound last summer")
print(spec.title)   # "Puget Sound — Chlorophyll-a, 2026"
print(spec.bbox)    # (-123.5, 47.0, -122.0, 48.8)

# field: duck-typed — dict, numpy bundle, or a GlseaField-shaped object
# (has .times/.lats/.lons + 3D grid access). Never imports survey-currents.
frames, manifest = render_viz(spec, field, series, out_dir="frames")
```

## Parser rules (summary)

- **Region**: gazetteer display names/aliases, case-insensitive, longest match wins.
  33 regions: the 5 Great Lakes + 20 coastal regions/bays/seas + 8 ocean
  basins (`data/regions.yaml`).
- **Variable**: `temperature/sst/thermal/warmth/cold` → `sst`;
  `current/flow/stream/velocity` → `currents`; `chlorophyll/chl/algae/bloom` →
  `chlorophyll`. Earliest keyword in the text wins; **no keyword → `sst`**.
- **Source pinning** (new in v0.2.0): `high resolution` / `ultra` /
  `coastal detail` / `1 km` + SST → `source="mur"` (NASA JPL MUR v4.1,
  ~1 km). Otherwise `source` stays empty and
  `viz.sources.resolve_source` picks the regional default: Great Lakes
  SST → `glsea`, other SST → `oisst`.
- **Time**: `past N years`, `last N years`, `past N months`, `last summer`
  (most recent fully-completed Jun–Aug), `this year`, `2015 to 2020` /
  `2015-2020`, `since 2018`. **No time phrase → past 1 year.**
- Anything else raises `UnparseableDescription` with a helpful message,
  2–3 working examples, and a note about the future LLM upgrade path.

Full grammar: [docs/PARSER.md](docs/PARSER.md).

## Honest limitations

- **Fetchable for SST**: the 5 Great Lakes via GLSEA, everything else
  via OISST (default) or MUR (high-resolution requests) — all through
  `survey-currents` (v0.3.0+). `currents` and `chlorophyll` variables
  still have no fetch adapter: fetching them raises a clear "no adapter
  yet" error. See `viz.sources.resolve_source()` and the `notes` field
  in `data/regions.yaml`.
- The map panel is a plain `pcolormesh` over the region bbox — no coastlines
  (no cartopy dependency, fully offline).
- Research/offline tool: synthetic demo data is clearly synthetic.

## Docs

- [docs/PARSER.md](docs/PARSER.md) — the full deterministic grammar
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map and design decisions
- [docs/INTEROP.md](docs/INTEROP.md) — how survey-currents / survey-animate /
  a future app plug in
- [CHANGELOG.md](CHANGELOG.md)

## License

MIT — see [LICENSE](LICENSE).
