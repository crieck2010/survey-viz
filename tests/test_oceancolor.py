"""Tests for the ocean-color variable (survey-viz v0.13.0).

Covers: parser routing (keywords, combinations, disambiguation,
refusals), the canonical ``"ocean-color"`` variable with its legacy
``"chlorophyll"`` alias, the ``context`` spec field, source routing to
``"oceancolor"``, and the log-scaled renderer with honest cloud-gap
treatment. No network, no survey-currents peer needed except where
marked (importorskip).
"""

import datetime

import pytest

from viz.parser import UnparseableDescription, parse_description
from viz.sources import (default_source, explain_source, fetch_for_source,
                         is_fetchable, resolve_source)
from viz.spec import KNOWN_CONTEXTS, KNOWN_SOURCES, VizSpec, _VARIABLE_ALIASES

TODAY = datetime.date(2026, 9, 27)


def _parse(text):
    return parse_description(text, today=TODAY)


def _spec(**kwargs):
    base = dict(title="t", region_key="north-atlantic",
                bbox=(-80.0, 20.0, -60.0, 40.0),
                variable="ocean-color",
                start=datetime.date(2024, 1, 1),
                end=datetime.date(2024, 3, 1), cadence="monthly")
    base.update(kwargs)
    return VizSpec(**base)


# ---------------------------------------------------------------------------
# parser routing
# ---------------------------------------------------------------------------


class TestOceanColorParsing:
    @pytest.mark.parametrize("text", [
        "chlorophyll in the north atlantic over the past year",
        "ocean color in the gulf of mexico",
        "ocean colour in the caribbean sea",
        "phytoplankton in the north pacific",
        "algae bloom in lake erie",
        "green ocean in the north atlantic",
    ])
    def test_keywords_route_to_ocean_color(self, text):
        spec = _parse(text)
        assert spec.variable == "ocean-color"

    def test_monthly_cadence_default(self):
        spec = _parse("chlorophyll in the north atlantic")
        assert spec.cadence == "monthly"

    def test_daily_wording_requests_daily(self):
        spec = _parse("daily chlorophyll in the north atlantic")
        assert spec.cadence == "daily"

    def test_source_pinned_with_reason(self):
        spec = _parse("phytoplankton bloom in the north atlantic")
        assert spec.source == "oceancolor"
        assert "CoastWatch" in spec.source_reason
        assert "keyless" in spec.source_reason

    def test_bloom_with_currents_adds_currents_context(self):
        spec = _parse(
            "phytoplankton bloom with ocean currents in the north atlantic "
            "over the past year")
        assert spec.variable == "ocean-color"
        assert spec.context == ("currents",)

    def test_bloom_conditions_adds_sst_context(self):
        spec = _parse("bloom conditions in the gulf of mexico")
        assert spec.variable == "ocean-color"
        assert spec.context == ("sst",)

    def test_chlorophyll_vs_temperature_adds_sst_context(self):
        spec = _parse("chlorophyll vs temperature in the caribbean sea")
        assert spec.variable == "ocean-color"
        assert spec.context == ("sst",)

    def test_no_context_by_default(self):
        spec = _parse("chlorophyll in the north atlantic")
        assert spec.context == ()

    def test_green_ocean_not_terrestrial(self):
        # Generic "green ocean" wording must not route to terrestrial
        # water products (streamflow / water-storage).
        spec = _parse("green ocean in the north pacific")
        assert spec.variable == "ocean-color"
        assert spec.source == "oceancolor"

    def test_streamflow_still_wins_for_rivers(self):
        spec = _parse("streamflow in the gulf of mexico")
        assert spec.variable == "streamflow"

    @pytest.mark.parametrize("text", [
        "nitrate levels in lake erie",
        "turbidity in chesapeake bay",
        "water quality in the gulf of mexico",
        "dissolved oxygen in puget sound",
    ])
    def test_terrestrial_water_quality_refused(self, text):
        with pytest.raises(UnparseableDescription,
                           match="No water-quality adapter"):
            _parse(text)

    def test_lake_erie_bloom_uses_ocean_color(self):
        # Lake Erie is inside the global L3 grid geometry, so the
        # sensor covers it; inland-water limits are documented, not a
        # refusal.
        spec = _parse("algae bloom in lake erie over the past year")
        assert spec.variable == "ocean-color"
        assert spec.region_key == "lake-erie"
        assert resolve_source(spec) == "oceancolor"


