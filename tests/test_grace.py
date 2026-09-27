"""Tests for GRACE water-storage routing, parsing, and rendering."""

import datetime as dt
import warnings

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")

from viz import sources as src_mod
from viz.parser import parse_description
from viz.render import _VARIABLE_CMAPS, _VARIABLE_UNITS, render_viz
from viz.sources import (SOURCE_LABELS, default_source, explain_source,
                         resolve_source)
from viz.spec import KNOWN_SOURCES, KNOWN_VARIABLES, VizSpec

TODAY = dt.date(2026, 9, 27)
CALI = "Groundwater decline in California over the past 5 years"


def _parse(text):
    return parse_description(text, today=TODAY)


# ---------------------------------------------------------------------------
# Parser: water-storage -> GRACE
# ---------------------------------------------------------------------------

def test_parse_groundwater_pins_grace():
    spec = _parse(CALI)
    assert spec.variable == "water-storage"
    assert spec.source == "grace"
    assert spec.cadence == "monthly"
    assert "GRACE" in spec.source_reason
    assert resolve_source(spec) == "grace"


def test_parse_total_water_storage_since_2002():
    spec = _parse("Total water storage across California since 2002")
    assert spec.variable == "water-storage"
    assert spec.source == "grace"


def test_parse_keyword_variants():
    for text in (
        "Aquifer depletion in California over the past 10 years",
        "TWS anomalies across California since 2002",
        "Drought in California over the past 5 years",
        "GRACE-FO water storage in California over the past 5 years",
        "Terrestrial water changes in California over the past 5 years",
        "Total water storage in California over the past 5 years",
    ):
        spec = _parse(text)
        assert spec.variable == "water-storage", text
        assert spec.source == "grace", text


def test_parse_water_storage_always_monthly():
    spec = _parse("Daily groundwater changes in California over the past 3 months")
    assert spec.variable == "water-storage"
    assert spec.cadence == "monthly"


def test_parse_groundwater_vs_rainfall_adds_tp_overlay():
    spec = _parse(
        "Groundwater decline vs rainfall in California over the past 5 years")
    assert spec.variable == "water-storage"
    assert spec.source == "grace"
    assert spec.overlays == ("tp",)


def test_parse_precipitation_never_routes_to_grace():
    spec = _parse("Recent satellite rainfall over the Gulf of Mexico")
    assert spec.variable == "tp"
    assert spec.source == "imerg"
    assert resolve_source(spec) == "imerg"


def test_parse_era5_precipitation_stays_off_grace():
    spec = _parse("Precipitation trends in California since 1980")
    assert spec.variable == "tp"
    assert resolve_source(spec) == "era5"


# ---------------------------------------------------------------------------
# Parser: honest refusals (streamflow / sea-level)
# ---------------------------------------------------------------------------

def test_parse_river_discharge_is_refusal():
    spec = _parse("River discharge in California this year")
    assert spec.variable == "streamflow"
    assert resolve_source(spec) == ""
    msg = explain_source(spec)
    assert "no source" in msg
    assert "streamflow" in msg or "streamgage" in msg


def test_parse_sea_level_is_refusal_not_grace():
    spec = _parse("Sea level in California over the past 5 years")
    assert spec.variable == "sea-level"
    assert resolve_source(spec) == ""
    msg = explain_source(spec)
    assert "no source" in msg
    assert "altimetry" in msg
    assert "grace" not in resolve_source(spec)


def test_parse_sea_level_pressure_stays_msl():
    spec = _parse("Sea level pressure in California over the past 5 years")
    assert spec.variable == "msl"
    assert resolve_source(spec) == "era5"


def test_parse_sea_level_rise_is_refusal():
    spec = _parse("Sea level rise in California over the past 5 years")
    assert spec.variable == "sea-level"
    assert resolve_source(spec) == ""


# ---------------------------------------------------------------------------
# Sources registry
# ---------------------------------------------------------------------------

def test_known_registries_include_grace():
    assert "water-storage" in KNOWN_VARIABLES
    assert "streamflow" in KNOWN_VARIABLES
    assert "sea-level" in KNOWN_VARIABLES
    assert "grace" in KNOWN_SOURCES
    assert SOURCE_LABELS["grace"] == "CSR GRACE/GRACE-FO RL06.3"


def test_default_source_water_storage_is_global():
    assert default_source("water-storage", "california") == "grace"
    assert default_source("water-storage", "southern-ocean") == "grace"


def test_grace_adapter_and_min_version():
    assert src_mod._SOURCE_ADAPTERS["grace"] == ("currents.grace", "fetch_grace")
    assert src_mod._SOURCE_MIN_VERSIONS["grace"] == "0.12.0"


def test_spec_accepts_grace_and_tp_overlay():
    spec = VizSpec(title="t", region_key="california",
                   bbox=(-125.0, 30.0, -110.0, 45.0),
                   variable="water-storage", start=dt.date(2020, 1, 1),
                   end=dt.date(2020, 12, 31), cadence="monthly",
                   source="grace", overlays=("tp",))
    assert resolve_source(spec) == "grace"


