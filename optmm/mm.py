"""The quoting engine, run against the recorded tape.

Three quoters post two-sided quotes of the same size and the same half-spread on every option in the quoting universe
and differ only in the centre and the skew of the quote:

    market   centre = Deribit's mark IV                        (the screen's own mark, no surface, no inventory)
    surface  centre = our eSSVI fair IV blended with the screen mid (re-fitted every refit_s seconds from the quotes;
             blend = 0 is the pure surface, 1 the screen; no quote where the two disagree by more than the band)
    aware    centre = our fair IV shifted by the inventory:    quotes in vol are fair -/+ h_i - skew(V), skew = s_max clip(V / V_lim),
             h_i widened by (|V| / V_lim)^2, V the portfolio vega (Baldacci-Bergault-Gueant: the option market maker's
             problem reduces to the portfolio vega and the optimal quotes shift linearly in it near zero)

The half-spread h_i is a multiple of the screen's own half-spread in vol at that instrument (floored), so all three post
quotes of the same width and the comparison isolates the fair value and the skew.  Quotes are passive: a bid is capped
one tick below the market ask.  Fills come from the recorded prints: a print at our price or through it fills us once
the size that was ahead of us at that level (the displayed size when we posted) has traded.  Delta is hedged in the
perpetual at its recorded best bid/ask when the net delta exceeds a threshold; fees are charged at Deribit's schedule.
P&L is marked at Deribit's mark price and decomposed into spread capture at the fill, mark-outs at 1, 5 and 30 minutes
(raw and delta-hedged), hedge P&L and fees.

All three run under the same protections, which are what a desk's quoting engine has and are not part of the comparison:
quotes are re-priced off the live index (the recorded forward moved by the index since the ticker) whenever the index
has moved requote_bp since the last post or requote_s seconds have passed; quotes are pulled for pull_for_s seconds
when the index moved pull_bp within pull_window_s (a fast market); and a hard vega limit stops the side that would add
to the position.  Each protection can be switched off to measure what it is worth."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import bs, iv as iv_solver, marks as M

YEAR_MS = M.YEAR_MS
OPTION_FEE_BTC = 0.0003          # per contract (maker and taker alike in the instrument definition), capped at 12.5 % of the premium
OPTION_FEE_CAP = 0.125
PERP_TAKER_FEE = 0.00035        # the perpetual's taker commission in its instrument definition (public/get_instrument)


@dataclass
class MMConfig:
    quoter: str = "aware"            # market | surface | aware
    size: float = 1.0                # contracts per side per instrument
    spread_mult: float = 1.0         # our half-spread as a multiple of the screen's half-spread in vol
    min_half_vol: float = 0.004      # floor on the half-spread, in vol (0.4 vol points)
    max_pos: float = 5.0             # contracts per instrument
    vega_limit: float = 150.0        # USD per vol point of portfolio vega at which the skew saturates
    skew_max: float = 0.015          # vol shift at the vega limit (1.5 vol points)
    widen_max: float = 0.01          # extra half-spread at the vega limit
    blend: float = 0.5               # centre = model + blend * (screen mid vol - model): 0 = pure surface, 1 = the screen's own mid
    band: float = 0.03               # no quote where the surface and the screen mid disagree by more than this (3 vol points)
    min_price_btc: float = 0.0005    # no quote on options cheaper than five ticks
    vega_hard: float = 450.0         # USD per vol point beyond which the side that adds to the position is not quoted (0 = off)
    refit_s: int = 60
    requote_s: int = 5
    requote_bp: float = 2.0          # re-price the quotes when the index moved this much since the last post (0 = timer only)
    pull_bp: float = 15.0            # pull the quotes when the index moved this much within pull_window_s (0 = never)
    pull_window_s: int = 10
    pull_for_s: int = 10
    hedge_s: int = 30
    hedge_threshold_btc: float = 0.25    # net delta in BTC beyond which the perp hedge trades
    min_T_hours: float = 4.0
    max_T_days: float = 100.0
    max_abs_k: float = 0.25          # |ln(K/F)| beyond which no quote is posted
    fees: bool = True


def thin_ticks(tick: pd.DataFrame, mark_every_s: int = 10) -> pd.DataFrame:
    """the event loop needs every quote change and a mark refresh now and then, not the 100 ms mark updates that make
    up nine tenths of the ticker stream: keep rows whose best bid/ask or sizes changed plus the last row per instrument
    in every mark_every_s bucket"""
    t = tick.sort_values(["instrument", "ts"])
    prev = t.groupby("instrument")[["bid", "ask", "bid_size", "ask_size"]].shift()
    changed = (prev != t[["bid", "ask", "bid_size", "ask_size"]]).any(axis=1)
    bucket = t["ts"] // (mark_every_s * 1000)
    last_in_bucket = ~t.duplicated(subset=None, keep="last") & ~pd.DataFrame({"i": t["instrument"], "b": bucket}).duplicated(keep="last")
    return t[changed | last_in_bucket].sort_values("ts", kind="stable")


def tick_size(price_btc: float) -> float:
    return 0.0001 if price_btc < 0.005 else 0.0005


def round_down(p, tick):
    return math.floor(p / tick + 1e-9) * tick


def round_up(p, tick):
    return math.ceil(p / tick - 1e-9) * tick


@dataclass
class Quote:
    bid: float = 0.0; ask: float = 0.0; bid_size: float = 0.0; ask_size: float = 0.0
    bid_ahead: float = 0.0; ask_ahead: float = 0.0          # displayed size ahead of us at our level when posted
    bid_fair: float = np.nan; ask_fair: float = np.nan      # our fair (USD) at posting
    posted: int = 0


@dataclass
class State:
    pos: dict = field(default_factory=dict)          # instrument -> contracts (+ long)
    cash_btc: float = 0.0
    cash_usd: float = 0.0
    perp_pos: float = 0.0                             # BTC
    perp_avg: float = 0.0
    fees_usd: float = 0.0
    hedge_pnl_usd: float = 0.0
    n_hedges: int = 0
    hedge_notional: float = 0.0


def run_mm(tick: pd.DataFrame, trades: pd.DataFrame, index: pd.DataFrame, perp: pd.DataFrame, cfg: MMConfig, start_ms: int | None = None, end_ms: int | None = None, seed: int = 0, verbose: bool = False) -> dict:
    """Event-driven replay.  tick: option tickers; trades: option prints; index: index prints; perp: perpetual ticker.
    Returns fills, the quote log, the P&L path and a summary."""
    t0 = int(start_ms or max(tick["ts"].min(), perp["ts"].min())); t1 = int(end_ms or tick["ts"].max())
    tick_all = tick[(tick["ts"] >= t0 - 3600e3) & (tick["ts"] <= t1) & tick["cp"].notna()].sort_values("ts")
    tick = thin_ticks(tick_all)
    trades = trades[(trades["ts"] >= t0) & (trades["ts"] <= t1)].sort_values("ts")
    perp = perp[(perp["ts"] >= t0 - 3600e3) & (perp["ts"] <= t1)].sort_values("ts")
    # ---- event stream: (ts, kind, row index) ------------------------------------------------------------------------------------
    ev = pd.concat([pd.DataFrame({"ts": tick["ts"].values, "kind": 0, "i": np.arange(len(tick))}), pd.DataFrame({"ts": trades["ts"].values, "kind": 1, "i": np.arange(len(trades))}), pd.DataFrame({"ts": perp["ts"].values, "kind": 2, "i": np.arange(len(perp))})]).sort_values(["ts", "kind"], kind="stable")
    tk = {c: tick[c].values for c in ("ts", "instrument", "bid", "ask", "bid_size", "ask_size", "mark", "mark_iv", "delta", "gamma", "vega", "theta", "underlying", "index", "expiry_ms", "strike", "cp")}
    tr = {c: trades[c].values for c in ("ts", "instrument", "price", "amount", "direction")}
    pp = {c: perp[c].values for c in ("ts", "bid", "ask", "mark", "index")}
    # ---- market state ---------------------------------------------------------------------------------------------------------------
    mkt: dict = {}            # instrument -> dict(bid, ask, bs, as, mark, mark_iv, delta, gamma, vega, theta, F, index, expiry, K, cp)
    perp_bid = perp_ask = perp_mark = np.nan
    quotes: dict[str, Quote] = {}
    fair_iv: dict[str, float] = {}      # from the last surface fit
    st = State(); fills = []; qlog = []; path = []
    last_fit = last_quote = last_hedge = 0; surf = None; n_fits = 0; fit_fail = 0

    def port_vega_usd():
        v = 0.0
        for ins, q in st.pos.items():
            m = mkt.get(ins)
            if m and q:
                v += q * m["vega"]          # Deribit vega: USD per 1 vol point per contract
        return v

    def net_delta_btc():
        d = 0.0
        for ins, q in st.pos.items():
            m = mkt.get(ins)
            if m and q:
                d += q * m["delta"]
        return d + st.perp_pos

    def mtm_usd(ts):
        v = st.cash_usd + st.cash_btc * (index_px or 0.0)
        for ins, q in st.pos.items():
            m = mkt.get(ins)
            if m and q:
                v += q * m["mark"] * (index_px or m["index"])
        v += st.perp_pos * ((perp_mark if perp_mark == perp_mark else (index_px or 0.0)) - st.perp_avg) if st.perp_pos else 0.0
        return v

    def price_from_iv(m, iv, T):
        usd = bs.black(m["F"], m["K"], T, max(iv, 1e-4), m["cp"])
        return float(usd / (index_px or m["index"]))

    def post_quotes(ts):
        V = port_vega_usd(); u = max(-1.0, min(1.0, V / cfg.vega_limit))
        skew = cfg.skew_max * u if cfg.quoter == "aware" else 0.0
        widen = cfg.widen_max * u * u if cfg.quoter == "aware" else 0.0
        idx = index_px
        # one vectorised pass over the universe: eligibility, the screen's bid/ask vols, the centre, the quote prices
        names = list(mkt.keys())
        if not names:
            return
        arr = lambda key: np.array([mkt[n][key] for n in names], dtype=float)
        expiry = arr("expiry"); K = arr("K"); cp = arr("cp"); bid = arr("bid"); ask = arr("ask"); F = arr("F"); mark = arr("mark"); mark_iv = arr("mark_iv"); ix = arr("index")
        T = (expiry - ts) / YEAR_MS; ix = np.where(ix > 0, ix, idx or np.nan)
        # the ticker's forward is up to a mark refresh old: move it with the index since then (the screen's bid/ask
        # vols are solved on the ticker's own forward and index, which is what its quotes were made against)
        with np.errstate(divide="ignore", invalid="ignore"):
            F_live = np.where(ix > 0, F * (idx / ix), F) if idx else F
        with np.errstate(divide="ignore", invalid="ignore"):
            elig = (T * 365.25 * 24 >= cfg.min_T_hours) & (T * 365.25 <= cfg.max_T_days) & (bid > 0) & (ask > 0) & (F > 0) & (np.abs(np.log(K / F)) <= cfg.max_abs_k) & (mark >= cfg.min_price_btc)
        iv_b = np.full(len(names), np.nan); iv_a = np.full(len(names), np.nan)
        if elig.any():
            iv_b[elig] = iv_solver.implied_vol(bid[elig] * ix[elig], F[elig], K[elig], T[elig], cp[elig]); iv_a[elig] = iv_solver.implied_vol(ask[elig] * ix[elig], F[elig], K[elig], T[elig], cp[elig])
        elig &= np.isfinite(iv_b) & np.isfinite(iv_a)
        if cfg.quoter == "market":
            centre = mark_iv / 100.0
        else:
            model = np.array([fair_iv.get(n, np.nan) for n in names], dtype=float); screen_mid = 0.5 * (iv_b + iv_a)
            elig &= np.isfinite(model) & (np.abs(model - screen_mid) <= cfg.band)
            centre = model + cfg.blend * (screen_mid - model)
        h = np.maximum(cfg.min_half_vol, cfg.spread_mult * 0.5 * (iv_a - iv_b)) + widen
        bid_iv = centre - h - skew; ask_iv = centre + h - skew
        ix_live = idx or ix
        with np.errstate(invalid="ignore"):
            qb = bs.black(F_live, K, T, np.maximum(bid_iv, 1e-4), cp) / ix_live; qa = bs.black(F_live, K, T, np.maximum(ask_iv, 1e-4), cp) / ix_live; fair_usd = bs.black(F_live, K, T, np.maximum(centre, 1e-4), cp)
        stop_bids = cfg.vega_hard > 0 and V > cfg.vega_hard; stop_asks = cfg.vega_hard > 0 and V < -cfg.vega_hard
        for i in np.where(elig)[0]:
            ins = names[i]; m = mkt[ins]
            b = float(qb[i]); a = float(qa[i])
            if not (np.isfinite(b) and np.isfinite(a)):
                quotes.pop(ins, None); continue
            tb = tick_size(b); ta = tick_size(a)
            b = round_down(b, tb); a = round_up(a, ta)
            b = min(b, round_down(m["ask"] - tb, tb)); a = max(a, round_up(m["bid"] + ta, ta))      # passive: never cross the screen
            if a <= b:
                a = b + ta
            q = quotes.get(ins) or Quote()
            pos = st.pos.get(ins, 0.0)
            bsz = cfg.size if pos < cfg.max_pos and not stop_bids else 0.0; asz = cfg.size if pos > -cfg.max_pos and not stop_asks else 0.0
            # queue position: joining the displayed level puts the displayed size ahead of us; a new price is a new order
            if bsz and (b != q.bid or q.bid_size == 0):
                q.bid_ahead = m["bs"] if abs(b - m["bid"]) < 1e-12 else 0.0
            if asz and (a != q.ask or q.ask_size == 0):
                q.ask_ahead = m["as"] if abs(a - m["ask"]) < 1e-12 else 0.0
            q.bid, q.ask, q.bid_size, q.ask_size, q.posted = b, a, bsz, asz, ts
            q.bid_fair = float(fair_usd[i]); q.ask_fair = q.bid_fair
            quotes[ins] = q
        for i in np.where(~elig)[0]:
            quotes.pop(names[i], None)
        qlog.append({"ts": ts, "n_quotes": len(quotes), "port_vega": V, "skew": skew, "widen": widen, "net_delta_btc": net_delta_btc()})

    def fill(ts, ins, side, price_btc, amount, m):
        """side = +1 we buy (our bid was hit), -1 we sell (our ask was lifted)"""
        idx = index_px or m["index"]
        fee = min(OPTION_FEE_BTC * amount, OPTION_FEE_CAP * price_btc * amount) if cfg.fees else 0.0
        st.pos[ins] = st.pos.get(ins, 0.0) + side * amount
        st.cash_btc -= side * amount * price_btc + fee
        st.fees_usd += fee * idx
        fair = quotes[ins].bid_fair
        fills.append({"ts": ts, "instrument": ins, "side": side, "price_btc": price_btc, "price_usd": price_btc * idx, "amount": amount, "mark_usd": m["mark"] * idx, "mark_iv": m["mark_iv"], "fair_usd": fair, "index": idx, "F": m["F"], "K": m["K"], "cp": m["cp"], "T": (m["expiry"] - ts) / YEAR_MS,
                      "delta": m["delta"], "gamma": m["gamma"], "vega": m["vega"], "theta": m["theta"], "spread_capture_usd": -side * (price_btc * idx - m["mark"] * idx) * amount, "spread_vs_fair_usd": -side * (price_btc * idx - fair) * amount, "fee_usd": fee * idx, "port_vega_before": port_vega_usd() - side * amount * m["vega"]})

    def hedge(ts):
        d = net_delta_btc()
        if abs(d) < cfg.hedge_threshold_btc or not (perp_bid == perp_bid and perp_ask == perp_ask):
            return
        qty = -d                                   # BTC to trade in the perp
        px = perp_ask if qty > 0 else perp_bid
        fee = abs(qty) * px * PERP_TAKER_FEE if cfg.fees else 0.0
        # realised P&L on the part that closes
        if st.perp_pos * qty < 0:
            closed = min(abs(qty), abs(st.perp_pos)) * (1 if st.perp_pos > 0 else -1)
            st.hedge_pnl_usd += closed * (px - st.perp_avg)
        new_pos = st.perp_pos + qty
        if st.perp_pos * qty >= 0 and new_pos != 0:
            st.perp_avg = (st.perp_avg * abs(st.perp_pos) + px * abs(qty)) / abs(new_pos)
        elif abs(qty) > abs(st.perp_pos):
            st.perp_avg = px
        st.perp_pos = new_pos; st.fees_usd += fee; st.cash_usd -= fee; st.n_hedges += 1; st.hedge_notional += abs(qty) * px

    index_px = None; index_at_post = None; pulled_until = 0; n_pulls = 0
    ix_hist: deque = deque()            # (ts, index) over the pull window
    for ts, kind, i in zip(ev["ts"].values, ev["kind"].values, ev["i"].values):
        ts = int(ts)
        if kind == 0:
            ins = tk["instrument"][i]
            m = mkt.get(ins)
            if m is None:
                m = mkt[ins] = {"expiry": int(tk["expiry_ms"][i]), "K": float(tk["strike"][i]), "cp": float(tk["cp"][i])}
            m.update(bid=float(tk["bid"][i] or 0.0), ask=float(tk["ask"][i] or 0.0), bs=float(tk["bid_size"][i] or 0.0), **{"as": float(tk["ask_size"][i] or 0.0)}, mark=float(tk["mark"][i] or 0.0), mark_iv=float(tk["mark_iv"][i] or 0.0), delta=float(tk["delta"][i] or 0.0), gamma=float(tk["gamma"][i] or 0.0), vega=float(tk["vega"][i] or 0.0), theta=float(tk["theta"][i] or 0.0), F=float(tk["underlying"][i] or 0.0), index=float(tk["index"][i] or 0.0))
            index_px = m["index"] or index_px
            # the screen crossing our resting quote is a marketable order that would have executed against us
            q = quotes.get(ins)
            if q:
                if q.bid_size > 0 and m["ask"] > 0 and q.bid >= m["ask"] - 1e-12:
                    fill(ts, ins, +1, q.bid, q.bid_size, m); q.bid_size = 0.0
                if q.ask_size > 0 and m["bid"] > 0 and q.ask <= m["bid"] + 1e-12:
                    fill(ts, ins, -1, q.ask, q.ask_size, m); q.ask_size = 0.0
        elif kind == 2:
            # the perpetual's ticker is the live index: every 100 ms, not thinned
            perp_bid, perp_ask, perp_mark = float(pp["bid"][i] or np.nan), float(pp["ask"][i] or np.nan), float(pp["mark"][i] or np.nan)
            index_px = float(pp["index"][i] or 0.0) or index_px
        else:
            ins = tr["instrument"][i]; q = quotes.get(ins); m = mkt.get(ins)
            if q and m:
                p = float(tr["price"][i]); a = float(tr["amount"][i]); d = tr["direction"][i]
                if d == "sell" and q.bid_size > 0 and p <= q.bid + 1e-12:          # a seller hit the bid side at our level or through it
                    if p < q.bid - 1e-12:
                        take = min(a, q.bid_size)
                    else:
                        ahead = q.bid_ahead; q.bid_ahead = max(0.0, ahead - a); take = min(max(0.0, a - ahead), q.bid_size)
                    if take > 0:
                        fill(ts, ins, +1, q.bid, take, m); q.bid_size -= take
                elif d == "buy" and q.ask_size > 0 and p >= q.ask - 1e-12:
                    if p > q.ask + 1e-12:
                        take = min(a, q.ask_size)
                    else:
                        ahead = q.ask_ahead; q.ask_ahead = max(0.0, ahead - a); take = min(max(0.0, a - ahead), q.ask_size)
                    if take > 0:
                        fill(ts, ins, -1, q.ask, take, m); q.ask_size -= take
        if ts < t0:
            continue
        # ---- protections: the fast-market pull -------------------------------------------------------------------------------------
        if index_px and cfg.pull_bp > 0:
            if not ix_hist or ix_hist[-1][0] != ts:
                ix_hist.append((ts, index_px))
            while ix_hist and ix_hist[0][0] < ts - cfg.pull_window_s * 1000:
                ix_hist.popleft()
            lo = min(v for _, v in ix_hist); hi = max(v for _, v in ix_hist)
            if (hi / lo - 1.0) * 1e4 >= cfg.pull_bp and ts >= pulled_until:
                quotes.clear(); pulled_until = ts + cfg.pull_for_s * 1000; n_pulls += 1
        # ---- periodic tasks ----------------------------------------------------------------------------------------------------------
        if ts - last_fit >= cfg.refit_s * 1000 and cfg.quoter != "market":
            sub = tick_all[(tick_all["ts"] <= ts) & (tick_all["ts"] >= ts - 600e3)]
            surf, qq = M.fit_snapshot(sub, ts) if len(sub) else (None, None)
            if surf is not None:
                fair_iv = dict(zip(qq["instrument"], qq["iv_model"])); n_fits += 1
            else:
                fit_fail += 1
            last_fit = ts
        moved = index_px and index_at_post and cfg.requote_bp > 0 and abs(index_px / index_at_post - 1.0) * 1e4 >= cfg.requote_bp
        if ts >= pulled_until and (ts - last_quote >= cfg.requote_s * 1000 or moved):
            post_quotes(ts); last_quote = ts; index_at_post = index_px
        if ts - last_hedge >= cfg.hedge_s * 1000:
            hedge(ts); last_hedge = ts
            path.append({"ts": ts, "mtm_usd": mtm_usd(ts), "cash_usd": st.cash_usd + st.cash_btc * (index_px or 0.0), "port_vega": port_vega_usd(), "net_delta_btc": net_delta_btc(), "n_pos": sum(1 for v in st.pos.values() if v), "perp_pos": st.perp_pos, "index": index_px, "fees_usd": st.fees_usd, "hedge_pnl_usd": st.hedge_pnl_usd})
    # ---- mark-outs -----------------------------------------------------------------------------------------------------------------------
    fdf = pd.DataFrame(fills)
    if len(fdf):
        marks_all = tick_all[["ts", "instrument", "mark", "index", "delta"]]
        by_ins = {ins: g for ins, g in marks_all.groupby("instrument", sort=False)}
        # the mark at the fill from the full ticker stream (the event loop runs on the thinned one, whose mark can be a
        # refresh old): spread capture is the fill against that mark
        m0 = []; d0 = []
        for r in fdf.itertuples():
            g = by_ins.get(r.instrument); k = int(np.searchsorted(g["ts"].values, r.ts, side="right")) - 1 if g is not None else -1
            if k >= 0:
                m0.append(float(g["mark"].values[k] * r.index)); d0.append(float(g["delta"].values[k]))
            else:
                m0.append(r.mark_usd); d0.append(r.delta)
        fdf["mark_usd"] = m0; fdf["delta"] = d0
        fdf["spread_capture_usd"] = -fdf["side"] * (fdf["price_usd"] - fdf["mark_usd"]) * fdf["amount"]
        for h_min in (1, 5, 30):
            vals = []; vals_dh = []
            for r in fdf.itertuples():
                g = by_ins.get(r.instrument); k = int(np.searchsorted(g["ts"].values, r.ts + h_min * 60e3, side="left")) if g is not None else -1
                if g is not None and 0 <= k < len(g):
                    m1 = float(g["mark"].values[k] * g["index"].values[k]); s1 = float(g["index"].values[k])
                    vals.append(r.side * (m1 - r.price_usd) * r.amount)
                    vals_dh.append(r.side * ((m1 - r.price_usd) - r.delta * (s1 - r.index)) * r.amount)      # the delta hedged at the fill's index
                else:
                    vals.append(np.nan); vals_dh.append(np.nan)
            fdf[f"markout_{h_min}m_usd"] = vals; fdf[f"markout_dh_{h_min}m_usd"] = vals_dh
    final = mtm_usd(t1)
    pdf = pd.DataFrame(path)
    summary = {"quoter": cfg.quoter, "start": t0, "end": t1, "hours": (t1 - t0) / 3600e3, "fills": int(len(fdf)), "contracts": float(fdf["amount"].sum()) if len(fdf) else 0.0, "notional_usd": float((fdf["amount"] * fdf["price_usd"]).sum()) if len(fdf) else 0.0,
               "pnl_usd": float(final), "spread_capture_usd": float(fdf["spread_capture_usd"].sum()) if len(fdf) else 0.0, "spread_vs_fair_usd": float(fdf["spread_vs_fair_usd"].sum()) if len(fdf) else 0.0, "fees_usd": float(st.fees_usd), "hedge_pnl_usd": float(st.hedge_pnl_usd), "n_hedges": st.n_hedges, "hedge_notional_usd": float(st.hedge_notional),
               "markout_1m_usd": float(fdf["markout_1m_usd"].sum()) if len(fdf) else 0.0, "markout_5m_usd": float(fdf["markout_5m_usd"].sum()) if len(fdf) else 0.0, "markout_30m_usd": float(fdf["markout_30m_usd"].sum()) if len(fdf) else 0.0,
               "markout_dh_1m_usd": float(fdf["markout_dh_1m_usd"].sum()) if len(fdf) else 0.0, "markout_dh_5m_usd": float(fdf["markout_dh_5m_usd"].sum()) if len(fdf) else 0.0, "markout_dh_30m_usd": float(fdf["markout_dh_30m_usd"].sum()) if len(fdf) else 0.0, "n_pulls": n_pulls,
               "pnl_per_contract_usd": float(final / max(fdf["amount"].sum(), 1e-9)) if len(fdf) else 0.0, "max_abs_vega": float(pdf["port_vega"].abs().max()) if len(pdf) else 0.0, "max_abs_delta_btc": float(pdf["net_delta_btc"].abs().max()) if len(pdf) else 0.0, "n_fits": n_fits, "fit_failures": fit_fail, "avg_quotes": float(np.mean([q["n_quotes"] for q in qlog])) if qlog else 0.0, "min_mtm_usd": float(pdf["mtm_usd"].min()) if len(pdf) else 0.0}
    # adverse selection: what the delta-hedged position is worth five minutes after the fill, beyond the spread captured at it
    summary["adverse_selection_5m_usd"] = summary["markout_dh_5m_usd"] - summary["spread_capture_usd"] if len(fdf) else 0.0
    return {"fills": fdf, "quotes": pd.DataFrame(qlog), "path": pdf, "summary": summary, "positions": dict(st.pos)}
