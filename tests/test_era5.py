"""Tests for the ERA5 atmosphere variables (v0.3.0).

Covers the parser routing for wind/msl/t2m/tp, the storm combination
rule (wind + msl isobar overlay), the VizSpec.overlays field, ERA5
source routing, and contour-overlay rendering. Fully offline — no CDS
calls, no matplotlib requirement for the non-render tests.
"""

import datetime as dt

import pytest

from viz.parser import parse_description
from viz.spec import VizSpec
from viz.sources import (
    ERA5_VARIABLES,
    SOURCE_LABELS,
    default_source,
    fetch_for_source,
    is_fetchable,
    resolve_source,
)

TODAY = dt.date(2026, 9, 26)


# --- parser routing ----------------------------------------------------------


def test_wind_routes_to_era5():
    spec = parse_description("North Atlantic winds over the past year", today=TODAY)
    assert spec.variable == "wind"
    assert spec.source == ""
    assert spec.overlays == ()
    assert resolve_source(spec) == "era5"


def test_storm_maps_to_wind_with_msl_overlay():
    spec = parse_description(
        "Hurricane conditions in the Gulf of Mexico last summer", today=TODAY)
    assert spec.variable == "wind"
    assert spec.overlays == ("msl",)
    assert spec.region_key == "gulf-of-mexico"
    assert resolve_source(spec) == "era5"


@pytest.mark.parametrize("word", ["storm", "cyclone", "typhoon"])
def test_storm_synonyms(word):
    spec = parse_description(
        f"{word.title()} winds in the North Atlantic last summer", today=TODAY)
    assert spec.variable == "wind"
    assert spec.overlays == ("msl",)


def test_air_temperature_routes_to_t2m():
    spec = parse_description(
        "Air temperature across the US East Coast this year", today=TODAY)
    assert spec.variable == "t2m"
    assert spec.region_key == "us-east-coast"
    assert resolve_source(spec) == "era5"


def test_heat_disambiguates_to_t2m_not_sst():
    spec = parse_description(
        "Heatwave over the Bay of Bengal last summer", today=TODAY)
    assert spec.variable == "t2m"
    assert resolve_source(spec) == "era5"


def test_pressure_routes_to_msl():
    spec = parse_description(
        "Sea-level pressure over the North Pacific over the past year", today=TODAY)
    assert spec.variable == "msl"
    assert resolve_source(spec) == "era5"


def test_rain_routes_to_tp():
    spec = parse_description(
        "Rainfall over the North Atlantic over the past year", today=TODAY)
    assert spec.variable == "tp"
    assert resolve_source(spec) == "era5"


def test_sst_still_routes_to_oisst_not_era5():
    spec = parse_description(
        "Global ocean temperature over the past 5 years", today=TODAY)
    assert spec.variable == "sst"
    assert resolve_source(spec) == "oisst"


def test_warmth_stays_sst_backward_compatible():
    spec = parse_description("Superior warmth past 2 years", today=TODAY)
    assert spec.variable == "sst"
    assert resolve_source(spec) == "glsea"


def test_earliest_keyword_wins_storm_vs_pressure():
    # "pressure" first -> plain msl, no overlay from the later "hurricane".
    spec = parse_description(
        "Pressure falls as the hurricane nears the Gulf of Mexico last summer",
        today=TODAY)
    assert spec.variable == "msl"
    assert spec.overlays == ()
    # "hurricane" first -> the storm combination.
    spec2 = parse_description(
        "Hurricane pressure drops over the Gulf of Mexico last summer",
        today=TODAY)
    assert spec2.variable == "wind"
    assert spec2.overlays == ("msl",)


def test_era5_parse_is_deterministic():
    text = "Hurricane conditions in the Gulf of Mexico last summer"
    assert parse_description(text, today=TODAY) == parse_description(text, today=TODAY)


# --- VizSpec.overlays --------------------------------------------------------


def _base_spec_kwargs():
    return dict(
        title="t",
        region_key="gulf-of-mexico",
        bbox=(-98.0, 18.0, -80.0, 31.0),
        variable="wind",
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        cadence="monthly",
    )


def test_overlays_default_empty_and_backward_compatible():
    spec = VizSpec(**_base_spec_kwargs())
    assert spec.overlays == ()


def test_overlays_canonicalized_to_tuple():
    spec = VizSpec(**_base_spec_kwargs(), overlays=["msl"])
    assert spec.overlays == ("msl",)


def test_overlays_round_trip():
    spec = VizSpec(**_base_spec_kwargs(), overlays=["msl"])
    assert VizSpec.from_dict(spec.to_dict()) == spec


def test_overlays_absent_in_old_dicts():
    d = _base_spec_kwargs()
    d = {**d, "cadence": "monthly", "layout": "reel-vertical", "style": "reel-dark",
         "source": "", "vmin": None, "vmax": None}
    assert VizSpec.from_dict(d).overlays == ()


