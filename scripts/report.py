#!/usr/bin/env python3
"""report.pdf from results/summary.md and results/figures/*.png (fpdf2).   python scripts/report.py [results] [report.pdf]"""
import os
import sys

from fpdf import FPDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "report.pdf")

INTRO = """Question. A vol desk quotes options continuously off a surface it re-marks all day, hedges what it gets filled on, and takes voice requests for structures. How much of a systematic options market maker's edge is spread capture and how much is given back to adverse selection, hedging and fees; what does centring the quote on an arbitrage-free surface and leaning it with the inventory change; how fast does the screen re-mark after the underlying moves and what does a stale quote cost; and where do block prints sit against the screen? The stack answers on real option markets: Deribit's BTC options (every trade since 2021 and a tape of live quotes, books and prints recorded by the repository's own recorder) and the daily end-of-day chains of SPY, AAPL and NVDA.

Method. Python package (optmm) with a C++ implied-volatility solver (pybind11) and DuckDB/parquet storage. Surfaces: implied vols from prices, forwards from put-call parity, SSVI slices (Gatheral-Jacquier) with the butterfly conditions applied as a clamp and the eSSVI calendar ordering across expiries, raw SVI slices with a butterfly penalty and a repair step for the listed chains, and numerical checks of Durrleman's g(k) and of total-variance monotonicity after every fit. Quoting engine: three quoters post the same size at the screen's own half-spread on every option within 25 % of the index on five expiries and differ only in the centre (Deribit's mark, our surface, our surface leaned by the portfolio vega in the Baldacci-Bergault-Gueant manner); fills come from the recorded prints with the displayed size ahead of us as the queue; delta is hedged in the perpetual at its recorded touch; fees are Deribit's; P&L is marked at Deribit's mark and decomposed into spread capture, mark-outs, hedge and fees. Microstructure: re-mark latency and the stale-quote loss after index moves, spread and depth by moneyness and tenor, block prints against the mark. RFQ pricer for the standard structures with the package Greeks and a spread rule.

Caveats. The tape is as long as the recorder has run when the results were generated (its length is stated); the recorder keeps running and the pipeline recomputes on the longer tape. The market maker is a simulation against recorded books, never live trading: it assumes our quotes would not have changed other participants' behaviour. Crypto and US listed options are the free venues; the OTC vol markets a desk quotes by voice have no free history."""

FIGS = [("surface.png", "The surface on the tape: screen mids with bid-ask bars and the eSSVI slices at the latest snapshot; fit quality and arbitrage checks over the tape; ATM vol by expiry."),
        ("mm.png", "The quoters on the recorded tape: mark-to-market P&L paths (dashed: fees off), the decomposition into spread capture, adverse selection (the delta-hedged 5-minute mark-out beyond the spread), hedge P&L and fees, and every fill's spread at the print against what the hedged position did in the next five minutes."),
        ("mm_inventory.png", "Inventory: portfolio vega paths (the aware quoter leans against it) and the sensitivities to the spread multiple, the skew, the blend and fees."),
        ("micro.png", "Microstructure of quoting: re-mark latency after index moves and the stale-quote loss by move size; block prints against the mark by size."),
        ("spread_depth.png", "The screen's spread in vol points and displayed depth by tenor and moneyness, time-weighted over the tape."),
        ("history.png", "One surface a day from the trade prints since 2021: ATM vol and the 25-delta risk reversal, and the fit and calendar statistics by year."),
        ("chains.png", "Listed chains (SPY, AAPL, NVDA, daily since 2024): SVI fit RMSE, the share of quotes with the model inside the bid-ask, front ATM vol."),
        ("chains_example.png", "One SPY day: OTM mids and the SVI slices, and the residuals in units of the half-spread.")]


class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9); self.set_text_color(120); self.cell(0, 6, "options-mm - surface engine, quoting engine on recorded books, RFQ pricer, microstructure of quoting", align="R"); self.ln(8); self.set_text_color(0)

    def footer(self):
        self.set_y(-12); self.set_font("Helvetica", "", 8); self.set_text_color(120); self.cell(0, 6, f"{self.page_no()}", align="C")


def clean(s):
    return (s.replace("–", "-").replace("—", "-").replace("−", "-").replace("×", "x").replace("≥", ">=").replace("≤", "<=").replace("…", "...").replace("²", "^2").replace("±", "+/-").replace("**", "").replace("`", "")
             .replace("→", "->").replace("≈", "~").replace("é", "e").replace("ö", "o").replace("’", "'").replace("λ", "lambda").replace("σ", "sigma").replace("α", "alpha").replace("θ", "theta").replace("ρ", "rho").replace("ψ", "psi").replace("φ", "phi").replace("Δ", "d"))


def md_table(pdf, rows):
    cols = [c.strip() for c in rows[0].strip("|").split("|")]
    data = [[clean(c.strip()) for c in r.strip("|").split("|")] for r in rows[2:]]
    n = len(cols); w = (pdf.w - 20) / n; fs = 6.5 if n <= 7 else 5.0 if n <= 12 else 4.2; cut = 42 if n <= 7 else 22 if n <= 12 else 14
    pdf.set_font("Helvetica", "B", fs)
    for c in cols:
        pdf.cell(w, 5, clean(c)[:cut], border=1)
    pdf.ln(5); pdf.set_font("Helvetica", "", fs)
    for r in data[:80]:
        if pdf.get_y() > pdf.h - 20:
            pdf.add_page()
        for c in r:
            pdf.cell(w, 4.5, c[:cut], border=1)
        pdf.ln(4.5)
    pdf.ln(2)


def main():
    pdf = PDF(); pdf.set_auto_page_break(auto=True, margin=15); pdf.add_page()
    pdf.set_font("Helvetica", "B", 16); pdf.cell(0, 10, "Options Market-Making Engine", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for para in INTRO.split("\n\n"):
        pdf.multi_cell(0, 4.5, clean(para)); pdf.ln(2)
    for fn, cap in FIGS:
        p = os.path.join(R, "figures", fn)
        if not os.path.exists(p):
            continue
        if pdf.get_y() > pdf.h - 90:
            pdf.add_page()
        pdf.image(p, w=pdf.w - 20); pdf.set_font("Helvetica", "I", 8); pdf.multi_cell(0, 4, clean(cap)); pdf.ln(3); pdf.set_font("Helvetica", "", 9)
    sm = os.path.join(R, "summary.md")
    if os.path.exists(sm):
        pdf.add_page(); lines = open(sm, encoding="utf-8").read().splitlines(); i = 0
        while i < len(lines):
            l = lines[i]
            if l.startswith("## "):
                pdf.set_font("Helvetica", "B", 11); pdf.ln(2); pdf.cell(0, 7, clean(l[3:]), new_x="LMARGIN", new_y="NEXT"); pdf.set_font("Helvetica", "", 9); i += 1
            elif l.startswith("|"):
                j = i
                while j < len(lines) and lines[j].startswith("|"):
                    j += 1
                if j - i >= 2:
                    md_table(pdf, lines[i:j])
                i = j
            elif l.startswith("# "):
                i += 1
            elif l.strip():
                pdf.set_x(pdf.l_margin); pdf.multi_cell(0, 4.5, clean(l.strip())); i += 1
            else:
                i += 1
    pdf.output(OUT); print("wrote", OUT)


if __name__ == "__main__":
    main()
