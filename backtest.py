#!/usr/bin/env python3
"""
backtest.py - does the forecast actually work? Measures it, then calibrates it.

Run AFTER:  python backend.py download
Run with:   python backtest.py            (IMD truth, the default)
            python backtest.py --truth era5   (weak fallback, see warning below)

What it does
  1. Loads past forecasts (what the model predicted N days ahead) and observed rainfall.
  2. For each state and day: x = the wettest forecast among the state's sample points;
     event = at least one sample point's observed cell reached the threshold.
  3. Fits a calibration curve (isotonic regression, pure Python) on the earlier data and
     scores it on the LAST year it has never seen (or the last 30% of dates if only one year).
  4. Compares against baselines: a plain yes/no model forecast, and "same as yesterday".
  5. Writes backtest_report.txt (read this!) and calibration.json (used by predict.py).
     The saved curves are refit on ALL data after the scoring is done.

Also (v2): scores temperature, humidity, wind, gusts, sunshine and cloud cover. For each one it
measures the forecast error against ERA5 (an analysis, NOT station readings), removes each state's
average bias, and compares against 'same as N days ago' and 'normal for the month'. The error
spread is saved so predict.py can show a likely range, not just one number.

What it can NOT do: storms. IMD does not publish gridded gusts/thunderstorm observations,
so storm output in predict.py stays uncalibrated and unverified.

Standard library only.
"""

import argparse
import json
import os
import sys
from bisect import bisect_left
from datetime import datetime, date, timedelta

import backend

HERE = backend.HERE
CAL_PATH = os.path.join(HERE, "calibration.json")
REPORT_PATH = os.path.join(HERE, "backtest_report.txt")


# --------------------------------------------------------------------------
# Samples
# --------------------------------------------------------------------------
def build_samples(forecasts, truth, leads):
    samples = []
    for state, pts in backend.STATES.items():
        keys = [backend.key(state, c) for c, _, _ in pts]
        need = (len(keys) + 1) // 2          # at least half the points must have data
        days = set()
        for k in keys:
            days.update(forecasts.get(k, {}))
        for d in sorted(days):
            prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            for lead in leads:
                fx, ob, ob_prev = [], [], []
                for k in keys:
                    f = forecasts.get(k, {}).get(d, {}).get(lead)
                    o = truth.get(k, {}).get(d)
                    if f is None or o is None:
                        continue
                    fx.append(f)
                    ob.append(o)
                    op = truth.get(k, {}).get(prev)
                    if op is not None:
                        ob_prev.append(op)
                if len(fx) < need:
                    continue
                samples.append({
                    "date": d, "state": state, "lead": lead, "x": round(max(fx), 1), "group": backend.GROUPS.get(state),
                    "y": {ev: any(o >= thr for o in ob) for ev, thr in backend.EVENTS.items()},
                    "persist": {ev: (any(o >= thr for o in ob_prev) if ob_prev else None)
                                for ev, thr in backend.EVENTS.items()},
                })
    return samples


def split_samples(samples):
    years = sorted({s["date"][:4] for s in samples})
    if len(years) >= 2:
        ty = years[-1]
        train = [s for s in samples if s["date"][:4] != ty]
        test = [s for s in samples if s["date"][:4] == ty]
        return train, test, f"trained on {', '.join(years[:-1])}; tested on {ty}"
    days = sorted({s["date"] for s in samples})
    cut = days[int(len(days) * 0.7)]
    train = [s for s in samples if s["date"] < cut]
    test = [s for s in samples if s["date"] >= cut]
    return train, test, f"trained on dates before {cut}; tested from {cut} (only one year available)"


