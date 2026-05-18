#!/usr/bin/env python3
"""Figures from results/run.json and data/derived/*.parquet.   python scripts/plots.py [results] [results/figures]"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
R = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results")
F = sys.argv[2] if len(sys.argv) > 2 else os.path.join(R, "figures")
from optmm import data as _D  # noqa: E402
DER = _D.DER
os.makedirs(F, exist_ok=True)
plt.rcParams.update({"font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8, "legend.fontsize": 7, "figure.dpi": 130})
QCOL = {"market": "C7", "surface": "C0", "aware": "C2", "surface_pure": "C9", "aware_pure": "C8", "aware_wide": "C1", "aware_tight": "C3", "aware_lean_half": "C4", "aware_lean_double": "C4", "aware_nofees": "C5", "market_nofees": "C6", "aware_slow": "C1", "aware_noprot": "C3", "market_noprot": "C7"}


def load(name):
    p = os.path.join(R, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def pq(name):
    p = os.path.join(DER, name)
    return pd.read_parquet(p) if os.path.exists(p) else None


def hhmm(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M")); ax.set_xlabel("UTC")


def save(fig, name):
    fig.tight_layout(); fig.savefig(os.path.join(F, name)); plt.close(fig); print("wrote", os.path.join(F, name))


run = load("run.json") or {}


def fig_surface():
    from optmm import data as D, marks as M
    tick = D.load_tape("ticker")
    if tick is None or tick.empty:
        return
    opt = tick[~tick["instrument"].str.endswith("PERPETUAL")]; ts = int(opt["ts"].max())
    surf, q = M.fit_snapshot(opt, ts)
    reps = pq("surface_reports.parquet"); sl = pq("surfaces.parquet")
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.6))
    if surf is not None:
        for i, s in enumerate(surf.slices):
            g = q[np.isclose(q["T"].values, s.T)].sort_values("K"); k = np.log(g["K"] / s.F)
            col = f"C{i % 10}"
            ax[0].errorbar(k, g["iv"] * 100, yerr=[(g["iv"] - g["iv_bid"]).clip(lower=0) * 100, (g["iv_ask"] - g["iv"]).clip(lower=0) * 100], fmt=".", color=col, ms=3, elinewidth=0.5, alpha=0.7)
            kk = np.linspace(k.min(), k.max(), 100); ax[0].plot(kk, s.iv(s.F * np.exp(kk)) * 100, color=col, lw=1.2, label=f"{s.T * 365.25:.1f}d  atm {100 * s.atm_vol():.1f}  rr25 {100 * s.skew_25():+.1f}")
        ax[0].set_xlabel("log-moneyness ln(K/F)"); ax[0].set_ylabel("implied vol (%)"); ax[0].set_title(f"BTC surface {pd.to_datetime(ts, unit='ms', utc=True):%d %b %H:%M} UTC: screen mids, eSSVI slices", fontsize=8); ax[0].legend(fontsize=6)
    if reps is not None and len(reps):
        t = pd.to_datetime(reps["ts"], unit="ms", utc=True)
        ax[1].plot(t, reps["rmse_vol_points"], color="C0", lw=0.8, label="RMSE, vol points"); ax[1].plot(t, reps["max_err_vol_points"], color="C3", lw=0.6, alpha=0.6, label="max error")
        ax2 = ax[1].twinx(); ax2.plot(t, 100 * reps["within_spread_share"], color="C2", lw=0.8, label="fair inside bid-ask (%)"); ax2.set_ylim(0, 100); ax2.set_ylabel("%")
        ax[1].set_title(f"fit quality: {len(reps)} surfaces, {int(reps['butterfly_violations'].sum())} butterfly violations", fontsize=8); h1, l1 = ax[1].get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels(); ax[1].legend(h1 + h2, l1 + l2); ax[1].set_ylabel("vol points"); hhmm(ax[1])
    if sl is not None and len(sl):
        for T, g in sl.groupby(sl["T"].round(3)):
            ax[2].plot(pd.to_datetime(g["ts"], unit="ms", utc=True), 100 * g["atm_vol"], lw=0.8, label=f"{T * 365.25:.1f}d"); hhmm(ax[2])
        ax[2].set_ylabel("ATM vol (%)"); ax[2].set_title("ATM vol by expiry through the tape"); ax[2].legend(fontsize=6, ncol=2)
    save(fig, "surface.png")


def fig_history():
    sl = pq("slices.parquet"); days = pq("slice_days.parquet")
    if sl is None or len(sl) == 0:
        return
    sl["day"] = pd.to_datetime(sl["day"]); sl["days"] = sl["T"] * 365.25
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
    front = sl[(sl["days"] >= 5) & (sl["days"] <= 45)].groupby("day").agg(atm=("atm_vol", "median"), rr=("rr25", "median"))
    ax[0].plot(front.index, 100 * front["atm"], color="C0", lw=0.7); ax[0].set_ylabel("ATM vol (%)"); ax[0].set_title("BTC ATM vol, 5-45 day expiries, one surface a day from the prints", fontsize=8)
    ax[1].plot(front.index, 100 * front["rr"], color="C3", lw=0.7); ax[1].axhline(0, color="k", lw=0.5); ax[1].set_ylabel("25-delta RR (put - call, vol pts)"); ax[1].set_title("skew: 25-delta risk reversal")
    if days is not None and len(days):
        days["day"] = pd.to_datetime(days["day"]); yr = days.groupby(days["day"].dt.year)
        ax[2].bar(yr["rmse_vol_points"].mean().index - 0.2, yr["rmse_vol_points"].mean().values, 0.4, color="C0", label="RMSE (vol points)")
        ax2 = ax[2].twinx(); ax2.bar(yr["calendar_ok"].mean().index + 0.2, 100 * yr["calendar_ok"].mean().values, 0.4, color="C2", label="calendar-arbitrage-free days (%)"); ax2.set_ylim(0, 105)
        ax[2].set_title(f"fit quality by year: {len(sl):,} slices, {int(days['butterfly_violations'].sum())} butterfly violations"); h1, l1 = ax[2].get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels(); ax[2].legend(h1 + h2, l1 + l2, loc="lower left")
    save(fig, "history.png")


def fig_chains():
    days = pq("chain_days.parquet")
    if days is None or len(days) == 0:
        return
    days["date"] = pd.to_datetime(days["date"]); days = days[days["n_slices"] > 0]
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
    for i, (sym, g) in enumerate(days.groupby("symbol")):
        ax[0].plot(g["date"], g["rmse_vol_points"].rolling(10, min_periods=1).median(), lw=0.8, label=sym)
        ax[1].plot(g["date"], 100 * g["within_spread_share"].rolling(10, min_periods=1).mean(), lw=0.8, label=sym)
        ax[2].plot(g["date"], 100 * g["atm_vol_front"], lw=0.7, label=sym)
    ax[0].set_ylabel("vol points"); ax[0].set_title("listed chains: SVI fit RMSE, 10-day median"); ax[0].legend()
    ax[1].set_ylabel("%"); ax[1].set_title("share of quotes with the model price inside the bid-ask"); ax[1].legend()
    ax[2].set_ylabel("ATM vol (%)"); ax[2].set_title("front-expiry ATM vol from the fitted surfaces"); ax[2].legend()
    save(fig, "chains.png")
    # an example smile: the last SPY day
    from optmm import chains as C, data as D
    ch = D.load_chains("SPY")
    if ch.empty:
        return
    d0 = ch["date"].max(); g = ch[ch["date"] == d0]; r = float(D.load_rates().reindex([d0], method="ffill").iloc[0])
    surf, recs, q = C.fit_day(g, r)
    if surf is None:
        return
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.4))
    for i, s in enumerate(surf.slices):
        gg = q[np.isclose(q["T"].values, s.T) & q["otm"]].sort_values("K"); col = f"C{i}"
        ivb = np.sqrt(np.maximum(gg["iv"], 0)); ax[0].plot(gg["K"], 100 * gg["iv"], ".", color=col, ms=4)
        kk = np.linspace(gg["K"].min(), gg["K"].max(), 100); ax[0].plot(kk, 100 * s.iv(kk), color=col, lw=1, label=f"{s.T * 365.25:.0f}d rmse {100 * s.rmse_vol:.2f}vp")
        ax[1].plot(gg["K"], gg["err_half_spreads"], ".", color=col, ms=4)
    ax[0].set_title(f"SPY {d0.date()}: OTM mids and SVI slices"); ax[0].set_xlabel("strike"); ax[0].set_ylabel("implied vol (%)"); ax[0].legend(fontsize=6)
    ax[1].axhspan(-1, 1, color="C2", alpha=0.15, label="inside the bid-ask"); ax[1].axhline(0, color="k", lw=0.5); ax[1].set_xlabel("strike"); ax[1].set_ylabel("model - mid, in half-spreads"); ax[1].set_title("residuals in units of the half-spread"); ax[1].legend()
    save(fig, "chains_example.png")


def fig_mm():
    pnl = pq("mm_pnl.parquet"); fills = pq("mm_fills.parquet"); m = run.get("mm", {})
    if pnl is None or len(pnl) == 0:
        return
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.6))
    for qn, g in pnl.groupby("quoter"):
        if qn in ("market", "surface", "aware", "aware_nofees", "market_nofees"):
            ax[0].plot(pd.to_datetime(g["ts"], unit="ms", utc=True), g["mtm_usd"], color=QCOL.get(qn, "C9"), lw=1 if qn in ("market", "surface", "aware") else 0.6, ls="-" if "nofees" not in qn else "--", label=qn)
    ax[0].axhline(0, color="k", lw=0.5); ax[0].set_ylabel("USD"); ax[0].set_title("mark-to-market P&L on the recorded tape", fontsize=8); ax[0].legend(fontsize=6); hhmm(ax[0])
    runs = m.get("runs", {})
    names = [n for n in ("market", "surface", "aware") if n in runs]
    comps = [("spread_capture_usd", "spread capture at the fill", "C2"), ("adverse_selection_5m_usd", "adverse selection (hedged 5-min mark-out beyond the spread)", "C3"), ("hedge_pnl_usd", "hedge P&L", "C0"), ("fees_usd", "fees", "C7")]
    x = np.arange(len(names)); w = 0.2
    for j, (key, lab, col) in enumerate(comps):
        vals = [runs[n][key] * (-1 if key == "fees_usd" else 1) for n in names]
        ax[1].bar(x + (j - 1.5) * w, vals, w, color=col, label=lab)
    ax[1].scatter(x, [runs[n]["pnl_usd"] for n in names], color="k", marker="_", s=300, zorder=3, label="total P&L")
    ax[1].axhline(0, color="k", lw=0.5); ax[1].set_xticks(x); ax[1].set_xticklabels([f"{n}\n{runs[n]['fills']} fills" for n in names]); ax[1].set_ylabel("USD"); ax[1].set_title("P&L decomposition"); ax[1].legend(fontsize=6, loc="upper right")
    if fills is not None and len(fills):
        for qn, g in fills.groupby("quoter"):
            if qn in names:
                mo = g["markout_dh_5m_usd"] if "markout_dh_5m_usd" in g else g["markout_5m_usd"]
                ax[2].scatter(g["spread_capture_usd"], mo - g["spread_capture_usd"], s=10, color=QCOL[qn], alpha=0.7, label=qn)
        ax[2].axhline(0, color="k", lw=0.5); ax[2].axvline(0, color="k", lw=0.5); ax[2].set_xlabel("spread capture at the fill (USD)"); ax[2].set_ylabel("hedged 5-minute mark-out beyond the spread (USD)"); ax[2].set_title("each fill: spread at the print vs what the position did in the next 5 minutes", fontsize=8); ax[2].legend(fontsize=6)
    save(fig, "mm.png")
    # inventory paths and the sensitivity table
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.4))
    for qn, g in pnl.groupby("quoter"):
        if qn in names:
            ax[0].plot(pd.to_datetime(g["ts"], unit="ms", utc=True), g["port_vega"], color=QCOL[qn], lw=0.8, label=qn)
    ax[0].axhline(0, color="k", lw=0.5); ax[0].set_ylabel("portfolio vega (USD per vol point)"); ax[0].set_title("inventory: the skew leans against the vega"); ax[0].legend(fontsize=6); hhmm(ax[0])
    sens = [n for n in runs if n.startswith("aware") or n.endswith("nofees") or n.endswith("noprot")]
    vals = np.array([runs[n]["pnl_usd"] for n in sens]); lim = 1.3 * np.percentile(np.abs(vals), 90) if len(vals) else 1.0
    ax[1].bar(range(len(sens)), np.clip(vals, -lim, lim), color=[QCOL.get(n, "C9") for n in sens])
    for i_, (n, x) in enumerate(zip(sens, vals)):
        if abs(x) > lim:
            ax[1].annotate(f"{x:,.0f}", (i_, np.sign(x) * lim), ha="center", va="top" if x < 0 else "bottom", fontsize=6)      # off the scale: the number is written on the bar
    ax[1].set_xticks(range(len(sens))); ax[1].set_xticklabels(sens, rotation=30, ha="right", fontsize=6); ax[1].axhline(0, color="k", lw=0.5); ax[1].set_ylabel("USD (clipped)"); ax[1].set_title("sensitivities: spread multiple, lean, blend, fees, protections")
    save(fig, "mm_inventory.png")


def fig_micro():
    pk = pq("pickoff.parquet"); sd = pq("spread_depth.parquet"); bl = pq("block_prints.parquet")
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
    if pk is not None and len(pk):
        lat = pk["latency_s"].dropna(); ax[0].hist(lat.clip(upper=60), bins=40, color="C0", alpha=0.8)
        ax[0].set_xlabel("seconds until the best quote changed after the index moved"); ax[0].set_ylabel("option x event pairs"); ax[0].set_title(f"re-mark latency after index moves: median {lat.median():.1f}s, {100 * pk['stale_5s'].mean():.0f}% stale after 5s", fontsize=8)
        bb = pk.groupby("bucket", observed=True)["pickoff_loss_usd"].mean(); bbs = pk.groupby("bucket", observed=True)["stale_5s"].mean()
        ax[1].bar(range(len(bb)), bb.values, color="C3"); ax[1].set_xticks(range(len(bb))); ax[1].set_xticklabels(bb.index); ax[1].set_ylabel("USD per contract"); ax[1].set_title("stale-quote loss: delta x move beyond the half-spread", fontsize=8)
        ax2 = ax[1].twinx(); ax2.plot(range(len(bbs)), 100 * bbs.values, "o-", color="C0"); ax2.set_ylabel("% stale after 5 s")
    if bl is not None and len(bl):
        # the absolute distance from the mark, block against screen prints, in buckets with at least 30 block prints
        n_blk = bl[bl["is_block"]].groupby("size_bucket", observed=True).size(); keep = n_blk[n_blk >= 30].index
        b2 = bl[bl["size_bucket"].isin(keep)]
        g = b2.assign(a=b2["diff_volpts"].abs()).groupby(["size_bucket", "is_block"], observed=True)["a"].median().unstack()
        xb = np.arange(len(g)); ax[2].bar(xb - 0.2, g.get(False, pd.Series(index=g.index)).values, 0.4, color="C0", label="screen prints"); ax[2].bar(xb + 0.2, g.get(True, pd.Series(index=g.index)).values, 0.4, color="C1", label="block trades")
        ax[2].set_xticks(xb); ax[2].set_xticklabels([f"{b} contracts\n{int(n_blk[b]):,} blocks" for b in g.index], fontsize=6); ax[2].axhline(0, color="k", lw=0.5); ax[2].set_ylabel("|print IV - mark IV|, median (vol points)"); ax[2].set_title("distance of prints from the mark, by size"); ax[2].legend(fontsize=6)
    save(fig, "micro.png")
    if sd is not None and len(sd):
        fig, ax = plt.subplots(1, 2, figsize=(9, 3.2))
        piv = sd.pivot(index="tenor", columns="moneyness", values="spread_volpts"); piv2 = sd.pivot(index="tenor", columns="moneyness", values="bid_size")
        for a, p, title in ((ax[0], piv, "screen spread, vol points (time-weighted median)"), (ax[1], piv2, "displayed bid size, contracts")):
            im = a.imshow(p.values.astype(float), aspect="auto", cmap="viridis"); a.set_xticks(range(len(p.columns))); a.set_xticklabels(p.columns, fontsize=7); a.set_yticks(range(len(p.index))); a.set_yticklabels(p.index, fontsize=7); a.set_title(title)
            for i in range(p.shape[0]):
                for j in range(p.shape[1]):
                    v = p.values[i, j]
                    if np.isfinite(v):
                        a.text(j, i, f"{v:.1f}", ha="center", va="center", color="w", fontsize=7)
        save(fig, "spread_depth.png")


if __name__ == "__main__":
    for f in (fig_surface, fig_history, fig_chains, fig_mm, fig_micro):
        try:
            f()
        except Exception as e:  # noqa: BLE001
            print("skipped", f.__name__, repr(e))
