"""optmm command line.

  python -m optmm tape                       convert the recorded tape (data/raw/tape/*.jsonl.gz) to parquet tables
  python -m optmm surfaces [--step 60]       fit a surface every step seconds over the tape -> data/derived/surfaces.parquet
  python -m optmm history [--months 6]       one SSVI slice per expiry per day from the trade prints -> data/derived/slices.parquet
  python -m optmm chains [--symbols SPY,AAPL] daily SVI surfaces from the end-of-day chains -> data/derived/chain_fits.parquet
  python -m optmm mm [--quoter aware] [--blend 0.5] [--spread-mult 1.0]     one quoter on the tape, printed
  python -m optmm micro                      pick-off, spread and depth, block prints
  python -m optmm rfq                        price the standard structures off the latest surface
  python -m optmm run [--quick]              everything -> results/run.json
  python -m optmm sample                     a small copy of the data under data/sample for CI (OPTMM_DATA=data/sample)
"""
from __future__ import annotations

import argparse
import json

from . import data as D


def cmd_tape(a):
    print(D.build_tape())


def cmd_surfaces(a):
    from .run import split_tape, tape_surface_study
    opt, *_ = split_tape(); print(json.dumps(tape_surface_study(opt, step_s=a.step, verbose=False), indent=1, default=str))


def cmd_history(a):
    from .run import history_surface_study
    months = D.trade_months(); out, _ = history_surface_study(months[-a.months:] if a.months else months, verbose=False); print(json.dumps(out, indent=1, default=str))


def cmd_chains(a):
    from .run import chain_study
    print(json.dumps(chain_study(tuple(a.symbols.split(",")), verbose=False), indent=1, default=str))


def cmd_mm(a):
    from . import mm
    from .run import split_tape
    opt, trades, index, perp, _ = split_tape()
    cfg = mm.MMConfig(quoter=a.quoter, blend=a.blend, spread_mult=a.spread_mult)
    r = mm.run_mm(opt, trades, index, perp, cfg); print(json.dumps(r["summary"], indent=1, default=str))
    if len(r["fills"]):
        print(r["fills"][["ts", "instrument", "side", "price_usd", "amount", "mark_usd", "fair_usd", "spread_capture_usd", "markout_5m_usd"]].to_string(max_rows=40))


def cmd_micro(a):
    from .run import micro_study, split_tape
    opt, trades, index, perp, _ = split_tape(); months = D.trade_months()
    tr = D.add_instrument_columns(D.load_trades(months=months[-3:]), "instrument_name") if months else None
    print(json.dumps(micro_study(opt, trades, index, tr, verbose=False), indent=1, default=str)[:6000])


def cmd_rfq(a):
    from .run import rfq_examples, split_tape
    opt, *_ = split_tape(); ex = rfq_examples(opt)
    for k, v in ex.items():
        if isinstance(v, dict) and "quote" in v:
            print(f"{k:14s} {v['quote']}")


def cmd_run(a):
    from .run import run_all
    run_all(quick=a.quick)


def cmd_sample(a):
    D.make_sample(tape_minutes=a.minutes)


def main(argv=None):
    p = argparse.ArgumentParser(prog="optmm"); sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tape").set_defaults(fn=cmd_tape)
    s = sub.add_parser("surfaces"); s.add_argument("--step", type=int, default=60); s.set_defaults(fn=cmd_surfaces)
    h = sub.add_parser("history"); h.add_argument("--months", type=int, default=0); h.set_defaults(fn=cmd_history)
    c = sub.add_parser("chains"); c.add_argument("--symbols", default="SPY,AAPL,NVDA"); c.set_defaults(fn=cmd_chains)
    m = sub.add_parser("mm"); m.add_argument("--quoter", default="aware"); m.add_argument("--blend", type=float, default=0.5); m.add_argument("--spread-mult", dest="spread_mult", type=float, default=1.0); m.set_defaults(fn=cmd_mm)
    sub.add_parser("micro").set_defaults(fn=cmd_micro)
    sub.add_parser("rfq").set_defaults(fn=cmd_rfq)
    r = sub.add_parser("run"); r.add_argument("--quick", action="store_true"); r.set_defaults(fn=cmd_run)
    sm = sub.add_parser("sample"); sm.add_argument("--minutes", type=int, default=30); sm.set_defaults(fn=cmd_sample)
    a = p.parse_args(argv); a.fn(a)


if __name__ == "__main__":
    main()