# --------------------------------------------------------------------------
# Calibration (isotonic regression via pool-adjacent-violators)
# --------------------------------------------------------------------------
def fit_isotonic(xs, ys):
    groups = {}
    for x, y in zip(xs, ys):
        g = groups.setdefault(x, [0, 0])
        g[0] += y
        g[1] += 1
    blocks = []                                  # [sum_y, n, x_max]
    for x in sorted(groups):
        s, n = groups[x]
        blocks.append([s, n, x])
        while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            b = blocks.pop()
            blocks[-1][0] += b[0]
            blocks[-1][1] += b[1]
            blocks[-1][2] = b[2]
    return [[b[2], min(0.99, max(0.01, b[0] / b[1]))] for b in blocks]


MIN_GROUP_SAMPLES = 400
MIN_GROUP_CASES = 20


def bucket_of(group):
    return group.split("|")[1] if group and "|" in group else None


def fit_curves(rows, ev):
    """{'_all': curve} plus, for the world set, curves per region group ('Eastern Africa|6+') and per
    size bucket ('bucket:6+'). A group only gets its own curve when it has enough data."""
    yv = lambda s: 1 if s["y"][ev] else 0
    curves = {"_all": fit_isotonic([s["x"] for s in rows], [yv(s) for s in rows])}
    if not backend.GROUPS:
        return curves
    for prefix, keyf in (("bucket:", lambda s: bucket_of(s.get("group"))), ("", lambda s: s.get("group"))):
        by = {}
        for s in rows:
            k = keyf(s)
            if k is not None:
                by.setdefault(k, []).append(s)
        for k, rs in by.items():
            pos = sum(yv(s) for s in rs)
            if len(rs) >= MIN_GROUP_SAMPLES and pos >= MIN_GROUP_CASES and len(rs) - pos >= MIN_GROUP_CASES:
                curves[prefix + k] = fit_isotonic([s["x"] for s in rs], [yv(s) for s in rs])
    return curves


def curve_for(curves, group):
    if group:
        if group in curves:
            return curves[group]
        b = bucket_of(group)
        if b and "bucket:" + b in curves:
            return curves["bucket:" + b]
    return curves["_all"]


def apply_curve(curve, x):
    xs = [c[0] for c in curve]
    i = min(bisect_left(xs, x), len(curve) - 1)
    return curve[i][1]


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def mean(v):
    return sum(v) / len(v) if v else None


def contingency(pred, obs):
    hits = sum(1 for p, o in zip(pred, obs) if p and o)
    miss = sum(1 for p, o in zip(pred, obs) if (not p) and o)
    fals = sum(1 for p, o in zip(pred, obs) if p and (not o))
    pod = hits / (hits + miss) if hits + miss else None
    far = fals / (hits + fals) if hits + fals else None
    csi = hits / (hits + miss + fals) if hits + miss + fals else None
    return pod, far, csi


def pc(v):
    return "  n/a" if v is None else f"{100 * v:4.0f}%"


def f2(v):
    return "  n/a" if v is None else f"{v:5.2f}"


def verdict(bss, n_pos, min_events):
    if n_pos < min_events:
        return "too few events"
    if bss is None:
        return "n/a"
    if bss >= 0.10:
        return "useful"
    if bss >= 0.02:
        return "marginal"
    return "no skill"


def evaluate(train, test, leads, min_events):
    results = {}
    for ev, thr in backend.EVENTS.items():
        for lead in leads:
            tr = [s for s in train if s["lead"] == lead]
            te = [s for s in test if s["lead"] == lead]
            if len(tr) < 50 or len(te) < 50:
                continue
            ytr = [1 if s["y"][ev] else 0 for s in tr]
            yte = [1 if s["y"][ev] else 0 for s in te]
            curves = fit_curves(tr, ev)
            p = [apply_curve(curve_for(curves, s.get("group")), s["x"]) for s in te]
            base_tr = mean(ytr)
            brier = mean([(pi - yi) ** 2 for pi, yi in zip(p, yte)])
            brier_clim = mean([(base_tr - yi) ** 2 for yi in yte])
            bss = (1 - brier / brier_clim) if brier_clim else None
            det = contingency([s["x"] >= thr for s in te], yte)
            cal = contingency([pi >= 0.5 for pi in p], yte)
            pers_rows = [(s["persist"][ev], yi) for s, yi in zip(te, yte) if s["persist"][ev] is not None]
            pers = contingency([a for a, _ in pers_rows], [b for _, b in pers_rows]) if pers_rows else (None,) * 3
            bins = []
            for b in range(10):
                idx = [i for i, pi in enumerate(p) if b / 10 <= pi < (b + 1) / 10 or (b == 9 and pi >= 0.9)]
                bins.append((b, len(idx), mean([p[i] for i in idx]), mean([yte[i] for i in idx])))
            results[(ev, lead)] = {
                "n_test": len(te), "n_pos": sum(yte), "base_te": mean(yte), "base_tr": base_tr,
                "brier": brier, "brier_clim": brier_clim, "bss": bss,
                "verdict": verdict(bss, sum(yte), min_events),
                "det": det, "cal": cal, "pers": pers, "bins": bins,
            }
    return results


