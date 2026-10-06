#!/usr/bin/env python3
"""
backend.py - DATA ACCESS ONLY (no prediction logic lives here).

What it does
  * Downloads and caches the data the other two scripts need, in ./data_cache
  * Past forecasts  : Open-Meteo "Previous Runs" API (what the model predicted
                      1, 2, 3, 5 days before each date)
  * Truth (observed): IMD 0.25-degree gridded rainfall (default), downloaded directly, or
                      ERA5 via Open-Meteo (weak fallback: not independent of models)
  * Live data       : forecast + ensemble for a future date, and hourly weather

Also (v2): temperature, humidity, wind, gusts, sunshine and cloud cover - past forecasts,
ERA5 "truth" for them, live forecasts, and the recent past of every state (history).

Commands
  python backend.py download                 # fetch + cache everything (run once)
  python backend.py download --no-extras     # rain only (skip temperature/humidity/wind/sun)
  python backend.py download --truth era5    # if the IMD site is a problem
  python backend.py status                   # show what is cached

Needs: Python 3.8+ and nothing else (standard library only - no pip installs).

Day definition: IMD rainfall for a date is the 24 h ending 08:30 IST that day.
WINDOW_START = 9 makes every model sum use the same window (08:30 -> 08:30).
Set WINDOW_START = 25 for calendar days instead (then re-download).
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "data_cache")
IMD_DIR = os.path.join(CACHE_DIR, "imd")

# state -> list of (place, lat, lon). FIRST point = primary (used for the 30-min table).
STATES = {
    "Andhra Pradesh": [("Amaravati", 16.51, 80.52), ("Visakhapatnam", 17.69, 83.22),
                       ("Tirupati", 13.63, 79.42), ("Kurnool", 15.83, 78.04)],
    "Arunachal Pradesh": [("Itanagar", 27.08, 93.61), ("Tawang", 27.59, 91.86)],
    "Assam": [("Dispur", 26.14, 91.79), ("Dibrugarh", 27.48, 94.91), ("Silchar", 24.83, 92.78)],
    "Bihar": [("Patna", 25.59, 85.14), ("Gaya", 24.80, 85.00), ("Darbhanga", 26.15, 85.90)],
    "Chhattisgarh": [("Raipur", 21.25, 81.63), ("Bilaspur", 22.08, 82.15), ("Jagdalpur", 19.07, 82.03)],
    "Goa": [("Panaji", 15.49, 73.83)],
    "Gujarat": [("Gandhinagar", 23.22, 72.65), ("Surat", 21.17, 72.83),
                ("Rajkot", 22.30, 70.80), ("Bhuj", 23.25, 69.67)],
    "Haryana": [("Chandigarh", 30.73, 76.78), ("Hisar", 29.15, 75.72), ("Gurugram", 28.46, 77.03)],
    "Himachal Pradesh": [("Shimla", 31.10, 77.17), ("Dharamshala", 32.22, 76.32), ("Kullu", 31.96, 77.11)],
    "Jharkhand": [("Ranchi", 23.34, 85.31), ("Jamshedpur", 22.80, 86.20), ("Dhanbad", 23.80, 86.43)],
    "Karnataka": [("Bengaluru", 12.97, 77.59), ("Mangaluru", 12.91, 74.86),
                  ("Belagavi", 15.85, 74.50), ("Kalaburagi", 17.33, 76.83)],
    "Kerala": [("Thiruvananthapuram", 8.52, 76.94), ("Kochi", 9.93, 76.27), ("Kozhikode", 11.26, 75.78)],
    "Madhya Pradesh": [("Bhopal", 23.26, 77.41), ("Indore", 22.72, 75.86),
                       ("Jabalpur", 23.18, 79.99), ("Gwalior", 26.22, 78.18)],
    "Maharashtra": [("Mumbai", 19.08, 72.88), ("Pune", 18.52, 73.86), ("Nagpur", 21.15, 79.09),
                    ("Chh. Sambhajinagar", 19.88, 75.34), ("Latur", 18.40, 76.58),
                    ("Ratnagiri", 16.99, 73.30), ("Nashik", 20.00, 73.79), ("Kolhapur", 16.70, 74.24)],
    "Manipur": [("Imphal", 24.82, 93.94)],
    "Meghalaya": [("Shillong", 25.58, 91.89), ("Sohra (Cherrapunji)", 25.30, 91.70)],
    "Mizoram": [("Aizawl", 23.73, 92.72)],
    "Nagaland": [("Kohima", 25.67, 94.11)],
    "Odisha": [("Bhubaneswar", 20.30, 85.82), ("Sambalpur", 21.47, 83.97), ("Berhampur", 19.31, 84.79)],
    "Punjab": [("Ludhiana", 30.90, 75.86), ("Amritsar", 31.63, 74.87), ("Bathinda", 30.21, 74.95)],
    "Rajasthan": [("Jaipur", 26.91, 75.79), ("Jodhpur", 26.24, 73.02), ("Udaipur", 24.59, 73.71),
                  ("Bikaner", 28.02, 73.31), ("Kota", 25.21, 75.86)],
    "Sikkim": [("Gangtok", 27.33, 88.61)],
    "Tamil Nadu": [("Chennai", 13.08, 80.27), ("Coimbatore", 11.02, 76.96),
                   ("Madurai", 9.93, 78.12), ("Tiruchirappalli", 10.79, 78.70)],
    "Telangana": [("Hyderabad", 17.38, 78.48), ("Warangal", 17.97, 79.59), ("Nizamabad", 18.67, 78.09)],
    "Tripura": [("Agartala", 23.83, 91.28)],
    "Uttar Pradesh": [("Lucknow", 26.85, 80.95), ("Varanasi", 25.32, 83.01), ("Agra", 27.18, 78.01),
                      ("Prayagraj", 25.44, 81.85), ("Meerut", 28.98, 77.71), ("Gorakhpur", 26.76, 83.37)],
    "Uttarakhand": [("Dehradun", 30.32, 78.03), ("Nainital", 29.38, 79.46), ("Pithoragarh", 29.58, 80.21)],
    "West Bengal": [("Kolkata", 22.57, 88.36), ("Siliguri", 26.72, 88.43), ("Asansol", 23.68, 86.98)],
    "Andaman & Nicobar": [("Port Blair", 11.62, 92.73)],
    "Chandigarh (UT)": [("Chandigarh", 30.73, 76.78)],
    "Dadra & Nagar Haveli and Daman & Diu": [("Daman", 20.40, 72.83)],
    "Delhi": [("New Delhi", 28.61, 77.21)],
    "Jammu & Kashmir": [("Srinagar", 34.08, 74.80), ("Jammu", 32.73, 74.87)],
    "Ladakh": [("Leh", 34.15, 77.58)],
    "Lakshadweep": [("Kavaratti", 10.57, 72.64)],
    "Puducherry": [("Puducherry", 11.93, 79.83)],
}

# ---- region set (India by default; the world site swaps these via configure_world) ----
TZ = "Asia/Kolkata"           # "auto" = each place's own local time zone (world site)
SET_NAME = "India"
GROUPS = {}                   # region -> calibration group (world site only)
REGION_INFO = {}              # region -> {"continent": ..., "subregion": ...} (world site only)

PREV_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

DEFAULT_MODEL = "ecmwf_ifs025"
DEFAULT_LEADS = [1, 2, 3, 5]
DEFAULT_MONTHS = [5, 6, 7, 8, 9, 10, 11]   # May-Nov: pre-monsoon, monsoon, withdrawal, post-monsoon
WINDOW_START = 9                            # 9 = IMD day (08:30->08:30 IST); 25 = calendar day
ENSEMBLE_MODELS = "ecmwf_ifs025,gfs025"
ENSEMBLE_FALLBACK = "ecmwf_ifs025"

# Rain events (mm per day, per grid cell). 2.5/15.6/64.5 follow IMD classes; "local" is a
# grid-scale proxy for heavier spot rain and is tunable.
EVENTS = {"rain": 2.5, "moderate": 15.6, "local": 35.0, "heavy": 64.5}
GUST_KMH = 50.0
CAPE_J = 1000.0
TS_HOURLY_MM = 0.5

# Continuous weather variables. "src" = Open-Meteo hourly variable, "agg" = how hours become a
# calendar-day value, "scale" converts units (sunshine: seconds -> hours).
DEG_C = "\u00b0C"
CONT_VARS = {
    "tmax":  {"src": "temperature_2m",       "agg": "max",  "scale": 1.0,      "unit": DEG_C,   "dp": 1,
              "label": "Maximum temperature",       "lo": None, "hi": None},
    "tmin":  {"src": "temperature_2m",       "agg": "min",  "scale": 1.0,      "unit": DEG_C,   "dp": 1,
              "label": "Minimum temperature",       "lo": None, "hi": None},
    "rh":    {"src": "relative_humidity_2m", "agg": "mean", "scale": 1.0,      "unit": "%",     "dp": 0,
              "label": "Humidity (daily average)",  "lo": 0,    "hi": 100},
    "wind":  {"src": "wind_speed_10m",       "agg": "max",  "scale": 1.0,      "unit": "km/h",  "dp": 0,
              "label": "Wind speed (daily peak)",   "lo": 0,    "hi": None},
    "gust":  {"src": "wind_gusts_10m",       "agg": "max",  "scale": 1.0,      "unit": "km/h",  "dp": 0,
              "label": "Wind gusts (daily peak)",   "lo": 0,    "hi": None},
    "sun":   {"src": "sunshine_duration",    "agg": "sum",  "scale": 1/3600.0, "unit": "h",     "dp": 1,
              "label": "Sunshine",                  "lo": 0,    "hi": 24},
    "cloud": {"src": "cloud_cover",          "agg": "mean", "scale": 1.0,      "unit": "%",     "dp": 0,
              "label": "Cloud cover (daily average)", "lo": 0,  "hi": 100},
}
CONT_SRCS = []
for _v in CONT_VARS.values():
    if _v["src"] not in CONT_SRCS:
        CONT_SRCS.append(_v["src"])
DEFAULT_EXTRA_LEADS = [1, 3, 5]

HOURLY_VARS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "precipitation", "wind_speed_10m", "surface_pressure",
    "cloud_cover", "weather_code",
]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def parse_date(text):
    """Accept dd/mm/yy (preferred) or dd/mm/yyyy."""
    text = text.strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("Invalid date. Use dd/mm/yy, e.g. 05/10/26")


def day_text():
    return "08:30 -> 08:30 IST (IMD convention)" if WINDOW_START == 9 else "calendar day (local time)"


def load_regions(path):
    """Replace STATES / GROUPS / REGION_INFO with a region set stored as JSON (see world_points.json)."""
    global STATES, GROUPS, REGION_INFO
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    STATES = {r["name"]: [(p[0], float(p[1]), float(p[2])) for p in r["points"]] for r in data["regions"]}
    GROUPS = {r["name"]: r.get("group") for r in data["regions"]}
    REGION_INFO = {r["name"]: {"continent": r.get("continent"), "subregion": r.get("subregion"),
                               "parent": r.get("parent")} for r in data["regions"]}


def configure_world(points_path=None):
    """Switch everything in this module to the world set: country sample points, local time zones,
    calendar-day windows and a separate cache folder (world_cache)."""
    global TZ, WINDOW_START, CACHE_DIR, SET_NAME
    TZ = "auto"
    WINDOW_START = 25
    SET_NAME = "World"
    CACHE_DIR = os.path.join(HERE, "world_cache")
    load_regions(points_path or os.path.join(HERE, "world_points.json"))


def key(state, city):
    return f"{state}|{city}"


def all_points():
    return [(s, c, la, lo) for s, pts in STATES.items() for (c, la, lo) in pts]


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def get_json(url, params, retries=4, timeout=180):
    """GET + parse JSON. Retries timeouts, dropped connections and temporary server
    errors with a growing wait; raises RuntimeError only after the last attempt."""
    full_url = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        wait = 15 * (attempt + 1)
        last = attempt == retries - 1
        try:
            with urllib.request.urlopen(full_url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            if e.code == 429 and "daily" in body.lower():
                raise RuntimeError("Open-Meteo's daily request limit is used up. Everything downloaded so far "
                                   "is cached; run the same command again tomorrow.")
            if e.code in (429, 500, 502, 503, 504) and not last:
                print(f"  server said {e.code}; retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"API error {e.code}: {body}")
        except (OSError, ValueError) as e:     # TimeoutError, URLError, reset connection, cut-off JSON
            if not last:
                print(f"  network problem ({type(e).__name__}); retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"network error after {retries} tries: {type(e).__name__}: {e}")


_DEAD = set()          # (url, variable) pairs the server rejected with HTTP 400


def get_json_resilient(url, params, field="hourly"):
    """Like get_json, but if the server rejects the request (HTTP 400) because one variable is not
    available, find which ones, leave them out and carry on. The rest of the data is still returned."""
    names = [n for n in params[field].split(",") if (url, n) not in _DEAD]
    params = dict(params)
    params[field] = ",".join(names)
    try:
        return get_json(url, params)
    except RuntimeError as e:
        if "API error 400" not in str(e) or len(names) < 2:
            raise
        probe = dict(params)
        probe["latitude"] = params["latitude"].split(",")[0]
        probe["longitude"] = params["longitude"].split(",")[0]
        if "past_days" in probe:
            probe["past_days"] = 1
        elif "start_date" in probe:
            probe["end_date"] = probe["start_date"]
        bad = []
        for n in names:
            try:
                get_json(url, dict(probe, **{field: n}))
            except RuntimeError as e2:
                if "API error 400" not in str(e2):
                    raise
                bad.append(n)
        if not bad or len(bad) == len(names):
            raise
        for n in bad:
            _DEAD.add((url, n))
        print(f"  note: the weather service does not offer {', '.join(bad)}; carrying on without "
              f"{'it' if len(bad) == 1 else 'them'}.")
        params[field] = ",".join(n for n in names if n not in bad)
        return get_json(url, params)


def pick(h, name):
    """Find a series by name; tolerate a model suffix like name_ecmwf_ifs025."""
    if name in h:
        return h[name]
    for k, v in h.items():
        if k.startswith(name + "_"):
            return v
    return None


def as_list(data):
    return [data] if isinstance(data, dict) else data


def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Day windows (so forecast, ensemble and IMD truth all use the same 24 h)
# --------------------------------------------------------------------------
def group_by_window(times):
    """Map window-date -> list of hourly indices. Hourly rain at label T is the
    total of the hour ending at T."""
    groups = {}
    shift = timedelta(hours=24 - WINDOW_START)
    for i, t in enumerate(times):
        d = (datetime.fromisoformat(t) + shift).date().isoformat()
        groups.setdefault(d, []).append(i)
    return groups


def window_sum(values, idx, min_valid=20):
    if values is None or len(idx) < 24:
        return None
    vals = [values[i] for i in idx if values[i] is not None]
    return round(sum(vals), 2) if len(vals) >= min_valid else None


def window_max(values, idx, min_valid=20):
    if values is None or len(idx) < 24:
        return None
    vals = [values[i] for i in idx if values[i] is not None]
    return round(max(vals), 1) if len(vals) >= min_valid else None


def group_by_calendar(times):
    """Map calendar date (IST) -> list of hourly indices."""
    groups = {}
    for i, t in enumerate(times):
        groups.setdefault(t[:10], []).append(i)
    return groups


def day_value(values, idx, spec, min_valid=20):
    """Daily value of a CONT_VARS variable from its hourly series (None if the day is incomplete)."""
    if values is None or len(idx) < 24:
        return None
    vals = [values[i] for i in idx if values[i] is not None]
    if len(vals) < min_valid:
        return None
    agg = spec["agg"]
    if agg == "max":
        v = max(vals)
    elif agg == "min":
        v = min(vals)
    elif agg == "mean":
        v = sum(vals) / len(vals)
    else:
        v = sum(vals) * len(idx) / len(vals)
    return round(v * spec["scale"], 2)


def limit_hit(err):
    return "daily request limit" in str(err)


def month_range(year, month):
    first = date(year, month, 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    return first, nxt - timedelta(days=1)


# --------------------------------------------------------------------------
# Past forecasts (Previous Runs API)
# --------------------------------------------------------------------------
def fetch_prev_runs_month(year, month, model=DEFAULT_MODEL, leads=None, points=None):
    """{key: {date: {lead(str): mm}}} for one month, cached on disk."""
    leads = sorted(leads or DEFAULT_LEADS)
    points = points or all_points()
    path = _cache_path(f"prev_{model}_w{WINDOW_START}_{year}-{month:02d}.json")
    cached = _read_json(path) or {}
    data = cached.get("data", {}) if cached.get("leads") == leads else {}

    missing = [p for p in points if key(p[0], p[1]) not in data]
    if not missing:
        return data

    first, last = month_range(year, month)
    hourly = ",".join(f"precipitation_previous_day{n}" for n in leads)
    prefix = f"{year}-{month:02d}"
    got_any = False
    for n, chunk in enumerate(chunks(missing, 4)):
        params = {
            "latitude": ",".join(str(p[2]) for p in chunk),
            "longitude": ",".join(str(p[3]) for p in chunk),
            "hourly": hourly,
            "models": model,
            "start_date": (first - timedelta(days=1)).isoformat(),
            "end_date": (last + timedelta(days=1)).isoformat(),
            "timezone": TZ,
        }
        res_list = as_list(get_json(PREV_RUNS_URL, params))
        for (state, city, _, _), res in zip(chunk, res_list):
            h = res["hourly"]
            groups = group_by_window(h["time"])
            per_day = {}
            for lead in leads:
                vals = pick(h, f"precipitation_previous_day{lead}")
                for d, idx in groups.items():
                    if not d.startswith(prefix):
                        continue
                    s = window_sum(vals, idx)
                    if s is not None:
                        per_day.setdefault(d, {})[str(lead)] = s
                        got_any = True
            if per_day:                      # never cache empty results; retry next time
                data[key(state, city)] = per_day
        _write_json(path, {"leads": leads, "model": model, "data": data})
        time.sleep(1.0)
    if not got_any:
        print(f"  WARNING: no previous-run values returned for {model} in {prefix} "
              f"(archive may not reach back this far for this model).")
    return data


# --------------------------------------------------------------------------
# Truth (observed rainfall)
# --------------------------------------------------------------------------
def fetch_truth_era5_month(year, month, points=None):
    """ERA5 window sums via Open-Meteo. WEAK truth: it is itself a model product."""
    points = points or all_points()
    path = _cache_path(f"truth_era5_w{WINDOW_START}_{year}-{month:02d}.json")
    data = _read_json(path) or {}
    missing = [p for p in points if key(p[0], p[1]) not in data]
    if not missing:
        return data
    first, last = month_range(year, month)
    prefix = f"{year}-{month:02d}"
    for chunk in chunks(missing, 12):
        params = {
            "latitude": ",".join(str(p[2]) for p in chunk),
            "longitude": ",".join(str(p[3]) for p in chunk),
            "hourly": "precipitation",
            "start_date": (first - timedelta(days=1)).isoformat(),
            "end_date": (last + timedelta(days=1)).isoformat(),
            "timezone": TZ,
        }
        for (state, city, _, _), res in zip(chunk, as_list(get_json(ARCHIVE_URL, params))):
            h = res["hourly"]
            vals = pick(h, "precipitation")
            per_day = {}
            for d, idx in group_by_window(h["time"]).items():
                if d.startswith(prefix):
                    s = window_sum(vals, idx)
                    if s is not None:
                        per_day[d] = s
            data[key(state, city)] = per_day
        _write_json(path, data)
        time.sleep(1.0)
    return data


def fetch_prev_extra_month(year, month, model=DEFAULT_MODEL, leads=None, points=None):
    """Past forecasts of temperature/humidity/wind/sun/cloud for one month, cached.
    -> {key: {date: {var: {lead(str): value}}}}"""
    leads = sorted(leads or DEFAULT_EXTRA_LEADS)
    points = points or all_points()
    path = _cache_path(f"prevx_{model}_{year}-{month:02d}.json")
    cached = _read_json(path) or {}
    data = cached.get("data", {}) if cached.get("leads") == leads else {}
    missing = [p for p in points if key(p[0], p[1]) not in data]
    if not missing:
        return data
    first, last = month_range(year, month)
    hourly = ",".join(f"{src}_previous_day{n}" for src in CONT_SRCS for n in leads)
    prefix = f"{year}-{month:02d}"
    got_any = False
    for chunk in chunks(missing, 4):
        params = {
            "latitude": ",".join(str(p[2]) for p in chunk),
            "longitude": ",".join(str(p[3]) for p in chunk),
            "hourly": hourly,
            "models": model,
            "start_date": first.isoformat(),
            "end_date": last.isoformat(),
            "timezone": TZ,
        }
        for (state, city, _, _), res in zip(chunk, as_list(get_json_resilient(PREV_RUNS_URL, params))):
            h = res["hourly"]
            groups = group_by_calendar(h["time"])
            per_day = {}
            for lead in leads:
                for var, spec in CONT_VARS.items():
                    vals = pick(h, f"{spec['src']}_previous_day{lead}")
                    for d, idx in groups.items():
                        if d.startswith(prefix):
                            v = day_value(vals, idx, spec)
                            if v is not None:
                                per_day.setdefault(d, {}).setdefault(var, {})[str(lead)] = v
                                got_any = True
            if per_day:
                data[key(state, city)] = per_day
        _write_json(path, {"leads": leads, "model": model, "data": data})
        time.sleep(1.0)
    if not got_any:
        print(f"  WARNING: no extra-variable forecasts returned for {model} in {prefix}.")
    return data


def fetch_truth_extra_month(year, month, points=None):
    """ERA5 daily values of the same variables (reference for the backtest). Cached.
    -> {key: {date: {var: value}}}"""
    points = points or all_points()
    path = _cache_path(f"truthx_era5_{year}-{month:02d}.json")
    data = _read_json(path) or {}
    missing = [p for p in points if key(p[0], p[1]) not in data]
    if not missing:
        return data
    first, last = month_range(year, month)
    prefix = f"{year}-{month:02d}"
    for chunk in chunks(missing, 6):
        params = {
            "latitude": ",".join(str(p[2]) for p in chunk),
            "longitude": ",".join(str(p[3]) for p in chunk),
            "hourly": ",".join(CONT_SRCS),
            "start_date": first.isoformat(),
            "end_date": last.isoformat(),
            "timezone": TZ,
        }
        for (state, city, _, _), res in zip(chunk, as_list(get_json_resilient(ARCHIVE_URL, params))):
            h = res["hourly"]
            groups = group_by_calendar(h["time"])
            per_day = {}
            for var, spec in CONT_VARS.items():
                vals = pick(h, spec["src"])
                for d, idx in groups.items():
                    if d.startswith(prefix):
                        v = day_value(vals, idx, spec)
                        if v is not None:
                            per_day.setdefault(d, {})[var] = v
            if per_day:
                data[key(state, city)] = per_day
        _write_json(path, data)
        time.sleep(1.0)
    return data


IMD_URL = "https://imdpune.gov.in/cmpg/Griddata/rainfall.php"
IMD_NLAT, IMD_NLON = 129, 135                 # IMD 0.25-degree rainfall grid
IMD_LAT0, IMD_LON0, IMD_STEP = 6.5, 66.5, 0.25
IMD_DAY_BYTES = IMD_NLAT * IMD_NLON * 4       # one day = float32 grid, layout (lat, lon), lon fastest


def _imd_find(year):
    """Path of the year's IMD file if present (accepts the names IMD / imdlib use)."""
    for p in (os.path.join(IMD_DIR, "rain", f"{year}.grd"),
              os.path.join(IMD_DIR, "rain", f"Rainfall_ind{year}_rfp25.grd"),
              os.path.join(IMD_DIR, f"Rainfall_ind{year}_rfp25.grd")):
        if os.path.isfile(p) and os.path.getsize(p) >= IMD_DAY_BYTES * 28 and \
                os.path.getsize(p) % IMD_DAY_BYTES == 0:
            return p
    return None


