"""Tests for the deterministic description parser (fully offline)."""

import datetime as dt

import pytest

from viz.parser import UnparseableDescription, parse_description

TODAY = dt.date(2026, 9, 26)


def test_full_example():
    spec = parse_description(
        "Lake Superior surface temperature over the past 5 years", today=TODAY
    )
    assert spec.region_key == "lake-superior"
    assert spec.variable == "sst"
    assert spec.start == dt.date(2021, 9, 26)
    assert spec.end == TODAY
    assert spec.cadence == "monthly"
    assert spec.bbox == (-92.5, 46.0, -84.5, 48.8)


def test_region_alias_superior():
    spec = parse_description("superior warmth past 2 years", today=TODAY)
    assert spec.region_key == "lake-superior"


def test_region_alias_gitche_gumee():
    spec = parse_description("gitche gumee temps 2020 to 2022", today=TODAY)
    assert spec.region_key == "lake-superior"
    assert spec.start == dt.date(2020, 1, 1)
    assert spec.end == dt.date(2022, 12, 31)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Gulf of Mexico currents, 2015 to 2020", "currents"),
        ("Chesapeake Bay flow last summer", "currents"),
        ("Puget Sound stream velocity this year", "currents"),
        ("Gulf of Maine circulation since 2018", "currents"),
    ],
)
def test_variable_currents_keywords(text, expected):
    assert parse_description(text, today=TODAY).variable == expected


@pytest.mark.parametrize(
    "text",
    [
        "Chlorophyll in Puget Sound last summer",
        "Lake Erie algae bloom 2019 to 2021",
        "Mediterranean Sea chl past 3 years",
        "Red Sea phytoplankton this year",
    ],
)
def test_variable_chlorophyll_keywords(text):
    assert parse_description(text, today=TODAY).variable == "ocean-color"  # canonical since v0.13.0


@pytest.mark.parametrize(
    "text",
    [
        "Lake Superior thermal map past year",
        "Lake Michigan warmth 2018 to 2020",
        "Hudson Bay cold water since 2015",
        "North Sea SST this year",
    ],
)
def test_variable_sst_keywords(text):
    assert parse_description(text, today=TODAY).variable == "sst"


def test_variable_defaults_to_sst_with_documented_rule():
    spec = parse_description("Lake Ontario past 2 years", today=TODAY)
    assert spec.variable == "sst"


def test_mixed_keywords_earliest_in_text_wins():
    # "temperature" appears before "currents" -> sst.
    spec = parse_description("Lake Huron temperature and currents 2019 to 2021", today=TODAY)
    assert spec.variable == "sst"
    # "currents" appears first -> currents.
    spec = parse_description("Lake Huron currents and temperature 2019 to 2021", today=TODAY)
    assert spec.variable == "currents"


def test_past_n_years():
    spec = parse_description("Lake Erie sst past 3 years", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2023, 9, 26), TODAY)


def test_last_n_years():
    spec = parse_description("Lake Erie sst last 3 years", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2023, 9, 26), TODAY)


def test_past_n_months():
    spec = parse_description("Lake Erie sst past 6 months", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2026, 3, 26), TODAY)


def test_last_summer_most_recent_complete_window():
    spec = parse_description("Chlorophyll in Puget Sound last summer", today=TODAY)
    # TODAY is 2026-09-26, so the completed summer is Jun-Aug 2026.
    assert (spec.start, spec.end) == (dt.date(2026, 6, 1), dt.date(2026, 8, 31))
    # Mid-summer: the in-progress summer is not complete -> previous year.
    spec2 = parse_description("Chlorophyll in Puget Sound last summer", today=dt.date(2026, 7, 15))
    assert (spec2.start, spec2.end) == (dt.date(2025, 6, 1), dt.date(2025, 8, 31))


def test_this_year():
    spec = parse_description("Gulf of Mexico sst this year", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2026, 1, 1), TODAY)


def test_explicit_range_to():
    spec = parse_description("Gulf of Mexico currents, 2015 to 2020", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2015, 1, 1), dt.date(2020, 12, 31))


def test_explicit_range_dash():
    spec = parse_description("Caribbean Sea sst 2015-2020", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2015, 1, 1), dt.date(2020, 12, 31))


def test_since_year():
    spec = parse_description("Red Sea sst since 2018", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2018, 1, 1), TODAY)


