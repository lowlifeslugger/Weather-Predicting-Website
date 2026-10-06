#!/usr/bin/env python3
"""
world.py - the world version: every country, with the same forecasts, accuracy checks and
past-weather views as the India site, plus India's states in detail when you zoom in on India.
It reuses backend.py, backtest.py, predict.py and server.py.

Needs in this folder:  backend.py backtest.py predict.py server.py index.html
                       world_points.json  world_map.json   (and this file)
Standard library only.

Steps (the first two are one-time, the data is cached in the world_cache folder and is resumable):
    py world.py download          # 690 sample points (238 countries + India's 36 states), all of 2025 (~130 MB)
    py world.py backtest          # measures accuracy, writes world_calibration.json + world_backtest_report.txt
    py world.py serve             # opens http://localhost:8000

Handy:
    py world.py status
    py world.py download --years 2024-2025     # more history: better accuracy numbers, ~230 MB, twice the downloads
    py world.py download --months 6-9          # only some months (calibration then only covers those months)
    py world.py serve --port 8080 --no-browser

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
    if not argv or argv[0] not in ("download", "status", "backtest", "serve"):
        print(__doc__)
        sys.exit(0 if not argv else 1)
    cmd, rest = argv[0], argv[1:]
    need_points()

    if cmd == "serve":
        import server
        server.main(["--site", "world"] + rest)
        return

    backend.configure_world()
    if cmd == "download":
        rest, start, end = years_arg(rest)
        flags = set(rest)
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