def download_imd_year(year):
    """Download IMD's gridded daily rainfall for one year (about 25 MB) - standard library only."""
    path = _imd_find(year)
    if path:
        return path
    dest = os.path.join(IMD_DIR, "rain", f"{year}.grd")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    body = urllib.parse.urlencode({"rain": year}).encode()
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(IMD_URL, data=body, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                content = resp.read()
            if len(content) < IMD_DAY_BYTES * 28 or len(content) % IMD_DAY_BYTES:
                raise RuntimeError(f"IMD returned {len(content)} bytes, which is not a rainfall data file "
                                   f"(server error page or blocked request)")
            with open(dest + ".tmp", "wb") as f:
                f.write(content)
            os.replace(dest + ".tmp", dest)
            return dest
        except (OSError, RuntimeError) as e:
            last = e
            if attempt < 2:
                print(f"  IMD download problem ({type(e).__name__}: {e}); retrying in {20 * (attempt + 1)}s...")
                time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"could not download IMD {year}: {last}. Manual option: get the year's rainfall "
                       f".grd from IMD's gridded-data page in a browser and save it as {dest}")


def read_imd_points(path, year, points):
    """Daily rainfall at the nearest grid cell of every point -> {key: {date: mm}}."""
    from array import array
    days = os.path.getsize(path) // IMD_DAY_BYTES
    cells = []
    for (state, city, lat, lon) in points:
        i = min(max(int(round((lat - IMD_LAT0) / IMD_STEP)), 0), IMD_NLAT - 1)
        j = min(max(int(round((lon - IMD_LON0) / IMD_STEP)), 0), IMD_NLON - 1)
        cells.append((key(state, city), i * IMD_NLON + j))
    out = {k: {} for k, _ in cells}
    start = date(year, 1, 1)
    with open(path, "rb") as f:
        for d in range(days):
            buf = array("f")
            buf.fromfile(f, IMD_NLAT * IMD_NLON)
            if sys.byteorder == "big":
                buf.byteswap()
            ds = (start + timedelta(days=d)).isoformat()
            for k, pos in cells:
                v = buf[pos]
                if v == v and v >= 0:                 # skips NaN and the -999 'no data' value
                    out[k][ds] = round(v, 2)
    return out, days


