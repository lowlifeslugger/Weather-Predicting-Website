#!/usr/bin/env python3
"""
world.py - the world version: every country AND every state / province / region inside it, with the
same forecasts, accuracy checks and past-weather views as the India site. Zoom into a country and its
states or provinces appear; India uses its own official 36-state layer. It reuses backend.py,
backtest.py, predict.py and server.py.

Needs in this folder:  backend.py backtest.py predict.py server.py index.html
                       world_points.json  world_map.json   (and this file)
Standard library only (build_states.py, only needed to rebuild the shapes, also needs `pip install shapely`).

The world set: 238 countries + 4,538 states/provinces/regions (Natural Earth admin-1) = 4,776 regions and
about 5,300 sample points (each state: its biggest city, plus up to 4 more for huge ones).

Steps (the first two are one-time, the data is cached in the world_cache folder and is resumable):
    py world.py download          # past forecasts + ERA5 for every sample point, all of 2025 (BIG, see below)
    py world.py backtest          # measures accuracy, writes world_calibration.json + world_backtest_report.txt
    py world.py serve             # opens http://localhost:8000

You do NOT need the downloads to use the site: forecasts for any country, state or province are fetched live.
The downloads only feed the accuracy report and the calibration of the chances. Without new data the
existing calibration is used for the new states too (it is grouped by world region and number of points).

The full set is about 7x bigger than the old 690 points (roughly 1 GB for a year, and several days of
Open-Meteo's free daily limit). Narrow it, and widen it later - everything already downloaded is kept:
    py world.py download --scope countries                 # only the 238 countries
    py world.py download --states-per-country 6            # 6 states/provinces per country (a fixed spread)
    py world.py download --countries USA,BRA,India         # only those countries and all their states
    py world.py download --scope states --countries FRA    # only France's departments
    py world.py download --extras-per-country 0            # temperature/humidity/... for countries only
    py world.py download --no-extras                       # rain only: about 4x fewer requests

Handy:
    py world.py status                          # what is cached (add --countries / --scope to look at part of it)
    py world.py download --years 2024-2025      # more history: better accuracy numbers, twice the downloads
    py world.py download --months 6-9           # only some months (calibration then only covers those months)
    py world.py backtest --scope states         # score only the states/provinces (default: everything cached)
    py world.py serve --port 8080 --no-browser
    py world.py build                           # rebuild the state shapes/points (needs shapely, see build_states.py)

The accuracy reference here is ERA5 (a reanalysis), NOT weather stations: IMD's rainfall grid covers
India only. Scores therefore show agreement with ERA5 and are optimistic. The site says so.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backend  # noqa: E402


def years_arg(argv):
    """Turn '--years 2024-2025' into --start-year/--end-year; default is 2025 only."""
    out, start, end, i = [], "2025", "2025", 0
    while i < len(argv):
        if argv[i] == "--years" and i + 1 < len(argv):
            a = argv[i + 1].split("-")
            start, end = a[0], a[-1]
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out, start, end


def need_points():
    if not os.path.isfile(os.path.join(HERE, "world_points.json")):
        print("Missing world_points.json. Put it in the same folder as world.py.")
        sys.exit(1)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("download", "status", "backtest", "serve", "build"):
        print(__doc__)
        sys.exit(0 if not argv else 1)
    cmd, rest = argv[0], argv[1:]

    if cmd == "build":
        import build_states
        build_states.main(rest)
        return

    need_points()

    if cmd == "serve":
        import server
        server.main(["--site", "world"] + rest)
        return

    backend.configure_world()
    if cmd == "download":
        rest, start, end = years_arg(rest)
        base = ["download", "--truth", "era5", "--start-year", start, "--end-year", end]
        if "--months" not in rest:
            base += ["--months", "1-12"]
        backend.main(base + rest)
    elif cmd == "status":
        backend.main(["status"] + rest)
    elif cmd == "backtest":
        import backtest
        backtest.CAL_PATH = os.path.join(HERE, "world_calibration.json")
        backtest.REPORT_PATH = os.path.join(HERE, "world_backtest_report.txt")
        backtest.main(["--truth", "era5"] + rest)


if __name__ == "__main__":
    main()
