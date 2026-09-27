"""Tests for NASA Black Marble night-lights routing (v0.8.0).

Covers the ``"night-lights"`` variable (keywords, parser pinning to
``"blackmarble"`` with an inspectable ``source_reason``, daily cadence,
regional defaults, the lazy adapter map) and the honest
``"power-outage"`` refusal (change detection, not a single-epoch map).
"""

from __future__ import annotations

import datetime as dt

import pytest

import viz
from viz import explain_source, parse_description, resolve_source
from viz.spec import KNOWN_SOURCES, KNOWN_VARIABLES, VizSpec
from viz.sources import (SOURCE_LABELS, _SOURCE_ADAPTERS,
                         _SOURCE_MIN_VERSIONS, default_source,
                         fetch_for_source, is_fetchable)


def _parse(text: str, today: dt.date | None = None) -> VizSpec:
    return parse_description(text, today=today or dt.date(2026, 9, 27))


# ---------------------------------------------------------------------------
# parser examples (the v0.8.0 acceptance cases)
# ---------------------------------------------------------------------------


def test_city_lights_california():
    spec = _parse("City lights across California this year")
    assert spec.variable == "night-lights"
    assert spec.region_key == "california"
    assert spec.source == "blackmarble"
    assert spec.source_reason == (
        "night-lights observations -> "
        "NASA Black Marble VNP46A2 daily corrected radiance")
    assert spec.cadence == "daily"
    assert "Night Lights" in spec.title


def test_electrification_amazon():
    spec = _parse("Electrification across the Amazon Basin since 2019")
    assert spec.variable == "night-lights"
    assert spec.region_key == "amazon-basin"
    assert spec.source == "blackmarble"
    assert spec.source_reason
    assert spec.start == dt.date(2019, 1, 1)


def test_growth_of_cities_australia():
    spec = _parse("Growth of cities in Southeast Australia over the past 5 years")
    assert spec.variable == "night-lights"
    assert spec.region_key == "australia-southeast"
    assert spec.source == "blackmarble"


def test_power_outage_is_refusal():
    spec = _parse("Power outage in California last week")
    assert spec.variable == "power-outage"
    assert spec.source == ""
    assert resolve_source(spec) == ""
    assert is_fetchable(spec) is False
    msg = explain_source(spec)
    assert msg.startswith("no source:")
    assert "change detection" in msg
    assert "blackmarble" not in msg


def test_blackout_singular():
    spec = _parse("blackout across the Gulf of Mexico")
    assert spec.variable == "power-outage"
    assert resolve_source(spec) == ""


def test_power_outage_wins_tie_against_night_lights():
    # "city lights during the blackout": the change-detection reading is
    # the honest one, like burn-scar winning ties against fire.
    spec = _parse("city lights during the blackout in California")
    assert spec.variable == "power-outage"


# ---------------------------------------------------------------------------
# keyword coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", [
    "night lights over California",
    "urban lights in the Amazon Basin",
    "light pollution across California",
    "Black Marble view of California",
    "development in the Amazon Basin",
])
def test_night_lights_keywords(phrase):
    spec = _parse(phrase)
    assert spec.variable == "night-lights"
    assert spec.source == "blackmarble"
    assert "Black Marble" in spec.source_reason


@pytest.mark.parametrize("phrase", [
    "power outages in California",
    "the great blackout over the Gulf of Mexico",
])
def test_power_outage_keywords(phrase):
    spec = _parse(phrase)
    assert spec.variable == "power-outage"
    assert resolve_source(spec) == ""


# ---------------------------------------------------------------------------
# default_source / resolve_source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("region_key", [
    "california", "amazon-basin", "australia-southeast", "north-atlantic",
    "global", "arctic-ocean",
])
def test_default_source_night_lights_any_region(region_key):
    assert default_source("night-lights", region_key) == "blackmarble"


@pytest.mark.parametrize("region_key", ["california", "global", "arctic-ocean"])
def test_default_source_power_outage_refused(region_key):
    assert default_source("power-outage", region_key) == ""


def test_explicit_source_pin_still_validated():
    base = dict(title="T", region_key="california",
                bbox=(-125.0, 25.0, -66.0, 49.0), variable="night-lights",
                start=dt.date(2024, 1, 1), end=dt.date(2024, 12, 31),
                cadence="daily", source="blackmarble")
    assert resolve_source(VizSpec(**base)) == "blackmarble"


def test_explain_source_quotes_pinned_reason():
    spec = _parse("City lights across California this year")
    msg = explain_source(spec)
    assert msg.startswith("source 'blackmarble' (NASA Black Marble VNP46A2):")
    assert "NASA Black Marble VNP46A2 daily corrected radiance" in msg


def test_explain_source_default_why_without_pin():
    # A hand-built spec with no source_reason still gets an inspectable
    # explanation from the regional default.
    spec = VizSpec(title="T", region_key="california",
                   bbox=(-125.0, 25.0, -66.0, 49.0), variable="night-lights",
                   start=dt.date(2024, 1, 1), end=dt.date(2024, 12, 31),
                   cadence="daily")
    msg = explain_source(spec)
    assert "regional default" in msg
    assert "night-lights observations" in msg


def test_is_fetchable():
    lights = VizSpec(title="T", region_key="california",
                     bbox=(-125.0, 25.0, -66.0, 49.0), variable="night-lights",
                     start=dt.date(2024, 1, 1), end=dt.date(2024, 12, 31),
                     cadence="daily")
    outage = VizSpec(title="T", region_key="california",
                     bbox=(-125.0, 25.0, -66.0, 49.0), variable="power-outage",
                     start=dt.date(2024, 1, 1), end=dt.date(2024, 12, 31),
                     cadence="daily")
    assert is_fetchable(lights) is True
    assert is_fetchable(outage) is False


# ---------------------------------------------------------------------------
# registry entries
# ---------------------------------------------------------------------------


def test_known_registries():
    assert "night-lights" in KNOWN_VARIABLES
    assert "power-outage" in KNOWN_VARIABLES
    assert "blackmarble" in KNOWN_SOURCES
    assert SOURCE_LABELS["blackmarble"] == "NASA Black Marble VNP46A2"


def test_lazy_adapter_map():
    assert _SOURCE_ADAPTERS["blackmarble"] == (
        "currents.blackmarble", "fetch_blackmarble")
    assert _SOURCE_MIN_VERSIONS["blackmarble"] == "0.9.0"


def test_fetch_for_source_blackmarble():
    currents = pytest.importorskip(
        "currents", reason="optional survey-currents peer not installed")
    fn = fetch_for_source("blackmarble")
    assert fn is currents.blackmarble.fetch_blackmarble