def ensure_truth_imd(year, points=None):
    """IMD 0.25-degree daily rainfall at the nearest grid cell of every point (cached)."""
    points = points or all_points()
    cpath = _cache_path(f"truth_imd_{year}.json")
    cached = _read_json(cpath)
    if cached:
        return cached
    path = download_imd_year(year)
    out, days = read_imd_points(path, year, points)
    if days >= 365:                                    # don't freeze a partial current-year file
        _write_json(cpath, out)
    else:
        print(f"  note: {year} file has only {days} days (partial year); not cached")
    return out


# --------------------------------------------------------------------------
# Loading cached data (used by backtest.py)
# --------------------------------------------------------------------------
def cached_periods(model=DEFAULT_MODEL):
    prefix = f"prev_{model}_w{WINDOW_START}_"
    out = []
    if os.path.isdir(CACHE_DIR):
        for fn in sorted(os.listdir(CACHE_DIR)):
            if fn.startswith(prefix) and fn.endswith(".json"):
                ym = fn[len(prefix):-5]
                try:
                    y, m = ym.split("-")
                    out.append((int(y), int(m)))
                except ValueError:
                    pass
    return out


def load_prev_runs(model=DEFAULT_MODEL):
    """-> (forecasts {key:{date:{lead:int->mm}}}, periods, leads_available)"""
    forecasts, leads = {}, set()
    periods = cached_periods(model)
    for y, m in periods:
        c = _read_json(_cache_path(f"prev_{model}_w{WINDOW_START}_{y}-{m:02d}.json")) or {}
        for k, days in c.get("data", {}).items():
            for d, lv in days.items():
                for l, mm in lv.items():
                    forecasts.setdefault(k, {}).setdefault(d, {})[int(l)] = mm
                    leads.add(int(l))
    return forecasts, periods, sorted(leads)


