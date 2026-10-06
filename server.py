#!/usr/bin/env python3
"""
server.py - local website: click a state on the map of India, pick a future date,
see the chance of rain / heavy rain / storms.

What it shows for the chosen state: the forecast for a future date (rain chances, temperature,
humidity, wind, sunshine, cloud) and the recent past (last 7 / 30 / 90 days).

Run:      py server.py            (opens http://localhost:8000 in your browser)
Options:  py server.py --port 8080 --no-browser
          py server.py --site world      (the world version: every country, plus any spot you click;
                                          needs world_map.json and world_points.json - see world.py)

Put these files in the same folder (the one that has backend.py and predict.py):
    server.py   index.html   india_states.json
(or put index.html and india_states.json in a sub-folder called "web").

Needs only the standard library. It listens on 127.0.0.1, so only your own
computer can open it. Forecasts come from backend.py / predict.py, so the numbers
are the same as the text-file report; if calibration.json exists (from backtest.py)
the chances are calibrated, otherwise they are marked UNCALIBRATED.
"""

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import backend
import predict

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
SITES = {
    "india": {"name": "India", "title": "India rain outlook", "map_file": "india_states.json",
              "region_word": "state", "region_plural": "states", "select_label": "State or union territory", "pin": False,
              "calib_hint": "run backend.py download, then backtest.py",
              "intro": "Pick a state on the map or from the list. See the forecast for any date up to 15 days ahead, "
                       "or what the weather has been lately.",
              "history_days": [7, 30, 90]},
    "world": {"name": "World", "title": "World weather outlook", "map_file": "world_map.json",
              "region_word": "country", "region_plural": "countries", "select_label": "Country or territory", "pin": True,
              "calib_hint": "run py world.py download, then py world.py backtest",
              "intro": "Click a country on the map, search for one, or pick any spot on Earth. See the forecast for any "
                       "date up to 15 days ahead, or what the weather has been lately.",
              "history_days": [7, 30, 90, 365]},
}
SITE = SITES["india"]
STATIC = {}


def configure_site(name):
    """Choose the India or world version. For world this also switches backend, calibration paths, etc."""
    global SITE, STATIC, HISTORY_CHOICES
    SITE = SITES[name]
    HISTORY_CHOICES = tuple(SITE["history_days"])
    STATIC = {"/": "index.html", "/index.html": "index.html", "/" + SITE["map_file"]: SITE["map_file"]}
    if name == "world":
        backend.configure_world()
        import backtest
        predict.CAL_PATH = os.path.join(HERE, "world_calibration.json")
        backtest.CAL_PATH = predict.CAL_PATH
        backtest.REPORT_PATH = os.path.join(HERE, "world_backtest_report.txt")
    _cache.clear()
    _hist_cache.clear()
    _layer_cache.clear()

MIME = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}
MAX_LEAD = 15
CACHE_TTL = 30 * 60
HISTORY_TTL = 60 * 60
HISTORY_CHOICES = (7, 30, 90)
CUSTOM = "Selected spot"

_cache = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# Forecast for one state
# --------------------------------------------------------------------------
def r1(v):
    return None if v is None else round(v, 1)


def var_meta():
    return {k: {"label": v["label"], "unit": v["unit"], "dp": v["dp"]} for k, v in backend.CONT_VARS.items()}


def rnd(v, dp):
    return None if v is None else round(v, dp)


def weather_payload(summary):
    out = []
    for key_, v in summary.items():
        dp = v["dp"]
        out.append({"key": key_, "label": v["label"], "unit": v["unit"], "dp": dp,
                    "value": rnd(v["best"], dp), "low": rnd(v["low"], dp), "high": rnd(v["high"], dp),
                    "adjusted": v["adjusted_flag"], "verdict": v["verdict"], "lead_used": v["lead_used"],
                    "min": rnd(v["min"], dp), "max": rnd(v["max"], dp),
                    "points": {c: rnd(x, dp) for c, x in v["points"].items()}})
    return out