def test_overlay_duplicating_variable_rejected():
    kw = _base_spec_kwargs()
    kw["variable"] = "msl"
    with pytest.raises(ValueError):
        VizSpec(**kw, overlays=["msl"])


def test_unknown_overlay_rejected():
    with pytest.raises(ValueError):
        VizSpec(**_base_spec_kwargs(), overlays=["sst"])


def test_too_many_overlays_rejected():
    with pytest.raises(ValueError):
        VizSpec(**_base_spec_kwargs(), overlays=["msl", "t2m"])


# --- source routing ----------------------------------------------------------


def test_default_source_era5_for_all_era5_variables_any_region():
    for variable in ERA5_VARIABLES:
        assert default_source(variable, "gulf-of-mexico") == "era5"
        assert default_source(variable, "lake-superior") == "era5"
        assert default_source(variable, "north-atlantic") == "era5"


def test_era5_label():
    assert SOURCE_LABELS["era5"] == "Copernicus ERA5 (CDS)"


def test_is_fetchable_era5():
    spec = VizSpec(**_base_spec_kwargs(), overlays=["msl"])
    assert is_fetchable(spec)


def test_fetch_for_source_era5_resolves_real_adapter():
    # survey-currents 0.4.0+ is installed in this env: the lazy import must
    # resolve to the real fetch_era5 callable (validates the module path
    # in _SOURCE_ADAPTERS end to end).
    import inspect

    import currents.era5

    fn = fetch_for_source("era5")
    assert fn is currents.era5.fetch_era5
    assert "stride_hours" in inspect.signature(fn).parameters


# --- region ------------------------------------------------------------------


def test_us_east_coast_region():
    from viz.gazetteer import get_region
    region = get_region("us-east-coast")
    assert region is not None
    assert region["bbox"] == (-82.0, 25.0, -65.0, 45.0)


# --- renderer overlays (matplotlib required) ---------------------------------


matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")

from viz.render import render_viz  # noqa: E402


class _FakeEra5Field:
    """Era5Field-shaped duck-type: .values base grid + .overlay_grids."""

    def __init__(self, n=3):
        rng = np.random.default_rng(11)
        self.lats = np.linspace(18.0, 31.0, 12)
        self.lons = np.linspace(-98.0, -80.0, 16)
        self.times = [dt.date(2024, 1, d) for d in (6, 16, 26)][:n]
        u = rng.normal(4, 3, size=(n, len(self.lats), len(self.lons)))
        v = rng.normal(1, 2, size=(n, len(self.lats), len(self.lons)))
        self._u, self._v = u, v
        self._msl = rng.normal(1013, 8, size=(n, len(self.lats), len(self.lons)))

    @property
    def values(self):  # render-ready base grid: wind speed, m/s
        return np.hypot(self._u, self._v)

    @property
    def overlay_grids(self):
        return {"msl": self._msl}


def test_render_wind_with_msl_overlay(tmp_path):
    spec = VizSpec(
        title="Gulf of Mexico — 10-m Wind, 2024",
        region_key="gulf-of-mexico",
        bbox=(-98.0, 18.0, -80.0, 31.0),
        variable="wind",
        overlays=["msl"],
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        cadence="daily",
    )
    field = _FakeEra5Field()
    frames, manifest_path = render_viz(spec, field, None, out_dir=tmp_path / "f")
    assert len(frames) == 3
    for f in frames:
        assert __import__("pathlib").Path(f).stat().st_size > 0


def test_render_missing_overlay_raises():
    spec = VizSpec(
        title="t",
        region_key="gulf-of-mexico",
        bbox=(-98.0, 18.0, -80.0, 31.0),
        variable="wind",
        overlays=["msl"],
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        cadence="monthly",
    )
    field = {
        "times": [dt.date(2024, 1, 6)],
        "lats": np.linspace(18.0, 31.0, 4),
        "lons": np.linspace(-98.0, -80.0, 5),
        "values": np.ones((1, 4, 5)),
        # no "overlay_grids" key -> must raise, never silently drop
    }
    with pytest.raises(ValueError, match="overlay"):
        render_viz(spec, field, None, out_dir="/tmp/nope")


def test_render_overlay_all_nan_skipped(tmp_path):
    spec = VizSpec(
        title="t",
        region_key="gulf-of-mexico",
        bbox=(-98.0, 18.0, -80.0, 31.0),
        variable="wind",
        overlays=["msl"],
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        cadence="monthly",
    )
    field = {
        "times": [dt.date(2024, 1, 6)],
        "lats": np.linspace(18.0, 31.0, 4),
        "lons": np.linspace(-98.0, -80.0, 5),
        "values": np.ones((1, 4, 5)),
        "overlay_grids": {"msl": np.full((1, 4, 5), np.nan)},
    }
    frames, _ = render_viz(spec, field, None, out_dir=tmp_path / "f")
    assert len(frames) == 1
