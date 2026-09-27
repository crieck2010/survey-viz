# EARTHQUAKES — USGS Earthquake Catalog rendering (survey-viz v0.14.0+)

This module renders **USGS Earthquake Catalog (ComCat)** seismic events:
earthquakes, quarry blasts, and other catalogued seismic events as
**point markers**, not a scalar grid. The data come from
`currents.earthquakes.fetch_earthquakes` (survey-currents v0.15.0+),
which hits the public **ComCat FDSN event web service** with **no API
key**:

- Endpoint: `https://earthquake.usgs.gov/fdsnws/event/1/query`
- `format=geojson`; parameters `starttime`, `endtime`, `minlatitude`,
  `maxlatitude`, `minlongitude`, `maxlongitude`, `minmagnitude`,
  `limit`, `offset`.

The parser (see docs/PARSER.md §6f) routes descriptions like
"Earthquakes …", "Quakes …", "Seismic activity …", "Tremors …",
"Aftershocks …", "Foreshocks …", and **"Seismic hazard …"** to
`variable="earthquakes"`, `source="comcat"`, `cadence="daily"`, with an
inspectable `source_reason`. The earthquake keyword block is
pre-checked before the earliest-wins race: the `bathymetry` group's
bare "depth"/"depths" pattern would otherwise win on phrasing like
"The depth of the earthquake swarm …" (quake depth is hypocentral,
not seafloor). "earthquakes and topography" wording keeps
`variable="earthquakes"` — topographic context is the default GEBCO
underlay beneath every quake render (no separate fetch), and the
`source_reason` says so.

## Access truth

The access facts below are per the survey-currents v0.15.0 build notes
(verified live against the ComCat FDSN service 2026-09-27):

- **Keyless.** No API key, token, or Earthdata login; plain HTTPS GET.
- **Default FDSN limit is 20000** events per response. Verified live:
  global M4+ over one week = **179 events** in a single response;
  global M0+ over one week = **1906 events** in a single response —
  both comfortably under the default limit, so routine reels never
  page.
- **Pagination:** when responses do page, the GeoJSON metadata swaps
  `count` for `limit`/`offset` fields; the fetch layer follows them
  (per the peer contract).
- **No rate-limit headers observed** on the live checks.
- **`fetch_earthquakes(bbox, start, end)`** is called positionally
  (like `fetch_oceancolor`), with keyword args `min_magnitude=...`,
  `event_type=...` (`None` for all types), `page_size=...`,
  `cache_dir=...`.

## What the catalog is and is not

- **Observed events, NOT a forecast hazard model.** This is the
  central honesty rule: "seismic hazard" descriptions render the
  *observed* ComCat catalog, never a hazard forecast. Every
  earthquakes manifest records the catalog note
  ("USGS Earthquake Catalog (ComCat) is a catalog of observed events,
  not a forecast hazard model"), and the frame footer reads "…
  observed events — not a forecast".
- **Magnitude of completeness.** The catalog is complete only above
  its magnitude of completeness (roughly M4.5+ globally, lower in
  well-instrumented regions like California/Japan). Small quakes are
  missing unevenly — a sparse map means "sparse catalog", not "no
  shaking".
- **`event_type` includes non-tectonic events.** ComCat catalogues
  quarry blasts, mining explosions, and other non-tectonic sources
  alongside earthquakes. The renderer shows `event_type` honestly in
  the largest-events readout (e.g. "M6.3 · deep event · 650 km deep
  [quarry blast]") — it does not re-label them as earthquakes.
- **Hypocentral depth, not seafloor depth.** Marker color is the
  *hypocenter* depth bin (shallow <70 km / intermediate 70–300 km /
  deep ≥300 km); the GEBCO tint beneath is topographic context, not
  the quake's depth.

## Rendering

- **Markers:** one per event at its (lon, lat). **Area** (points²)
  follows the documented power law `80 × 10**(0.75·(M − 4))` — each
  +1 magnitude unit multiplies marker area by ≈5.62 (a damped visual
  proxy for seismic-moment scaling, which would let great quakes
  swallow the map). Events with no reported magnitude draw at a fixed
  small size. **Color** is the hypocentral-depth bin: shallow
  (#ffd166), intermediate (#f4845f), deep (#e5383b), unknown
  (#9aa3b2). Largest markers draw first so small ones stay visible.
- **Frames are CUMULATIVE:** frame *n* shows every event with
  `time <= frame_date`. A swarm unfolds over the reel; a quake stays
  visible once it has happened. One frame per day (`cadence="daily"`).
- **Panels:** the map panel (markers over GEBCO tint + Natural Earth
  coastlines, magnitude marker chips M4–M7, depth-bin legend,
  burned-in timestamp, cumulative count readout), the largest-events
  ranking readout (top 5 by magnitude, with place + depth), and the
  daily event-count time series with a cumulative playhead (mirrors
  the gage hydrograph approach).
- **Empty field:** an explicit "No earthquakes in this catalog
  window" message (with the catalog's `empty_reason` when present) —
  never fabricated markers. The manifest records `"empty": true` and
  still carries the catalog honesty note.
- **Manifest** (`render` block): `n_events`, `daily_counts`,
  `largest` (top 5 with event_id/time/lat/lon/depth_km/magnitude/
  mag_type/place/event_type), `min_magnitude`, `event_type`,
  `skipped_no_time`, `cumulative: true`, `catalog_note`,
  `comcat` (the field provenance, incl. request URLs), and the
  underlay status.
- **Footer:** "N events · largest M{x} · observed events — not a
  forecast".
