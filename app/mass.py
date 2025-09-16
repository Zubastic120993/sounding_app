
#!/usr/bin/env python3
"""
Mass calculator from observed volume using an ASTM 54B-style VCF approximation.

Inputs:
  - observed volume (default unit m3)
  - density at 15°C (can be kg/L or kg/m3)
  - observed temperature (°C)

Process:
  1) Compute VCF (Volume Correction Factor) ~ ASTM 54B approximation
  2) V15 = Vobs * VCF
  3) Mass = V15 * density@15°C

Notes:
  - This is an *approximation* to ASTM D1250 Table 54B in code form (no external tables).
  - For custody-transfer grade accuracy, replace vcf_astm54b() with a formal implementation.
"""

import argparse

# ---------- 54B-like VCF approximation ----------
# For middle distillates / residual fuels in the 0.75–1.05 kg/L range.
# Uses a density-dependent thermal expansion coefficient and a mild quadratic term.
# VCF = 1 / (1 + a*ΔT + b*ΔT^2), clamped sensibly for ΔT within about ±40°C.
def vcf_astm54b_approx(density15_kg_per_L: float, temp_c: float) -> float:
    d = float(density15_kg_per_L)
    # Clamp density to a sensible petroleum range for stability
    if d < 0.70:
        d = 0.70
    if d > 1.05:
        d = 1.05

    dT = temp_c - 15.0

    # Empirical thermal expansion ~ decreases with heavier products
    # (values around 0.0009–0.0012 1/°C are typical)
    alpha = 0.00125 - 0.00045 * (d - 0.80)  # 0.00125 at 0.80, ~0.0010 at ~0.90

    # Small quadratic stabilizer to mimic table curvature
    beta = 2.0e-6

    # Denominator form behaves better at larger |dT| than simple (1 - alpha*dT)
    denom = 1.0 + alpha * dT + beta * (dT ** 2)

    # Gentle clamping to avoid extreme extrapolation
    if denom < 0.90:
        denom = 0.90
    if denom > 1.10:
        denom = 1.10

    return 1.0 / denom


def kg_per_l_to_kg_per_m3(d_kg_per_l: float) -> float:
    return d_kg_per_l * 1000.0


def m3_to_liters(v_m3: float) -> float:
    return v_m3 * 1000.0


def liters_to_m3(v_l: float) -> float:
    return v_l / 1000.0


def compute_mass(
    volume_value: float,
    volume_unit: str,
    density15_value: float,
    density15_unit: str,
    temperature_c: float
):
    # Normalize units
    if volume_unit.lower() in {"m3", "m^3", "m³"}:
        v_obs_m3 = float(volume_value)
    elif volume_unit.lower() in {"l", "liter", "liters"}:
        v_obs_m3 = liters_to_m3(float(volume_value))
    else:
        raise ValueError("Unsupported volume unit. Use m3 or L.")

    if density15_unit.lower() in {"kg/l", "kgperl", "kg_l"}:
        d15_kgm3 = kg_per_l_to_kg_per_m3(float(density15_value))
        d15_kgL = float(density15_value)
    elif density15_unit.lower() in {"kg/m3", "kg_m3", "kgper m3", "kg/m^3"}:
        d15_kgm3 = float(density15_value)
        d15_kgL = d15_kgm3 / 1000.0
    else:
        raise ValueError("Unsupported density unit. Use kg/L or kg/m3.")

    # 1) VCF
    vcf = vcf_astm54b_approx(d15_kgL, temperature_c)

    # 2) Correct volume to 15°C
    v15_m3 = v_obs_m3 * vcf

    # 3) Mass
    mass_kg = v15_m3 * d15_kgm3

    return {
        "observed_volume_m3": v_obs_m3,
        "density15_kgm3": d15_kgm3,
        "density15_kgL": d15_kgL,
        "temperature_c": float(temperature_c),
        "vcf": vcf,
        "volume_at_15c_m3": v15_m3,
        "mass_kg": mass_kg,
        "mass_tonnes": mass_kg / 1000.0,
    }


def main():
    p = argparse.ArgumentParser(prog="sounding-mass", description="Mass from observed volume using ASTM 54B-style VCF.")
    p.add_argument("--volume", type=float, required=True, help="Observed volume value")
    p.add_argument("--volume-unit", default="m3", help="m3 or L (default: m3)")
    p.add_argument("--density15", type=float, required=True, help="Density at 15°C")
    p.add_argument("--density15-unit", default="kg/L", help="kg/L or kg/m3 (default: kg/L)")
    p.add_argument("--temperature", type=float, required=True, help="Observed temperature, °C")
    args = p.parse_args()

    res = compute_mass(
        volume_value=args.volume,
        volume_unit=args.volume_unit,
        density15_value=args.density15,
        density15_unit=args.density15_unit,
        temperature_c=args.temperature
    )

    print("\nMass calculation (approx ASTM 54B):")
    print(f"  Observed Volume : {res['observed_volume_m3']:.3f} m³")
    print(f"  Density @15°C   : {res['density15_kgm3']:.1f} kg/m³ ({res['density15_kgL']:.4f} kg/L)")
    print(f"  Temperature     : {res['temperature_c']:.2f} °C")
    print(f"  VCF (approx)    : {res['vcf']:.6f}")
    print(f"  Volume @15°C    : {res['volume_at_15c_m3']:.3f} m³")
    print(f"  MASS            : {res['mass_kg']:.1f} kg  ({res['mass_tonnes']:.3f} t)")


if __name__ == "__main__":
    main()