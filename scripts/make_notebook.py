#!/usr/bin/env python3
"""Writes notebooks/results.ipynb, a walkthrough of results/run.json, the derived tables, the DuckDB store and the
figures; with --execute the code cells are run in-process (no Jupyter needed) and their outputs stored in the notebook
so that it renders with results.   python scripts/make_notebook.py [--execute]"""
import base64
import contextlib
import io
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s})


md("# options-mm: results walkthrough\n\nLoads `results/run.json`, the derived tables under `data/derived/` and the figures produced by `scripts/run_all.sh` (or `python -m optmm run`), and shows the headline numbers. The tape is whatever the recorder had produced when the pipeline last ran; its length is printed below. `OPTMM_DATA=data/sample` points everything at the small committed sample.")
code("import json, os\nimport numpy as np, pandas as pd\nfrom IPython.display import Image, display\nos.chdir(os.path.dirname(os.getcwd()) if os.path.basename(os.getcwd()) == 'notebooks' else os.getcwd())\nrun = json.load(open('results/run.json', encoding='utf-8'))\nDER = os.path.join(os.environ.get('OPTMM_DATA', 'data'), 'derived')\nt = run['tape']\nprint(f\"tape: {t['tape_hours_span']:.1f} h from {t['from']} to {t['to']}: {t['ticker_rows']:,} ticker updates on {t['instruments']} options, {t['prints']} prints; pipeline {run['seconds']:.0f} s\")")
md("## 1. The one number: three quoters on the recorded tape\n\nSame size, same half-spread (the screen's own, in vol), same protections; the centre differs: Deribit's mark, our eSSVI surface blended with the screen mid, the surface leaned by the portfolio vega. Fills come from the recorded prints with the displayed size ahead of us as the queue; delta is hedged in the perpetual; fees at Deribit's public schedule.")
code("m = run.get('mm', {}); runs = m.get('runs', {})\ncols = ['fills', 'contracts', 'pnl_usd', 'pnl_per_contract_usd', 'spread_capture_usd', 'spread_vs_fair_usd', 'markout_5m_usd', 'markout_dh_5m_usd', 'adverse_selection_5m_usd', 'fees_usd', 'hedge_pnl_usd', 'n_hedges', 'n_pulls', 'max_abs_vega', 'max_abs_delta_btc', 'avg_quotes']\ntab = pd.DataFrame({k: {c: v.get(c) for c in cols} for k, v in runs.items()}).T\ndisplay(tab.round(1))\ndisplay(Image('results/figures/mm.png'))\ndisplay(Image('results/figures/mm_inventory.png'))")
md("Spread capture is the fill against Deribit's mark at the print; `spread_vs_fair` against our own fair; the hedged 5-minute mark-out removes the delta times the index move; adverse selection is that mark-out beyond the spread captured. The `_nofees` runs are the economics of a participant on the exchange's liquidity-provider programme; `_slow` re-prices on the timer only; `_noprot` has no protections at all.")
code("fills = pd.read_parquet(os.path.join(DER, 'mm_fills.parquet')) if os.path.exists(os.path.join(DER, 'mm_fills.parquet')) else pd.DataFrame()\nif len(fills):\n    f = fills[fills['quoter'] == 'aware'].copy(); f['t'] = pd.to_datetime(f['ts'], unit='ms', utc=True).dt.strftime('%H:%M:%S')\n    display(f[['t', 'instrument', 'side', 'amount', 'price_usd', 'mark_usd', 'fair_usd', 'spread_capture_usd', 'markout_dh_5m_usd', 'fee_usd', 'port_vega_before']].head(20).round(2))\n    print('by quoter: fills, spread capture, hedged 5-minute mark-out')\n    display(fills.groupby('quoter')[['spread_capture_usd', 'markout_dh_5m_usd', 'fee_usd']].agg(['count', 'sum']).round(0))")
md("## 2. The surface on the tape\n\nOur implied vols from the quotes (C++ solver), the forward from the ticker, one eSSVI slice per expiry with the butterfly conditions applied as a clamp and the calendar ordering enforced, a numerical Durrleman check on the quoted range after every fit.")
code("ts = run.get('tape_surfaces', {})\nprint({k: (round(v, 3) if isinstance(v, float) else v) for k, v in ts.items() if k not in ('atm_vol_by_expiry', 'rr25_by_expiry')})\ndisplay(pd.DataFrame({'atm_vol': ts.get('atm_vol_by_expiry', {}), 'rr25': ts.get('rr25_by_expiry', {})}).round(4))\ndisplay(Image('results/figures/surface.png'))")
code("import duckdb\ncon = duckdb.connect(os.path.join(DER, 'optmm.duckdb'), read_only=True)\nfor q in [\"select round(T*365.25,1) as days, count(*) as n_fits, round(avg(rmse_vol)*100,3) as rmse_volpts, round(avg(atm_vol)*100,2) as atm_vol, round(avg(rr25)*100,2) as rr25, sum(case when butterfly_ok then 0 else 1 end) as butterfly_violations from surfaces group by 1 order by 1\",\n          \"select quoter, count(*) as fills, round(sum(spread_capture_usd)) as spread_capture, round(sum(markout_dh_5m_usd)) as hedged_markout_5m, round(sum(fee_usd)) as fees from mm_fills group by 1 order by 1\"]:\n    try:\n        display(con.execute(q).df())\n    except Exception as e:\n        print('no table yet:', str(e)[:80])\ncon.close()")
md("## 3. Microstructure of quoting\n\nHow long the screen's best quotes stay put after the index moves and what a stale quote would have given up; spread and depth by tenor and moneyness; block prints against the mark.")
code("mi = run.get('micro', {}); pk = mi.get('pickoff', {})\nprint({k: (round(v, 3) if isinstance(v, float) else v) for k, v in pk.items() if k != 'by_bucket'})\nif pk.get('by_bucket'):\n    display(pd.DataFrame(pk['by_bucket']).T.round(3))\ndisplay(pd.DataFrame(mi.get('spread_depth', [])).round(2))\nb = mi.get('blocks', {})\nif b:\n    display(pd.DataFrame({k: b[k] for k in ('all', 'block', 'screen')}).round(3))\ndisplay(Image('results/figures/micro.png'))\ndisplay(Image('results/figures/spread_depth.png'))")
md("## 4. The RFQ pricer\n\nStructures priced off the latest surface with the package Greeks and a spread rule: the sum of the legs' half-spreads in vol through their vegas, times a package factor (1 leg 1.0, 2 legs 0.6, 3 legs 0.8).")
code("r = run.get('rfq', {})\nprint('as of', pd.to_datetime(r.get('asof', 0), unit='ms', utc=True), 'index', r.get('index'))\nfor k, v in r.items():\n    if isinstance(v, dict) and 'quote' in v:\n        print(f\"{k:14s} {v['quote']}\")")
md("## 5. Surfaces from the trade history and the listed chains\n\nOne SSVI surface a day from the Deribit prints in the two hours before the 08:00 UTC settlement since 2021; daily SVI surfaces for SPY, AAPL and NVDA from the DoltHub end-of-day chains with the forward from put-call parity.")
code("h = run.get('history_surfaces', {})\nprint({k: (round(v, 3) if isinstance(v, float) else v) for k, v in h.items() if k not in ('by_year', 'atm_vol_percentiles', 'rr25_percentiles')})\ndisplay(pd.DataFrame(h.get('by_year', {})).T.round(3))\ndisplay(pd.DataFrame({'atm_vol': h.get('atm_vol_percentiles', {}), 'rr25': h.get('rr25_percentiles', {})}).round(4))\ndisplay(Image('results/figures/history.png'))\nch = {k: v for k, v in run.get('chains', {}).items() if isinstance(v, dict)}\ndisplay(pd.DataFrame(ch).T.round(3))\ndisplay(Image('results/figures/chains.png'))\ndisplay(Image('results/figures/chains_example.png'))")