# --------------------------------------------------------------------------
# Continuous variables (temperature, humidity, wind, gusts, sunshine, cloud)
# --------------------------------------------------------------------------
def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = q * (len(sorted_vals) - 1)
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def state_truth_means(truth_x):
    """{var: {state: {date: mean over the state's points}}}"""
    tm = {}
    for state, pts in backend.STATES.items():
        keys = [backend.key(state, c) for c, _, _ in pts]
        need = (len(keys) + 1) // 2
        days = set()
        for k in keys:
            days.update(truth_x.get(k, {}))
        for d in days:
            for var in backend.CONT_VARS:
                vals = [truth_x[k][d][var] for k in keys if var in truth_x.get(k, {}).get(d, {})]
                if len(vals) >= need:
                    tm.setdefault(var, {}).setdefault(state, {})[d] = sum(vals) / len(vals)
    return tm


def build_cont_samples(forecasts_x, truth_x, leads, only=None, tm=None):
    tm = tm if tm is not None else state_truth_means(truth_x)
    names = [only] if only else list(backend.CONT_VARS)
    rows = {var: [] for var in names}
    for state, pts in backend.STATES.items():
        keys = [backend.key(state, c) for c, _, _ in pts]
        need = (len(keys) + 1) // 2
        days = set()
        for k in keys:
            days.update(forecasts_x.get(k, {}))
        for d in sorted(days):
            for lead in leads:
                prev = (date.fromisoformat(d) - timedelta(days=lead)).isoformat()
                for var in names:
                    fx, tx = [], []
                    for k in keys:
                        f = forecasts_x.get(k, {}).get(d, {}).get(var, {}).get(lead)
                        t = truth_x.get(k, {}).get(d, {}).get(var)
                        if f is not None and t is not None:
                            fx.append(f)
                            tx.append(t)
                    if len(fx) >= need:
                        rows[var].append({"date": d, "state": state, "lead": lead, "f": mean(fx), "t": mean(tx),
                                          "persist": tm.get(var, {}).get(state, {}).get(prev)})
    return rows


def cont_verdict(skill, n, min_n=200):
    if n < min_n or skill is None:
        return "too few samples"
    if skill >= 0.20:
        return "useful"
    if skill >= 0.05:
        return "marginal"
    return "no skill"


def mae(vals):
    return mean([abs(v) for v in vals])


