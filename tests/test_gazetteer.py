"""Tests for the gazetteer: entry validity + region matching."""

from viz.gazetteer import (
    FETCHABLE_KEYS,
    find_region,
    get_region,
    is_fetchable,
    load_regions,
    regions_path,
)


def test_regions_yaml_resolves():
    assert regions_path().is_file()


def test_region_count():
    regions = load_regions()
    assert len(regions) == 33  # 5 Great Lakes + 20 coastal + 8 ocean basins


def test_keys_unique():
    regions = load_regions()
    keys = [r["key"] for r in regions]
    assert len(keys) == len(set(keys))


def test_all_bboxes_sane():
    for r in load_regions():
        bbox = r["bbox"]
        assert len(bbox) == 4, r["key"]
        lon_min, lat_min, lon_max, lat_max = bbox
        assert lon_min < lon_max, r["key"]
        assert lat_min < lat_max, r["key"]
        assert -180.0 <= lon_min <= 180.0 and -180.0 <= lon_max <= 180.0, r["key"]
        assert -90.0 <= lat_min <= 90.0 and -90.0 <= lat_max <= 90.0, r["key"]


def test_required_fields_present():
    for r in load_regions():
        assert r["key"] and r["name"], r
        assert isinstance(r["aliases"], list), r["key"]
        assert r["notes"], r["key"]  # honesty note required on every entry


def test_great_lakes_present():
    keys = {r["key"] for r in load_regions()}
    for k in FETCHABLE_KEYS:
        assert k in keys


def test_find_region_case_insensitive():
    hit = find_region("show me LAKE SUPERIOR temps")
    assert hit is not None and hit["key"] == "lake-superior"


def test_find_region_longest_match_wins():
    # "lake superior" (13 chars) must beat the bare alias "superior".
    hit = find_region("lake superior surface temperature")
    assert hit is not None and hit["key"] == "lake-superior"


def test_find_region_alias():
    assert find_region("gitche gumee warmth")["key"] == "lake-superior"
    assert find_region("sea of cortez currents")["key"] == "gulf-of-california"
    assert find_region("med sea temps")["key"] == "mediterranean-sea"


def test_find_region_period_normalization():
    assert find_region("Gulf of St. Lawrence chlorophyll")["key"] == "gulf-of-st-lawrence"


def test_find_region_unknown_returns_none():
    assert find_region("the middle of nowhere") is None


def test_get_region_by_key():
    r = get_region("puget-sound")
    assert r is not None and r["name"] == "Puget Sound"
    assert get_region("nope") is None


def test_is_fetchable_only_great_lakes():
    for r in load_regions():
        if r["key"] in FETCHABLE_KEYS:
            assert is_fetchable(r["key"]), r["key"]
        else:
            assert not is_fetchable(r["key"]), r["key"]
    assert len(FETCHABLE_KEYS) == 5
