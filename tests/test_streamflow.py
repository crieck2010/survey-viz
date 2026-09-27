"""Tests for USGS streamflow routing, parsing, and the gage renderer."""

import datetime as dt
import json

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")

from viz import sources as src_mod
from viz.parser import parse_description
from viz.render import (_VARIABLE_UNITS, _gage_category, _normalize_gage_field,
                        _regional_median_series, _value_percentile,
                        render_viz)
from viz.sources import (SOURCE_LABELS, default_source, explain_source,
                         fetch_for_source, is_fetchable, resolve_source)
from viz.spec import KNOWN_SOURCES, KNOWN_VARIABLES, VizSpec

TODAY = dt.date(2026, 9, 27)
CALI = "Streamflow in California over the past 3 months"


def _parse(text):
    return parse_description(text, today=TODAY)


def _gage_dict(n_gages=2, empty=False, units="native"):
    """A GageField.to_dict()-shaped field (no survey-currents needed)."""
    if empty:
        records = []
    else:
        records = []
        for i in range(n_gages):
            dates = [dt.date(2026, 6, 1) + dt.timedelta(days=k)
                     for k in range(10)]
            vals = [100.0 + 5.0 * k + (3.0 if i else 0.0)
                    for k in range(10)]
            if i == 1:
                vals[4] = None  # an honest gap
            records.append({
                "site_no": f"0415913{i}",
                "site_name": f"TEST RIVER AT SITE {i}",
                "lat": 42.1 + 0.1 * i,
                "lon": -83.28 - 0.1 * i,
                "huc": "04100009",
                "drain_area_sqmi": 120.5,
                "tz": "EST",
                "series": {
                    "00060": {
                        "dates": [d.isoformat() for d in dates],
                        "values": vals,
                        "unit": "m3/s" if units == "si" else "ft3/s",
                        "qualifiers": [["P"]] * 10,
                    }
                },
            })
    return {
        "gage_records": records,
        "bbox": [-83.5, 41.2, -82.8, 43.0],
        "start": "2026-06-01",
        "end": "2026-06-10",
        "parameters": ["00060"],
        "units": units,
        "source": "usgs",
        "provenance": {
            "source": "usgs",
            "n_sites_discovered": n_gages,
            "n_sites_with_data": n_gages,
            "empty_reason": "no USGS streamgages" if empty else None,
        },
    }


def _spec(**kw):
    base = dict(title="t", region_key="lake-erie",
                bbox=(-83.5, 41.2, -78.8, 43.0),
                variable="streamflow", start=dt.date(2026, 6, 1),
                end=dt.date(2026, 6, 10), cadence="daily",
                source="usgs")
    base.update(kw)
    return VizSpec(**base)


# ---------------------------------------------------------------------------
# Parser: streamflow keywords -> usgs
# ---------------------------------------------------------------------------

def test_parse_streamflow_pins_usgs():
    spec = _parse(CALI)
    assert spec.variable == "streamflow"
    assert spec.source == "usgs"
    assert spec.cadence == "daily"
    assert "USGS" in spec.source_reason
    assert resolve_source(spec) == "usgs"
    assert is_fetchable(spec)


def test_parse_keyword_variants():
    for text in (
        "River discharge in California over the past 3 months",
        "Stream discharge in California over the past 3 months",
        "River flow in California over the past 3 months",
        "Gage data in California over the past 3 months",
        "Streamgages in California over the past 3 months",
        "Streamflow in California over the past 3 months",
    ):
        spec = _parse(text)
        assert spec.variable == "streamflow", text
        assert spec.source == "usgs", text


def test_parse_river_flow_not_currents():
    # The broad "currents" patterns (bare "flows?") must not steal this.
    spec = _parse("River flow in California over the past 3 months")
    assert spec.variable == "streamflow"
    assert spec.variable != "currents"


def test_parse_bare_flow_stays_currents():
    # A bare ocean/current "flow" request must NOT route to USGS.
    spec = _parse("Ocean flow patterns in the Gulf of Mexico, 2015 to 2020")
    assert spec.variable == "currents"


def test_parse_flooding_after_heavy_rainfall():
    spec = _parse("Flooding after heavy rainfall in California "
                  "over the past 3 months")
    assert spec.variable == "streamflow"
    assert spec.source == "usgs"
    assert spec.overlays == ("tp",)


def test_parse_flood_without_rainfall_is_not_streamflow():
    # Flood wording alone is not streamflow intent (flood-inundation
    # mapping is survey-flood's imagery domain, not gage discharge).
    spec = _parse("Flooding in California over the past 3 months")
    assert spec.variable != "streamflow"


def test_parse_streamflow_with_rainfall_overlay():
    spec = _parse("Streamflow with heavy rainfall in California "
                  "over the past 3 months")
    assert spec.variable == "streamflow"
    assert spec.overlays == ("tp",)


def test_parse_sea_level_still_refused():
    spec = _parse("Sea level in California over the past 5 years")
    assert spec.variable == "sea-level"
    assert resolve_source(spec) == ""
    assert "altimetry" in explain_source(spec)


# ---------------------------------------------------------------------------
# Sources registry
# ---------------------------------------------------------------------------

def test_known_registries_include_usgs():
    assert "streamflow" in KNOWN_VARIABLES
    assert "usgs" in KNOWN_SOURCES
    assert SOURCE_LABELS["usgs"] == "USGS Water Services (NWIS)"


def test_default_source_streamflow_is_global():
    # NWIS is US-only, but routing is global: bboxes outside USGS
    # coverage yield an honest empty field (never fabricated data).
    assert default_source("streamflow", "california") == "usgs"
    assert default_source("streamflow", "southern-ocean") == "usgs"