def load_truth(source, periods):
    truth = {}

    def merge(block):
        for k, days in block.items():
            truth.setdefault(k, {}).update(days)

    if source == "imd":
        for y in sorted({y for y, _ in periods}):
            block = _read_json(_cache_path(f"truth_imd_{y}.json"))
            if block:
                merge(block)
    else:
        for y, m in periods:
            block = _read_json(_cache_path(f"truth_era5_w{WINDOW_START}_{y}-{m:02d}.json"))
            if block:
                merge(block)
    return truth


def cached_extra_periods(model=DEFAULT_MODEL):
    prefix = f"prevx_{model}_"
    out = []
    if os.path.isdir(CACHE_DIR):
        for fn in sorted(os.listdir(CACHE_DIR)):
            if fn.startswith(prefix) and fn.endswith(".json"):
                try:
                    y, m = fn[len(prefix):-5].split("-")
                    out.append((int(y), int(m)))
                except ValueError:
                    pass
    return out


def load_prev_extra(model=DEFAULT_MODEL):
    """-> (forecasts {key:{date:{var:{lead:int->value}}}}, periods, leads_available)"""
    forecasts, leads = {}, set()
    periods = cached_extra_periods(model)
    for y, m in periods:
        c = _read_json(_cache_path(f"prevx_{model}_{y}-{m:02d}.json")) or {}
        for k, days in c.get("data", {}).items():
            for d, byvar in days.items():
                for var, lv in byvar.items():
                    for l, v in lv.items():
                        forecasts.setdefault(k, {}).setdefault(d, {}).setdefault(var, {})[int(l)] = v
                        leads.add(int(l))
    return forecasts, periods, sorted(leads)


