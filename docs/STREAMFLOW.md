# STREAMFLOW — USGS streamgage rendering (survey-viz v0.12.0+)

This module renders **USGS Water Services (NWIS)** streamgage data:
daily MEAN values of river discharge (`00060`, ft³/s) and gage height
(`00065`, ft) measured at individual monitoring stations. The data
come from `currents.streamgages.fetch_usgs` (survey-currents v0.13.0+),
which hits the public NWIS endpoints with **no API key**.

The parser (see docs/PARSER.md §6e) routes descriptions like
"Streamflow …", "River discharge …", "Stream discharge …",
"River flow …", "Gage data …", and "Streamgages …" to
`variable="streamflow"`, `source="usgs"`, `cadence="daily"`, with an
inspectable `source_reason`. The keyword block is pre-checked before
the earliest-wins race so river-flow requests never land on ocean
currents; a bare ocean/current "flow" request still routes to
`currents`.

## Point/time-series, not a scalar grid

Streamgages are **point observations with time series**, not gridded
fields. The renderer therefore runs a dedicated path — a `GageField`
is never converted into a scalar grid:

- **Gage markers** over the GEBCO/Natural Earth underlay, sized by
  discharge (or fixed size when the selected parameter is gage
  height / a gage has no current value) and **colored by the current
  discharge percentile category** (USGS WaterWatch-style classes):
  *Much below normal* (0–10), *Below normal* (10–25), *Normal*
  (25–75), *Above normal* (75–90), *Much above normal* (90–100),
  and *Not ranked* (gray) when the record has no finite discharge
  value. The percentile ranks the gage's current value within its
  own retrieved record: `100 * (# values below) / (n - 1)`; a
  single-value record ranks 50. Legend in the top-right.
- **Hydrograph panel** (bottom strip):
  - if the spec carries `gage_site` (a NWIS site number, set by a
    caller such as the Reel Studio pipeline), the hydrograph is that
    gage's full daily record;
  - otherwise it is the **documented regional-median rule**: the
    daily median across all gages with finite values that day.
    The panel title and `manifest["render"]["hydrograph"]` always
    state which rule produced the series, so there is no ambiguity.
  - Missing daily values are **gaps**, never interpolated or filled.
- **Precipitation context** (`tp` overlay from "with rainfall" or
  flood-plus-rain wording): best-effort contours from
  `overlay_grids`; when unavailable, the frame still renders and the
  manifest records `"precipitation_context": {"tp": "absent"}` —
  the failure is declared, never silently dropped.
- **Units**: marker/hydrograph units follow the field's unit system
  (`ft³/s` native, `m³/s` after `GageField.to_si()`); they are
  recorded in `manifest["render"]["gages"][i]["unit"]`.

## Empty results

NWIS is **US-only**: a bbox with no USGS gages (or a window with no
daily values) returns an empty field. The renderer does not fabricate
data — it draws one frame per requested day with an explicit
"No USGS streamgages with daily data in this window" message, and
`manifest["render"]` records `empty: true` with the field's
`empty_reason` (always present on empty fields from
survey-currents v0.13.0+).

## Manifest contract

`manifest["render"]` carries:

- `"cmap": "gage-percentile"`, `"empty": bool`,
- `"gages"`: per-gage `{site_no, site_name, lat, lon,
  latest_value, latest_percentile, category, n_values, unit}`,
- `"hydrograph"`: `"site:<site_no>"` or `"regional-median"`,
- `"precipitation_context"`: per-overlay `"drawn"` / `"absent"`,
- `"usgs"`: the field's full NWIS provenance (URLs, retrieval time,
  parameters, cache state).

## Survey-flood reuse

`currents.streamgages.GageField` is the shared streamgage model:
survey-flood consumes it for its fluvial context, and survey-viz
renders it — the same object, no duplication. The gage-renderer
accepts any `GageField`-shaped mapping (duck-typed via
`GageField.to_dict()`), so nothing in survey-viz imports
survey-currents.

## Honest limitations

- NWIS is US-only; non-US bboxes return an honest empty field.
- Daily MEAN values (statistic `00003`); real-time / instantaneous
  unit values are not covered.
- Percentiles rank within the retrieved window's record, not
  long-term historical flow-duration curves — categories describe
  the window, not climatology.
- Gage inventory comes from the site service `siteOutput=expanded`
  (includes drainage area); inventory and daily values are cached
  for 7 days with SHA-256 verification in survey-currents.
