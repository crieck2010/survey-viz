"""Thin CLI for survey-viz.

    viz parse "Lake Superior surface temperature over the past 5 years"
    viz demo [--out-dir DIR]
    viz render --spec spec.json --field-npz field.npz [--series-csv series.csv] --out-dir frames
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

from . import __version__
from .gazetteer import get_region
from .sources import is_fetchable as _spec_is_fetchable
from .parser import UnparseableDescription, parse_description
from .render import render_viz
from .spec import VizSpec


def _cmd_parse(args: argparse.Namespace) -> int:
    today = _dt.date.fromisoformat(args.today) if args.today else None
    try:
        spec = parse_description(args.description, title=args.title, today=today)
    except UnparseableDescription as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(spec.to_dict(), indent=2))
    region = get_region(spec.region_key)
    if region and not _spec_is_fetchable(spec):
        print(
            f"\nnote: {region['name']} parses fine, but no fetch adapter exists yet "
            "for this variable/region combination (see `viz parse` sources and "
            "docs/PARSER.md).",
            file=sys.stderr,
        )
    return 0


def _synthetic_demo():
    """Build a small synthetic field + series over Lake Superior (fully offline)."""
    import numpy as np

    from .gazetteer import get_region

    region = get_region("lake-superior")
    lon_min, lat_min, lon_max, lat_max = region["bbox"]
    rng = np.random.default_rng(42)
    lats = np.linspace(lat_min, lat_max, 36)
    lons = np.linspace(lon_min, lon_max, 56)
    today = _dt.date.today()
    # 12 monthly timesteps ending this month.
    times = []
    y, m = today.year, today.month
    for _ in range(12):
        times.append(_dt.date(y, m, 15))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    times.reverse()

    lon2d, lat2d = np.meshgrid(lons, lats)
    values = np.empty((len(times), len(lats), len(lons)))
    for i in range(len(times)):
        seasonal = 12 + 8 * np.sin(2 * np.pi * i / 12)  # °C annual cycle
        gradient = (lat2d - lat_min) / (lat_max - lat_min) * 3.0
        noise = rng.normal(0, 0.4, size=lat2d.shape)
        values[i] = seasonal - gradient + noise

    field = {"times": times, "lats": lats, "lons": lons, "values": values}
    monthly_means = [float(np.mean(values[i])) for i in range(len(times))]
    series = {"dates": times, "values": monthly_means}

    spec = parse_description(
        "Lake Superior surface temperature over the past year", today=today
    )
    return spec, field, series


def _cmd_demo(args: argparse.Namespace) -> int:
    spec, field, series = _synthetic_demo()
    # The demo is documented as fully offline: no basemap underlay fetch.
    frames, manifest = render_viz(spec, field, series, out_dir=args.out_dir,
                                  underlay=False)
    print(f"survey-viz {__version__} demo")
    print(f"  spec : {spec.title}")
    print(f"  frames: {len(frames)} -> {args.out_dir}/")
    print(f"  manifest: {manifest}")
    print("  feed the frames dir to survey-animate to encode a reel.")
    return 0


def _load_field_npz(path: Path):
    import numpy as np

    data = np.load(path, allow_pickle=True)
    times = [str(t) for t in data["times"]]  # ISO date strings
    return {
        "times": times,
        "lats": np.asarray(data["lats"], dtype=float),
        "lons": np.asarray(data["lons"], dtype=float),
        "values": np.asarray(data["values"], dtype=float),
    }


def _load_series_csv(path: Path):
    import csv

    dates, values = [], []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            dates.append(row["date"])
            values.append(float(row["value"]))
    return {"dates": dates, "values": values}


def _cmd_render(args: argparse.Namespace) -> int:
    with open(args.spec, encoding="utf-8") as fh:
        spec = VizSpec.from_dict(json.load(fh))
    field = _load_field_npz(Path(args.field_npz))
    series = _load_series_csv(Path(args.series_csv)) if args.series_csv else None
    try:
        frames, manifest = render_viz(
            spec, field, series, out_dir=args.out_dir, layout=args.layout,
            style=args.style, underlay=not args.no_underlay,
            preset=args.preset, rotation=args.rotation,
            watermark=args.watermark, subtitle=args.subtitle,
            encoding_line=not args.no_encoding_line,
            place_labels=args.place_labels,
            max_labels=args.max_labels,
            min_population=args.min_population,
            basemap=args.basemap,
            strand_count=args.strand_count,
            strand_linewidth=args.strand_linewidth,
        )
    except RuntimeError as exc:  # e.g. matplotlib missing
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(f"rendered {len(frames)} frames -> {args.out_dir}/")
    print(f"manifest: {manifest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="viz",
        description="survey-viz: plain-English descriptions -> vertical map frame sequences.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="Parse a description and print the VizSpec as JSON.")
    p.add_argument("description", help="Plain-English description of the visualization.")
    p.add_argument("--title", default=None, help="Override the derived title.")
    p.add_argument(
        "--today",
        default=None,
        help="Reference date YYYY-MM-DD for relative time phrases (default: today).",
    )
    p.set_defaults(func=_cmd_parse)

    d = sub.add_parser("demo", help="Render a small synthetic vertical reel (fully offline).")
    d.add_argument("--out-dir", default="viz-demo-frames", help="Where to write frames.")
    d.set_defaults(func=_cmd_demo)

    r = sub.add_parser(
        "render", help="Render frames from a spec JSON + .npz field (+ optional series CSV)."
    )
    r.add_argument("--spec", required=True, help="VizSpec JSON file (see `viz parse`).")
    r.add_argument(
        "--field-npz",
        required=True,
        help=".npz with arrays times (ISO strings), lats, lons, values (T,H,W).",
    )
    r.add_argument("--series-csv", default=None, help="CSV with date,value columns.")
    r.add_argument("--out-dir", default="frames", help="Where to write frames.")
    r.add_argument("--layout", default="reel-vertical")
    r.add_argument("--style", default=None, help="reel-dark (default) or light.")
    r.add_argument("--no-underlay", action="store_true",
                   help="Disable the GEBCO/coastline basemap underlay.")
    r.add_argument("--preset", default=None,
                   choices=["dark_flow", "dark_glow", "paper_prism",
                            "dark_strands"],
                   help="mapped.earth aesthetic preset (needs the "
                        "survey-aesthetics peer: pip install "
                        "'survey-viz[aesthetics]').")
    r.add_argument("--rotation", default=None,
                   help="Frame rotation for the preset path: 'auto' "
                        "(optimal for the region bbox) or degrees "
                        "counter-clockwise.")
    r.add_argument("--watermark", default=None,
                   help="Brand handle burned into the preset furniture "
                        "(off by default).")
    r.add_argument("--subtitle", default=None,
                   help="Explicit editorial subtitle (default: the time "
                        "window, e.g. '16-23 September 2026').")
    r.add_argument("--no-encoding-line", action="store_true",
                   help="Hide the preset's honesty line "
                        "(e.g. 'BRIGHTNESS = SPEED').")
    r.add_argument("--place-labels",
                   dest="place_labels",
                   action=argparse.BooleanOptionalAction, default=True,
                   help="Place geographic labels on the preset path "
                        "(default: auto via the survey-gazetteer peer; "
                        "--no-place-labels turns them off).")
    r.add_argument("--max-labels", type=int, default=8,
                   help="Cap for automatic place labels (default 8).")
    r.add_argument("--min-population", type=int, default=0,
                   help="Minimum place population for automatic place "
                        "labels (default 0).")
    r.add_argument("--basemap", default=None,
                   choices=["void_black", "no_basemap", "subtle_land"],
                   help="Basemap style for the preset path (default: the "
                        "preset's bundled style).")
    r.add_argument("--strand-count", type=int, default=3000,
                   help="Particle count per frame for the dark_strands "
                        "preset (default 3000).")
    r.add_argument("--strand-linewidth", type=float, default=1.4,
                   help="Strand width in points for the dark_strands "
                        "preset (default 1.4).")
    r.set_defaults(func=_cmd_render)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