def evaluate_cont(rows_by_var, leads):
    """Honest test: bias and normals come from the training part only; scores from the unseen part."""
    out = {}
    split_desc = ""
    for var, rows in rows_by_var.items():
        if len(rows) < 300:
            continue
        train, test, split_desc = split_samples(rows)
        for lead in leads:
            tr = [r for r in train if r["lead"] == lead]
            te = [r for r in test if r["lead"] == lead]
            if len(tr) < 100 or len(te) < 50:
                continue
            pooled = mean([r["f"] - r["t"] for r in tr])
            by_state = {}
            for r in tr:
                by_state.setdefault(r["state"], []).append(r["f"] - r["t"])
            bias = {st: (mean(v) if len(v) >= 60 else pooled) for st, v in by_state.items()}
            clim = {}
            for r in tr:
                clim.setdefault((r["state"], int(r["date"][5:7])), []).append(r["t"])
            clim_all = {}
            for r in tr:
                clim_all.setdefault(int(r["date"][5:7]), []).append(r["t"])
            err_raw = [r["f"] - r["t"] for r in te]
            err_adj = [r["f"] - bias.get(r["state"], pooled) - r["t"] for r in te]
            sub = [(ea, r) for ea, r in zip(err_adj, te) if r["persist"] is not None]
            m_adj = mae(err_adj)
            m_pers = mae([r["persist"] - r["t"] for _, r in sub]) if sub else None
            m_adj_sub = mae([ea for ea, _ in sub]) if sub else None
            cl = []
            for r in te:
                c = clim.get((r["state"], int(r["date"][5:7])))
                c = mean(c) if c and len(c) >= 10 else (mean(clim_all.get(int(r["date"][5:7]), [])) if clim_all.get(int(r["date"][5:7])) else None)
                cl.append(abs(c - r["t"]) if c is not None else None)
            cl_ok = [x for x in cl if x is not None]
            m_clim = mean(cl_ok) if cl_ok else None
            sk = []
            if m_pers:
                sk.append(1 - m_adj_sub / m_pers)
            if m_clim:
                sk.append(1 - m_adj / m_clim)
            skill = min(sk) if sk else None
            out[(var, lead)] = {
                "n_test": len(te), "mae_raw": mae(err_raw), "mae_adj": m_adj,
                "rmse_adj": (mean([e * e for e in err_adj])) ** 0.5, "bias_raw": mean(err_raw),
                "mae_persist": m_pers, "mae_clim": m_clim, "skill": skill,
                "verdict": cont_verdict(skill, len(te)),
            }
    return out, split_desc


def fit_cont_stats(rows_by_var, leads):
    """Final per-state bias and error spread, from ALL data (saved for predict.py)."""
    stats = {}
    for var, rows in rows_by_var.items():
        for lead in leads:
            rl = [r for r in rows if r["lead"] == lead]
            if len(rl) < 100:
                continue
            by_state = {}
            for r in rl:
                by_state.setdefault(r["state"], []).append(r)
            entry, pooled_err = {}, []
            all_bias = mean([r["f"] - r["t"] for r in rl])
            for st, rs in by_state.items():
                b = mean([r["f"] - r["t"] for r in rs]) if len(rs) >= 60 else all_bias
                errs = sorted(r["f"] - b - r["t"] for r in rs)
                pooled_err += errs
                if len(rs) >= 60:
                    entry[st] = {"n": len(rs), "bias": round(b, 3), "mae": round(mae(errs), 3),
                                 "q10": round(quantile(errs, 0.10), 3), "q90": round(quantile(errs, 0.90), 3)}
            pooled_err.sort()
            entry["_all"] = {"n": len(rl), "bias": round(all_bias, 3), "mae": round(mae(pooled_err), 3),
                             "q10": round(quantile(pooled_err, 0.10), 3), "q90": round(quantile(pooled_err, 0.90), 3)}
            stats.setdefault(var, {})[str(lead)] = entry
    return stats


