#!/usr/bin/env python3
"""
build_states.py - adds EVERY state / province / region of EVERY country to the world version.

You normally do NOT need to run this: the results are already inside world_map.json and
world_points.json. Run it only to rebuild them or to change how detailed the shapes are.

What it does
  1. Downloads Natural Earth 1:10m "admin-1" boundaries and populated places (public domain,
     ~46 MB, cached in the ne_cache folder).
  2. For every country with 2 or more subdivisions it simplifies the shapes (neighbouring
     borders are simplified together, so no gaps or overlaps), projects them onto your map and
     writes them as one zoom layer into world_map.json.
  3. Adds one region per subdivision to world_points.json, with sample points: its biggest
     city, plus up to 4 more for very large regions.

India keeps its own 36-state layer (official boundaries) that is already in your files.
Running this again replaces everything it generated before, so it is safe to repeat.

Needs:  pip install shapely          (numpy comes with it)
Run:    py build_states.py
        py build_states.py --detail 0.7        # smaller/coarser file   (default 1.0, 1.5 = finer)
        py build_states.py --only USA,BRA      # just those countries (testing)
"""

import argparse
import collections
import json
import math
import os
import sys
import urllib.request

try:
    import numpy as np
    import shapely
    from shapely import STRtree
    from shapely.geometry import MultiPolygon, Point, box
except ImportError:
    sys.exit("This needs shapely.  Install it with:  pip install shapely")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "ne_cache")
NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
ADMIN1 = "ne_10m_admin_1_states_provinces.geojson"
PLACES = "ne_10m_populated_places_simple.geojson"
OWN_LAYER = {"IND"}                # already in the files with official boundaries: left alone
SRC = "ne10"                       # tag on everything this script generates

KIND = {                           # Natural Earth type -> the word shown in the site
    "Province": "province", "State": "state", "Region": "region", "District": "district",
    "County": "county", "Department": "department", "Metropolitan department": "department",
    "Municipality": "municipality", "Commune|Municipality": "municipality", "Prefecture": "prefecture",
    "Statistical Region": "region", "Governorate": "governorate", "Parish": "parish",
    "Unitary Authority": "authority", "Autonomous Community": "community", "Canton": "canton",
    "Metropolitan Borough": "borough", "London Borough": "borough", "Administrative County": "county",
    "Autonomous Province": "province", "Highly Urbanized City": "city", "City": "city",
    "Autonomous Region": "region", "Division": "division", "Emirate": "emirate", "Voivodeship": "voivodeship",
    "Oblast": "oblast", "Republic": "republic", "Territory": "territory", "Island": "island",
}
PLURAL = {"authority": "authorities", "county": "counties", "community": "communities", "city": "cities",
          "territory": "territories", "municipality": "municipalities", "republic": "republics"}
MAX_PTS = 5                        # sample points per region (1 + one per 400,000 km2)
KM_PER_PTS = 400000.0


def plural(w):
    return PLURAL.get(w, w + "s")


