# options-mm

An options market-making stack on real option markets: an arbitrage-free surface engine (SSVI and SVI), a quoting engine replayed against recorded Deribit books with fills taken from the recorded prints, an RFQ pricer for the structures a vol desk quotes by voice, and the microstructure of quoting (how fast the screen re-marks after the underlying moves, what a stale quote costs, where block prints sit against the screen). The recorder in `tools/record.py` produces the tape; everything is measured on **2.9 hours of Deribit BTC options recorded 2026-09-18 09:44 to 12:38 UTC** (2,845,817 ticker updates on 386 options, 596 prints), on every BTC option print on Deribit since 2021-01 (2,086 settlement days) and on the daily chains of SPY, AAPL and NVDA since 2024.

**The one number.** Quoting every option within 25 % of the index on five expiries at the screen's own half-spread for 2.9 hours, the three quoters ended at **-520 USD (Deribit's mark as the centre), -971 USD (our surface) and -821 USD (our surface leaned by the inventory)** on 28, 65 and 57 fills. The decomposition is the point: spread captured at the print +377 / +140 / +151 USD, the delta-hedged five-minute mark-out beyond it (adverse selection) -46 / -54 / -10 USD, hedge P&L +54 / -226 / -267 USD, **fees 576 / 1,165 / 1,191 USD**. At Deribit's public schedule the fee on a contract (23 USD at this index level) is larger than the half-spread on most of the options that get filled, so the screen's spreads are only sustainable for a participant on the exchange's liquidity-provider programme: with fees off the same quoters make +55 / +370 USD. The protections a desk's engine carries are worth more than the choice of centre: without re-pricing off the live index, the fast-market pull and the hard vega limit the inventory-aware quoter makes -3,484 USD on the same tape (97 fills, largest vega carried 381 against 57 USD per vol point), and re-pricing on the 5-second timer alone -3,381 USD.

## Questions and answers

*How much of a systematic options market maker's edge is spread capture and how much is given back to adverse selection, hedging and fees; what does centring the quote on an arbitrage-free surface and leaning it with the inventory change; how fast does the screen re-mark after the underlying moves and what does a stale quote cost; and where do block prints sit against the screen?*