def write_cont_report(path, cont, leads, truth_note):
    L = []
    w = L.append
    w("")
    w("=" * 100)
    w("CONTINUOUS WEATHER VARIABLES: temperature, humidity, wind, gusts, sunshine, cloud")
    w(truth_note)
    w("Forecast = average over the state's sample points. Each state's average bias (from the training")
    w("period) is subtracted. 'persist' = the value N days earlier; 'normal' = average for that month.")
    w("Skill = 1 - (MAE of adjusted forecast / MAE of the harder baseline). >= 0.20 useful, 0.05-0.20")
    w("marginal, below that no skill. MAE = typical size of the error, in the variable's own unit.")
    for var, spec in backend.CONT_VARS.items():
        w("")
        w(f"{spec['label'].upper()} ({spec['unit']})")
        hdr = f"{'lead':>4} {'n_test':>7} {'bias':>7} {'MAE raw':>8} {'MAE adj':>8} {'persist':>8} {'normal':>8} {'skill':>7}  {'verdict':<16}{'80% range':>10}"
        w(hdr)
        w("-" * len(hdr))
        for lead in leads:
            r = cont["scores"].get((var, lead))
            st = cont["stats"].get(var, {}).get(str(lead), {}).get("_all")
            if not r:
                w(f"{lead:>3}d  not enough samples")
                continue
            half = (st["q90"] - st["q10"]) / 2 if st else None
            f = lambda v: "     n/a" if v is None else f"{v:8.2f}"
            w(f"{lead:>3}d {r['n_test']:>7} {r['bias_raw']:7.2f} {f(r['mae_raw'])} {f(r['mae_adj'])} {f(r['mae_persist'])} "
              f"{f(r['mae_clim'])} {f2(r['skill']):>7}  {r['verdict']:<16}" + ("       n/a" if half is None else f"  +/-{half:5.1f}"))
    w("")
    w("-" * 100)
    lead0 = leads[0]
    w(f"PER STATE: typical error (MAE) of the adjusted forecast at {lead0} day ahead")
    cols = list(backend.CONT_VARS)
    w(f"{'state':<38}" + "".join(f"{c:>8}" for c in cols))
    for state in backend.STATES:
        row = f"{state[:37]:<38}"
        for c in cols:
            e = cont["stats"].get(c, {}).get(str(lead0), {}).get(state)
            row += f"{e['mae']:8.2f}" if e else f"{'-':>8}"
        w(row)
    w("")
    useful = sorted({(v, l) for (v, l), r in cont["scores"].items() if r["verdict"] == "useful"})
    weak = sorted({(v, l) for (v, l), r in cont["scores"].items() if r["verdict"] in ("no skill", "marginal")})
    w("Honest summary (weather variables)")
    w("  Useful   : " + (", ".join(f"{v}@{l}d" for v, l in useful) or "none"))
    w("  Weak/none: " + (", ".join(f"{v}@{l}d" for v, l in weak) or "none"))
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def write_report(path, args, meta, results, leads, n_samples, split_desc):
    L = []
    w = L.append
    w("BACKTEST REPORT - how good are the forecasts, really?")
    w(f"Built       : {meta['built_at']}")
    w(f"Model       : {args.model} (past forecasts from Open-Meteo Previous Runs)")
    w(f"Truth       : {args.truth.upper()} daily rainfall at each sample point's grid cell")
    w(f"Period      : years {meta['years']}, months {meta['months']}")
    w(f"Samples     : {n_samples} state-days x leads; {split_desc}")
    w(f"Day window  : {backend.day_text()}")
    w("")
    if args.truth == "era5":
        w("!! WARNING: ERA5 'truth' is itself a model product (the same family as the forecast).")
        w("!! Scores below are optimistic and say little about real-world accuracy. Use IMD truth")
        w("!! for anything you present as an accuracy claim.")
        w("")
    w("How to read it")
    w("  Event      = at least one of the state's sample points has an observed cell >= threshold.")
    w("  Forecast x = wettest forecast among those same points at that lead time.")
    w("  BSS        = Brier skill score vs. always predicting the long-run base rate.")
    w("               >= 0.10 useful | 0.02-0.10 marginal | below 0.02 no skill. (1.0 = perfect)")
    w("  POD / FAR / CSI = hit rate / false-alarm ratio / critical success index.")
    w("  'det'  = plain yes/no: forecast >= threshold.  'cal>=50%' = calibrated chance >= 50%.")
    w("  'persist' = same as yesterday (the baseline a forecast must beat).")
    w("  Events with fewer than %d cases in the test period are marked 'too few events'." % args.min_events)
    w("")
    w("NOT COVERED: storms/gusts. IMD publishes no gridded gust or thunderstorm observations, so")
    w("there is nothing to score them against. predict.py shows them as raw and UNVERIFIED.")
    w("=" * 100)

    for ev, thr in backend.EVENTS.items():
        w("")
        w(f"EVENT '{ev}': observed rain >= {thr} mm in 24 h at any sample point of the state")
        hdr = (f"{'lead':>4} {'n_test':>7} {'events':>6} {'base':>5} {'BSS':>6}  {'verdict':<15}"
               f"{'det POD':>8}{'FAR':>6}{'CSI':>6}  {'cal50 POD':>9}{'FAR':>6}{'CSI':>6}  {'persist CSI':>11}")
        w(hdr)
        w("-" * len(hdr))
        for lead in leads:
            r = results.get((ev, lead))
            if not r:
                w(f"{lead:>3}d  not enough samples")
                continue
            w(f"{lead:>3}d {r['n_test']:>7} {r['n_pos']:>6} {pc(r['base_te'])} {f2(r['bss'])}  {r['verdict']:<15}"
              f"{pc(r['det'][0]):>8}{pc(r['det'][1]):>6}{pc(r['det'][2]):>6}  "
              f"{pc(r['cal'][0]):>9}{pc(r['cal'][1]):>6}{pc(r['cal'][2]):>6}  {pc(r['pers'][2]):>11}")
        w("")
        w("  Reliability (does a stated 30% happen ~30% of the time?)  [test period]")
        for lead in leads:
            r = results.get((ev, lead))
            if not r or r["n_pos"] < args.min_events:
                continue
            row = [f"{lead}d:"]
            for b, n, mp, of in r["bins"]:
                if n >= 20:
                    row.append(f"{int(100 * mp)}%->{int(100 * of)}%(n={n})")
            w("   " + " | ".join(row))
    w("")
    w("=" * 100)
    w("Honest summary")
    useful = sorted({(ev, lead) for (ev, lead), r in results.items() if r["verdict"] == "useful"})
    weak = sorted({(ev, lead) for (ev, lead), r in results.items() if r["verdict"] in ("no skill", "marginal")})
    w("  Useful   : " + (", ".join(f"{e}@{l}d" for e, l in useful) or "none"))
    w("  Weak/none: " + (", ".join(f"{e}@{l}d" for e, l in weak) or "none"))
    w("  Scores are only as good as the truth data; point-sampled 'anywhere in state' events are")
    w("  a proxy, not IMD's official district warnings.")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Backtest forecasts against observed rainfall and calibrate them.")
    ap.add_argument("--model", default=backend.DEFAULT_MODEL)
    ap.add_argument("--truth", choices=["imd", "era5"], default="imd")
    ap.add_argument("--min-events", type=int, default=30)
    ap.add_argument("--skip-continuous", action="store_true", help="only score rain events")
    args = ap.parse_args(argv)

    forecasts, periods, leads = backend.load_prev_runs(args.model)
    if not periods:
        print("No cached forecasts. Run first:  " + ("py world.py download" if backend.SET_NAME == "World" else "python backend.py download"))
        sys.exit(1)
    truth = backend.load_truth(args.truth, periods)
    if not truth:
        print(f"No cached '{args.truth}' truth data. Run:  python backend.py download"
              + ("" if args.truth == "imd" else " --truth era5"))
        sys.exit(1)

    samples = build_samples(forecasts, truth, leads)
    if len(samples) < 500:
        print(f"Only {len(samples)} usable samples - too few. Download more months/years "
              f"(python backend.py download --start-year 2023) or check 'python backend.py status'.")
        sys.exit(1)

    train, test, split_desc = split_samples(samples)
    results = evaluate(train, test, leads, args.min_events)

    months = sorted({int(s["date"][5:7]) for s in samples})
    years = sorted({int(s["date"][:4]) for s in samples})
    curves = {}
    for ev in backend.EVENTS:
        curves[ev] = {}
        for lead in leads:
            rows = [s for s in samples if s["lead"] == lead]
            if len(rows) >= 100:
                fits = fit_curves(rows, ev)
                curves[ev][str(lead)] = fits if backend.GROUPS else fits["_all"]
    summary = {}
    for (ev, lead), r in results.items():
        summary.setdefault(ev, {})[str(lead)] = {
            "bss": None if r["bss"] is None else round(r["bss"], 3),
            "verdict": r["verdict"], "events_in_test": r["n_pos"]}
    meta = {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": args.model, "truth": args.truth, "window_start": backend.WINDOW_START,
        "leads": leads, "months": months, "years": years, "split": split_desc,
        "events": backend.EVENTS, "n_samples": len(samples), "skill": summary,
        "grouped": bool(backend.GROUPS), "set": backend.SET_NAME,
    }
    out = {"meta": meta, "curves": curves}
    cont = None
    if not args.skip_continuous:
        fx, xperiods, xleads = backend.load_prev_extra(args.model)
        tx = backend.load_truth_extra(xperiods) if xperiods else {}
        if fx and tx:
            tm = state_truth_means(tx)
            scores, stats, csplit = {}, {}, ""
            for var in backend.CONT_VARS:                  # one variable at a time keeps memory low
                rows = build_cont_samples(fx, tx, xleads, only=var, tm=tm)
                sc, sp = evaluate_cont(rows, xleads)
                scores.update(sc)
                stats.update(fit_cont_stats(rows, xleads))
                csplit = csplit or sp
                rows = None
            del tm
            if stats:
                cont = {"scores": scores, "stats": stats}
                out["continuous"] = {
                    "meta": {"truth": "era5", "leads": xleads, "split": csplit,
                             "months": sorted({int(d[5:7]) for k in tx for d in tx[k]}),
                             "years": sorted({int(d[:4]) for k in tx for d in tx[k]}),
                             "built_at": meta["built_at"]},
                    "stats": stats,
                    "skill": {v: {str(l): {"skill": None if r["skill"] is None else round(r["skill"], 3),
                                           "verdict": r["verdict"], "mae": round(r["mae_adj"], 3)}
                                  for (v2, l), r in scores.items() if v2 == v} for v in backend.CONT_VARS}}
        else:
            print("\n(No temperature/humidity/wind data cached. Run 'python backend.py download' "
                  "without --no-extras to add it.)")
    with open(CAL_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f)
    write_report(REPORT_PATH, args, meta, results, leads, len(samples), split_desc)
    if cont:
        write_cont_report(REPORT_PATH, cont, out["continuous"]["meta"]["leads"],
                          "Reference: ERA5 (a reanalysis). These scores show agreement with ERA5, NOT with station "
                          "readings. ERA5 is close to observations for temperature, less so for wind and sunshine.")

    print(f"{len(samples)} samples; {split_desc}\n")
    print(f"{'event':<10}{'lead':>5}{'events':>8}{'BSS':>7}  verdict")
    for (ev, lead), r in sorted(results.items()):
        print(f"{ev:<10}{lead:>4}d{r['n_pos']:>8}{f2(r['bss'])}  {r['verdict']}")
    if cont:
        print(f"\n{'variable':<10}{'lead':>5}{'MAE adj':>9}{'persist':>9}{'normal':>9}{'skill':>7}  verdict")
        for (v, l), r in sorted(cont["scores"].items()):
            f = lambda x: "      n/a" if x is None else f"{x:9.2f}"
            print(f"{v:<10}{l:>4}d{f(r['mae_adj'])}{f(r['mae_persist'])}{f(r['mae_clim'])}{f2(r['skill'])}  {r['verdict']}")
    print(f"\nSaved:\n  {REPORT_PATH}\n  {CAL_PATH}")
    if args.truth == "era5":
        print("\nWARNING: ERA5 truth is not independent of the model. Do not quote these as accuracy.")


if __name__ == "__main__":
    main()
