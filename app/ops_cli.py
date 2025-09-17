
#!/usr/bin/env python3
import argparse
import os
import sqlite3
from typing import Optional, Tuple

# --- local modules ---
from app.ops_db import (
    connect as ops_connect,
    ensure_tank,
    start_session,
    close_session,   # now imported from ops_db
    add_reading,
)
from app.vcf54b import vcf_54b as vcf54b

# Reuse the capacity DB (volumes/heels)
DB_CAP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sounding.db")

# ---------------- capacity helpers (copied from cli.py, trimmed) ----------------
def _q(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()

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
    Falls back to heel rows with trim IS NULL if needed.
    """
    field = "correction_m3" if heel_code else "volume_m3"

    def run_query(require_trim: bool):
        sql = [
            f"SELECT {axis_field}, {field}",
            "FROM readings",
            "WHERE name=?",
        ]
        params = [name]

        if require_trim:
            if trim is None:
                sql.append("AND trim IS NULL")
            else:
                sql.append("AND CAST(trim AS REAL) = ?")
                params.append(float(trim))
        else:
            sql.append("AND trim IS NULL")

        if heel_code is None:
            sql.append("AND heel IS NULL")
        else:
            sql.append("AND heel = ?")
            params.append(heel_code)

        sql.append(f"AND {axis_field} IS NOT NULL AND {field} IS NOT NULL")
        sql.append(f"ORDER BY {axis_field} ASC;")
        return _q(conn, " ".join(sql), tuple(params))

    rows = run_query(require_trim=True)
    if not rows and heel_code is not None:
        rows = run_query(require_trim=False)
    if not rows:
        return (None, None, None, None)

    # Clamp to bounds (no extrapolation)
    xmin, xmax = rows[0][0], rows[-1][0]
    x = max(min(x, xmax), xmin)

    lo = None
    hi = None
    for xx, yy in rows:
        if xx <= x:
            lo = (xx, yy)
        if xx >= x:
            hi = (xx, yy)
            break
    if lo is None:
        lo = rows[0]
    if hi is None:
        hi = rows[-1]
    return lo[0], lo[1], hi[0], hi[1]

def _get_available_trims(conn, name: str):
    rows = _q(conn, "SELECT DISTINCT CAST(trim AS REAL) FROM readings WHERE name=? AND trim IS NOT NULL ORDER BY CAST(trim AS REAL);", (name,))
    return [r[0] for r in rows if r[0] is not None]

def _nearest_trims(trims, t):
    if not trims:
        return (None, None)
    for tv in trims:
        if tv == t:
            return (t, t)
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
    x0,y0,x1,y1 = _nearest_pair_along(conn, name, trim, heel_code=None, axis_field=axis_field, x=x)
    return None if x0 is None else _interp(x, x0,y0,x1,y1)

def _base_volume_cross_trim(conn, name: str, trim: float, sounding: Optional[float], ullage: Optional[float]) -> Optional[float]:
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
        return v0 if v1 is None else v1
    return _interp(trim, t0, v0, t1, v1)

def _base_volume(conn, name: str, trim: float, sounding: Optional[float], ullage: Optional[float]) -> Optional[float]:
    if sounding is not None:
        v = _base_volume_at_trim(conn, name, trim, "sounding_cm", sounding)
        return v if v is not None else _base_volume_cross_trim(conn, name, trim, sounding, None)
    if ullage is not None:
        v = _base_volume_at_trim(conn, name, trim, "ullage_cm", ullage)
        return v if v is not None else _base_volume_cross_trim(conn, name, trim, None, ullage)
    return None

def _heel_corr_at(conn, name: str, trim: float, heel_code: str, sounding: Optional[float], ullage: Optional[float]) -> float:
    axis = "sounding_cm" if sounding is not None else "ullage_cm"
    x = sounding if sounding is not None else ullage
    x0,y0,x1,y1 = _nearest_pair_along(conn, name, trim, heel_code=heel_code, axis_field=axis, x=x)
    return 0.0 if x0 is None else float(_interp(x, x0,y0,x1,y1))

def _parse_heel(heel_raw: Optional[str]) -> Tuple[Optional[str], Optional[float]]:
    if not heel_raw:
        return (None, None)
    s = heel_raw.strip().upper().replace("DEG", "").replace("°", "")
    if s in {"P1","P2","S-1","S-2"}:
        return ("DISCRETE", None)
    try:
        deg_only = float(s)
        if deg_only == 0:
            return (None, None)
        side = "P" if deg_only > 0 else "S"
        return (side, abs(deg_only))
    except Exception:
        pass
    import re
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([PS])\s*$", s) or re.match(r"^\s*([PS])\s*(\d+(?:\.\d+)?)\s*$", s)
    if m:
        if m.lastindex == 2:
            if m.group(1) in ("P", "S"):
                side, deg = m.group(1), float(m.group(2))
            else:
                side, deg = m.group(2), float(m.group(1))
            if deg == 0:
                return (None, None)
            return (side, deg)
    return ("DISCRETE", None)

def _continuous_heel_corr(conn, name, trim, side: str, deg: float, sounding: Optional[float], ullage: Optional[float]) -> float:
    if deg <= 0:
        return 0.0
    if deg >= 2:
        deg = 2.0
    one = f"{side}1" if side == "P" else "S-1"
    two = f"{side}2" if side == "P" else "S-2"
    if deg <= 1.0:
        c1 = _heel_corr_at(conn, name, trim, one, sounding, ullage)
        return deg * c1
    c1 = _heel_corr_at(conn, name, trim, one, sounding, ullage)
    c2 = _heel_corr_at(conn, name, trim, two, sounding, ullage)
    return c1 + (deg - 1.0) * (c2 - c1)

# ----------------------------- commands -----------------------------
def cmd_tanks_list(_):
    conn = ops_connect()
    rows = conn.execute("SELECT name, product, mode, density15_kg_m3 FROM tanks ORDER BY name;").fetchall()
    conn.close()
    if not rows:
        print("No tanks in ops DB yet. Use 'tanks-ensure' first.")
        return
    print("\nOps tanks:")
    for name, product, mode, d15 in rows:
        extra = f"product={product or '-'} mode={mode or '-'} density15={d15 or '-'}"
        print(f"  - {name}  [{extra}]")

def cmd_tanks_ensure(args):
    conn = ops_connect()
    ensure_tank(conn, args.name, product=args.product, density15_kg_m3=args.density15, mode=args.mode)
    conn.close()
    print(f"Ensured tank in ops DB: {args.name}")

def cmd_sessions_start(args):
    conn = ops_connect()
    sid = start_session(conn, kind=args.kind, title=args.title)
    conn.close()
    print(f"Started session id={sid} kind={args.kind} title={args.title!r}")

def cmd_sessions_close(args):
    conn = ops_connect()
    close_session(conn, args.id)
    conn.close()
    print(f"Closed session id={args.id}")

def cmd_sessions_list(args):
    conn = ops_connect()
    sql = "SELECT id, kind, title, started_at, closed_at FROM sessions"
    if args.open_only:
        sql += " WHERE closed_at IS NULL"
    sql += " ORDER BY COALESCE(closed_at, started_at) DESC;"
    rows = conn.execute(sql).fetchall()
    conn.close()
    if not rows:
        print("No sessions yet.")
        return
    for id_, kind, title, started, closed in rows:
        status = "OPEN " if closed is None else "CLOSED"
        print(f"[{status}] #{id_:>4}  {kind:<10}  {title or ''}  (start={started}, end={closed})")

def _compute_volumes(name: str, trim: float,
                     sounding: Optional[float], ullage: Optional[float],
                     heel: Optional[str]) -> Tuple[float, float, float]:
    conn = sqlite3.connect(DB_CAP_PATH)
    try:
        base = _base_volume(conn, name, trim, sounding, ullage)
        if base is None:
            raise ValueError("No base volume found (check tank, trim, and sounding/ullage range).")

        side, deg = _parse_heel(heel)
        if side is None:
            corr = 0.0
        elif side == "DISCRETE":
            corr = _heel_corr_at(conn, name, trim, heel.strip().upper(), sounding, ullage)
        else:
            corr = _continuous_heel_corr(conn, name, trim, side, deg, sounding, ullage)

        return float(base), float(corr), float(base + corr)
    finally:
        conn.close()

def cmd_readings_add(args):
    base, corr, v_obs = _compute_volumes(
        name=args.tank,
        trim=args.trim,
        sounding=args.sounding,
        ullage=args.ullage,
        heel=args.heel,
    )

    # Convert density to kg/m3 if given in kg/L
    d15 = args.density15
    if args.density15_unit == "kg/L":
        d15 = d15 * 1000.0

    vcf = vcf54b(d15, args.temperature)  # positional args: (density15_kg_m3, temperature_c)
    v15 = v_obs * vcf
    mass_kg = v15 * d15

    conn = ops_connect()
    ensure_tank(conn, args.tank, product=args.product, density15_kg_m3=d15,
                mode=("sounding" if args.sounding is not None else "ullage"))
    add_reading(conn,
        tank_name=args.tank,
        mode=("sounding" if args.sounding is not None else "ullage"),
        sounding_cm=args.sounding, ullage_cm=args.ullage,
        trim=args.trim, heel_label=(args.heel or "0"),
        temperature_c=args.temperature, density15_kg_m3=d15,
        base_vol_m3=base, heel_corr_m3=corr, volume_obs_m3=v_obs,
        vcf=vcf, volume_15c_m3=v15, mass_kg=mass_kg,
        note=args.note, session_id=args.session)
    conn.close()

    axis = "sounding" if args.sounding is not None else "ullage"
    xval = args.sounding if args.sounding is not None else args.ullage
    print("\nSaved reading:")
    print(f"  tank         : {args.tank}")
    print(f"  session      : {args.session or '-'}")
    print(f"  {axis:<12}: {xval}")
    print(f"  trim         : {args.trim}")
    print(f"  heel         : {args.heel or '0'}")
    print(f"  temperature  : {args.temperature} °C")
    print(f"  density@15   : {d15:.1f} kg/m³")
    print(f"  base volume  : {base:.3f} m³")
    print(f"  heel corr    : {corr:+.3f} m³")
    print(f"  observed vol : {v_obs:.3f} m³")
    print(f"  VCF          : {vcf:.6f}")
    print(f"  vol @15°C    : {v15:.3f} m³")
    print(f"  mass         : {mass_kg:.1f} kg ({mass_kg/1000:.3f} t)")


def close_session(conn: sqlite3.Connection, session_id: int, closed_at: str | None = None, note: str | None = None):
    """
    Mark a session as closed in the 'sessions' table.
    If closed_at is None, uses CURRENT_TIMESTAMP.
    If note is provided, appends it to the existing note (newline-separated).
    """
    # set closed_at
    if closed_at is None:
        conn.execute(
            "UPDATE sessions SET closed_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (session_id,)
        )
    else:
        conn.execute(
            "UPDATE sessions SET closed_at = ? WHERE id = ?;",
            (closed_at, session_id)
        )

    # optional note append
    if note is not None:
        row = conn.execute("SELECT note FROM sessions WHERE id = ?;", (session_id,)).fetchone()
        prev = row[0] if row and row[0] else ""
        new_note = prev + ("\n" if prev else "") + note
        conn.execute("UPDATE sessions SET note = ? WHERE id = ?;", (new_note, session_id))

    conn.commit()

def cmd_readings_list(args):
    conn = ops_connect()
    rows = conn.execute("""
        SELECT r.id, r.tank_name, r.created_at, r.mode, r.sounding_cm, r.ullage_cm,
               r.trim, r.heel_label, r.temperature_c, r.density15_kg_m3,
               r.volume_obs_m3, r.volume_15c_m3, r.mass_kg, r.session_id, r.note
        FROM readings r
        WHERE (? IS NULL OR r.session_id = ?)
        ORDER BY r.created_at DESC
        LIMIT ?;
    """, (args.session, args.session, args.limit)).fetchall()
    conn.close()
    if not rows:
        print("No readings yet.")
        return
    for (rid, name, created, mode, snd, ull, trim, heel, t, d15, vobs, v15, mass, sid, note) in rows:
        axis = "snd" if snd is not None else "ull"
        xval = snd if snd is not None else ull
        print(f"[#{rid}] {created}  {name}  {axis}={xval} trim={trim} heel={heel}  T={t}°C  d15={d15:.1f}  Vobs={vobs:.3f}  V15={v15:.3f}  m={mass/1000:.3f} t  sid={sid or '-'}  {note or ''}")

# ----------------------------- parser -----------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="ops-cli", description="Operational logging for tank soundings/ROB/bunkering")
    sub = p.add_subparsers(dest="cmd", required=True)

    # tanks
    t1 = sub.add_parser("tanks-list", help="List tanks present in ops DB")
    t1.set_defaults(func=cmd_tanks_list)

    t2 = sub.add_parser("tanks-ensure", help="Create/update basic tank entry in ops DB")
    t2.add_argument("--name", required=True)
    t2.add_argument("--product", default=None, help="e.g. HFO, MGO, Bilge, Sludge")
    t2.add_argument("--density15", type=float, default=None, help="kg/m3 (optional default for this tank)")
    t2.add_argument("--mode", choices=["sounding","ullage"], default=None)
    t2.set_defaults(func=cmd_tanks_ensure)

    # sessions
    s1 = sub.add_parser("session-start", help="Start a logging session (e.g., survey, bunkering)")
    s1.add_argument("--kind", required=True, choices=["survey","bunkering","other"])
    s1.add_argument("--title", required=True)
    s1.set_defaults(func=cmd_sessions_start)

    s2 = sub.add_parser("session-close", help="Close a session by id")
    s2.add_argument("--id", type=int, required=True)
    s2.set_defaults(func=cmd_sessions_close)

    s3 = sub.add_parser("sessions", help="List sessions")
    s3.add_argument("--open-only", action="store_true")
    s3.set_defaults(func=cmd_sessions_list)

    # readings
    r1 = sub.add_parser("reading-add", help="Compute from capacity DB & save to ops DB")
    r1.add_argument("--tank", required=True)
    grp = r1.add_mutually_exclusive_group(required=True)
    grp.add_argument("--sounding", type=float)
    grp.add_argument("--ullage", type=float)
    r1.add_argument("--trim", type=float, required=True)
    r1.add_argument("--heel", type=str, default=None, help="P1,P2,S-1,S-2 or 0.7P / 1.2S / 0")
    r1.add_argument("--temperature", type=float, required=True)
    r1.add_argument("--density15", type=float, required=True, help="density at 15°C, unit chosen below")
    r1.add_argument("--density15-unit", choices=["kg/m3","kg/L"], default="kg/m3")
    r1.add_argument("--product", default=None, help="optional override product label when ensuring tank")
    r1.add_argument("--session", type=int, default=None, help="optional session id to attach")
    r1.add_argument("--note", default=None)
    r1.set_defaults(func=cmd_readings_add)

    r2 = sub.add_parser("readings", help="List recent readings")
    r2.add_argument("--session", type=int, default=None)
    r2.add_argument("--limit", type=int, default=20)
    r2.set_defaults(func=cmd_readings_list)

    return p

def main():
    args = build_parser().parse_args()
    args.func(args)

if __name__ == "__main__":
    main()