def load_truth_extra(periods):
    truth = {}
    for y, m in periods:
        block = _read_json(_cache_path(f"truthx_era5_{y}-{m:02d}.json")) or {}
        for k, days in block.items():
            truth.setdefault(k, {}).update(days)
    return truth


# --------------------------------------------------------------------------
# Live data for a future date (used by predict.py)
# --------------------------------------------------------------------------
def _forecast_end(target):
    today = date.today()
    nxt = target + timedelta(days=1)
    return nxt if nxt <= today + timedelta(days=15) else target


def fetch_forecast_window(target, model=DEFAULT_MODEL, points=None):
    """{key: {'rain': mm (IMD day), 'gust': km/h (IMD day), 'cont': {var: value}}} for the target day.
    'cont' holds the calendar-day temperature, humidity, wind, gusts, sunshine and cloud values."""
    points = points or all_points()
    out = {}
    hourly = ",".join(["precipitation"] + CONT_SRCS)
    for chunk in chunks(points, 8):
        params = {
            "latitude": ",".join(str(p[2]) for p in chunk),
            "longitude": ",".join(str(p[3]) for p in chunk),
            "hourly": hourly,
            "models": model,
            "start_date": (target - timedelta(days=1)).isoformat(),
            "end_date": _forecast_end(target).isoformat(),
            "timezone": TZ,
        }
        for (state, city, _, _), res in zip(chunk, as_list(get_json_resilient(FORECAST_URL, params))):
            h = res["hourly"]
            idx = group_by_window(h["time"]).get(target.isoformat(), [])
            cal_idx = group_by_calendar(h["time"]).get(target.isoformat(), [])
            out[key(state, city)] = {
                "rain": window_sum(pick(h, "precipitation"), idx),
                "gust": window_max(pick(h, "wind_gusts_10m"), idx),
                "cont": {var: day_value(pick(h, spec["src"]), cal_idx, spec) for var, spec in CONT_VARS.items()},
            }
        time.sleep(0.5)
    return out


