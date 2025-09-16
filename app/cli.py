#!/usr/bin/env python3
import argparse
import os
import sqlite3
from typing import Optional, Tuple

# Path to the SQLite DB created earlier
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sounding.db")


# ------------------------ DB helper ------------------------

def q(conn: sqlite3.Connection, sql: str, args=()):
    cur = conn.execute(sql, args)
    return cur.fetchall()


# ------------------------ CLI commands ------------------------

def list_tanks(conn: sqlite3.Connection):
    rows = q(conn, "SELECT DISTINCT name FROM readings ORDER BY name;")
    print("\nTanks:")
    for (name,) in rows:
        print(f"  - {name}")
    print(f"\nTotal: {len(rows)}")


def info_tank(conn: sqlite3.Connection, name: str):
    rows = q(conn, "SELECT COUNT(*) FROM readings WHERE name=?;", (name,))
    if not rows:
        print("Tank not found")
        return
    (count,) = rows[0]
    rng_s = q(conn, "SELECT MIN(sounding_cm), MAX(sounding_cm) FROM readings WHERE name=?;", (name,))
    rng_u = q(conn, "SELECT MIN(ullage_cm), MAX(ullage_cm) FROM readings WHERE name=?;", (name,))
    trims = [t[0] for t in q(conn,
                             "SELECT DISTINCT trim FROM readings "
                             "WHERE name=? AND trim IS NOT NULL "
                             "ORDER BY CAST(trim AS REAL);", (name,))]
    heels = [h[0] for h in q(conn,
                             "SELECT DISTINCT heel FROM readings "
                             "WHERE name=? AND heel IS NOT NULL "
                             "ORDER BY heel;", (name,))]
    print(f"\nTank: {name}")
    print(f"Rows: {count}")
    if rng_s and rng_s[0][0] is not None:
        print(f"Sounding range (cm): {rng_s[0][0]} \u2192 {rng_s[0][1]}")
    if rng_u and rng_u[0][0] is not None:
        print(f"Ullage   range (cm): {rng_u[0][0]} \u2192 {rng_u[0][1]}")
    if trims:
        print("Trims:", ", ".join(str(t) for t in trims))
    if heels:
        print("Heels:", ", ".join(heels))


def show_rows(conn: sqlite3.Connection, name: str, limit: int):
    rows = q(conn, """
        SELECT name, sounding_cm, ullage_cm, trim, heel, volume_m3, correction_m3
        FROM readings
        WHERE name=?
        ORDER BY trim IS NOT NULL DESC, heel IS NOT NULL DESC, sounding_cm, ullage_cm
        LIMIT ?;""", (name, limit))
    if not rows:
        print("No rows.")
        return
    headers = ["name", "sounding_cm", "ullage_cm", "trim", "heel", "volume_m3", "correction_m3"]
    print("  ".join(headers))
    for r in rows:
        print("  ".join("" if v is None else str(v) for v in r))


# ------------------------ numeric interpolation ------------------------

def _interp(x, x0, y0, x1, y1):
    if x0 is None or x1 is None:
        return None
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return (1 - t) * y0 + t * y1


def _nearest_pair_along(conn, name, trim, heel_code, axis_field, x):
    """
    Return (x0,y0,x1,y1) for linear interpolation along sounding/ullage.

    - For base volume (heel_code is None) we match the requested trim numerically
      with a small tolerance to avoid text/float equality issues.
    - For heel corrections, if we don't find rows at the requested trim,
      we fall back to heel rows where trim IS NULL (many tables store heels that way).
    """
    field = "correction_m3" if heel_code else "volume_m3"

    def run_query_exact_trim():
        sql = [
            f"SELECT {axis_field}, {field}",
            "FROM readings",
            "WHERE name=?",
            "AND trim IS NOT NULL",
            "AND ABS(CAST(trim AS REAL) - ?) < 1e-6",
        ]
        params = [name, float(trim)]
        if heel_code is None:
            sql.append("AND heel IS NULL")
        else:
            sql.append("AND heel = ?")
            params.append(heel_code)
        sql.append(f"AND {axis_field} IS NOT NULL AND {field} IS NOT NULL")
        sql.append(f"ORDER BY {axis_field} ASC;")
        return q(conn, " ".join(sql), tuple(params))

    def run_query_trim_null_for_heel():
        sql = [
            f"SELECT {axis_field}, {field}",
            "FROM readings",
            "WHERE name=?",
            "AND trim IS NULL",
            "AND heel = ?",
            f"AND {axis_field} IS NOT NULL AND {field} IS NOT NULL",
            f"ORDER BY {axis_field} ASC;"
        ]
        return q(conn, " ".join(sql), (name, heel_code))

    # 1) try rows at the requested trim (approx numeric match)
    rows = run_query_exact_trim()

    # 2) if heel rows were requested and none found, try trim IS NULL fallback
    if not rows and heel_code is not None:
        rows = run_query_trim_null_for_heel()

    if not rows:
        return (None, None, None, None)

    # clamp x to table bounds (avoid extrapolation surprises)
    xmin, xmax = rows[0][0], rows[-1][0]
    xx = max(min(x, xmax), xmin)

    # find neighbors around xx
    lo = None
    hi = None
    for X, Y in rows:
        if X is None:
            continue
        if X <= xx:
            lo = (X, Y)
        if X >= xx:
            hi = (X, Y)
            break
    if lo is None:
        lo = rows[0]
    if hi is None:
        hi = rows[-1]
    return lo[0], lo[1], hi[0], hi[1]


