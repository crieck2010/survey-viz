"""Tests for the survey-timescales peer integration (v0.21.0).

The parser consults the survey-timescales engine (``suggest_window``)
when — and ONLY when — no explicit time phrase was parsed:

* season language ("fire season", "hurricane season", "melt season",
  ...) -> the engine's ``intent="season"`` window;
* otherwise no time phrase -> the engine's default-mode window;
* explicit user dates / relative time phrases -> the engine is never
  consulted (precedence rule #1); behavior is byte-identical to v0.20.0.

Tests asserting concrete registry answers take the ``real_peer``
fixture (skipped when the peer is absent); the graceful-degradation
and fake-peer tests run with or without the peer installed, so the
suite stays green on a fresh clone.
"""

import datetime as dt
import sys
import types

import pytest

from viz.parser import parse_description

TODAY = dt.date(2026, 9, 30)


@pytest.fixture
def real_peer():
    """The real survey-timescales engine (skips when not installed).

    Only the tests asserting concrete registry answers need this; the
    graceful-degradation and fake-peer tests run with or without it.
    """
    return pytest.importorskip("timescales", reason="survey-timescales peer not installed")


# --- season-language resolution (concrete dates, fixed today) -----------------

def test_fire_season_resolves_to_current_fire_season(real_peer):
    spec = parse_description("California fire season", today=TODAY)
    assert spec.variable == "fire"
    assert spec.region_key == "california"
    assert (spec.start, spec.end) == (dt.date(2026, 5, 1), TODAY)
    assert spec.cadence == "daily"
    assert "California fire season" in spec.timescale_reason


def test_hurricane_season_uses_engine_default_mode(real_peer):
    # "hurricane season" keeps the parser's existing variable routing
    # (generic hurricane wording -> ERA5 wind + msl, not storm-tracks);
    # the registry has no "season" mode for "wind", so the engine's
    # default (annual cycle) applies — the ValueError fallback path.
    spec = parse_description("Atlantic hurricane season", today=TODAY)
    assert spec.variable == "wind"
    assert spec.region_key == "north-atlantic"
    assert (spec.start, spec.end) == (dt.date(2025, 9, 30), TODAY)
    assert spec.timescale_reason  # engine was consulted


def test_melt_season_resolves_to_sea_ice_melt_window(real_peer):
    spec = parse_description("Arctic melt season", today=TODAY)
    assert spec.variable == "sea-ice"
    assert spec.region_key == "arctic-ocean"
    assert (spec.start, spec.end) == (dt.date(2026, 6, 1), TODAY)
    assert "melt season" in spec.timescale_reason


def test_no_date_default_uses_engine_window(real_peer):
    # No season language and no time phrase -> the engine's default
    # mode. For sst that is the same 1-year trailing window the parser
    # used to hard-code; for fire it is the current fire season.
    sst = parse_description("Lake Superior surface temperature", today=TODAY)
    assert (sst.start, sst.end) == (dt.date(2025, 9, 30), TODAY)
    assert sst.timescale_reason

    fire = parse_description("California wildfires", today=TODAY)
    assert (fire.start, fire.end) == (dt.date(2026, 5, 1), TODAY)
    assert "fire season" in fire.timescale_reason


# --- explicit dates always win (regression: peer never consulted) -------------

def test_explicit_range_beats_season_language():
    spec = parse_description("California fire season 2020 to 2022", today=TODAY)
    assert spec.variable == "fire"
    assert (spec.start, spec.end) == (dt.date(2020, 1, 1), dt.date(2022, 12, 31))
    assert spec.timescale_reason == ""  # engine not consulted


def test_relative_time_phrase_never_consults_engine():
    spec = parse_description(
        "California wildfires over the past 5 years", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2021, 9, 30), TODAY)
    assert spec.timescale_reason == ""

    spec2 = parse_description("Chlorophyll in Puget Sound last summer", today=TODAY)
    assert (spec2.start, spec2.end) == (dt.date(2026, 6, 1), dt.date(2026, 8, 31))
    assert spec2.timescale_reason == ""


def test_explicit_dates_unchanged_without_peer(monkeypatch):
    # With the peer blocked, explicit-date specs are identical to the
    # v0.20.0 behavior (no peer involvement at all).
    monkeypatch.setitem(sys.modules, "timescales", None)
    spec = parse_description("Gulf of Mexico currents, 2015 to 2020", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2015, 1, 1), dt.date(2020, 12, 31))
    assert spec.timescale_reason == ""