# ---------------------------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------------------------
def fetch(name):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        print(f"downloading {name} ...")
        urllib.request.urlretrieve(NE_BASE + name, path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["features"]


# ---------------------------------------------------------------------------------------------
# Projection: the same Miller projection as the rest of the site (see reproject.py / index.html)
# ---------------------------------------------------------------------------------------------
class Proj:
    def __init__(self, p):
        if p.get("type") != "miller":
            sys.exit("world_map.json is not in the Miller projection. Run reproject.py first.")
        self.lon0, self.S, self.yTop = p["lon0"], p["s"], p["yTop"]
        self.latTop, self.latBot = p["latTop"], p["latBot"]

    def xy(self, lon, lat):
        lon = np.asarray(lon, dtype=float)
        lat = np.clip(np.asarray(lat, dtype=float), -89.9, 89.9)
        x = (lon - self.lon0) * np.pi / 180 * self.S
        y = (self.yTop - 1.25 * np.log(np.tan(np.pi / 4 + 0.4 * np.radians(lat)))) * self.S
        return x, y


def polys(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if g.geom_type in ("MultiPolygon", "GeometryCollection"):
        out = []
        for h in g.geoms:
            out += polys(h)
        return out
    return []


def km2(g):
    return sum(p.area * 111.32 ** 2 * math.cos(math.radians(p.centroid.y)) for p in polys(g))


def hav(la1, lo1, la2, lo2):
    r = math.pi / 180
    a = math.sin((la2 - la1) * r / 2) ** 2 + math.cos(la1 * r) * math.cos(la2 * r) * math.sin((lo2 - lo1) * r / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(a)))


def core_box(geoms):
    """Bounding box (lon/lat) of the polygons that hold 90% of the area: the 'main body' of a country
    (leaves out far islands and the bit that wraps over the date line)."""
    ps = sorted((p for g in geoms for p in polys(g)), key=lambda p: -p.area)
    tot, acc, sel = sum(p.area for p in ps), 0.0, []
    for p in ps:
        sel.append(p)
        acc += p.area
        if acc >= 0.9 * tot:
            break
    b = np.array([p.bounds for p in sel])
    return b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()


# ---------------------------------------------------------------------------------------------
# Path strings
# ---------------------------------------------------------------------------------------------
def ring_path(proj, ring, dec):
    c = np.asarray(ring.coords)
    x, y = proj.xy(c[:, 0], c[:, 1])
    pts, last = [], None
    for a, b in zip(np.round(x, dec), np.round(y, dec)):
        if (a, b) != last:
            pts.append((a, b))
            last = (a, b)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    if len(pts) < 3:
        return ""
    f = "%." + str(dec) + "f"
    return "M" + "L".join((f % a) + "," + (f % b) for a, b in pts) + "Z"


def shape_path(proj, g, dec, min_px2):
    """SVG path for a (multi)polygon; drops specks smaller than min_px2 (but never the biggest part)."""
    ps = polys(g)
    if not ps:
        return "", None
    info = []
    for p in ps:
        c = np.asarray(p.exterior.coords)
        x, y = proj.xy(c[:, 0], c[:, 1])
        a = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
        info.append((a, p, x, y))
    top = max(i[0] for i in info)
    d, xs, ys = [], [], []
    for a, p, x, y in info:
        if a < min_px2 and a != top:
            continue
        s = ring_path(proj, p.exterior, dec)
        if not s:
            continue
        d.append(s)
        xs += [x.min(), x.max()]
        ys += [y.min(), y.max()]
        for h in p.interiors:
            hc = np.asarray(h.coords)
            hx, hy = proj.xy(hc[:, 0], hc[:, 1])
            ha = 0.5 * abs(np.dot(hx, np.roll(hy, 1)) - np.dot(hy, np.roll(hx, 1)))
            if ha >= min_px2:
                hs = ring_path(proj, h, dec)
                if hs:
                    d.append(hs)
    if not d:
        return "", None
    return "".join(d), [round(min(xs), dec), round(min(ys), dec), round(max(xs), dec), round(max(ys), dec)]


# ---------------------------------------------------------------------------------------------
# Colours: neighbours get different colour numbers (0-5), so borders stay readable
# ---------------------------------------------------------------------------------------------
def colour(geoms):
    n = len(geoms)
    tree = STRtree(geoms)
    nb = [set() for _ in range(n)]
    for i, g in enumerate(geoms):
        for j in tree.query(g, predicate="intersects"):
            if j != i:
                nb[i].add(int(j))
                nb[int(j)].add(i)
    col = [-1] * n
    for _ in range(n):                                   # DSATUR
        best, key = -1, None
        for i in range(n):
            if col[i] >= 0:
                continue
            k = (len({col[j] for j in nb[i] if col[j] >= 0}), len(nb[i]))
            if key is None or k > key:
                best, key = i, k
        if best < 0:
            break
        used = {col[j] for j in nb[best]}
        col[best] = next((c for c in range(6) if c not in used), best % 6)
    return col


# ---------------------------------------------------------------------------------------------
# Sample points
# ---------------------------------------------------------------------------------------------
def compass(cy, cx, la, lo, size_km):
    dx, dy = (lo - cx) * math.cos(math.radians(cy)), la - cy
    if math.hypot(dx, dy) * 111.32 < 0.12 * size_km:
        return "centre"
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((ang + 22.5) // 45) % 8]


def pick_points(geom, rname, cand, n, area):
    """cand: [(pop, name, lat, lon)] inside the region, any order. -> [[name, lat, lon], ...]"""
    cand = sorted(cand, key=lambda c: -c[0])
    out = []
    if cand:
        out.append([cand[0][1], cand[0][2], cand[0][3]])
    else:
        r = geom.representative_point()
        out.append([rname, r.y, r.x])
    if n > 1:
        gap = 0.3 * math.sqrt(area)
        for _, nm, la, lo in cand[1:]:
            if len(out) >= n:
                break
            if all(hav(la, lo, p[1], p[2]) >= gap for p in out) and nm not in {p[0] for p in out}:
                out.append([nm, la, lo])
        if len(out) < n:                                  # not enough towns: spread extra points over the region
            minx, miny, maxx, maxy = geom.bounds
            gx, gy = np.meshgrid(np.linspace(minx, maxx, 34)[1:-1], np.linspace(miny, maxy, 34)[1:-1])
            gx, gy = gx.ravel(), gy.ravel()
            ok = shapely.contains_xy(geom, gx, gy)
            grid = list(zip(gy[ok], gx[ok]))
            c = geom.centroid
            used = set()
            while len(out) < n and grid:
                la, lo = max(grid, key=lambda q: min(hav(q[0], q[1], p[1], p[2]) for p in out))
                grid.remove((la, lo))
                label = compass(c.y, c.x, la, lo, math.sqrt(area))
                nm = f"{rname} {label}"
                k = 2
                while nm in used or nm in {p[0] for p in out}:
                    nm = f"{rname} {label} {k}"
                    k += 1
                used.add(nm)
                out.append([nm, la, lo])
    return [[p[0], round(float(p[1]), 2), round(float(p[2]), 2)] for p in out]


# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Add every state/province of every country to the world map.")
    ap.add_argument("--detail", type=float, default=1.0, help="shape detail: 0.7 smaller file, 1.5 finer")
    ap.add_argument("--only", default="", help="comma list of 3-letter country codes (testing)")
    ap.add_argument("--map", default=os.path.join(HERE, "world_map.json"))
    ap.add_argument("--points", default=os.path.join(HERE, "world_points.json"))
    a = ap.parse_args(argv)
    only = {c.strip().upper() for c in a.only.split(",") if c.strip()}

    with open(a.map, "r", encoding="utf-8") as f:
        wmap = json.load(f)
    with open(a.points, "r", encoding="utf-8") as f:
        wpts = json.load(f)
    proj = Proj(wmap["proj"])
    world_box = box(-180, proj.latBot, 180, proj.latTop)

    # drop what an earlier run generated
    wmap["layers"] = [L for L in wmap.get("layers", []) if L.get("src") != SRC]
    wpts["regions"] = [r for r in wpts["regions"] if r.get("src") != SRC]
    countries = {r["code"]: r for r in wpts["regions"] if not r.get("parent")}
    taken = {r["name"] for r in wpts["regions"]}

    print("reading Natural Earth ...")
    feats = fetch(ADMIN1)
    places = fetch(PLACES)
    by = collections.defaultdict(list)
    for f in feats:
        p = f["properties"]
        if p["adm0_a3"] in countries and p["adm0_a3"] not in OWN_LAYER and (p.get("name") or "").strip():
            if not only or p["adm0_a3"] in only:
                by[p["adm0_a3"]].append(f)

    pl_pts = [Point(f["geometry"]["coordinates"]) for f in places]
    pl_tree = STRtree(pl_pts)
    pl_by_adm1 = collections.defaultdict(list)
    for f, pt in zip(places, pl_pts):
        p = f["properties"]
        pl_by_adm1[(p["adm0_a3"], (p.get("adm1name") or "").strip().lower())].append(
            (p.get("pop_max") or 0, p["name"], pt.y, pt.x))

    n_reg = n_pts = n_vert = 0
    new_layers, new_regions, skipped = [], [], []
    for code in sorted(by):
        fs = by[code]
        if len(fs) < 2:
            skipped.append(countries[code]["name"])
            continue
        ctry = countries[code]
        geoms = []
        for f in fs:
            g = shapely.from_geojson(json.dumps(f["geometry"]))
            if not g.is_valid:
                g = shapely.make_valid(g)
            if g.bounds[1] < proj.latBot - 0.01 or g.bounds[3] > proj.latTop + 0.01:
                g = g.intersection(world_box)
            ps = polys(g)                                  # keep plain polygons only
            geoms.append(ps[0] if len(ps) == 1 else MultiPolygon(ps))

        # how big is the country? decides how coarse the shapes can be and when the layer appears
        x0, y0, x1, y1 = core_box(geoms)
        clat = (y0 + y1) / 2
        size_km = max((x1 - x0) * 111.32 * math.cos(math.radians(clat)), (y1 - y0) * 111.32, 1.0)
        px, py = proj.xy([x0, x1], [y0, y1])
        size_px = max(abs(px[1] - px[0]), abs(py[1] - py[0]), 0.05)
        tol_km = min(5.0, max(0.12, 0.002 * size_km)) / a.detail
        dec = 2 if size_px >= 10 else (3 if size_px >= 1.5 else 4)
        min_px2 = min(0.02, max(1e-6, (0.0025 * size_px / a.detail) ** 2))
        min_zoom = round(min(120.0, max(2.0, 255.0 / (0.85 * size_px))), 1)

        arr = np.array(geoms, dtype=object)
        simp = shapely.coverage_simplify(arr, tol_km / 111.32)
        cols = colour(list(arr))

        # names (unique across the whole world)
        kinds = [KIND.get(f["properties"].get("type_en") or "", None) for f in fs]
        mode = collections.Counter(k for k in kinds if k).most_common(1)
        base_kind = mode[0][0] if mode else "region"
        states, seen_here = [], set()
        pidx = {}
        for i, f in enumerate(fs):
            p = f["properties"]
            nm = p["name"].strip()
            rid = f"{nm} ({ctry['name']})"
            kind = kinds[i] or base_kind
            if rid in taken or rid in seen_here:
                rid = f"{nm} {kind} ({ctry['name']})"
            if rid in taken or rid in seen_here:
                rid = f"{nm} [{p['adm1_code']}] ({ctry['name']})"
            seen_here.add(rid)
            taken.add(rid)

            g, s = geoms[i], simp[i]
            d, bb = shape_path(proj, s, dec, min_px2)
            if not d:                                     # collapsed by simplification: use the exact shape
                d, bb = shape_path(proj, g, dec, 0.0)
            if not d:
                continue
            area = km2(g)
            st = {"id": rid, "d": d, "area": int(area), "bbox": bb, "c": cols[i]}
            if max(bb[2] - bb[0], bb[3] - bb[1]) < 0.35:
                cx, cy = proj.xy([g.representative_point().x], [g.representative_point().y])
                st["marker"] = [round(float(cx[0]), dec), round(float(cy[0]), dec)]
            states.append(st)
            n_vert += d.count("L") + d.count("M")

            # sample points
            idx = pl_tree.query(g, predicate="contains")
            cand = [(pl_by_adm1_pop(places, int(j))) for j in idx]
            if not cand:
                cand = list(pl_by_adm1.get((code, nm.lower()), []))
            n = min(MAX_PTS, 1 + int(area // KM_PER_PTS))
            pts = pick_points(g, nm, cand, n, max(area, 1.0))
            sub = ctry.get("subregion") or ""
            new_regions.append({
                "name": rid, "code": code, "continent": ctry.get("continent"), "subregion": sub,
                "group": f"{sub}|{'1' if len(pts) == 1 else '2-5'}", "points": pts,
                "area_km2": int(area), "parent": ctry["name"], "kind": kind, "src": SRC})
            n_pts += len(pts)
            n_reg += 1

        if len(states) < 2:
            continue
        bx0, by0 = proj.xy([x0], [y1])
        bx1, by1 = proj.xy([x1], [y0])
        new_layers.append({
            "parent": ctry["name"], "label": f"{ctry['name']}'s {plural(base_kind)}", "kind": base_kind,
            "minZoom": min_zoom, "fbox": [round(float(bx0[0]), 1), round(float(by0[0]), 1),
                                          round(float(bx1[0]), 1), round(float(by1[0]), 1)],
            "src": SRC, "states": states})

    wmap.setdefault("layers", []).extend(new_layers)
    wpts["regions"].extend(new_regions)
    wpts.setdefault("meta", {})["note"] = (
        "India's states come from official boundaries; every other country's states/provinces/regions are "
        "Natural Earth 1:10m admin-1 (public domain), one or more sample points each (see build_states.py).")
    wpts["meta"]["density"] = ("countries: about one point per 250,000 km2, at least 1 and at most 30; "
                               "states/provinces: their biggest city, plus one more per 400,000 km2 (max 5)")
    with open(a.map, "w", encoding="utf-8") as f:
        json.dump(wmap, f, ensure_ascii=False, separators=(",", ":"))
    with open(a.points, "w", encoding="utf-8") as f:
        json.dump(wpts, f, ensure_ascii=False, separators=(",", ":"))
    mb = os.path.getsize(a.map) / 1e6
    print(f"\n{len(new_layers)} countries now show their states/provinces when you zoom in")
    print(f"{n_reg} regions, {n_pts} sample points, ~{n_vert} shape vertices; world_map.json is {mb:.1f} MB")
    if skipped:
        print(f"{len(skipped)} countries have no subdivisions in the data and stay as one shape: " + ", ".join(sorted(skipped)[:40]))


def pl_by_adm1_pop(places, j):
    p = places[j]["properties"]
    c = places[j]["geometry"]["coordinates"]
    return (p.get("pop_max") or 0, p["name"], c[1], c[0])


if __name__ == "__main__":
    main()