def _get_available_trims(conn, name: str):
    rows = q(conn, """
        SELECT DISTINCT CAST(trim AS REAL)
        FROM readings
        WHERE name=? AND trim IS NOT NULL
        ORDER BY CAST(trim AS REAL);
    """, (name,))
    return [float(r[0]) for r in rows if r[0] is not None]

                        


def _get_available_trims(conn, name: str):
    rows = q(conn, "SELECT DISTINCT CAST(trim AS REAL) FROM readings WHERE name=? AND trim IS NOT NULL ORDER BY CAST(trim AS REAL);", (name,))
    return [r[0] for r in rows if r[0] is not None]

def _nearest_trims(trims, t):
    """Return (t0, t1) bracketing t (could collapse to same value at edges)."""
    if not trims:
        return (None, None)
    # if exact
    for tv in trims:
        if tv == t:
            return (t, t)
    # find neighbors
    lo = None
    hi = None
    for tv in trims:
        if tv <= t:
            lo = tv
        if tv >= t and hi is None:
            hi = tv
            break
    if lo is None:
        lo = trims[0]
    if hi is None:
        hi = trims[-1]
    return (lo, hi)

def _base_volume_at_trim(conn, name: str, trim: float, axis_field: str, x: float) -> Optional[float]:
    """Base volume at an exact trim (interpolating along sounding/ullage)."""
    x0,y0,x1,y1 = _nearest_pair_along(conn, name, trim, heel_code=None, axis_field=axis_field, x=x)
    return None if x0 is None else _interp(x, x0,y0,x1,y1)

def _base_volume_cross_trim(conn, name: str, trim: float, sounding: Optional[float], ullage: Optional[float]) -> Optional[float]:
    """If exact trim is missing, interpolate base volume across the two nearest trims."""
    axis_field = "sounding_cm" if sounding is not None else "ullage_cm"
    x = sounding if sounding is not None else ullage
    trims = _get_available_trims(conn, name)
    if not trims:
        return None
    t0, t1 = _nearest_trims(trims, trim)
    if t0 == t1:
        return _base_volume_at_trim(conn, name, t0, axis_field, x)
    v0 = _base_volume_at_trim(conn, name, t0, axis_field, x)
    v1 = _base_volume_at_trim(conn, name, t1, axis_field, x)
    if v0 is None or v1 is None:
        # if one side missing, fall back to the other
        return v0 if v1 is None else v1
    # linear across trim
    return _interp(trim, t0, v0, t1, v1)


def _base_volume(conn, name: str, trim: float, sounding: Optional[float], ullage: Optional[float]) -> Optional[float]:
    if sounding is not None:
        # try exact trim first
        v = _base_volume_at_trim(conn, name, trim, "sounding_cm", sounding)
        if v is not None:
            return v
        # fallback: interpolate across trims
        return _base_volume_cross_trim(conn, name, trim, sounding, None)

    if ullage is not None:
        v = _base_volume_at_trim(conn, name, trim, "ullage_cm", ullage)
        if v is not None:
            return v
        return _base_volume_cross_trim(conn, name, trim, None, ullage)

    return None

def _heel_corr_at(conn: sqlite3.Connection,
                  name: str,
                  trim: float,
                  heel_code: str,
                  sounding: Optional[float],
                  ullage: Optional[float]) -> float:
    """Interpolate heel correction along sounding/ullage for a discrete heel code like 'P1'."""
    axis = "sounding_cm" if sounding is not None else "ullage_cm"
    x = sounding if sounding is not None else ullage
    x0, y0, x1, y1 = _nearest_pair_along(conn, name, trim, heel_code=heel_code,
                                         axis_field=axis, x=x)
    return 0.0 if x0 is None else float(_interp(x, x0, y0, x1, y1))


# ------------------------ heel parsing & interpolation ------------------------