def test_explain_refusals_name_the_gap():
    for variable, needle in (("streamflow", "streamgage"),
                             ("sea-level", "altimetry")):
        spec = VizSpec(title="t", region_key="california",
                       bbox=(-125.0, 30.0, -110.0, 45.0),
                       variable=variable, start=dt.date(2020, 1, 1),
                       end=dt.date(2020, 12, 31), cadence="monthly")
        assert resolve_source(spec) == ""
        assert needle in explain_source(spec)


# ---------------------------------------------------------------------------
# Renderer: diverging anomaly scale + gap frames
# ---------------------------------------------------------------------------

BBOX = (-125.0, 30.0, -110.0, 45.0)


def _water_field(n_months=4, gap=(), seed=11):
    rng = np.random.default_rng(seed)
    lats = np.linspace(30.0, 45.0, 8)
    lons = np.linspace(-125.0, -110.0, 8)
    times = [dt.date(2020, m, 1) for m in range(1, n_months + 1)]
    values = rng.normal(0.0, 4.0, size=(n_months, len(lats), len(lons)))
    for m in gap:
        values[m - 1] = np.nan
    with warnings.catch_warnings():
        # gap frames are all-NaN by design; ignore "Mean of empty slice"
        warnings.simplefilter("ignore", RuntimeWarning)
        series = {"dates": times,
                  "values": [float(np.nanmean(values[i]))
                             for i in range(n_months)]}
    return {"times": times, "lats": lats, "lons": lons, "values": values}, series


def _water_spec(**over):
    kw = dict(title="California — Terrestrial Water Storage, 2020",
              region_key="california", bbox=BBOX, variable="water-storage",
              start=dt.date(2020, 1, 1), end=dt.date(2020, 4, 30),
              cadence="monthly", source="grace",
              source_reason="water storage / groundwater description -> "
                            "CSR GRACE/GRACE-FO RL06.3")
    kw.update(over)
    return VizSpec(**kw)


def test_water_storage_units_and_cmap():
    assert _VARIABLE_UNITS["water-storage"] == "cm"
    assert _VARIABLE_CMAPS["water-storage"] == "BrBG"


def test_water_storage_scale_is_zero_centered(tmp_path):
    field, series = _water_field()
    _, manifest_path = render_viz(_water_spec(), field, series,
                                  underlay=False, out_dir=tmp_path / "f")
    import json
    manifest = json.loads((tmp_path / "f" / "manifest.json").read_text())
    vmin, vmax = manifest["render"]["vmin"], manifest["render"]["vmax"]
    assert vmin == pytest.approx(-vmax)
    assert vmin < 0 < vmax


def test_water_storage_explicit_vmin_vmax_win(tmp_path):
    field, series = _water_field()
    _, manifest_path = render_viz(_water_spec(vmin=-10.0, vmax=10.0), field,
                                  series, underlay=False,
                                  out_dir=tmp_path / "f")
    import json
    manifest = json.loads((tmp_path / "f" / "manifest.json").read_text())
    assert manifest["render"]["vmin"] == -10.0
    assert manifest["render"]["vmax"] == 10.0


def test_gap_month_frame_marked_and_listed(tmp_path):
    field, series = _water_field(n_months=4, gap=(2,))
    frames, _ = render_viz(_water_spec(), field, series,
                           underlay=False, out_dir=tmp_path / "f")
    assert len(frames) == 4  # gap month keeps its frame: never dropped
    import json
    manifest = json.loads((tmp_path / "f" / "manifest.json").read_text())
    assert manifest["render"]["gap_months"] == ["2020-02-01"]
    assert "2004" in manifest["render"]["anomaly_baseline"]


def test_manifest_baseline_absent_for_other_variables(tmp_path):
    lats = np.linspace(30.0, 45.0, 8)
    lons = np.linspace(-125.0, -110.0, 8)
    times = [dt.date(2020, m, 1) for m in range(1, 4)]
    values = np.random.default_rng(3).normal(2.0, 0.5, (3, 8, 8))
    field = {"times": times, "lats": lats, "lons": lons, "values": values}
    series = {"dates": times, "values": [2.0, 2.1, 1.9]}
    spec = _water_spec(variable="tp", title="t")
    _, _ = render_viz(spec, field, series, underlay=False,
                      out_dir=tmp_path / "f")
    import json
    manifest = json.loads((tmp_path / "f" / "manifest.json").read_text())
    assert manifest["render"]["anomaly_baseline"] is None
    assert manifest["render"]["gap_months"] is None


def test_water_storage_with_tp_overlay_renders(tmp_path):
    field, series = _water_field()
    nt, ny, nx = field["values"].shape
    tp = np.abs(np.random.default_rng(5).normal(50, 10, (nt, ny, nx)))
    field["overlay_grids"] = {"tp": tp}
    frames, _ = render_viz(_water_spec(overlays=("tp",)), field, series,
                           underlay=False, out_dir=tmp_path / "f")
    assert len(frames) == 4
