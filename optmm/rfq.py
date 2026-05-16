"""The voice / RFQ pricer: a structure (straddle, strangle, risk reversal, butterfly, calendar, ratio, or any list of
legs) priced off the fitted surface with the package Greeks, a spread rule calibrated to the screen's half-spreads at
the legs, and the delta hedge to execute against it.

Spread rule: the package half-spread is the sum of the legs' half-spreads in vol, converted to price through each
leg's vega, times a package factor (0.6 for two-leg risk-offsetting structures, 0.8 for three legs, 1.0 for a single
leg): legs whose vegas offset are cheaper to quote than the sum of their parts; the factor is a desk parameter and is
stated.  The output is what a trader would read back on the line: mid, bid, ask, Greeks, hedge."""
from __future__ import annotations

import math

import numpy as np

from . import bs, surface as S

STRUCTURES = {
    "straddle": lambda K, T: [(K, T, +1, +1), (K, T, -1, +1)],
    "strangle": lambda Kp, Kc, T: [(Kp, T, -1, +1), (Kc, T, +1, +1)],
    "risk_reversal": lambda Kp, Kc, T: [(Kc, T, +1, +1), (Kp, T, -1, -1)],            # long call, short put
    "butterfly": lambda K1, K2, K3, T: [(K1, T, +1, +1), (K2, T, +1, -2), (K3, T, +1, +1)],
    "calendar": lambda K, T1, T2: [(K, T2, +1, +1), (K, T1, +1, -1)],
    "ratio_call": lambda K1, K2, T: [(K1, T, +1, +1), (K2, T, +1, -2)],
}
PACKAGE_FACTOR = {1: 1.0, 2: 0.6, 3: 0.8}


def price_structure(surf: S.Surface, legs: list, screen_half_vol: float | callable = 0.01, size: float = 1.0, index: float | None = None) -> dict:
    """legs: [(K, T, cp, qty)]; screen_half_vol: the screen's half-spread in vol at a leg (a number or a function of (K, T))."""
    out_legs = []; mid = 0.0; greeks = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}; spread_usd = 0.0
    for K, T, cp, qty in legs:
        F = float(np.interp(T, surf.expiries, [s.F for s in surf.slices])) if len(surf.slices) > 1 else surf.slices[0].F
        iv = float(surf.iv(np.array([K]), T)[0])
        px = float(bs.black(F, K, T, iv, cp)); g = bs.greeks(F, K, T, iv, cp)
        h = screen_half_vol(K, T) if callable(screen_half_vol) else screen_half_vol
        leg_spread = abs(qty) * size * float(g["vega"]) * h                    # vega x half-spread in vol -> USD
        out_legs.append({"K": K, "T": T, "cp": cp, "qty": qty, "F": F, "iv": iv, "price": px, "vega": float(g["vega"]), "delta": float(g["delta"]), "half_spread_vol": h, "half_spread_usd": leg_spread / max(abs(qty) * size, 1e-9)})
        mid += qty * size * px; spread_usd += leg_spread
        for k in greeks:
            greeks[k] += qty * size * float(g[k])
    factor = PACKAGE_FACTOR.get(len(legs), 0.8)
    half = spread_usd * factor
    hedge_btc = -greeks["delta"] if index else None
    return {"legs": out_legs, "size": size, "mid_usd": mid, "bid_usd": mid - half, "ask_usd": mid + half, "half_spread_usd": half, "package_factor": factor, "greeks": greeks, "hedge_delta_contracts": hedge_btc, "mid_btc": mid / index if index else None}


def structure(name: str, *args) -> list:
    return STRUCTURES[name](*args)


def describe(q: dict) -> str:
    g = q["greeks"]
    legs = ", ".join(f"{'+' if l['qty'] > 0 else ''}{l['qty']:g} {('C' if l['cp'] > 0 else 'P')}{l['K']:g} {l['T'] * 365.25:.0f}d @ {100 * l['iv']:.1f}v" for l in q["legs"])
    return f"{legs} | mid {q['mid_usd']:,.0f} bid {q['bid_usd']:,.0f} ask {q['ask_usd']:,.0f} (half-spread {q['half_spread_usd']:,.0f}, factor {q['package_factor']}) | delta {g['delta']:.3f} gamma {g['gamma']:.6f} vega {g['vega']:,.0f}/vol theta {g['theta'] / 365.25:,.0f}/day"