| | market mark | our surface | surface + inventory lean |
|---|---|---|---|
| fills / contracts | 28 / 21.3 | 65 / 50.0 | 57 / 48.1 |
| spread captured at the print (vs Deribit's mark) | +377 | +140 | +151 |
| spread captured vs our own fair | +371 | +778 | +732 |
| delta-hedged mark-out at 5 min | +331 | +86 | +140 |
| adverse selection (hedged mark-out beyond the spread) | -46 | -54 | -10 |
| hedge P&L (perpetual, 17 hedges for the leaned quoter) | +54 | -226 | -267 |
| fees | 576 | 1,165 | 1,191 |
| **P&L, USD** | **-520** | **-971** | **-821** |
| P&L with fees off | +55 | – | +370 |
| largest \|vega\| carried, USD per vol point | 253 | 296 | 57 |
| quotes live on average | 200 | 150 | 150 |

All three lose money at the public fee schedule; the market-mark quoter does best (-520 USD). Before fees the leaned quoter made +370 USD and the market-mark quoter +55 USD; fees of 1,191 and 576 USD turn that into -821 and -520 (the fee-free runs, which also skip the hedge's taker fee, make +370 and +55 USD): the fee is the first-order cost of quoting cheap options at the screen's spread. The lean does keep the book smaller: the largest vega carried is 57 USD per vol point against 296 for the same centre without the lean, and it captured 11 USD more spread at the print (fewer fills, 57 against 65, and the leaned side is the one the flow wants). Centring on Deribit's mark captures positive spread at the print (+377 USD) because its quotes sit at the screen's own levels and only fill when the queue ahead has traded (28 fills); the surface quoters, centred elsewhere, are filled more often (65 and 57) and capture spread against their own fair (+778 and +732 USD) rather than against the mark. The size of the lean and of the spread matter more than the centre: half the lean (0.75 vol points at the limit) gives -617 USD, a half-spread 1.5 times the screen's -194 USD on 34 fills, and a lean of 3 vol points, wider than the half-spread on most of the universe, posts the leaned side at the screen's touch and is run over (867 fills, -45,079 USD): the lean has to stay inside the half-spread. The protections matter most: without them the leaned quoter's fills rise to 97, its largest vega to 381, its adverse selection to -219 USD and its P&L to -3,484 USD, most of it picked off in the seconds after index moves, which is what the microstructure section measures directly.

**The surface.** 174 eSSVI surfaces on the tape (one a minute, 7 expiries, 130 two-sided quotes each): RMSE 0.74 vol points, the fitted fair inside the screen's bid-ask for 88 % of quotes, 0 butterfly violations, calendar-arbitrage-free in 100 % of the surfaces, a median 0.52 vol points from Deribit's own mark IV. On the history, one surface per settlement day from the prints in the two hours before 08:00 UTC: 11,429 slices on 2,086 days, RMSE 1.40 vol points (prints, not quotes), calendar-free on 100 % of days; ATM vol p5/p50/p95 30 / 51 / 93 %, 25-delta risk reversal (put minus call) -2.4 / +3.3 / +13.3 %. On the listed chains, daily SVI surfaces with the forward from put-call parity: SPY RMSE 0.47 vol points over 619 days (18 % of quotes with the model inside the bid-ask, a median 0.14 vol points from the vendor's own IV), AAPL 0.22, NVDA 0.34.

**Microstructure.** After an index move of 5 bp or more within 30 seconds (50 events on the tape), the best bid or ask of the options within 15 % of the forward took a median 23.8 s to change; 85 % were unchanged after 5 s. Left where it was, a quote would have given up a mean 0.31 USD per contract beyond its half-spread, and the move exceeded the half-spread for 3 % of option-event pairs (5-8bp: 0.04 USD, 2 % beyond the half-spread, n = 9764; 8-12bp: 0.54 USD, 11 % beyond the half-spread, n = 419; 12-20bp: 12.74 USD, 64 % beyond the half-spread, n = 210). The screen's spread is 3.5 vol points at the money under two days to expiry, 1.9 at 2–10 days, 1.1 at 10–45 days and 0.6 beyond, time-weighted. Block trades are 1.6 % of prints and 38 % of contracts since 2021-01; against Deribit's mark at the print they sit a median 0.51 vol points away against 0.56 for screen prints, and the aggressor's median cost against the mark is 1.24 % on blocks and 0.65 % on the screen.

**RFQ.** Off the last surface on the tape (index 78,056): straddle +1 C78000 2d @ 21.8v, +1 P78000 2d @ 21.8v | mid 9,541 bid 9,073 ask 10,009 (half-spread 468, factor 0.6) | delta 0.528 gamma 0.006666 vega 43,710/vol theta -2,632/day; risk reversal +1 C82000 2d @ 37.6v, -1 P74000 2d @ 31.5v | mid 217 bid 180 ask 254 (half-spread 37, factor 0.6) | delta 0.401 gamma 0.000232 vega 2,845/vol theta -315/day; calendar +1 C78000 70d @ 35.4v, -1 C78000 2d @ 21.8v | mid 47,236 bid 46,676 ask 47,797 (half-spread 561, factor 0.6) | delta 0.303 gamma -0.003009 vega 114,172/vol theta 971/day.

## Layout

```
tools/record.py        the tape recorder: Deribit websocket, tickers at 100 ms on every option within 30 % of the index on the
                       two nearest dailies, the weekly, the monthly and the quarterly, 10-level books within 12 %, every print,
                       the index and the perpetual; hourly gzip JSON lines; re-selects the universe every hour
tools/download.py      the trade history (every BTC option print since 2021, monthly parquet), the instrument catalogue, DVOL,
                       the DoltHub end-of-day chains, prices, dividends, SOFR/EFFR
optmm/bs.py            Black (1976) prices and Greeks, the implied-vol solver (numpy); cpp/iv.cpp the same solver in C++ (pybind11)
optmm/surface.py       SSVI and raw SVI slices, the butterfly conditions and Durrleman's g, the calendar ordering, the fits (LM
                       with penalties), the repair step, the Surface object with interpolation in total variance
optmm/marks.py         snapshots of the quoting universe from the tape, the surface per snapshot, fair values and Greeks; the
                       daily surfaces from the trade prints
optmm/chains.py        the listed chains: forward and discount factor from parity, SVI surfaces per day, vendor IV comparison
optmm/mm.py            the quoting engine: three quoters, the fill model, the hedge, the protections, the P&L decomposition
optmm/micro.py         pick-off after index moves, spread and depth by tenor and moneyness, block prints against the mark
optmm/rfq.py           the structure pricer: legs, package Greeks, the spread rule, the hedge
optmm/data.py          loaders, the tape reader (multi-member gzip), the DuckDB views, the CI sample builder
optmm/run.py, cli.py   the pipeline and the command line (python -m optmm ...)
tests/                 24 tests: pricing identities and the solver against the numpy version, SSVI/SVI fits on synthetic smiles
                       with arbitrage checks and the repair, the engine on a synthetic tape (fills, queue, crossing, hedge, fees,
                       mark-outs), the tape reader on truncated gzip members
scripts/               run_all.sh, plots.py, summarize.py, report.py, make_notebook.py, readme.py
data/                  reference/ (prices, dividends, rates), derived/ (the small result tables), sample/ (a 30-minute slice of the
                       tape and small slices of the history and the chains on which CI runs the whole pipeline)
results/               run.json, figures/, summary.md;  report.pdf;  notebooks/results.ipynb
.github/workflows/     ci.yml: builds the C++ solver, runs the tests and the whole pipeline on data/sample
```

## Data

| source | what | notes |
|---|---|---|
| [Deribit websocket](https://docs.deribit.com/) `ticker.{name}.100ms`, `book.{name}.none.10.100ms`, `trades.{name}.100ms`, `deribit_price_index.btc_usd` | the tape: best bid/ask and sizes, mark price and mark IV, bid/ask IV, Greeks, forward and index on every selected option every 100 ms; 10-level books; every print with its IV, mark and block flag; the index; the perpetual | recorded by `tools/record.py`; option prices in BTC (USD = price × index), expiry 08:00 UTC, tick 0.0001 below 0.005 BTC else 0.0005; the recording machine's clock is not the exchange's, so everything is ordered by the exchange timestamp |
| [Deribit history API](https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time) | every BTC option print since 2021: price, amount, IV, mark, index, direction, block-trade id | 69 months, monthly parquet, ~25 M rows (git-ignored; `tools/download.py trades` rebuilds it) |
| Deribit `get_instruments`, `get_volatility_index_data` | the instrument catalogue (expired included), DVOL daily | |
| [DoltHub `post-no-preference/options`](https://www.dolthub.com/repositories/post-no-preference/options) | end-of-day chains: bid, ask, vendor IV and Greeks per strike and expiry, daily since 2024 | SPY, AAPL, NVDA; two to three expiries and ~20 strikes a day, so the listed surfaces are sparse; QQQ, IWM and TLT are not in the database |
| [Yahoo chart API](https://query2.finance.yahoo.com/v8/finance/chart/SPY), [NY Fed](https://markets.newyorkfed.org/) | closes and dividends; SOFR and EFFR for the discount factor | |

What is not free: the OTC vol markets a desk quotes by voice have no public history, and the listed-equity intraday books (OPRA) are paid. Crypto options are the free venue with a full public tape, which is why the quoting engine runs on Deribit and the listed chains are used for the surface engine only.

## Method

**Prices and implied vols.** Black (1976) on the forward: $C = DF\,[F\,N(d_1) - K\,N(d_2)]$, $d_{1,2} = \frac{\ln(F/K) \pm \sigma^2 T/2}{\sigma\sqrt T}$. The solver works on the normalised price $\beta = C/(DF\sqrt{FK})$ of the out-of-the-money option after put-call parity, mapped by the call-put symmetry to an OTM call with $x = \ln(F/K) \le 0$, and takes Halley steps on the total vol $s = \sigma\sqrt T$ inside a bisection bracket (the shape of Jäckel's *Let's be rational*, without its rational initial guess); the C++ version in `cpp/iv.cpp` and the numpy version agree to $10^{-11}$ and the tests check both against round-tripped prices. Deribit's forward is the ticker's `underlying_price`; on the listed chains the forward is the spread-weighted median of $K + (C - P)/DF$ over the strikes quoted on both sides, with $DF = e^{-rT}$ from SOFR (end-of-day mids at two decimals cannot resolve the rate).

**Surfaces.** A slice is total implied variance $w(k) = \sigma^2(k)\,T$ in log-moneyness $k = \ln(K/F)$. On the tape and the history the slices are SSVI (Gatheral and Jacquier 2014) with a power-law $\varphi$ collapsed to one parameter per slice,

$$w(k) = \frac{\theta}{2}\left(1 + \rho\,\psi k + \sqrt{(\psi k + \rho)^2 + 1 - \rho^2}\right),$$

fitted by Levenberg–Marquardt on the OTM quotes weighted by tightness and depth, with the butterfly conditions $\psi(1 + |\rho|) < 4$ and $\psi^2(1 + |\rho|) \le 4\theta$ applied as a clamp. On the listed chains, where three parameters are too stiff (SSVI left an RMSE of 1.4 vol points on SPY), the slices are raw SVI (Gatheral 2004), $w(k) = a + b\,(\rho (k - m) + \sqrt{(k-m)^2 + \sigma^2})$, fitted with a penalty on the Durrleman function

$$g(k) = \left(1 - \frac{k\,w'}{2w}\right)^2 - \frac{w'^2}{4}\left(\frac{1}{w} + \frac14\right) + \frac{w''}{2} \ge 0$$

on a 41-point grid over the quoted range, and a repair step that shrinks $b$ and $|\rho|$ until $g \ge 0$ when the penalty was not enough. Across expiries the sequential ordering of the eSSVI construction (Hendriks and Martini 2019) is enforced on the SSVI slices, $\theta$ and $\psi$ non-decreasing in $T$ with $\theta$ lifted in 1 % steps until the slice's total variance dominates the previous one on the checked range, and an SVI slice that dips under the previous one is lifted by a constant in $a$ (which leaves its own butterfly condition intact); after every fit the numerical checks are run on the quoted range, $g(k) \ge 0$ and $w(k, T_{i+1}) \ge w(k, T_i)$ on $k \in [-0.5, 0.5]$, and what they report is what the tables show. Between expiries the surface interpolates linearly in total variance. The RMSE reported everywhere is in vol points on the fitting quotes; *inside the bid-ask* is the share of quotes whose fair from the surface lies between the screen's bid and ask.

**The quoting engine** (`optmm/mm.py`) replays the tape event by event (ticker updates thinned to quote changes and a 10-second mark refresh, every print, the perpetual). Every 5 seconds, or as soon as the index has moved 2 bp since the last post, each quoter posts a bid and an ask of one contract on every option with $|\ln(K/F)| \le 0.25$, more than four hours and less than 100 days to expiry, a two-sided screen and a mark of at least five ticks. All three post in vol at the same half-spread $h_i = \max(0.4\,\text{vp},\ m \cdot \tfrac12(\sigma^{ask}_i - \sigma^{bid}_i))$, the screen's own half-spread in vol times a multiple $m$ (1 in the headline), and differ only in the centre:

$$\sigma^{bid}_i = c_i - h_i - s(V), \qquad \sigma^{ask}_i = c_i + h_i - s(V), \qquad c_i = \begin{cases} \sigma^{mark}_i & \text{market} \\ \sigma^{model}_i + \lambda\,(\sigma^{mid}_i - \sigma^{model}_i) & \text{surface, aware} \end{cases}$$

with $\lambda = 0.5$ in the headline (0 is the pure surface; no quote where the surface and the screen mid disagree by more than 3 vol points), and the lean $s(V) = s_{max}\,\mathrm{clip}(V / V_{lim}, -1, 1)$ on the portfolio vega $V$ in USD per vol point ($s_{max}$ = 1.5 vol points at $V_{lim}$ = 150, plus a widening of $(V/V_{lim})^2$ vol points) for the aware quoter only, the linear-in-inventory shift that the Baldacci, Bergault and Guéant (2021) reduction of the option market maker's problem to its portfolio vega gives near zero inventory. Prices come from Black on the forward moved with the live index since the ticker, are rounded to the tick, and are capped so that a quote never crosses the screen (a bid at most one tick below the market ask). *Fills:* a print at our price or through it fills us once the size that was displayed at that level when we posted has traded (joining a level puts the displayed size ahead of us; a new price level has nothing ahead), a print through our price fills us in full, and a screen quote crossing our resting order is a marketable order that executes against us; the position per instrument is capped at five contracts. *Hedge:* every 30 seconds the net delta beyond 0.25 BTC is traded in the perpetual at its recorded touch. *Protections*, identical for all quoters and switchable: re-pricing on the index move, a pull of all quotes for 10 seconds when the index moved 15 bp within 10 seconds, and a hard vega limit of 450 USD per vol point beyond which the side that would add to the position is not quoted. *P&L* is marked at Deribit's mark and decomposed into spread capture at the print ($-\text{side} \times (p - \text{mark}) \times q$, also against our own fair), mark-outs at 1, 5 and 30 minutes (the mark's move from the fill in the direction of the position, raw and delta-hedged: $\text{side}\,[(M_{t+h} - p) - \Delta\,(S_{t+h} - S_t)]\,q$), hedge P&L and fees (Deribit's 0.0003 BTC per option contract capped at 12.5 % of the premium, 0.035 % taker on the perpetual, from the instrument definitions). Adverse selection is the hedged five-minute mark-out beyond the spread captured. The tape's prints are what happened against the real book; the simulation assumes our quotes would not have changed anyone's behaviour.

**RFQ pricer** (`optmm/rfq.py`). A structure is a list of legs $(K, T, \pm, q)$ priced off the surface with the package Greeks summed over the legs; the package half-spread is $\kappa \sum_\ell |q_\ell|\,\mathcal V_\ell\, h_\ell$, each leg's half-spread in vol at the nearest screen quote through its vega, times a package factor $\kappa$ (1 leg 1.0, 2 legs 0.6, 3 legs 0.8: risk-offsetting legs are cheaper to quote than the sum of their parts; the factors are desk parameters and are stated); the output is what is read back on the line: mid, bid, ask, Greeks, the delta to hedge.

**Microstructure** (`optmm/micro.py`). *Pick-off:* for every non-overlapping index move of at least 5 bp within 30 seconds, and every option within 15 % of the forward with a two-sided screen, the time until its best bid or ask changed, and the loss a quote left at the old level would have suffered, $\max(|\Delta\,dS| - h^{\$}, 0)$ per contract with $h^{\$}$ the half-spread in USD. *Spread and depth:* the time-weighted median spread in vol points and in % of the mid and the displayed sizes by tenor (< 2 d, 2–10 d, 10–45 d, > 45 d) and standardised moneyness $k/(\sigma\sqrt T)$. *Blocks:* every block print in the history and one screen print in ten, against Deribit's mark at the print, in % of the mark and in vol points (our solver on the mark, forward = index), by size and moneyness, with the aggressor's cost signed by the print's direction.

## Results

![surface](results/figures/surface.png)

*The surface on the tape: screen mids with the bid-ask in vol and the eSSVI slices at the last snapshot; fit RMSE and the arbitrage checks over the tape; ATM vol by expiry.*

![mm](results/figures/mm.png)

*The quoters on the tape: mark-to-market P&L (solid, and dashed with fees off), the decomposition into spread capture, adverse selection, hedge P&L and fees, and every fill's spread at the print against what the delta-hedged position did in the next five minutes.*

![inventory](results/figures/mm_inventory.png)

*Portfolio vega paths (the aware quoter leans against it) and the sensitivities: spread multiple 0.7 and 1.5, a 3-vol-point lean, the pure surface, fees off, the protections off.*

*All runs, USD over 2.9 hours of tape:*

| run | what changes | fills | P&L | spread at print | hedged mark-out 5m | adverse selection | fees | hedge P&L | pulls | max vega |
|---|---|---|---|---|---|---|---|---|---|---|
| market | centre = Deribit's mark | 28 | -520 | +377 | +331 | -46 | 576 | +54 | 1 | 253 |
| surface | centre = surface blended 0.5 with the screen mid | 65 | -971 | +140 | +86 | -54 | 1,165 | -226 | 1 | 296 |
| aware | surface + lean on the portfolio vega | 57 | -821 | +151 | +140 | -10 | 1,191 | -267 | 1 | 57 |
| surface_pure | pure surface (blend 0) | 129 | -2,756 | -384 | -313 | +72 | 2,375 | -383 | 1 | 616 |
| aware_pure | aware, pure surface | 201 | -5,974 | -1,864 | -1,732 | +132 | 4,291 | -533 | 1 | 211 |
| aware_wide | aware, half-spread × 1.5 | 34 | -194 | +197 | +164 | -32 | 630 | -219 | 1 | 57 |
| aware_tight | aware, half-spread × 0.7 | 125 | -2,001 | -18 | -79 | -61 | 2,417 | -665 | 1 | 70 |
| aware_lean_half | aware, lean 0.75 vol points at the limit | 53 | -617 | +143 | +107 | -35 | 1,038 | -260 | 1 | 84 |
| aware_lean_double | aware, lean 3 vol points at the limit | 867 | -45,079 | -18,411 | -19,784 | -1,372 | 23,407 | -1,439 | 1 | 907 |
| aware_nofees | aware, fees off | 57 | +370 | +151 | +140 | -10 | 0 | -267 | 1 | 57 |
| market_nofees | market, fees off | 28 | +55 | +377 | +331 | -46 | 0 | +54 | 1 | 253 |
| aware_slow | aware, re-pricing on the 5-second timer only | 119 | -3,381 | -410 | -530 | -120 | 2,790 | -380 | 1 | 965 |
| aware_noprot | aware, no protections | 97 | -3,484 | -85 | -304 | -219 | 2,361 | -125 | 0 | 381 |
| market_noprot | market, no protections | 46 | -1,629 | +359 | +60 | -299 | 1,195 | -158 | 0 | 1,721 |


![micro](results/figures/micro.png)

*Re-mark latency after index moves and the stale-quote loss by move size; block prints against the mark by size.*

![spread_depth](results/figures/spread_depth.png)

*The screen's spread in vol points and its displayed depth by tenor and moneyness, time-weighted over the tape.*

![history](results/figures/history.png)

*One surface a day from the prints since 2021-01: ATM vol and the 25-delta risk reversal on the front slices, fit RMSE and the calendar check by year.*

![chains](results/figures/chains.png)

*The listed chains: SVI fit RMSE by day, the share of quotes with the model inside the bid-ask, front ATM vol.*

![chains_example](results/figures/chains_example.png)

*One SPY day: the OTM mids and the SVI slices, and the residuals in units of the half-spread.*

## Running it

```
pip install -e .[test]                       # numpy pandas pyarrow duckdb scipy matplotlib fpdf2 websockets pybind11
python build_ext.py build_ext --inplace      # the C++ solver (optional; the numpy solver is used otherwise)
python tools/record.py --currency BTC        # the recorder: leave it running in its own terminal
scripts/run_all.sh [--skip-download] [--quick]
OPTMM_DATA=data/sample python -m optmm run --quick      # the whole pipeline on the committed sample (what CI runs)
python -m optmm mm --quoter aware --blend 0.5 --spread-mult 1.0     # one quoter on the tape, fills printed
python -m optmm rfq                                                # the structures off the latest surface
```

`python -m pytest -q` runs the 24 tests; CI (`.github/workflows/ci.yml`) builds the extension, runs them and the whole pipeline on `data/sample`. `results/run.json` holds every number in this README (`scripts/readme.py` fills it), `results/summary.md` the tables, `report.pdf` the write-up, `notebooks/results.ipynb` the walkthrough with the DuckDB queries (`data/derived/optmm.duckdb` has views over the tape, the surfaces, the fills and the P&L paths).

## Caveats

The tape is 2.9 hours long: the recorder keeps running and the pipeline recomputes every number on whatever it has, but nothing here is a long-run estimate of market-making returns, and the fill counts are small enough that the ordering of the three centres is not established. The market maker is a replay against recorded books, never live trading: fills are inferred from prints that happened against other people's quotes, the queue model uses the displayed size at the best level only, and nothing we did would have changed the flow. Fees are the public schedule; a market maker on Deribit's liquidity-provider programme pays close to nothing, which is why the fee-free runs are shown. The daily history surfaces are fitted to prints (volume-weighted per instrument in the settlement window, with a robust second pass), not to quotes, so their RMSE is not comparable with the tape's. The listed chains are sparse (two to three expiries, ~20 strikes) and end-of-day; the listed surface engine is a fit-quality exercise, not a quoting one.

## References

- Black, F. (1976). The pricing of commodity contracts. *Journal of Financial Economics* 3, 167–179.
- Gatheral, J. (2004). A parsimonious arbitrage-free implied volatility parameterization with application to the valuation of volatility derivatives. Global Derivatives.
- Gatheral, J. and Jacquier, A. (2014). Arbitrage-free SVI volatility surfaces. *Quantitative Finance* 14(1), 59–71. [arXiv:1204.0646](https://arxiv.org/abs/1204.0646)
- Hendriks, S. and Martini, C. (2019). The extended SSVI volatility surface. *Journal of Computational Finance* 22(5). [arXiv:1708.00752](https://arxiv.org/abs/1708.00752)
- Jäckel, P. (2015). Let's be rational. *Wilmott* 2015(75), 40–53.
- Baldacci, B., Bergault, P. and Guéant, O. (2021). Algorithmic market making for options. *Quantitative Finance* 21(1), 85–97. [arXiv:1907.12433](https://arxiv.org/abs/1907.12433)
- Avellaneda, M. and Stoikov, S. (2008). High-frequency trading in a limit order book. *Quantitative Finance* 8(3), 217–224.
- Stoikov, S. and Sağlam, M. (2009). Option market making under inventory risk. *Review of Derivatives Research* 12, 55–79.
- Muravyev, D. (2016). Order flow and expected option returns. *Journal of Finance* 71(2), 673–708.
- Deribit API documentation, https://docs.deribit.com/ (channels, fees and tick sizes from the instrument definitions).
