#!/usr/bin/env python3
"""
predict.py - forward prediction for a FUTURE date (tomorrow up to 15 days ahead).

Usage:
    python predict.py 05/10/26            (dd/mm/yy)
    python predict.py 05/10/26 --no-ensemble --no-tables

Needs backend.py in the same folder. Standard library only.
For best results run once first:  python backend.py download  ->  python backtest.py
Without calibration.json every number is stamped UNCALIBRATED.

Output: india_forecast_DD-MM-YYYY.txt next to this script, with, per state:
  * temperature, humidity, wind, gusts, sunshine and cloud cover: the state average, adjusted for
    the model's past bias and shown with a likely range once backtest.py has measured it
  * the deterministic forecast at every sample point (wettest point highlighted)
  * CALIBRATED chances (from the backtest) for 4 rain thresholds, "anywhere in state"
  * RAW ensemble fractions (incl. storm/gust, which can NOT be calibrated or verified)
  * a risk level and the state's 30-minute table (interpolated from hourly data)

Past dates are not predictions: use india_weather.py for those.
"""

import argparse
import json
import os
import sys
import urllib.error
from bisect import bisect_left
from datetime import date, timedelta
from statistics import mean, median

import backend

HERE = backend.HERE
CAL_PATH = os.path.join(HERE, "calibration.json")

WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Heavy showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "T-storm + hail", 99: "Severe T-storm + hail",
}
SMOOTH_VARS = ["temperature_2m", "apparent_temperature", "relative_humidity_2m",
               "wind_speed_10m", "surface_pressure", "cloud_cover"]


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def fmt(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def fmtp(v):
    return "n/a" if v is None else f"{v:.0f}%"


def risk_label(p):
    if p is None:
        return "n/a"
    if p < 20:
        return "Low"
    if p < 50:
        return "Moderate"
    if p < 75:
        return "High"
    return "Very high"


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------
def load_calibration():
    try:
        with open(CAL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def apply_curve(curve, x):
    xs = [c[0] for c in curve]
    i = min(bisect_left(xs, x), len(curve) - 1)
    return curve[i][1]


def calibrate(cal, x, lead, month, model, group=None):
    """-> (probs {event: 0-100}, lead_used, note). probs is None when calibration can't be used."""
    if cal is None:
        return None, None, "UNCALIBRATED: no calibration.json (run backend.py download, then backtest.py)"
    meta = cal["meta"]
    if meta.get("window_start") != backend.WINDOW_START:
        return None, None, "UNCALIBRATED: calibration was built with a different day window; rerun backtest.py"
    if meta.get("model") != model:
        return None, None, f"UNCALIBRATED: calibration is for model {meta.get('model')}, not {model}"
    if month not in meta["months"]:
        return None, None, (f"UNCALIBRATED: target month {month} is outside the calibrated months "
                            f"{meta['months']}; treat raw numbers with care")
    if x is None:
        return None, None, "UNCALIBRATED: no deterministic forecast available for this state"
    leads = sorted(meta["leads"])
    near = min(leads, key=lambda l: abs(l - lead))
    if abs(near - lead) > 1:
        return None, None, (f"UNCALIBRATED: {lead}-day lead is beyond the calibrated leads {leads}; "
                            f"skill this far out is unknown")
    note = "" if near == lead else f"lead {lead}d approximated with calibrated {near}d curve"
    out = {}
    for ev in backend.EVENTS:
        curve = cal["curves"].get(ev, {}).get(str(near))
        if isinstance(curve, dict):                      # world set: pick the region group's curve
            pick_ = None
            if group and group in curve:
                pick_ = curve[group]
            elif group and "|" in group and "bucket:" + group.split("|")[1] in curve:
                pick_ = curve["bucket:" + group.split("|")[1]]
            curve = pick_ or curve.get("_all")
        out[ev] = None if not curve else 100.0 * apply_curve(curve, x)
    return out, near, note


# --------------------------------------------------------------------------
# Raw ensemble statistics ("anywhere in state")
# --------------------------------------------------------------------------
def ensemble_stats(tables):
    tables = [t for t in tables if t]
    if not tables:
        return None
    common = set(tables[0])
    for t in tables[1:]:
        common &= set(t)
    if not common:
        return None
    rows = []
    for s in common:
        rain = max(t[s]["rain"] for t in tables)
        gusts = [t[s]["gust"] for t in tables if t[s]["gust"] is not None]
        rows.append((rain, max(gusts) if gusts else None, any(t[s]["ts"] for t in tables)))
    n = len(rows)

    def share(f):
        return 100.0 * sum(1 for r in rows if f(r)) / n

    have_gust = any(r[1] is not None for r in rows)
    have_cape = any(t[s]["has_cape"] for t in tables for s in common)
    gust_hit = lambda r: r[1] is not None and r[1] >= backend.GUST_KMH
    gusts_all = [r[1] for r in rows if r[1] is not None]
    rains = [r[0] for r in rows]
    ev = backend.EVENTS
    return {
        "members": n,
        "p_rain": share(lambda r: r[0] >= ev["rain"]),
        "p_local": share(lambda r: r[0] >= ev["local"]),
        "p_heavy": share(lambda r: r[0] >= ev["heavy"]),
        "p_gust": share(gust_hit) if have_gust else None,
        "p_ts": share(lambda r: r[2]) if have_cape else None,
        "p_storm": share(lambda r: gust_hit(r) or r[2]) if (have_gust or have_cape) else None,
        "mean_rain": mean(rains), "max_rain": max(rains),
        "med_gust": median(gusts_all) if gusts_all else None,
    }


# --------------------------------------------------------------------------
# 30-minute table (interpolated from hourly)
# --------------------------------------------------------------------------
def to_half_hour(h):
    n = len(h["time"])
    rows = []
    for i in range(n):
        hour = h["time"][i][11:13]
        for half in (0, 1):
            row = {"time": f"{hour}:{'00' if half == 0 else '30'}"}
            for var in SMOOTH_VARS:
                a = h[var][i]
                b = h[var][i + 1] if i + 1 < n else a
                if half == 0 or a is None or b is None:
                    row[var] = a
                else:
                    row[var] = (a + b) / 2
            p = h["precipitation"][i]
            row["precipitation"] = None if p is None else p / 2
            row["weather_code"] = h["weather_code"][i]
            rows.append(row)
    return rows


# --------------------------------------------------------------------------
# Temperature, humidity, wind, sunshine, cloud
# --------------------------------------------------------------------------
def clip(v, spec):
    if v is None:
        return None
    if spec.get("lo") is not None:
        v = max(v, spec["lo"])
    if spec.get("hi") is not None:
        v = min(v, spec["hi"])
    return v


def cont_info(cal, var, lead, state, month):
    """(error stats, skill entry, lead used) when the weather backtest covers this lead and month."""
    c = (cal or {}).get("continuous")
    if not c or month not in c["meta"]["months"]:
        return None
    leads = sorted(c["meta"]["leads"])
    near = min(leads, key=lambda l: abs(l - lead))
    if abs(near - lead) > 1:
        return None
    st = c["stats"].get(var, {}).get(str(near))
    if not st:
        return None
    entry = st.get(state) or st.get("_all")
    return (entry, c.get("skill", {}).get(var, {}).get(str(near)), near) if entry else None


def weather_summary(state, target, lead, det, cal, defs=None):
    """{var: {...}} - state average of each variable, with a bias-adjusted value and range if backtested.
    defs = the region's places [(name, lat, lon), ...]; leave None to use backend.STATES[state]."""
    out = {}
    places = defs if defs is not None else backend.STATES[state]
    for var, spec in backend.CONT_VARS.items():
        vals = {}
        for c, _, _ in places:
            v = ((det.get(backend.key(state, c)) or {}).get("cont") or {}).get(var)
            if v is not None:
                vals[c] = v
        item = {"label": spec["label"], "unit": spec["unit"], "dp": spec["dp"], "raw": None, "adjusted": None,
                "low": None, "high": None, "best": None, "verdict": None, "adjusted_flag": False,
                "min": None, "max": None, "points": vals, "lead_used": None}
        if vals:
            raw = sum(vals.values()) / len(vals)
            item.update(raw=raw, best=clip(raw, spec), min=min(vals.values()), max=max(vals.values()))
            info = cont_info(cal, var, lead, state, target.month)
            if info:
                e, sk, near = info
                adj = raw - e["bias"]
                item.update(adjusted=clip(adj, spec), best=clip(adj, spec),
                            low=clip(adj - e["q90"], spec), high=clip(adj - e["q10"], spec),
                            verdict=sk["verdict"] if sk else "not scored", adjusted_flag=True, lead_used=near)
        out[var] = item
    return out


def wfmt(item):
    if item["best"] is None:
        return "n/a"
    dp = item["dp"]
    txt = f"{item['best']:.{dp}f}{'' if item['unit'] == '%' else ' '}{item['unit']}"
    if item["low"] is not None:
        txt += f"  (likely {item['low']:.{dp}f} to {item['high']:.{dp}f})"
    return txt


# --------------------------------------------------------------------------
# Assemble per-state results
# --------------------------------------------------------------------------
def analyse(target, lead, model, cal, det, ens_tables):
    results = {}
    for state, pts in backend.STATES.items():
        per_point = {c: det.get(backend.key(state, c), {"rain": None, "gust": None}) for c, _, _ in pts}
        rains = {c: v["rain"] for c, v in per_point.items() if v["rain"] is not None}
        gusts = [v["gust"] for v in per_point.values() if v["gust"] is not None]
        x = max(rains.values()) if rains else None
        worst = max(rains, key=rains.get) if rains else pts[0][0]
        probs, lead_used, note = calibrate(cal, x, lead, target.month, model, backend.GROUPS.get(state))
        ens = ensemble_stats([ens_tables.get(backend.key(state, c)) for c, _, _ in pts]) if ens_tables else None
        if probs:
            headline, src = probs.get("local"), "calibrated"
        elif ens:
            headline, src = ens["p_local"], "raw ensemble, UNCALIBRATED"
        else:
            headline, src = None, "no data"
        results[state] = {"lead": lead, "weather": weather_summary(state, target, lead, det, cal),
                          "points": per_point, "x": x, "gust": max(gusts) if gusts else None,
                          "worst": worst, "probs": probs, "lead_used": lead_used, "note": note,
                          "ens": ens, "headline": headline, "src": src}
    return results


def skill_line(cal, lead):
    if not cal:
        return "Backtest skill: none (no calibration.json)"
    leads = sorted(cal["meta"]["leads"])
    near = min(leads, key=lambda l: abs(l - lead))
    if abs(near - lead) > 1:
        line = f"Backtest skill (rain): not available at {lead} days; the backtest covers leads {leads}"
    else:
        parts = []
        for ev in backend.EVENTS:
            s_ = cal["meta"].get("skill", {}).get(ev, {}).get(str(near))
            parts.append(f"{ev}: {s_['verdict']}" + (f" (BSS {s_['bss']})" if s_ and s_["bss"] is not None else "")
                         if s_ else f"{ev}: not scored")
        line = f"Backtest skill at ~{near}d lead ({cal['meta']['truth'].upper()} truth): " + " | ".join(parts)
    c = cal.get("continuous")
    if c:
        cl = sorted(c["meta"]["leads"])
        cn = min(cl, key=lambda l: abs(l - lead))
        if abs(cn - lead) > 1 or target_month_out(c, lead):
            line += f"\nBacktest skill, weather variables: not available at {lead} days (backtest leads {cl})"
        else:
            ws = [f"{v}: {c['skill'][v][str(cn)]['verdict']}" for v in backend.CONT_VARS
                  if c.get("skill", {}).get(v, {}).get(str(cn))]
            line += f"\nBacktest skill, weather variables at ~{cn}d lead (ERA5 reference): " + " | ".join(ws)
    return line


def target_month_out(c, lead):
    month = (date.today() + timedelta(days=lead)).month
    return month not in c["meta"]["months"]


def state_lines(state, r):
    r_lead = r.get("lead")
    lines = []
    pts = backend.STATES[state]
    lines.append(f"  Forecast (deterministic): wettest point {r['worst']} {fmt(r['x'])} mm | "
                 f"peak gust at any point {fmt(r['gust'], 0)} km/h")
    if r["probs"]:
        p = r["probs"]
        e = backend.EVENTS
        lines.append(f"  CALIBRATED chance, anywhere among {len(pts)} point(s): "
                     f">= {e['rain']} mm {fmtp(p['rain'])} | >= {e['moderate']} mm {fmtp(p['moderate'])} | "
                     f">= {e['local']:.0f} mm {fmtp(p['local'])} | >= {e['heavy']} mm {fmtp(p['heavy'])}")
        if r["note"]:
            lines.append(f"    ({r['note']})")
    else:
        lines.append(f"  CALIBRATED chance: not available - {r['note']}")
    en = r["ens"]
    if en:
        lines.append(f"  RAW ensemble ({en['members']} members, uncalibrated): rain {fmtp(en['p_rain'])} | "
                     f">= {backend.EVENTS['local']:.0f} mm {fmtp(en['p_local'])} | "
                     f">= {backend.EVENTS['heavy']} mm {fmtp(en['p_heavy'])}")
        lines.append(f"  STORM (raw ensemble, UNVERIFIED - no truth data exists to check it): "
                     f"{fmtp(en['p_storm'])} [thunderstorm proxy {fmtp(en['p_ts'])} | "
                     f"gusts >= {backend.GUST_KMH:.0f} km/h {fmtp(en['p_gust'])}]")
    else:
        lines.append("  RAW ensemble: not available")
    lines.append(f"  Risk level: {risk_label(r['headline'])}  "
                 f"(from the '>= {backend.EVENTS['local']:.0f} mm' chance, {r['src']}; worst point: {r['worst']})")
    w = r.get("weather") or {}
    if any(v["best"] is not None for v in w.values()):
        adj = any(v["adjusted_flag"] for v in w.values())
        lines.append(f"  WEATHER (average of {len(pts)} place(s)" +
                     ("; past model bias removed, 'likely' = 80% range" if adj else "; raw model values, NOT bias-adjusted") + "):")
        used = {v["lead_used"] for v in w.values() if v["lead_used"]}
        if adj and used and used != {r_lead}:
            lines.append(f"    (the {sorted(used)[0]}-day backtest is used for this {r_lead}-day forecast)")
        for var, v in w.items():
            sk = f"   [backtest: {v['verdict']}]" if v["verdict"] else ""
            lines.append(f"    {v['label']:<30}{wfmt(v)}{sk}")
    if len(pts) > 1:
        lines.append("  By point (forecast rain / peak gust):")
        for c, v in r["points"].items():
            lines.append(f"    {c:<22} {fmt(v['rain']):>6} mm | {fmt(v['gust'], 0):>4} km/h")
    return lines


def write_report(path, target, lead, model, cal, results, ens_models, hourly):
    bar = "=" * 96
    with open(path, "w", encoding="utf-8") as f:
        f.write("INDIA FORECAST - STATE-WISE OUTLOOK FOR A FUTURE DATE\n")
        f.write(f"Target date : {target.strftime('%d/%m/%Y')}  (lead {lead} day{'s' if lead != 1 else ''}; made {date.today().strftime('%d/%m/%Y')})\n")
        f.write(f"Model       : {model} deterministic" + (f" + ensemble ({ens_models})" if ens_models else "") + "\n")
        f.write(f"Day window  : {backend.day_text()}\n")
        f.write(f"Calibration : " + (f"built {cal['meta']['built_at']} on years {cal['meta']['years']}, months {cal['meta']['months']}; "
                                     f"{cal['meta']['split']}" if cal else "NONE - all numbers are UNCALIBRATED") + "\n")
        f.write(skill_line(cal, lead) + "\n")
        f.write("Points      : several sample points per state; 'anywhere in state' = at least one of them.\n")
        f.write("Storm       : raw ensemble only. IMD publishes no gridded gust/thunderstorm observations,\n")
        f.write("              so storm numbers cannot be calibrated or verified. Do not rely on them.\n")
        f.write("30-min rows : source data is hourly; :30 rows are interpolated (primary point only).\n")
        f.write("Not an official warning. Official local warnings (IMD in India) take precedence.\n")
        f.write(bar + "\n\n")
        for state, pts in backend.STATES.items():
            city, lat, lon = pts[0]
            r = results[state]
            f.write(f"{state.upper()}  [primary point: {city}  {lat:.2f}N, {lon:.2f}E]\n")
            for ln in state_lines(state, r):
                f.write(ln + "\n")
            h = hourly.get(state) if hourly else None
            if h:
                f.write("\n")
                header = (f"{'Time':<6}{'Temp C':>8}{'Feels C':>9}{'Hum %':>7}{'Rain mm':>9}"
                          f"{'Wind km/h':>11}{'Press hPa':>11}{'Cloud %':>9}  Conditions")
                f.write(f"30-minute forecast, {city}\n" + header + "\n" + "-" * len(header) + "\n")
                for row in to_half_hour(h):
                    code = row["weather_code"]
                    cond = "-" if code is None else WMO.get(int(code), f"Code {int(code)}")
                    f.write(f"{row['time']:<6}{fmt(row['temperature_2m']):>8}{fmt(row['apparent_temperature']):>9}"
                            f"{fmt(row['relative_humidity_2m'], 0):>7}{fmt(row['precipitation'], 2):>9}"
                            f"{fmt(row['wind_speed_10m']):>11}{fmt(row['surface_pressure'], 1):>11}"
                            f"{fmt(row['cloud_cover'], 0):>9}  {cond}\n")
            f.write("\n" + bar + "\n\n")


def print_console(target, lead, cal, results):
    print(f"\nForecast for {target.strftime('%d/%m/%Y')} (lead {lead}d)")
    print(skill_line(cal, lead))
    e = backend.EVENTS
    header = (f"{'State / UT':<38}{'Tmax':>6}{'Tmin':>6}{'Hum%':>5}{'Wind':>5}{'Sun h':>6}{'Wet mm':>7}{'Rain%':>6}"
              f"{'>=' + str(int(e['local'])) + 'mm%':>8}{'Heavy%':>7}{'RawHvy%':>8}{'RawStorm%':>10}  {'Risk':<10}Worst point")
    print(header)
    print("-" * len(header))
    for state, r in results.items():
        p, en = r["probs"], r["ens"]
        wv = r["weather"]
        wb = lambda k, d=0: fmt(wv[k]["best"], d)
        print(f"{state[:37]:<38}{wb('tmax', 1):>6}{wb('tmin', 1):>6}{wb('rh'):>5}{wb('wind'):>5}{wb('sun', 1):>6}{fmt(r['x']):>7}"
              f"{fmtp(p['rain'] if p else None):>6}{fmtp(p['local'] if p else None):>8}"
              f"{fmtp(p['heavy'] if p else None):>7}"
              f"{fmtp(en['p_heavy'] if en else None):>8}{fmtp(en['p_storm'] if en else None):>10}"
              f"  {risk_label(r['headline']):<10}{r['worst']}")
    print("\n'-'/n/a in the calibrated columns = no calibration for this lead/season. "
          "Storm is raw and unverified.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Forward forecast for a future date.")
    ap.add_argument("date", nargs="?", help="future date, dd/mm/yy")
    ap.add_argument("--model", default=backend.DEFAULT_MODEL)
    ap.add_argument("--no-ensemble", action="store_true", help="skip the (slow) ensemble request")
    ap.add_argument("--no-tables", action="store_true", help="skip the 30-minute tables")
    a = ap.parse_args(argv)

    raw = a.date or input("Enter a FUTURE date (dd/mm/yy): ")
    try:
        target = backend.parse_date(raw)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    today = date.today()
    lead = (target - today).days
    if lead < 1:
        print("predict.py is forward-looking: pick a date after today. "
              "For past/today's data use india_weather.py.")
        sys.exit(1)
    if lead > 15:
        print("Forecasts only reach 15 days ahead.")
        sys.exit(1)

    cal = load_calibration()
    print(f"Target {target.strftime('%d/%m/%Y')} - lead {lead} day(s). "
          + ("Calibration loaded." if cal else "No calibration.json: output will be UNCALIBRATED."))
    try:
        print("Deterministic forecast...")
        det = backend.fetch_forecast_window(target, a.model)
    except (RuntimeError, urllib.error.URLError) as e:
        print(f"Error fetching forecast: {e}")
        sys.exit(1)

    ens_tables, ens_models = None, None
    if not a.no_ensemble:
        try:
            print("Ensemble (this is the slow part)...")
            ens_tables, ens_models = backend.fetch_ensemble_members(target)
        except (RuntimeError, urllib.error.URLError) as e:
            print(f"Warning: ensemble failed ({e}); continuing without it")

    hourly = None
    if not a.no_tables:
        try:
            print("30-minute tables...")
            hourly = backend.fetch_hourly_primary(target)
        except (RuntimeError, urllib.error.URLError) as e:
            print(f"Warning: hourly tables failed ({e}); continuing without them")

    results = analyse(target, lead, a.model, cal, det, ens_tables)
    path = os.path.join(HERE, f"india_forecast_{target.strftime('%d-%m-%Y')}.txt")
    write_report(path, target, lead, a.model, cal, results, ens_models, hourly)
    print_console(target, lead, cal, results)
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
