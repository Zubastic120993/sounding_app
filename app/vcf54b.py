
# app/vcf54b.py
import math
from typing import Optional

# Density is in kg/m³ at 15°C (DEN15), temperature is observed °C (DEGC)
# Returns VCF (volume correction factor per ASTM Table 54B)
def vcf_54b(den15: float, degc: float) -> float:
    """
    Implements the piecewise alpha and VCF from ASTM Table 54B (generalized products).

    Regions (DEN15 in kg/m³):
      - den15 <= 770:                K0=346.42278, K1=0.43884
      - 770 < den15 < 778 (transition): ALPHA = A + B / den15^2, with A=-0.0033612, B=2680.32
      - 778 <= den15 < 839:          K0=594.5418,  K1=0
      - den15 >= 839:                K0=186.9696,  K1=0.48618

    VCF = exp( -ALPHA * dT * (1 + 0.8 * ALPHA * dT) ), where dT = (DEGC - 15)
    """
    dT = float(degc) - 15.0
    rho = float(den15)

    # pick alpha
    if rho <= 770.0:
        K0, K1 = 346.42278, 0.43884
        alpha = (K0 + K1 * rho) / (rho ** 2)
    elif 770.0 < rho < 778.0:
        A, B = -0.0033612, 2680.32
        alpha = A + B / (rho ** 2)
    elif 778.0 <= rho < 839.0:
        K0, K1 = 594.5418, 0.0
        alpha = (K0 + K1 * rho) / (rho ** 2)
    else:  # rho >= 839
        K0, K1 = 186.9696, 0.48618
        alpha = (K0 + K1 * rho) / (rho ** 2)

    # VCF
    return math.exp(-alpha * dT * (1.0 + 0.8 * alpha * dT))


def density_from_sg15(sg15: float) -> float:
    """
    Convert specific gravity @15°C to density @15°C (kg/m³).
    Water @15°C ≈ 999.016 kg/m³.
    """
    return float(sg15) * 999.016


def corrected_volume_m3(observed_vol_m3: float, den15: float, degc: float) -> float:
    """
    Apply VCF to observed volume to get reference volume @15°C.
    """
    vcf = vcf_54b(den15, degc)
    return observed_vol_m3 * vcf


def mass_tonnes_from_obs_volume(
    observed_vol_m3: float,
    den15: Optional[float] = None,
    degc: Optional[float] = None,
    sg15: Optional[float] = None,
) -> float:
    """
    Convenience: corrected volume → mass (tonnes).
    Requires den15 or sg15, and degc.
    """
    if den15 is None and sg15 is None:
        raise ValueError("Provide either den15 (kg/m³) or sg15.")
    if den15 is None:
        den15 = density_from_sg15(sg15)  # kg/m³

    v_corr = corrected_volume_m3(observed_vol_m3, den15, degc)
    # mass in tonnes = (m³ * kg/m³) / 1000
    return (v_corr * den15) / 1000.0