def _member_series(h, var):
    out = {}
    for k, vals in h.items():
        if k.startswith(var) and k != "time":
            out[k[len(var):]] = vals
    return out


def member_table(h, idx):
    """Per-member summary for one point over the day window."""
    precip = _member_series(h, "precipitation")
    gust = _member_series(h, "wind_gusts_10m")
    cape = _member_series(h, "cape")
    table = {}
    if len(idx) < 24:
        return table
    for s, p in precip.items():
        pv = [p[i] for i in idx if p[i] is not None]
        if len(pv) < 20:
            continue
        g = [gust[s][i] for i in idx if s in gust and gust[s][i] is not None]
        c = cape.get(s)
        ts = bool(c) and any(c[i] is not None and p[i] is not None and
                             c[i] >= CAPE_J and p[i] >= TS_HOURLY_MM for i in idx)
        table[s] = {"rain": sum(pv), "gust": max(g) if g else None, "ts": ts, "has_cape": bool(c)}
    return table


def fetch_ensemble_members(target, points=None, models=ENSEMBLE_MODELS):
    """({key: member_table}, models_used). Falls back to ECMWF-only if the multi-model call fails."""
    points = points or all_points()
    tables = {}
    for i, chunk in enumerate(chunks(points, 6)):
        params = {
            "latitude": ",".join(str(p[2]) for p in chunk),
            "longitude": ",".join(str(p[3]) for p in chunk),
            "hourly": "precipitation,wind_gusts_10m,cape",
            "models": models,
            "start_date": (target - timedelta(days=1)).isoformat(),
            "end_date": _forecast_end(target).isoformat(),
            "timezone": TZ,
        }
        try:
            data = get_json(ENSEMBLE_URL, params)
        except RuntimeError as e:
            if i == 0 and models != ENSEMBLE_FALLBACK:
                print(f"  multi-model ensemble failed ({e}); falling back to ECMWF only")
                models = ENSEMBLE_FALLBACK
                params["models"] = models
                data = get_json(ENSEMBLE_URL, params)
            else:
                raise
        for (state, city, _, _), res in zip(chunk, as_list(data)):
            h = res["hourly"]
            idx = group_by_window(h["time"]).get(target.isoformat(), [])
            tables[key(state, city)] = member_table(h, idx)
        print(f"  ensemble: {min((i + 1) * 6, len(points))}/{len(points)} points")
        time.sleep(1.0)
    return tables, models


