"""Shared test fixtures for survey-viz.

The basemap underlay (``viz.underlay.fetch_underlay``) is ON by default
in ``render_viz`` and would hit the network (GEBCO / Natural Earth
downloads) in any render test that does not control it. This autouse
fixture keeps the whole suite offline and fast by stubbing the
underlay to ``unavailable`` — except for tests explicitly marked
``@pytest.mark.real_underlay`` (the dedicated basemap tests in
``tests/test_basemaps.py``), which monkeypatch the peer themselves.
"""

from __future__ import annotations

import pytest

from viz import underlay as _ul


@pytest.fixture(autouse=True)
def _offline_underlay(monkeypatch, request):
    if request.node.get_closest_marker("real_underlay") is None:
        monkeypatch.setattr(
            _ul,
            "fetch_underlay",
            lambda *a, **k: _ul._unavailable(
                "test: underlay disabled by tests/conftest.py"),
        )