def test_usgs_adapter_and_min_version():
    assert src_mod._SOURCE_ADAPTERS["usgs"] == (
        "currents.streamgages", "fetch_usgs")
    assert src_mod._SOURCE_MIN_VERSIONS["usgs"] == "0.13.0"


def test_fetch_for_source_usgs_names_peer_fix(monkeypatch):
    # Environment-independent: simulate a peer without the streamgages
    # module (the real env either has currents installed or not).
    import importlib
    real_import = importlib.import_module

    def fake_import(name, *a, **k):
        if name == "currents.streamgages":
            raise ImportError("No module named 'currents.streamgages' "
                              "(simulated old peer)")
        return real_import(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(ImportError, match="0.13.0"):
        fetch_for_source("usgs")


def test_explain_source_usgs():
    spec = _spec()
    msg = explain_source(spec)
    assert "usgs" in msg and "USGS" in msg


# ---------------------------------------------------------------------------
# Gage model helpers (pure)
# ---------------------------------------------------------------------------

def test_value_percentile():
    vals = np.array([1.0, 2.0, 3.0, 4.0])
    assert _value_percentile(vals, 1.0) == pytest.approx(0.0)
    assert _value_percentile(vals, 4.0) == pytest.approx(100.0)
    assert _value_percentile(vals, 2.5) == pytest.approx(66.6666667)
    assert _value_percentile(np.array([np.nan]), 1.0) is None
    assert _value_percentile(np.array([5.0]), 5.0) == pytest.approx(50.0)
    assert _value_percentile(vals, float("nan")) is None


def test_gage_category():
    assert _gage_category(5.0)[0] == "Much below normal"
    assert _gage_category(50.0)[0] == "Normal"
    assert _gage_category(95.0)[0] == "Much above normal"
    assert _gage_category(None)[0] == "Not ranked"


def test_normalize_gage_field_nan_gaps():
    gages = _normalize_gage_field(_gage_dict(n_gages=2))
    assert len(gages) == 2
    vals = gages[1]["series"]["00060"]["values"]
    assert np.isnan(vals[4])  # the engineered gap stays a gap
    assert vals[0] == pytest.approx(103.0)


def test_regional_median_series():
    gages = _normalize_gage_field(_gage_dict(n_gages=2))
    dates, med = _regional_median_series(
        gages, dt.date(2026, 6, 1), dt.date(2026, 6, 10))
    assert len(dates) == 10
    # Day 0: median of 100 and 103 -> 101.5.
    assert med[0] == pytest.approx(101.5)
    # Day 4: one gage is NaN -> median of the single finite value.
    assert med[4] == pytest.approx(100.0 + 5.0 * 4)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def test_render_gage_markers_and_regional_hydrograph(tmp_path):
    spec = _spec()
    frames, manifest_path = render_viz(
        spec, _gage_dict(n_gages=2), out_dir=str(tmp_path))
    assert len(frames) == 10  # daily cadence: one frame per day
    manifest = json.loads(open(manifest_path).read())
    rend = manifest["render"]
    assert rend["cmap"] == "gage-percentile"
    assert rend["empty"] is False
    assert rend["hydrograph"] == "regional-median"
    assert len(rend["gages"]) == 2
    g0 = rend["gages"][0]
    assert g0["site_no"] == "04159130"
    assert g0["n_values"] == 10
    assert g0["category"] in ("Normal", "Much above normal",
                              "Above normal", "Below normal",
                              "Much below normal")
    assert g0["latest_percentile"] is not None
    assert g0["unit"] == "ft³/s"


def test_render_empty_gages_explicit_message(tmp_path):
    spec = _spec()
    frames, manifest_path = render_viz(
        spec, _gage_dict(empty=True), out_dir=str(tmp_path))
    assert len(frames) == 10
    manifest = json.loads(open(manifest_path).read())
    rend = manifest["render"]
    assert rend["empty"] is True
    assert rend["gages"] == []
    assert "empty_reason" in rend["usgs"]


def test_render_selected_gage_hydrograph(tmp_path):
    spec = _spec()
    spec.gage_site = "04159131"
    frames, manifest_path = render_viz(
        spec, _gage_dict(n_gages=2), out_dir=str(tmp_path))
    manifest = json.loads(open(manifest_path).read())
    assert manifest["render"]["hydrograph"] == "site:04159131"


def test_render_si_units(tmp_path):
    spec = _spec()
    frames, manifest_path = render_viz(
        spec, _gage_dict(n_gages=1, units="si"), out_dir=str(tmp_path))
    manifest = json.loads(open(manifest_path).read())
    assert manifest["render"]["gages"][0]["unit"] == "m³/s"


def test_render_missing_tp_context_recorded_absent(tmp_path):
    spec = _spec(overlays=("tp",))
    frames, manifest_path = render_viz(
        spec, _gage_dict(n_gages=1), out_dir=str(tmp_path))
    manifest = json.loads(open(manifest_path).read())
    assert manifest["render"]["precipitation_context"] == {"tp": "absent"}


def test_render_tp_overlay_drawn_when_present(tmp_path):
    import numpy as _np
    field = _gage_dict(n_gages=1)
    field["overlay_grids"] = {
        "tp": {
            "times": ["2026-06-01", "2026-06-02"],
            "lats": _np.linspace(41.2, 43.0, 10),
            "lons": _np.linspace(-83.5, -82.8, 10),
            "grid": _np.arange(200.0).reshape(2, 10, 10),
        }
    }
    spec = _spec(overlays=("tp",))
    frames, manifest_path = render_viz(spec, field, out_dir=str(tmp_path))
    manifest = json.loads(open(manifest_path).read())
    assert manifest["render"]["precipitation_context"] == {"tp": "ok"}