# --- provenance ----------------------------------------------------------------

def test_timescale_reason_round_trips_through_dict(real_peer):
    from viz.spec import VizSpec
    spec = parse_description("California fire season", today=TODAY)
    assert spec.timescale_reason
    d = spec.to_dict()
    assert d["timescale_reason"] == spec.timescale_reason
    assert VizSpec.from_dict(d).timescale_reason == spec.timescale_reason


def test_old_spec_dicts_load_with_empty_reason():
    from viz.spec import VizSpec
    spec = parse_description("California wildfires 2020 to 2022", today=TODAY)
    d = spec.to_dict()
    del d["timescale_reason"]  # pre-v0.21.0 dict
    assert VizSpec.from_dict(d).timescale_reason == ""


def test_reason_recorded_in_spec_dict_for_manifest(real_peer):
    # The frame manifest's "spec" section IS VizSpec.to_dict() (see
    # docs/INTEROP.md), so the reason recorded there is the run
    # provenance for the window decision.
    spec = parse_description("California fire season", today=TODAY)
    assert spec.timescale_reason
    assert spec.to_dict()["timescale_reason"] == spec.timescale_reason


# --- graceful degradation (peer absent / unknown / static) ---------------------

def test_peer_absent_falls_back_to_documented_default(monkeypatch):
    monkeypatch.setitem(sys.modules, "timescales", None)
    spec = parse_description("California wildfires", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2025, 9, 30), TODAY)
    assert spec.timescale_reason == ""
    assert spec.variable == "fire"  # everything else still parses


def test_peer_absent_season_language_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "timescales", None)
    spec = parse_description("California fire season", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2025, 9, 30), TODAY)
    assert spec.timescale_reason == ""


def test_unknown_variable_keeps_old_default(real_peer):
    # "burn-scar" is not in the timescales registry -> UnknownVariableError
    # -> the documented past-1-year default, no reason recorded.
    spec = parse_description("Mediterranean burn scars", today=TODAY)
    assert spec.variable == "burn-scar"
    assert (spec.start, spec.end) == (dt.date(2025, 9, 30), TODAY)
    assert spec.timescale_reason == ""


def test_static_variable_keeps_old_default(real_peer):
    # bathymetry/elevation are time-invariant underlays -> StaticVariableError
    # -> old default, then the static single-frame cadence rule applies.
    spec = parse_description("Global bathymetry map", today=TODAY)
    assert spec.variable == "bathymetry"
    assert (spec.start, spec.end) == (TODAY, TODAY)
    assert spec.timescale_reason == ""


# --- tp source disambiguation (engine author gotcha #2) ------------------------

def _install_fake_peer(monkeypatch):
    """Install a fake ``timescales`` module recording suggest_window kwargs."""
    calls = []

    class _FakeSuggestion:
        start = dt.date(2026, 1, 1)
        end = dt.date(2026, 12, 31)
        reason = "fake"

    def fake_suggest_window(variable, **kwargs):
        calls.append({"variable": variable, **kwargs})
        return _FakeSuggestion()

    fake = types.ModuleType("timescales")
    fake.suggest_window = fake_suggest_window
    fake.StaticVariableError = type("StaticVariableError", (Exception,), {})
    fake.UnknownVariableError = type("UnknownVariableError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "timescales", fake)
    return calls


def test_tp_passes_pinned_source_to_engine(monkeypatch):
    calls = _install_fake_peer(monkeypatch)
    spec = parse_description("California recent rainfall event", today=TODAY)
    assert spec.source == "imerg"  # parser pinned IMERG
    assert calls and calls[0]["source"] == "imerg"


def test_tp_defaults_to_era5_source_for_engine(monkeypatch):
    calls = _install_fake_peer(monkeypatch)
    spec = parse_description("California rainfall", today=TODAY)
    assert spec.source == ""  # not pinned; fetch layer defaults to era5
    assert calls and calls[0]["source"] == "era5"


def test_non_tp_variables_do_not_pass_source(monkeypatch):
    calls = _install_fake_peer(monkeypatch)
    parse_description("California wildfires", today=TODAY)
    assert calls and "source" not in calls[0]