# ---------------------------------------------------------------------------
# spec: canonical variable, legacy alias, context field
# ---------------------------------------------------------------------------


class TestOceanColorSpec:
    def test_chlorophyll_alias_canonicalized(self):
        assert _VARIABLE_ALIASES["chlorophyll"] == "ocean-color"
        spec = _spec(variable="chlorophyll")
        assert spec.variable == "ocean-color"

    def test_old_dict_with_chlorophyll_loads(self):
        spec = _spec(variable="chlorophyll")
        data = spec.to_dict()
        assert data["variable"] == "ocean-color"  # canonical on the wire
        data["variable"] = "chlorophyll"  # simulate a pre-v0.13.0 dict
        assert VizSpec.from_dict(data).variable == "ocean-color"

    def test_old_dict_without_context_loads(self):
        data = _spec().to_dict()
        del data["context"]
        assert VizSpec.from_dict(data).context == ()

    def test_context_roundtrip(self):
        spec = _spec(context=("currents", "sst"))
        assert VizSpec.from_dict(spec.to_dict()).context == ("currents", "sst")

    def test_context_must_be_known(self):
        with pytest.raises(ValueError, match="not in"):
            _spec(context=("tp",))

    def test_context_cannot_duplicate_variable(self):
        with pytest.raises(ValueError, match="duplicates"):
            _spec(variable="currents", context=("currents",))

    def test_context_capped(self):
        with pytest.raises(ValueError, match="at most"):
            _spec(context=("currents", "sst", "currents"))

    def test_known_contexts(self):
        assert set(KNOWN_CONTEXTS) == {"currents", "sst"}


# ---------------------------------------------------------------------------
# source routing
# ---------------------------------------------------------------------------


class TestOceanColorSources:
    def test_oceancolor_in_known_sources(self):
        assert "oceancolor" in KNOWN_SOURCES

    @pytest.mark.parametrize("region", [
        "north-atlantic", "lake-erie", "global", "southern-ocean",
    ])
    def test_default_source_global(self, region):
        assert default_source("ocean-color", region) == "oceancolor"

    def test_legacy_chlorophyll_routes_too(self):
        assert default_source("chlorophyll", "north-atlantic") == "oceancolor"

    def test_resolve_source_pinned(self):
        spec = _spec(source="oceancolor", source_reason="test pin")
        assert resolve_source(spec) == "oceancolor"

    def test_explain_source_quotes_reason(self):
        spec = _spec(source="oceancolor", source_reason="parser pin")
        text = explain_source(spec)
        assert "oceancolor" in text and "parser pin" in text

    def test_explain_source_default(self):
        text = explain_source(_spec())
        assert "oceancolor" in text

    def test_is_fetchable(self):
        assert is_fetchable(_spec())

    def test_fetch_for_source_oceancolor(self):
        currents = pytest.importorskip(
            "currents", reason="optional survey-currents peer not installed")
        from viz.sources import _SOURCE_MIN_VERSIONS
        need = tuple(int(p) for p in
                     _SOURCE_MIN_VERSIONS["oceancolor"].split("."))
        have = tuple(int(p) for p in currents.__version__.split(".")[:3])
        if have >= need:
            fn = fetch_for_source("oceancolor")
            assert fn.__name__ == "fetch_oceancolor"
        else:
            with pytest.raises(ImportError, match="survey-currents>=0.14.0"):
                fetch_for_source("oceancolor")