def execute(nb):
    """run the code cells in one namespace; stdout, DataFrames and images become stored outputs"""
    ns = {}
    cwd = os.getcwd(); os.chdir(ROOT)
    try:
        for n, c in enumerate(nb["cells"]):
            if c["cell_type"] != "code":
                continue
            outputs = []
            def display(obj):
                import pandas as pd
                if hasattr(obj, "data") and hasattr(obj, "format") and getattr(obj, "format", None) in ("png", None) and hasattr(obj, "filename"):
                    with open(obj.filename, "rb") as fh:
                        outputs.append({"output_type": "display_data", "metadata": {}, "data": {"image/png": base64.b64encode(fh.read()).decode()}})
                elif isinstance(obj, (pd.DataFrame, pd.Series)):
                    df = obj.to_frame() if isinstance(obj, pd.Series) else obj
                    outputs.append({"output_type": "display_data", "metadata": {}, "data": {"text/html": df.to_html(max_rows=60, max_cols=30), "text/plain": df.to_string(max_rows=60, max_cols=30)}})
                else:
                    outputs.append({"output_type": "display_data", "metadata": {}, "data": {"text/plain": repr(obj)}})
            class _Image:
                def __init__(self, filename):
                    self.filename = filename; self.format = "png"; self.data = None
            ns["display"] = display; ns["Image"] = _Image
            buf = io.StringIO()
            src = c["source"].replace("from IPython.display import Image, display", "")
            try:
                with contextlib.redirect_stdout(buf):
                    exec(compile(src, f"<cell {n}>", "exec"), ns)
            except Exception:
                outputs.append({"output_type": "stream", "name": "stderr", "text": traceback.format_exc()})
            if buf.getvalue():
                outputs.insert(0, {"output_type": "stream", "name": "stdout", "text": buf.getvalue()})
            c["outputs"] = outputs; c["execution_count"] = n + 1
    finally:
        os.chdir(cwd)
    return nb


if __name__ == "__main__":
    nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
    if "--execute" in sys.argv:
        nb = execute(nb)
        errs = [o for c in nb["cells"] for o in c.get("outputs", []) if o.get("name") == "stderr"]
        if errs:
            print("cell errors:", len(errs)); print(errs[0]["text"][-600:])
    os.makedirs(os.path.join(ROOT, "notebooks"), exist_ok=True)
    with open(os.path.join(ROOT, "notebooks", "results.ipynb"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(nb, f, indent=1)
    print("wrote notebooks/results.ipynb", "(executed)" if "--execute" in sys.argv else "")
