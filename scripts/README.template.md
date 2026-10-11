# options-mm

An options market-making stack on real option markets, in Python with a C++ implied-vol solver: an arbitrage-free surface engine (SSVI and SVI), a quoting engine replayed against {{tape_hours}} hours of recorded Deribit BTC option books ({{ticker_rows}} ticker updates, {{instruments}} options), an RFQ pricer for vol-desk structures, and measurements of quoting microstructure. The recorder in `tools/record.py` produced the tape on {{tape_from}} to {{tape_to}} UTC.

The question: how much of an options market maker's edge is spread capture, and how much goes to adverse selection, hedging and fees?

## Results

- **All three quoters lose money after fees.** Quoting every option within 25 % of the index on five expiries at the screen's own half-spread, the quoter centred on Deribit's mark ended at {{pnl_market}} USD on {{fills_market}} fills. Our surface as the centre ended at {{pnl_surface}} USD on {{fills_surface}} fills, and the surface leaned by inventory at {{pnl_aware}} USD on {{fills_aware}}.
- **Fees are the cost.** Spread captured at the print was {{sc_market}} / {{sc_surface}} / {{sc_aware}} USD, adverse selection {{as_market}} / {{as_surface}} / {{as_aware}} USD, and fees {{fee_market}} / {{fee_surface}} / {{fee_aware}} USD. On {{fee_gt_n}} of {{fee_gt_total}} headline fills ({{fee_gt_share}} %) the per-contract fee exceeds the spread captured at the print, against Deribit's mark. An independent check against the screen's quoted half-spread on the raw tape found 92 %. With fees off, the mark-centred and leaned quoters make {{pnl_market_nofees}} / {{pnl_aware_nofees}} USD.
- **Protections matter more than the centre.** Without re-pricing off the live index, the fast-market pull and the hard vega limit, the leaned quoter makes {{pnl_aware_noprot}} USD on {{fills_aware_noprot}} fills.
- **The screen is slow to re-mark.** After an index move of 5 bp or more, the best bid or ask took a median {{pk_latency}} s to change, and {{pk_stale5}} % were unchanged after 5 s.
- **The surface fits.** {{n_surfaces}} eSSVI surfaces on the tape fit to {{tape_rmse}} vol points RMSE, with the fitted fair inside the bid-ask for {{tape_within}} % of quotes.

| | market mark | our surface | surface + inventory lean |
|---|---|---|---|
| fills | {{fills_market}} | {{fills_surface}} | {{fills_aware}} |
| spread captured at the print | {{sc_market}} | {{sc_surface}} | {{sc_aware}} |
| adverse selection | {{as_market}} | {{as_surface}} | {{as_aware}} |
| hedge P&L (perpetual; not in the total) | {{hp_market}} | {{hp_surface}} | {{hp_aware}} |
| fees | {{fee_market}} | {{fee_surface}} | {{fee_aware}} |
| **P&L, USD** | **{{pnl_market}}** | **{{pnl_surface}}** | **{{pnl_aware}}** |

![mm](results/figures/mm.png)
![micro](results/figures/micro.png)

All numbers are in `results/run.json` and `results/summary.md`; the detail is in [docs/method.md](docs/method.md).

## How it works

- **Surfaces.** SSVI slices (Gatheral and Jacquier 2014) on the tape, with butterfly and calendar checks. Raw SVI on the sparse listed chains of SPY, AAPL and NVDA. An implied-vol solver in C++ and numpy.
- **Quoting engine.** Replays the tape event by event. The three quoters post at the screen's own half-spread and differ only in the centre. The aware quoter leans by portfolio vega, after Baldacci, Bergault and Guéant (2021).
- **Fills.** A print at or through our price fills us once the displayed size ahead of us has traded. Delta is hedged every 30 seconds in the perpetual.
- **P&L.** Marked at Deribit's mark and split into spread, adverse selection, hedging and fees. Fees follow the public schedule.
- **Other pieces.** An RFQ pricer for straddles, risk reversals and calendars, and a study of pick-off after index moves, spread and depth, and block prints against the screen.

## Limitations

- The tape is {{tape_hours}} hours long. Fill counts are small and the ordering of the three centres is not established.
- It is a replay. Fills are inferred from prints against other people's quotes, and nothing we did would have changed the flow.
- The headline P&L omits the realised P&L on the perpetual hedge. In `optmm/mm.py`, `hedge()` adds it to `st.hedge_pnl_usd` but not to `cash_usd` or `mtm_usd()`. Adding it, an independent check puts the three totals at −466 / −1,197 / −1,088 USD, against the reported −520 / −971 / −821 USD. The code and results are unchanged here, because the raw tape is not available to rerun.
- Fees are the public schedule. A participant in Deribit's liquidity-provider programme pays close to nothing.

## Run

```
pip install -e .[test]
python build_ext.py build_ext --inplace      # C++ solver, optional
OPTMM_DATA=data/sample python -m optmm run --quick
python -m pytest -q                          # {{n_tests}} tests
```

CI builds the extension, runs the tests and the whole pipeline on the committed sample. `python scripts/readme.py` regenerates this file and `docs/method.md` from `results/run.json`.