def spot_label(lat, lon):
    return f"{abs(lat):.2f}\u00b0{'N' if lat >= 0 else 'S'} {abs(lon):.2f}\u00b0{'E' if lon >= 0 else 'W'}"


def forecast_payload(state, target, spot=None):
    """Forecast for a region (by name) or, with spot=(lat, lon), for one exact place on Earth."""
    lead = (target - date.today()).days
    if spot:
        state = "Spot " + spot_label(*spot)
        defs = [(CUSTOM, spot[0], spot[1])]
    else:
        defs = backend.STATES[state]
    points = [(state, c, la, lo) for c, la, lo in defs]
    model = backend.DEFAULT_MODEL
    cal = None if spot else predict.load_calibration()   # re-read each time: picks up a fresh backtest
    warnings = []

    det = backend.fetch_forecast_window(target, model, points)

    ens, ens_models = None, None
    try:
        tables, ens_models = backend.fetch_ensemble_members(target, points)
        ens = predict.ensemble_stats([tables.get(backend.key(state, c)) for c, _, _ in defs])
        if ens is None:
            warnings.append("The ensemble returned no usable members for this state.")
    except (RuntimeError, OSError) as e:
        warnings.append(f"Ensemble forecast unavailable: {e}")

    per_point, rains = [], {}
    for c, _, _ in defs:
        v = det.get(backend.key(state, c)) or {}
        per_point.append({"name": c, "rain_mm": r1(v.get("rain")), "gust_kmh": r1(v.get("gust"))})
        if v.get("rain") is not None:
            rains[c] = v["rain"]
    x = max(rains.values()) if rains else None
    worst = max(rains, key=rains.get) if rains else None
    gusts = [p["gust_kmh"] for p in per_point if p["gust_kmh"] is not None]

    group = None if spot else backend.GROUPS.get(state)
    probs, lead_used, note = predict.calibrate(cal, x, lead, target.month, model, group)
    if spot:
        note = "custom spots are not calibrated: these are raw model values"
    if probs:
        headline, source = probs.get("local"), "calibrated"
    elif ens:
        headline, source = ens["p_local"], "raw ensemble (uncalibrated)"
    else:
        headline, source = None, "none"

    skill = {}
    if cal:
        leads = sorted(cal["meta"]["leads"])
        near = lead_used or min(leads, key=lambda l: abs(l - lead))
        for ev in backend.EVENTS:
            s = cal["meta"].get("skill", {}).get(ev, {}).get(str(near))
            skill[ev] = s["verdict"] if s else "not scored"

    weather = predict.weather_summary(state, target, lead, det, cal, defs)
    weather_adjusted = any(v["adjusted_flag"] for v in weather.values())
    return {
        "state": state,
        "date": target.isoformat(),
        "lead_days": lead,
        "weather": weather_payload(weather),
        "weather_adjusted": weather_adjusted,
        "model": model,
        "events": backend.EVENTS,
        "forecast": {"wettest_point": worst, "rain_mm": r1(x),
                     "gust_kmh": max(gusts) if gusts else None},
        "points": per_point,
        "calibrated": None if not probs else {k: r1(v) for k, v in probs.items()},
        "ensemble": None if not ens else {
            "members": ens["members"], "models": ens_models,
            "p_rain": r1(ens["p_rain"]), "p_local": r1(ens["p_local"]), "p_heavy": r1(ens["p_heavy"]),
            "p_storm": r1(ens["p_storm"]), "p_ts": r1(ens["p_ts"]), "p_gust": r1(ens["p_gust"]),
            "mean_rain_mm": r1(ens["mean_rain"]), "gust_threshold_kmh": backend.GUST_KMH},
        "risk": {"label": predict.risk_label(headline), "percent": r1(headline), "source": source},
        "calibration": {
            "status": "calibrated" if probs else "uncalibrated",
            "note": (note or "").replace("UNCALIBRATED: ", ""),
            "built_at": cal["meta"]["built_at"] if cal else None,
            "truth": cal["meta"]["truth"] if cal else None,
            "skill": skill},
        "warnings": warnings,
    }


