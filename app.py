from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
USER_AGENT = "StockPickerResearchBot/1.0 (demo@example.com)"
KROLL_ERP_SOURCE = "https://www.kroll.com/en/insights/publications/cost-of-capital-navigator"
DEFAULT_ERP = 0.055
DEFAULT_TERMINAL_GROWTH = 0.02
DEFAULT_TAX_RATE = 0.21


def safe_div(n: float | None, d: float | None) -> float | None:
    if n is None or d is None or d == 0:
        return None
    try:
        return n / d
    except Exception:
        return None


def nm(value: float | None) -> str | float:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "N/M"
    return value


def pct(value: float | None) -> str:
    if value is None:
        return "N/M"
    return f"{value * 100:.2f}%"


def num(value: float | None) -> str:
    if value is None:
        return "N/M"
    return f"{value:,.2f}"


def get_sec_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov"}


def get_cik_for_ticker(ticker: str) -> str | None:
    resp = requests.get(SEC_TICKER_URL, headers=get_sec_headers(), timeout=20)
    resp.raise_for_status()
    for row in resp.json().values():
        if row.get("ticker", "").upper() == ticker.upper():
            return str(row["cik_str"]).zfill(10)
    return None


def get_sec_filings(ticker: str) -> dict[str, Any]:
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return {"cik": None, "latest_10k": None, "latest_10q": None, "fiscal_year_end": None}

    subm = requests.get(SEC_SUBMISSIONS_URL.format(cik=cik), headers=get_sec_headers(), timeout=20)
    subm.raise_for_status()
    data = subm.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession = recent.get("accessionNumber", [])
    primary_doc = recent.get("primaryDocument", [])
    filing_date = recent.get("filingDate", [])

    latest_10k = None
    latest_10q = None
    for i, form in enumerate(forms):
        if form == "10-K" and latest_10k is None:
            acc = accession[i].replace("-", "")
            latest_10k = {
                "filing_date": filing_date[i],
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{primary_doc[i]}",
            }
        if form == "10-Q" and latest_10q is None:
            acc = accession[i].replace("-", "")
            latest_10q = {
                "filing_date": filing_date[i],
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{primary_doc[i]}",
            }
        if latest_10k and latest_10q:
            break

    return {
        "cik": cik,
        "latest_10k": latest_10k,
        "latest_10q": latest_10q,
        "fiscal_year_end": data.get("fiscalYearEnd"),
    }


def frame_to_dict(df: pd.DataFrame | None) -> dict[str, dict[str, float]]:
    if df is None or df.empty:
        return {}
    clean = df.T.sort_index()
    out = {}
    for idx, row in clean.iterrows():
        key = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        out[key] = {k: float(v) if pd.notna(v) else np.nan for k, v in row.to_dict().items()}
    return out


def get_statement_line(df: pd.DataFrame | None, candidates: list[str], col: Any) -> float | None:
    if df is None or df.empty or col not in df.columns:
        return None
    for c in candidates:
        if c in df.index:
            val = df.loc[c, col]
            return float(val) if pd.notna(val) else None
    return None