def fetch_hourly_primary(day):
    """Hourly weather (calendar day) at each state's primary point, for the 30-min tables."""
    names = list(STATES)
    result = {}
    for chunk in chunks(names, 12):
        params = {
            "latitude": ",".join(str(STATES[n][0][1]) for n in chunk),
            "longitude": ",".join(str(STATES[n][0][2]) for n in chunk),
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "hourly": ",".join(HOURLY_VARS),
            "timezone": TZ,
        }
        for n, res in zip(chunk, as_list(get_json(FORECAST_URL, params))):
            result[n] = res["hourly"]
        time.sleep(0.5)
    return result


HISTORY_ARCHIVE_LAG = 6        # ERA5 archive is about 5 days behind


def fetch_history_points(label, defs, days=30):
    """Recent past for a list of places [(name, lat, lon), ...]: one daily value per day.
    Up to 90 days: Open-Meteo model analysis up to yesterday. More than that (12 months): the ERA5
    archive, which ends about 6 days ago, using at most 8 of the places to save request quota.
    Value for the region = average of its places (gusts: strongest place; rain: average and wettest)."""
    days = max(1, min(int(days), 365))
    today = date.today()
    hourly = ",".join(["precipitation"] + CONT_SRCS)
    if days > 90:
        end = today - timedelta(days=HISTORY_ARCHIVE_LAG)
        dates = [(end - timedelta(days=k)).isoformat() for k in range(days - 1, -1, -1)]
        idx = sorted({round(i * (len(defs) - 1) / 7) for i in range(8)}) if len(defs) > 8 else range(len(defs))
        use = [defs[i] for i in idx]
        url = ARCHIVE_URL
        extra = {"start_date": (end - timedelta(days=days)).isoformat(), "end_date": end.isoformat()}
        source = "archive"
    else:
        dates = [(today - timedelta(days=k)).isoformat() for k in range(days, 0, -1)]
        use = list(defs)
        url = FORECAST_URL
        extra = {"past_days": days + 1, "forecast_days": 1}
        source = "analysis"
    params = {"latitude": ",".join(str(p[1]) for p in use), "longitude": ",".join(str(p[2]) for p in use),
              "hourly": hourly, "timezone": TZ}
    params.update(extra)
    res_list = as_list(get_json_resilient(url, params))
    rain_pts, cont_pts = [], {v: [] for v in CONT_VARS}
    for res in res_list:
        h = res["hourly"]
        win, cal = group_by_window(h["time"]), group_by_calendar(h["time"])
        prec = pick(h, "precipitation")
        rain_pts.append([window_sum(prec, win.get(d, [])) for d in dates])
        for var, spec in CONT_VARS.items():
            vals = pick(h, spec["src"])
            cont_pts[var].append([day_value(vals, cal.get(d, []), spec) for d in dates])

    def combine(rows, fn):
        out = []
        for col in zip(*rows):
            v = [x for x in col if x is not None]
            out.append(round(fn(v), 1) if v else None)
        return out

    mean = lambda v: sum(v) / len(v)
    series = {var: combine(cont_pts[var], max if var == "gust" else mean) for var in CONT_VARS}
    series["rain_mean"] = combine(rain_pts, mean)
    series["rain_max"] = combine(rain_pts, max)
    return {"state": label, "dates": dates, "series": series, "n_points": len(use), "source_kind": source}