def test_default_time_is_past_one_year():
    spec = parse_description("Lake Superior surface temperature", today=TODAY)
    assert (spec.start, spec.end) == (dt.date(2025, 9, 26), TODAY)


def test_title_override():
    spec = parse_description("Lake Superior sst past year", title="My Custom Title", today=TODAY)
    assert spec.title == "My Custom Title"


def test_title_derived():
    spec = parse_description("Lake Superior surface temperature over the past 5 years", today=TODAY)
    assert spec.title == "Lake Superior — Surface Water Temperature, 2021–2026"


def test_title_derived_single_year():
    spec = parse_description("Puget Sound chlorophyll last summer", today=TODAY)
    assert spec.title == "Puget Sound — Chlorophyll-a (Ocean Color), 2026"


def test_title_derived_currents():
    spec = parse_description("Gulf of Mexico currents, 2015 to 2020", today=TODAY)
    assert spec.title == "Gulf of Mexico — Surface Currents, 2015–2020"


def test_no_region_raises_with_helpful_message():
    with pytest.raises(UnparseableDescription) as excinfo:
        parse_description("temperature over the past 5 years", today=TODAY)
    msg = str(excinfo.value)
    assert "no known region" in msg.lower()
    assert "Lake Superior" in msg  # includes examples that DO parse
    assert "LLM" in msg  # notes the upgrade path


def test_conflicting_time_phrases_raise():
    with pytest.raises(UnparseableDescription) as excinfo:
        parse_description("Lake Superior sst past 3 years 2015 to 2020", today=TODAY)
    assert "conflicting" in str(excinfo.value).lower()


def test_invalid_year_range_raises():
    with pytest.raises(UnparseableDescription):
        parse_description("Lake Superior sst 2020 to 2015", today=TODAY)


def test_empty_description_raises():
    with pytest.raises(UnparseableDescription):
        parse_description("   ", today=TODAY)


def test_non_great_lakes_region_parses():
    spec = parse_description("Mediterranean Sea sst 2015 to 2020", today=TODAY)
    assert spec.region_key == "mediterranean-sea"
    assert spec.bbox == (-6.0, 30.0, 36.5, 46.0)


# --- fire / burn-scar (v0.5.0) -------------------------------------------------

def test_variable_fire_basic():
    spec = parse_description("California wildfires last summer", today=TODAY)
    assert spec.variable == "fire"
    assert spec.region_key == "california"
    assert spec.cadence == "daily"  # fires render daily, not monthly


def test_fire_regions_parse():
    for text, key in [
        ("California fires 2020 to 2022", "california"),
        ("Pacific Northwest wildfires this year", "pacific-northwest"),
        ("Amazon burning 2019 to 2021", "amazon-basin"),
        ("Southeastern Australia fires last summer", "australia-southeast"),
        ("Boreal Canada wildfires since 2020", "boreal-canada"),
    ]:
        assert parse_description(text, today=TODAY).region_key == key


@pytest.mark.parametrize(
    "text",
    [
        "Mediterranean burn scars 2020 to 2024",
        "California burned area last summer",
        "Amazon burn severity this year",
    ],
)
def test_variable_burn_scar_wins_tie_with_fire(text):
    # "burn scar" matches both the fire and burn-scar groups at the same
    # position; the scar reading must win (FIRMS is detections-only).
    spec = parse_description(text, today=TODAY)
    assert spec.variable == "burn-scar"


def test_burn_scar_earliest_wins_still_applies():
    # An earlier fire keyword keeps the fire reading.
    spec = parse_description(
        "California wildfires and burn scars 2020 to 2022", today=TODAY)
    assert spec.variable == "fire"


def test_fire_title_label():
    spec = parse_description("California wildfires 2020 to 2022", today=TODAY)
    assert spec.title == "California — Active Fires, 2020–2022"


def test_fire_cadence_daily_others_monthly():
    assert parse_description(
        "California wildfires 2020 to 2022", today=TODAY).cadence == "daily"
    assert parse_description(
        "California sst 2020 to 2022", today=TODAY).cadence == "monthly"


def test_fire_does_not_pin_source():
    spec = parse_description(
        "California ultra high resolution wildfires 2020 to 2022",
        today=TODAY)
    assert spec.variable == "fire"
    assert spec.source == ""  # no quality pinning for fires; default -> firms