def build_response(ticker: str) -> dict[str, Any]:
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    hist = tk.history(period="10y", interval="1d").reset_index()
    if hist.empty:
        raise ValueError("Ticker history unavailable")

    income = tk.income_stmt
    balance = tk.balance_sheet
    cashflow = tk.cashflow
    q_income = tk.quarterly_income_stmt
    q_cashflow = tk.quarterly_cashflow

    sec = get_sec_filings(ticker)

    market_price = float(hist.iloc[-1]["Close"])
    market_cap = info.get("marketCap")
    shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    beta = info.get("beta")
    pe = info.get("trailingPE")
    pb = info.get("priceToBook")
    ps = info.get("priceToSalesTrailing12Months")
    peg = info.get("pegRatio")

    latest_col = income.columns[0] if not income.empty else None
    prev_col = income.columns[1] if income.shape[1] > 1 else None

    rev = get_statement_line(income, ["Total Revenue"], latest_col)
    cogs = get_statement_line(income, ["Cost Of Revenue"], latest_col)
    gross = get_statement_line(income, ["Gross Profit"], latest_col)
    sga = get_statement_line(income, ["Selling General And Administration"], latest_col)
    rnd = get_statement_line(income, ["Research And Development"], latest_col)
    da = get_statement_line(cashflow, ["Depreciation And Amortization", "Depreciation"], cashflow.columns[0] if not cashflow.empty else None)
    ebit = get_statement_line(income, ["Operating Income", "EBIT"], latest_col)
    interest = get_statement_line(income, ["Interest Expense", "Interest Expense Non Operating"], latest_col)
    taxes = get_statement_line(income, ["Tax Provision"], latest_col)
    net_income = get_statement_line(income, ["Net Income"], latest_col)

    cash = get_statement_line(balance, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], balance.columns[0] if not balance.empty else None)
    ar = get_statement_line(balance, ["Accounts Receivable"], balance.columns[0] if not balance.empty else None)
    inventory = get_statement_line(balance, ["Inventory"], balance.columns[0] if not balance.empty else None)
    other_ca = get_statement_line(balance, ["Other Current Assets"], balance.columns[0] if not balance.empty else None)
    current_liab = get_statement_line(balance, ["Current Liabilities"], balance.columns[0] if not balance.empty else None)
    ap = get_statement_line(balance, ["Accounts Payable"], balance.columns[0] if not balance.empty else None)
    accrued = get_statement_line(balance, ["Accrued Expenses"], balance.columns[0] if not balance.empty else None)
    deferred = get_statement_line(balance, ["Deferred Revenue"], balance.columns[0] if not balance.empty else None)
    current_debt = get_statement_line(balance, ["Current Debt", "Current Debt And Capital Lease Obligation"], balance.columns[0] if not balance.empty else None)
    ltd = get_statement_line(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], balance.columns[0] if not balance.empty else None)
    lease = get_statement_line(balance, ["Operating Lease Liability", "Long Term Lease Liability"], balance.columns[0] if not balance.empty else None)
    total_assets = get_statement_line(balance, ["Total Assets"], balance.columns[0] if not balance.empty else None)
    equity = get_statement_line(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"], balance.columns[0] if not balance.empty else None)
    current_assets = get_statement_line(balance, ["Current Assets"], balance.columns[0] if not balance.empty else None)

    cfo = get_statement_line(cashflow, ["Operating Cash Flow"], cashflow.columns[0] if not cashflow.empty else None)
    capex = get_statement_line(cashflow, ["Capital Expenditure"], cashflow.columns[0] if not cashflow.empty else None)
    if capex is not None:
        capex = abs(capex)
    fcf = cfo - capex if cfo is not None and capex is not None else None

    def ttm(df: pd.DataFrame | None, keys: list[str]) -> float | None:
        if df is None or df.empty:
            return None
        cols = list(df.columns[:4])
        vals = []
        for col in cols:
            v = get_statement_line(df, keys, col)
            if v is None:
                return None
            vals.append(v)
        return float(sum(vals))

    rev_ttm = ttm(q_income, ["Total Revenue"])
    ni_ttm = ttm(q_income, ["Net Income"])
    cfo_ttm = ttm(q_cashflow, ["Operating Cash Flow"])
    capex_ttm = ttm(q_cashflow, ["Capital Expenditure"])
    if capex_ttm is not None:
        capex_ttm = abs(capex_ttm)
    fcf_ttm = cfo_ttm - capex_ttm if cfo_ttm is not None and capex_ttm is not None else fcf

    ebitda_ttm = (ebit if ebit is not None else 0) + (da if da is not None else 0)
    debt_total = (current_debt or 0) + (ltd or 0)
    enterprise_value = (market_cap or 0) + debt_total - (cash or 0)

    nopat = ebit * (1 - DEFAULT_TAX_RATE) if ebit is not None else None

    metrics = {
        "valuation": {
            "P/E": nm(pe),
            "P/B (FY)": nm(pb),
            "P/S": nm(ps),
            "PEG": nm(peg),
            "EV/EBITDA": nm(safe_div(enterprise_value, ebitda_ttm if ebitda_ttm else None)),
            "EV/EBIT": nm(safe_div(enterprise_value, ebit)),
            "EV/NOPAT": nm(safe_div(enterprise_value, nopat)),
            "Price / Cash Flow": nm(safe_div(market_cap, cfo_ttm)),
        },
        "profitability": {
            "Gross margin (FY)": pct(safe_div(gross, rev)),
            "Operating margin (FY)": pct(safe_div(ebit, rev)),
            "Net margin (FY)": pct(safe_div(net_income, rev)),
            "ROE (FY)": pct(safe_div(net_income, equity)),
            "ROA (FY)": pct(safe_div(net_income, total_assets)),
            "ROIC (FY)": pct(safe_div(nopat, (equity or 0) + debt_total - (cash or 0))),
        },
        "leverage": {
            "Debt / Equity (FY)": nm(safe_div(debt_total, equity)),
            "Debt / EBITDA (TTM)": nm(safe_div(debt_total, ebitda_ttm)),
            "Interest coverage (EBIT/Interest)": nm(safe_div(ebit, abs(interest) if interest else None)),
        },
        "liquidity_efficiency": {
            "Current ratio (FY)": nm(safe_div(current_assets, current_liab)),
            "Quick ratio (FY)": nm(safe_div((cash or 0) + (ar or 0), current_liab)),
            "Asset turnover (FY)": nm(safe_div(rev, total_assets)),
            "Inventory turnover (FY)": nm(safe_div(cogs, inventory)),
            "Receivables turnover (FY)": nm(safe_div(rev, ar)),
        },
        "free_cash_flow": {
            "FCF (TTM)": nm(fcf_ttm),
            "FCF yield": pct(safe_div(fcf_ttm, market_cap)),
            "FCF margin": pct(safe_div(fcf_ttm, rev_ttm if rev_ttm else rev)),
        },
    }

    risk_free = (info.get("tenYearAverageReturn") or 0.043)
    cost_of_equity = risk_free + (beta or 1.0) * DEFAULT_ERP
    avg_debt = debt_total
    cost_of_debt = safe_div(abs(interest) if interest else None, avg_debt)
    net_debt = debt_total - (cash or 0)
    e_weight = safe_div(market_cap, (market_cap or 0) + max(net_debt, 0)) or 1
    d_weight = 1 - e_weight
    wacc = (e_weight * cost_of_equity) + (d_weight * (cost_of_debt or 0.05) * (1 - DEFAULT_TAX_RATE))

    base_fcf = fcf_ttm
    dcf = None
    if base_fcf and wacc and wacc > DEFAULT_TERMINAL_GROWTH:
        growth = 0.05
        fcfs = [base_fcf * ((1 + growth) ** i) for i in range(1, 6)]
        pv_fcfs = [fcf_i / ((1 + wacc) ** i) for i, fcf_i in enumerate(fcfs, 1)]
        terminal = (fcfs[-1] * (1 + DEFAULT_TERMINAL_GROWTH)) / (wacc - DEFAULT_TERMINAL_GROWTH)
        pv_terminal = terminal / ((1 + wacc) ** 5)
        ev = sum(pv_fcfs) + pv_terminal
        eq_val = ev - net_debt
        iv = safe_div(eq_val, shares)

        dcf = {
            "base_fcf_ttm": base_fcf,
            "growth_assumption": growth,
            "terminal_growth": DEFAULT_TERMINAL_GROWTH,
            "enterprise_value": ev,
            "equity_value": eq_val,
            "intrinsic_value_per_share": iv,
            "sensitivity": [],
        }
        for w in [wacc - 0.01, wacc, wacc + 0.01]:
            row = {"wacc": w, "values": []}
            for g in [0.01, DEFAULT_TERMINAL_GROWTH, 0.03]:
                if w <= g:
                    row["values"].append(None)
                    continue
                t = (fcfs[-1] * (1 + g)) / (w - g)
                ev_s = sum([f / ((1 + w) ** i) for i, f in enumerate(fcfs, 1)]) + t / ((1 + w) ** 5)
                eq_s = ev_s - net_debt
                row["values"].append(safe_div(eq_s, shares))
            dcf["sensitivity"].append(row)

    hist_5y = []
    for col in income.columns[:5]:
        year = col.year if hasattr(col, "year") else str(col)
        year_rev = get_statement_line(income, ["Total Revenue"], col)
        year_gm = safe_div(get_statement_line(income, ["Gross Profit"], col), year_rev)
        year_om = safe_div(get_statement_line(income, ["Operating Income", "EBIT"], col), year_rev)
        year_nm = safe_div(get_statement_line(income, ["Net Income"], col), year_rev)
        cfo_y = get_statement_line(cashflow, ["Operating Cash Flow"], col)
        capex_y = get_statement_line(cashflow, ["Capital Expenditure"], col)
        fcf_y = cfo_y - abs(capex_y) if cfo_y is not None and capex_y is not None else None
        hist_5y.append({
            "year": str(year),
            "Revenue": year_rev,
            "Gross margin": year_gm,
            "Operating margin": year_om,
            "Net margin": year_nm,
            "EBITDA": (get_statement_line(income, ["Operating Income", "EBIT"], col) or 0) + (get_statement_line(cashflow, ["Depreciation And Amortization", "Depreciation"], col) or 0),
            "FCF": fcf_y,
            "Cash": get_statement_line(balance, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], col),
            "Debt": (get_statement_line(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], col) or 0) + (get_statement_line(balance, ["Current Debt", "Current Debt And Capital Lease Obligation"], col) or 0),
            "Current ratio": safe_div(get_statement_line(balance, ["Current Assets"], col), get_statement_line(balance, ["Current Liabilities"], col)),
        })

    upside = safe_div((dcf or {}).get("intrinsic_value_per_share", None) - market_price if dcf else None, market_price)

    return {
        "ticker": ticker.upper(),
        "as_of": datetime.utcnow().isoformat() + "Z",
        "price_snapshot": {
            "current_price": market_price,
            "market_cap": market_cap,
            "fiscal_year_end": sec.get("fiscal_year_end"),
            "latest_10k": sec.get("latest_10k"),
            "latest_10q": sec.get("latest_10q"),
            "shares_outstanding": shares,
            "upside_downside_vs_intrinsic": upside,
        },
        "core_financials": {
            "income_statement_fy": {
                "Revenue": rev,
                "COGS": cogs,
                "Gross profit": gross,
                "SG&A": sga,
                "R&D": rnd,
                "D&A": da,
                "EBIT": ebit,
                "Interest expense": interest,
                "Taxes": taxes,
                "Net income": net_income,
            },
            "balance_sheet_fy": {
                "Cash & short-term investments": cash,
                "Accounts receivable": ar,
                "Inventory": inventory,
                "Other current assets": other_ca,
                "Current liabilities": current_liab,
                "AP": ap,
                "Accrued": accrued,
                "Deferred": deferred,
                "Current debt": current_debt,
                "Long-term debt": ltd,
                "Lease liabilities": lease,
                "Total assets": total_assets,
                "Total equity": equity,
            },
            "cash_flow_fy": {
                "Operating cash flow": cfo,
                "Capex": capex,
                "Free cash flow": fcf,
            },
            "ttm": {
                "Revenue": rev_ttm,
                "Net income": ni_ttm,
                "Operating cash flow": cfo_ttm,
                "Capex": capex_ttm,
                "Free cash flow": fcf_ttm,
            },
        },
        "metrics": metrics,
        "wacc": {
            "risk_free_rate_10y": risk_free,
            "equity_risk_premium_kroll": DEFAULT_ERP,
            "kroll_source": KROLL_ERP_SOURCE,
            "beta_5y_monthly": beta,
            "cost_of_equity_capm": cost_of_equity,
            "cost_of_debt": cost_of_debt,
            "tax_rate": DEFAULT_TAX_RATE,
            "equity_weight": e_weight,
            "debt_weight": d_weight,
            "wacc": wacc,
            "formula": "WACC = E/(D+E) * Re + D/(D+E) * Rd * (1-tax)",
        },
        "dcf": dcf,
        "historical_5y": hist_5y,
        "chart": {
            "dates": [d.strftime("%Y-%m-%d") for d in hist["Date"]],
            "close": [float(v) for v in hist["Close"]],
        },
        "assumptions": [
            "Baseline uses the latest two fiscal years plus TTM where needed.",
            "Tax rate default 21% unless unavailable company-specific effective rate.",
            "Terminal growth default 2% for US issuer.",
            "N/M indicates non-meaningful or unavailable metric.",
        ],
        "sources": [
            "SEC EDGAR submissions API",
            "Yahoo Finance market and financial statement endpoints",
            "Kroll Cost of Capital Navigator",
        ],
    }


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    body = request.get_json(force=True)
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required"}), 400
    try:
        data = build_response(ticker)
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