def fetch_history(state, days=30):
    return fetch_history_points(state, STATES[state], days)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_months(text):
    out = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(m for m in out if 1 <= m <= 12))


def cmd_download(a):
    months = parse_months(a.months)
    leads = sorted(int(x) for x in a.leads.split(","))
    today = date.today()
    periods = []
    for y in range(a.start_year, a.end_year + 1):
        for m in months:
            if date(y, m, 1) > today - timedelta(days=8):
                print(f"skip {y}-{m:02d} (too recent / future)")
                continue
            periods.append((y, m))
    if not periods:
        print("Nothing to download for those years/months.")
        return
    print(f"Model {a.model}, leads {leads}, truth {a.truth}, {len(periods)} month(s). "
          f"This takes a while; progress is cached, so you can stop and re-run.\n")

    done = []
    for y, m in periods:
        print(f"[forecasts] {y}-{m:02d}")
        try:
            data = fetch_prev_runs_month(y, m, a.model, leads)
            n = sum(len(v) for v in data.values())
            if n == 0:
                print("  no forecast data returned for this month (model archive may not cover it)")
            else:
                print(f"  ok: {len(data)} points, {n} point-days")
                done.append((y, m))
        except (RuntimeError, OSError) as e:
            print(f"  FAILED: {e}  (cached progress is kept; re-run to retry this month)")
            if limit_hit(e):
                break

    print()
    if a.truth == "imd":
        for y in sorted({y for y, _ in done}):
            print(f"[truth: IMD] {y}")
            try:
                ensure_truth_imd(y)
                print("  ok")
            except Exception as e:  # network, blocked request, bad file...
                print(f"  FAILED: {type(e).__name__}: {e}")
                print("  Re-run to retry, or use --truth era5 (weak: not independent of the model).")
    else:
        for y, m in done:
            print(f"[truth: ERA5] {y}-{m:02d}")
            try:
                fetch_truth_era5_month(y, m)
                print("  ok")
            except (RuntimeError, OSError) as e:
                print(f"  FAILED: {e}")
    if not a.no_extras:
        eleads = sorted(int(x) for x in a.extra_leads.split(","))
        print(f"\n[extra weather: temperature, humidity, wind, gusts, sunshine, cloud]  leads {eleads}, truth ERA5")
        for y, m in periods:
            print(f"[extras] {y}-{m:02d}")
            try:
                data = fetch_prev_extra_month(y, m, a.model, eleads)
                fetch_truth_extra_month(y, m)
                print(f"  ok: {sum(len(v) for v in data.values())} point-days")
            except (RuntimeError, OSError) as e:
                print(f"  FAILED: {e}  (cached progress is kept; re-run to retry this month)")
                if limit_hit(e):
                    break
    if SET_NAME == "World":
        print("\nDone. Next: py world.py backtest")
    else:
        print("\nDone. Next: python backtest.py" + ("" if a.truth == "imd" else " --truth era5"))


def cmd_status(a):
    print(f"Cache folder: {CACHE_DIR}")
    for model in sorted({fn.split('_w')[0][5:] for fn in (os.listdir(CACHE_DIR) if os.path.isdir(CACHE_DIR) else [])
                         if fn.startswith("prev_")} or [DEFAULT_MODEL]):
        fc, periods, leads = load_prev_runs(model)
        print(f"Forecast months cached for {model}: {len(periods)} {periods}  leads={leads}")
    _, xp, xl = load_prev_extra(a.model)
    print(f"Extra-variable months cached: {len(xp)} {xp}  leads={xl}")
    print(f"Extra-variable truth (ERA5): {len(load_truth_extra(xp))} points")
    for src in ("imd", "era5"):
        _, periods, _ = load_prev_runs(a.model)
        truth = load_truth(src, periods)
        n = sum(len(v) for v in truth.values())
        print(f"Truth '{src}': {len(truth)} points, {n} point-days")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Data access layer for the India weather scripts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download + cache forecasts and truth")
    d.add_argument("--start-year", type=int, default=2024)
    d.add_argument("--end-year", type=int, default=2025)
    d.add_argument("--months", default="5-11", help="e.g. 5-11 or 6,7,8,9")
    d.add_argument("--model", default=DEFAULT_MODEL)
    d.add_argument("--leads", default=",".join(map(str, DEFAULT_LEADS)))
    d.add_argument("--truth", choices=["imd", "era5"], default="imd")
    d.add_argument("--no-extras", action="store_true", help="skip temperature/humidity/wind/sunshine/cloud")
    d.add_argument("--extra-leads", default=",".join(map(str, DEFAULT_EXTRA_LEADS)),
                   help="lead days for the extra variables (default 1,3,5)")
    s = sub.add_parser("status", help="show what is cached")
    s.add_argument("--model", default=DEFAULT_MODEL)
    a = ap.parse_args(argv)
    try:
        {"download": cmd_download, "status": cmd_status}[a.cmd](a)
    except KeyboardInterrupt:
        print("\nStopped. Everything downloaded so far is cached; run the same command again to resume.")


if __name__ == "__main__":
    main()