def cached_forecast(state, target, spot=None):
    k = (state if not spot else ("spot", round(spot[0], 2), round(spot[1], 2)), target.isoformat())
    now = time.time()
    with _lock:
        hit = _cache.get(k)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    data = forecast_payload(state, target, spot)
    with _lock:
        _cache[k] = (time.time(), data)
    return data


_hist_cache = {}
_layer_cache = {}
configure_site("india")          # default; --site world switches it


def cached_history(state, days, spot=None):
    k = ((state if not spot else ("spot", round(spot[0], 2), round(spot[1], 2))), days)
    now = time.time()
    with _lock:
        hit = _hist_cache.get(k)
        if hit and now - hit[0] < HISTORY_TTL:
            return hit[1]
    if spot:
        data = backend.fetch_history_points("Spot " + spot_label(*spot), [(CUSTOM, spot[0], spot[1])], days)
    else:
        data = backend.fetch_history(state, days)
    data["variables"] = var_meta()
    rain_day = ("Rain uses IMD's day (08:30 to 08:30 IST); the rest use the calendar day."
                if backend.WINDOW_START == 9 else "Days are calendar days in local time.")
    if data.get("source_kind") == "archive":
        data["source"] = ("ERA5 reanalysis (the archive ends about 6 days ago), one value per day. These are model "
                          "values, not station readings. " + rain_day +
                          (f" To save request quota only {data['n_points']} representative places are used."
                           if data["n_points"] >= 8 and not spot else ""))
    else:
        data["source"] = ("Open-Meteo model analysis (best match), one value per day. These are model values, "
                          "not station readings. " + rain_day)
    with _lock:
        _hist_cache[k] = (time.time(), data)
    return data


LAYER_VARS = [
    {"key": "rain", "label": "Rain at the wettest place", "unit": "mm", "dp": 1},
    {"key": "local", "label": "Chance of heavy rain (35 mm or more)", "unit": "%", "dp": 0},
    {"key": "tmax", "label": "Maximum temperature", "unit": "\u00b0C", "dp": 1},
    {"key": "tmin", "label": "Minimum temperature", "unit": "\u00b0C", "dp": 1},
    {"key": "rh", "label": "Humidity", "unit": "%", "dp": 0},
    {"key": "wind", "label": "Wind speed (peak)", "unit": "km/h", "dp": 0},
    {"key": "gust", "label": "Wind gusts (peak)", "unit": "km/h", "dp": 0},
    {"key": "sun", "label": "Sunshine", "unit": "h", "dp": 1},
    {"key": "cloud", "label": "Cloud cover", "unit": "%", "dp": 0},
]


def layer_list():
    """Groups of regions that belong to a parent (India's states inside the world map)."""
    out = {}
    for r, inf in backend.REGION_INFO.items():
        if inf.get("parent"):
            out[inf["parent"]] = out.get(inf["parent"], 0) + 1
    return [{"parent": p, "label": p + "'s states", "count": n} for p, n in sorted(out.items())]


def layer_payload(parent, target):
    """Raw forecast conditions for every region inside `parent`, for colouring the map."""
    lead = (target - date.today()).days
    regions = [r for r, inf in backend.REGION_INFO.items() if inf.get("parent") == parent]
    points = [(r, c, la, lo) for r in regions for c, la, lo in backend.STATES[r]]
    model = backend.DEFAULT_MODEL
    cal = predict.load_calibration()
    det = backend.fetch_forecast_window(target, model, points)
    values, calibrated = {}, False
    for r in regions:
        defs = backend.STATES[r]
        w = predict.weather_summary(r, target, lead, det, cal, defs)
        rains = [(det.get(backend.key(r, c)) or {}).get("rain") for c, _, _ in defs]
        rains = [x for x in rains if x is not None]
        x = max(rains) if rains else None
        probs, _, _ = predict.calibrate(cal, x, lead, target.month, model, backend.GROUPS.get(r))
        calibrated = calibrated or bool(probs)
        row = {"rain": rnd(x, 1), "local": rnd(probs["local"], 0) if probs else None}
        for k, v in w.items():
            row[k] = rnd(v["best"], v["dp"])
        values[r] = row
    return {"parent": parent, "date": target.isoformat(), "lead_days": lead, "values": values,
            "calibrated": calibrated, "vars": LAYER_VARS, "places": len(points)}


