"""Tests for camera-motion intents in viz.refine.refine_spec."""

from __future__ import annotations

import datetime as _dt

import pytest

from viz.parser import UnparseableDescription, parse_description
from viz.refine import refine_spec

TODAY = _dt.date(2026, 9, 27)


def _base() -> "VizSpec":
    return parse_description(
        "Lake Superior water temperature 2020 to 2022", today=TODAY)


def test_motion_zoom_in_slow():
    result = refine_spec(_base(), "add a slow zoom in effect", today=TODAY)
    assert result.motion == {"zoom": "in", "zoom_speed": 0.15}
    # the geographic bbox is untouched — this was a camera intent
    assert result.spec.bbox == _base().bbox
    assert "motion.zoom" in [c.field for c in result.applied]


def test_bare_zoom_in_stays_geographic():
    # Backward compatibility: no camera context -> bbox scaling.
    result = refine_spec(_base(), "zoom in", today=TODAY)
    assert result.motion is None
    assert result.spec.bbox != _base().bbox


def test_zoom_in_on_place_stays_geographic():
    result = refine_spec(_base(), "zoom in on the Gulf of Mexico", today=TODAY)
    assert result.motion is None
    assert result.spec.region_key != _base().region_key


def test_motion_pan_direction():
    result = refine_spec(_base(), "pan left during the video", today=TODAY)
    assert result.motion == {"pan": "left", "pan_speed": 0.35}


def test_motion_pan_compass_normalized():
    result = refine_spec(_base(), "pan to the north during the clip",
                         today=TODAY)
    assert result.motion["pan"] == "up"
    result = refine_spec(_base(), "drift south-west over the reel",
                         today=TODAY)
    assert result.motion["pan"] == "down-left"


def test_motion_zoom_out_fast():
    result = refine_spec(_base(), "zoom out quickly during the video",
                         today=TODAY)
    assert result.motion == {"zoom": "out", "zoom_speed": 0.75}


def test_motion_slower_pan():
    result = refine_spec(_base(), "pan right slower during the video",
                         today=TODAY)
    assert result.motion == {"pan": "right", "pan_speed": 0.25}


def test_motion_very_fast():
    result = refine_spec(_base(),
                         "dramatic zoom in effect during the video",
                         today=TODAY)
    assert result.motion["zoom"] == "in"
    assert result.motion["zoom_speed"] == 1.0


def test_motion_off():
    result = refine_spec(_base(), "no camera motion", today=TODAY)
    assert result.motion == {"zoom": "off", "pan": "off", "smooth": False}


def test_motion_zoom_off_only():
    result = refine_spec(_base(), "turn off the zoom effect", today=TODAY)
    assert result.motion == {"zoom": "off"}


def test_motion_smooth_on():
    result = refine_spec(_base(),
                         "make the transitions smoother during the video",
                         today=TODAY)
    assert result.motion["smooth"] is True
    assert result.motion["smooth_steps"] == 3


def test_motion_smooth_off():
    result = refine_spec(
        _base(), "turn off smooth transitions during the video",
        today=TODAY)
    assert result.motion == {"smooth": False}


def test_motion_combined_with_region():
    result = refine_spec(
        _base(), "zoom in on the Gulf of Mexico with a cinematic slow zoom",
        today=TODAY)
    assert result.spec.region_key != _base().region_key  # geographic part
    assert result.motion == {"zoom": "in", "zoom_speed": 0.15}  # camera part


def test_motion_speed_without_direction_is_unparsed():
    result = refine_spec(_base(), "make the camera motion faster",
                         today=TODAY)
    assert result.motion is None
    assert any("speed was mentioned" in note for note in result.unparsed)


def test_motion_zoom_and_pan_share_speed():
    result = refine_spec(
        _base(), "slow zoom in and pan up during the video", today=TODAY)
    assert result.motion == {"zoom": "in", "zoom_speed": 0.15,
                             "pan": "up", "pan_speed": 0.15}


def test_motion_bare_drift_without_direction_is_unparseable():
    # "ken burns" is camera context, but "drift" alone names no
    # direction — nothing actionable was said.
    with pytest.raises(UnparseableDescription):
        refine_spec(_base(), "add a ken burns drift", today=TODAY)


def test_ocean_drift_still_means_currents():
    # No camera reading ("drift" without a direction or camera verb)
    # — the currents keyword must survive.
    result = refine_spec(_base(), "show ocean drift", today=TODAY)
    assert result.spec.variable == "currents"
    assert result.motion is None


def test_camera_drift_does_not_switch_variable():
    result = refine_spec(_base(), "drift north during the video",
                         today=TODAY)
    assert result.motion["pan"] == "up"
    assert result.spec.variable == "sst"  # not currents


def test_ken_burns_does_not_switch_variable():
    result = refine_spec(
        _base(), "add a ken burns zoom in effect during the video",
        today=TODAY)
    assert result.motion["zoom"] == "in"
    assert result.spec.variable == "sst"  # not fire
