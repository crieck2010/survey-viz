# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-26

### Added
- `VizSpec` dataclass (`src/viz/spec.py`): title, region_key, bbox, variable,
  start/end, cadence, layout (`reel-vertical`), style (`reel-dark`/`light`),
  optional `vmin`/`vmax`; `to_dict`/`from_dict` + strict validation.
- Built-in YAML gazetteer (`data/regions.yaml` + `src/viz/gazetteer.py`):
  25 named regions — the 5 Great Lakes plus 20 coastal regions/bays/seas —
  each with key, display name, aliases, bbox, and a data-availability note.
  `is_fetchable()` reports only the 5 Great Lakes as fetchable today (GLSEA
  adapter in survey-currents); other regions parse fine but fetching raises a
  clear "no adapter yet" error.
- Deterministic offline description parser (`src/viz/parser.py`):
  `parse_description(text) -> VizSpec`, stdlib-only (regex + gazetteer).
  Region matching (case-insensitive, longest match wins), variable keywords
  (earliest-in-text wins; default `sst`), time phrases (`past N years`,
  `last summer`, `this year`, explicit ranges, `since YYYY`; default past
  1 year), derived titles. `UnparseableDescription` with helpful message,
  working examples, and LLM-upgrade-path note.
- Renderer (`src/viz/render.py`): `render_viz(spec, field, series, out_dir)`
  → `(frames, manifest_path)`. Duck-typed field/series inputs (dicts, numpy,
  or GlseaField-shaped objects; never imports survey-currents). 1080×1920
  `reel-vertical` layout: title block, pcolormesh map panel with fixed
  vmin/vmax (no colormap flicker) and burned-in timestamp, time-series panel
  with playhead line + dot. `reel-dark` default, `light` option. matplotlib
  and numpy are lazy imports. Writes `manifest.json` with schema
  `survey-viz.frame-manifest/1.0`, directly consumable by survey-animate.
- Thin CLI (`src/viz/cli.py`): `viz parse`, `viz demo` (synthetic reel,
  fully offline), `viz render --spec --field-npz [--series-csv]`.
- Docs: README, docs/PARSER.md (full grammar), docs/ARCHITECTURE.md,
  docs/INTEROP.md (survey-currents/survey-animate/future-app wiring).
- 65 pytest tests, fully offline (render tests skip if matplotlib missing).