def cached_layer(parent, target):
    k = (parent, target.isoformat())
    now = time.time()
    with _lock:
        hit = _layer_cache.get(k)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    data = layer_payload(parent, target)
    with _lock:
        _layer_cache[k] = (time.time(), data)
    return data


def info_payload():
    today = date.today()
    cal = predict.load_calibration()
    return {
        "site": {k: SITE[k] for k in ("name", "title", "region_word", "region_plural", "select_label", "pin", "intro", "calib_hint")},
        "layers": layer_list(),
        "layer_vars": LAYER_VARS,
        "states": [{"id": s, "name": s.replace(" (UT)", ""), "points": len(p),
                    "continent": backend.REGION_INFO.get(s, {}).get("continent"),
                    "parent": backend.REGION_INFO.get(s, {}).get("parent"),
                    "places": [[c, la, lo] for c, la, lo in p]} for s, p in backend.STATES.items()],
        "variables": var_meta(),
        "history_days": list(HISTORY_CHOICES),
        "map": "/" + SITE["map_file"],
        "min_date": (today + timedelta(days=1)).isoformat(),
        "max_date": (today + timedelta(days=MAX_LEAD)).isoformat(),
        "calibration": None if not cal else {
            "built_at": cal["meta"]["built_at"], "truth": cal["meta"]["truth"],
            "months": cal["meta"]["months"], "leads": cal["meta"]["leads"]},
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def find_static(name):
    """Exact file name first. Otherwise accept the usual download renames, e.g.
    'india states.json' or 'india_states (1).json' (newest match wins)."""
    folders = (os.path.join(HERE, "web"), HERE)
    for folder in folders:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    stem, ext = os.path.splitext(name)
    want = "".join(ch for ch in stem.lower() if ch.isalpha())
    best = None
    for folder in folders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for fn in names:
            f_stem, f_ext = os.path.splitext(fn)
            if f_ext.lower() != ext:
                continue
            if "".join(ch for ch in f_stem.lower() if ch.isalpha()).startswith(want):
                p = os.path.join(folder, fn)
                m = os.path.getmtime(p)
                if best is None or m > best[0]:
                    best = (m, p)
    return best[1] if best else None


class Handler(BaseHTTPRequestHandler):
    server_version = "IndiaRain/1.0"

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), MIME[".json"])

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path in STATIC:
            f = find_static(STATIC[path])
            if not f:
                self._send(404, f"Missing file: {STATIC[path]} (put it next to server.py)", "text/plain; charset=utf-8")
                return
            with open(f, "rb") as fh:
                self._send(200, fh.read(), MIME[os.path.splitext(f)[1]])
        elif path == "/api/info":
            self._json(200, info_payload())
        elif path == "/api/forecast":
            self.handle_forecast(parse_qs(url.query))
        elif path == "/api/history":
            self.handle_history(parse_qs(url.query))
        elif path == "/api/layer":
            self.handle_layer(parse_qs(url.query))
        else:
            self._send(404, "Not found", "text/plain; charset=utf-8")

    def _spot(self, q):
        """(lat, lon) from the query, None if absent, or the string 'bad'."""
        if "lat" not in q and "lon" not in q:
            return None
        try:
            lat, lon = float(q["lat"][0]), float(q["lon"][0])
        except (KeyError, ValueError, IndexError):
            return "bad"
        if not (SITE["pin"] and -90 <= lat <= 90 and -180 <= lon <= 180):
            return "bad"
        return (round(lat, 2), round(lon, 2))

    def handle_forecast(self, q):
        state = (q.get("state") or [""])[0]
        raw = (q.get("date") or [""])[0]
        spot = self._spot(q)
        if spot == "bad":
            return self._json(400, {"error": "That spot isn't valid. Latitude is -90 to 90 and longitude -180 to 180."})
        if not spot and state not in backend.STATES:
            return self._json(400, {"error": f"Unknown {SITE['region_word']}. Pick one from the map or the list."})
        try:
            target = date.fromisoformat(raw)
        except ValueError:
            return self._json(400, {"error": "Choose a date first."})
        lead = (target - date.today()).days
        if lead < 1:
            return self._json(400, {"error": "Pick a future date. Tomorrow is the earliest."})
        if lead > MAX_LEAD:
            return self._json(400, {"error": f"Forecasts reach {MAX_LEAD} days ahead. Pick an earlier date."})
        try:
            self._json(200, cached_forecast(state, target, spot))
        except (RuntimeError, OSError) as e:
            self._json(502, {"error": "The weather service didn't respond. Check your internet connection and try again.",
                             "detail": str(e)[:300]})
        except Exception as e:  # never leave the page hanging on a bug
            self._json(500, {"error": "Something broke while building this forecast.",
                             "detail": f"{type(e).__name__}: {e}"})


    def handle_layer(self, q):
        parent = (q.get("parent") or [""])[0]
        if parent not in {l["parent"] for l in layer_list()}:
            return self._json(400, {"error": "There is no such map layer."})
        try:
            target = date.fromisoformat((q.get("date") or [""])[0])
        except ValueError:
            return self._json(400, {"error": "Choose a date first."})
        lead = (target - date.today()).days
        if lead < 1 or lead > MAX_LEAD:
            return self._json(400, {"error": f"Pick a date from tomorrow to {MAX_LEAD} days ahead."})
        try:
            self._json(200, cached_layer(parent, target))
        except (RuntimeError, OSError) as e:
            self._json(502, {"error": "The weather service didn't respond. Check your internet connection and try again.",
                             "detail": str(e)[:300]})
        except Exception as e:
            self._json(500, {"error": "Something broke while building the map layer.",
                             "detail": f"{type(e).__name__}: {e}"})

    def handle_history(self, q):
        state = (q.get("state") or [""])[0]
        spot = self._spot(q)
        if spot == "bad":
            return self._json(400, {"error": "That spot isn't valid. Latitude is -90 to 90 and longitude -180 to 180."})
        if not spot and state not in backend.STATES:
            return self._json(400, {"error": f"Unknown {SITE['region_word']}. Pick one from the map or the list."})
        try:
            days = int((q.get("days") or ["30"])[0])
        except ValueError:
            days = 30
        if days not in HISTORY_CHOICES:
            return self._json(400, {"error": f"Choose one of {', '.join(map(str, HISTORY_CHOICES))} days."})
        try:
            self._json(200, cached_history(state, days, spot))
        except (RuntimeError, OSError) as e:
            self._json(502, {"error": "The weather service didn't respond. Check your internet connection and try again.",
                             "detail": str(e)[:300]})
        except Exception as e:
            self._json(500, {"error": "Something broke while loading the past data.",
                             "detail": f"{type(e).__name__}: {e}"})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local website for the weather outlook.")
    ap.add_argument("--site", choices=sorted(SITES), default="india", help="india (default) or world")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    if a.site != "india":
        try:
            configure_site(a.site)
        except (OSError, ValueError) as e:
            print(f"Could not load the {a.site} data: {e}\nPut world_points.json next to server.py.")
            sys.exit(1)

    for need in ("index.html", SITE["map_file"]):
        if not find_static(need):
            print(f"Missing {need}. Download it again and put it in the same folder as server.py "
                  f"(a copy renamed by the browser, like 'india states.json', is fine too).")
            sys.exit(1)
    try:
        httpd = ThreadingHTTPServer((HOST, a.port), Handler)
    except OSError as e:
        print(f"Could not start on port {a.port}: {e}\nTry another port: py server.py --port 8080")
        sys.exit(1)

    url = f"http://localhost:{a.port}"
    print(f"{SITE['title']} running at {url}   (Ctrl+C to stop)")
    if not predict.load_calibration():
        script = "world.py" if a.site == "world" else "backend.py download, then backtest.py"
        print(f"Note: no calibration file yet, so chances will be marked UNCALIBRATED (run {script}).")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