def _parse_heel(heel_raw: Optional[str]) -> Tuple[Optional[str], Optional[float]]:
    """
    Returns:
      ('P'|'S', degrees float) for continuous heels like '1.5P', 'P1.5', '0.7 S'
      (None, None) if no heel provided (including '0')
      ('DISCRETE', None) if user passed existing codes: P1, P2, S-1, S-2
    """
    if not heel_raw:
        return (None, None)
    s = heel_raw.strip().upper().replace("DEG", "").replace("°", "")

    # exact known codes
    if s in {"P1", "P2", "S-1", "S-2"}:
        return ("DISCRETE", None)

    # numeric only; treat 0 as no heel, otherwise PORT if >0, STBD if <0
    try:
        deg_only = float(s)
        if deg_only == 0:
            return (None, None)
        side = "P" if deg_only > 0 else "S"
        return (side, abs(deg_only))
    except Exception:
        pass

    # formats like "1.5P" or "2S"
    import re
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([PS])\s*$", s)
    if m:
        deg = float(m.group(1))
        if deg == 0:
            return (None, None)
        return (m.group(2), deg)
    # formats like "P1.5" or "S 0.7"
    m = re.match(r"^\s*([PS])\s*(\d+(?:\.\d+)?)\s*$", s)
    if m:
        deg = float(m.group(2))
        if deg == 0:
            return (None, None)
        return (m.group(1), deg)

    # fallback: treat as a discrete DB code the user provided
    return ("DISCRETE", None)


def _continuous_heel_corr(conn: sqlite3.Connection,
                          name: str,
                          trim: float,
                          side: str,
                          deg: float,
                          sounding: Optional[float],
                          ullage: Optional[float]) -> float:
    """Interpolate correction for any 0..2° on a side using anchors at 0, 1, 2."""
    # clamp to [0,2] with extrapolation prevention
    if deg <= 0:
        return 0.0
    if deg >= 2:
        deg = 2.0

    # pick discrete codes for 1° and 2° on that side
    one = f"{side}1" if side == "P" else "S-1"
    two = f"{side}2" if side == "P" else "S-2"

    if deg <= 1.0:
        c1 = _heel_corr_at(conn, name, trim, one, sounding, ullage)
        return deg * c1  # linear between 0 and 1°
    # between 1 and 2
    c1 = _heel_corr_at(conn, name, trim, one, sounding, ullage)
    c2 = _heel_corr_at(conn, name, trim, two, sounding, ullage)
    return c1 + (deg - 1.0) * (c2 - c1)


def cmd_volume(conn: sqlite3.Connection,
               name: str,
               trim: float,
               sounding: Optional[float],
               ullage: Optional[float],
               heel_raw: Optional[str]):
    base = _base_volume(conn, name, trim, sounding, ullage)
    if base is None:
        print("No base volume found. Check tank name, trim value, and sounding/ullage within range.")
        return

    side, deg = _parse_heel(heel_raw)
    if side is None:
        corr = 0.0
        heel_label = "no-heel"
    elif side == "DISCRETE":
        corr = _heel_corr_at(conn, name, trim, heel_raw.strip().upper(), sounding, ullage)
        heel_label = heel_raw
    else:
        corr = _continuous_heel_corr(conn, name, trim, side, deg, sounding, ullage)
        heel_label = f"{deg}°{side}"

    total = base + corr
    axis = "sounding" if sounding is not None else "ullage"
    xval = sounding if sounding is not None else ullage
    print(f"\nTank: {name}")
    print(f"Trim: {trim}")
    print(f"{axis.title()}: {xval}")
    print(f"Heel: {heel_label}")
    print(f"Base volume (m³): {base:.3f}")
    print(f"Heel corr  (m³): {corr:+.3f}")
    print(f"TOTAL       (m³): {total:.3f}")


# ------------------------ main ------------------------

def main():
    parser = argparse.ArgumentParser(prog="sounding-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    p_info = sub.add_parser("info")
    p_info.add_argument("name")

    p_show = sub.add_parser("show")
    p_show.add_argument("name")
    p_show.add_argument("--limit", type=int, default=30)

    p_vol = sub.add_parser("volume")
    p_vol.add_argument("name")
    group = p_vol.add_mutually_exclusive_group(required=True)
    group.add_argument("--sounding", type=float)
    group.add_argument("--ullage", type=float)
    p_vol.add_argument("--trim", type=float, required=True)
    p_vol.add_argument("--heel", type=str, default=None,
                       help="Examples: P1, P2, S-1, S-2, 0.7P, 1.5P, 2S, P0.3, or 0 (no heel)")

    args = parser.parse_args()
    conn = sqlite3.connect(DB_PATH)

    try:
        if args.cmd == "list":
            list_tanks(conn)
        elif args.cmd == "info":
            info_tank(conn, args.name)
        elif args.cmd == "show":
            show_rows(conn, args.name, args.limit)
        elif args.cmd == "volume":
            cmd_volume(conn, args.name, args.trim, args.sounding, args.ullage, args.heel)
    finally:
        conn.close()


if __name__ == "__main__":
    main()