# ---------------------------------------------------------------------------
# renderer: log scale, gap treatment, manifest
# ---------------------------------------------------------------------------

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
np = pytest.importorskip("numpy")


def _chl_field(nt=3, ny=8, nx=10, seed=12, gap_fraction=0.2,
               all_nan_frame=None):
    rng = np.random.default_rng(seed)
    lats = np.linspace(20.0, 40.0, ny)
    lons = np.linspace(-80.0, -60.0, nx)
    values = 0.3 * 10.0 ** rng.normal(0, 0.4, size=(nt, ny, nx))
    mask = rng.random((nt, ny, nx)) < gap_fraction
    values = np.where(mask, np.nan, values)
    if all_nan_frame is not None:
        values[all_nan_frame] = np.nan
    times = [f"2024-0{m}-01" for m in range(1, nt + 1)]
    return {"times": times, "lats": lats, "lons": lons, "values": values}


class TestOceanColorRender:
    def test_log_scale_manifest(self, tmp_path):
        from viz.render import render_viz
        import json
        frames, manifest_path = render_viz(
            _spec(underlay=False), _chl_field(), out_dir=str(tmp_path))
        manifest = json.load(open(manifest_path))
        assert manifest["render"]["log_scale"] is True
        assert manifest["render"]["cmap"] == "viridis"
        assert manifest["render"]["vmin"] > 0  # LogNorm needs vmin > 0
        assert manifest["render"]["vmax"] > manifest["render"]["vmin"]
        assert len(frames) == len(manifest["frames"]) > 0

    def test_cloud_gaps_stay_nan(self, tmp_path):
        # Masked (cloud) cells must never be filled: they render as the
        # background/underlay, and all-NaN frames get the NO
        # OBSERVATION panel instead of an interpolated map.
        from viz.render import _normalize_field, render_viz
        import json
        field = _chl_field(all_nan_frame=2)
        _, _, _, values = _normalize_field(field)
        assert np.isnan(values[2]).all()
        assert np.isnan(values[0]).any()  # partial gaps survive too
        frames, manifest_path = render_viz(
            _spec(underlay=False), field, out_dir=str(tmp_path))
        manifest = json.load(open(manifest_path))
        assert manifest["render"]["gap_frames"] == ["2024-03-01"]

    def test_masked_array_input_keeps_nans(self):
        # OceanColorField.values is a masked array; the renderer must
        # not drop the mask (regression guard for _normalize_field).
        from viz.render import _normalize_field
        masked = np.ma.array(
            [[[1.0, 2.0], [3.0, 4.0]]], mask=[[[0, 1], [0, 0]]])

        class F:
            times = ["2024-01-01"]
            lats = np.array([0.0, 1.0])
            lons = np.array([0.0, 1.0])
            values = masked

        _, _, _, values = _normalize_field(F())
        assert np.isnan(values[0, 0, 1])

    def test_context_in_manifest_and_footer(self, tmp_path):
        from viz.render import render_viz
        import json
        spec = _spec(underlay=False, context=("currents", "sst"))
        _, manifest_path = render_viz(spec, _chl_field(), out_dir=str(tmp_path))
        manifest = json.load(open(manifest_path))
        assert manifest["render"]["context"] == ["currents", "sst"]
        assert manifest["spec"]["context"] == ["currents", "sst"]

    def test_legacy_alias_renders_identically(self, tmp_path):
        from viz.render import render_viz
        import json
        _, mp1 = render_viz(_spec(underlay=False), _chl_field(),
                            out_dir=str(tmp_path / "a"))
        _, mp2 = render_viz(_spec(underlay=False, variable="chlorophyll"),
                            _chl_field(), out_dir=str(tmp_path / "b"))
        m1, m2 = json.load(open(mp1)), json.load(open(mp2))
        assert m1["render"]["cmap"] == m2["render"]["cmap"] == "viridis"
        assert m1["render"]["log_scale"] == m2["render"]["log_scale"] is True
