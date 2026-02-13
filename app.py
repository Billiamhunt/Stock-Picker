from __future__ import annotations

from datetime import datetime
from typing import Any

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
    return n / d


def nm_ratio(n: float | None, d: float | None, require_positive_denominator: bool = False) -> float | str:
    if n is None or d is None or d == 0:
        return "N/M"
    if require_positive_denominator and d <= 0:
        return "N/M"
    out = safe_div(n, d)
    if out is None:
        return "N/M"
    return out


def nm_percent(n: float | None, d: float | None) -> str:
    value = safe_div(n, d)
    if value is None:
        return "N/M"
    return f"{value * 100:.2f}%"


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

    latest_10k = None
    latest_10q = None
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])

    for i, form in enumerate(forms):
        if form in {"10-K", "10-Q"}:
            acc = accessions[i].replace("-", "")
            payload = {
                "filing_date": dates[i],
                "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{docs[i]}",
            }
            if form == "10-K" and latest_10k is None:
                latest_10k = payload
            if form == "10-Q" and latest_10q is None:
                latest_10q = payload
        if latest_10k and latest_10q:
            break

    return {
        "cik": cik,
        "latest_10k": latest_10k,
        "latest_10q": latest_10q,
        "fiscal_year_end": data.get("fiscalYearEnd"),
    }


def get_statement_line(df: pd.DataFrame | None, candidates: list[str], col: Any) -> float | None:
    if df is None or df.empty or col not in df.columns:
        return None
    for c in candidates:
        if c in df.index:
            val = df.loc[c, col]
            return float(val) if pd.notna(val) else None
    return None


def ttm_sum(df: pd.DataFrame | None, keys: list[str]) -> float | None:
    if df is None or df.empty or len(df.columns) < 4:
        return None
    vals: list[float] = []
    for col in list(df.columns[:4]):
        v = get_statement_line(df, keys, col)
        if v is None:
            return None
        vals.append(v)
    return float(sum(vals))


def get_risk_free_rate() -> float | None:
    # ^TNX is 10Y Treasury yield * 10, so divide by 100 for decimal
    tnx = yf.Ticker("^TNX").history(period="5d", interval="1d")
    if tnx.empty:
        return None
    return float(tnx["Close"].iloc[-1]) / 100.0


def get_beta_5y_monthly(ticker: str) -> float | None:
    stock = yf.Ticker(ticker).history(period="5y", interval="1mo")
    market = yf.Ticker("^GSPC").history(period="5y", interval="1mo")
    if stock.empty or market.empty:
        return None

    s = stock["Close"].pct_change().dropna()
    m = market["Close"].pct_change().dropna()
    joined = pd.concat([s, m], axis=1, join="inner").dropna()
    if joined.empty:
        return None

    joined.columns = ["s", "m"]
    var_m = joined["m"].var()
    if var_m == 0:
        return None
    cov = joined["s"].cov(joined["m"])
    return float(cov / var_m)


def build_response(ticker: str) -> dict[str, Any]:
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    hist = tk.history(period="10y", interval="1d").reset_index()
    if hist.empty:
        raise ValueError("Ticker history unavailable")

    sec = get_sec_filings(ticker)

    income = tk.income_stmt
    balance = tk.balance_sheet
    cashflow = tk.cashflow
    q_income = tk.quarterly_income_stmt
    q_cashflow = tk.quarterly_cashflow

    price = float(hist.iloc[-1]["Close"])
    market_cap = info.get("marketCap")
    shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")

    latest_col = income.columns[0] if not income.empty else None
    prev_col = income.columns[1] if income.shape[1] > 1 else None
    bs_col = balance.columns[0] if not balance.empty else None
    bs_prev = balance.columns[1] if balance.shape[1] > 1 else None
    cf_col = cashflow.columns[0] if not cashflow.empty else None

    rev = get_statement_line(income, ["Total Revenue"], latest_col)
    cogs = get_statement_line(income, ["Cost Of Revenue"], latest_col)
    gross = get_statement_line(income, ["Gross Profit"], latest_col)
    sga = get_statement_line(income, ["Selling General And Administration"], latest_col)
    rnd = get_statement_line(income, ["Research And Development"], latest_col)
    ebit = get_statement_line(income, ["Operating Income", "EBIT"], latest_col)
    da = get_statement_line(cashflow, ["Depreciation And Amortization", "Depreciation"], cf_col)
    interest = get_statement_line(income, ["Interest Expense", "Interest Expense Non Operating"], latest_col)
    taxes = get_statement_line(income, ["Tax Provision"], latest_col)
    net_income = get_statement_line(income, ["Net Income"], latest_col)

    cash = get_statement_line(balance, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], bs_col)
    ar = get_statement_line(balance, ["Accounts Receivable"], bs_col)
    inventory = get_statement_line(balance, ["Inventory"], bs_col)
    other_ca = get_statement_line(balance, ["Other Current Assets"], bs_col)
    current_assets = get_statement_line(balance, ["Current Assets"], bs_col)
    current_liab = get_statement_line(balance, ["Current Liabilities"], bs_col)
    ap = get_statement_line(balance, ["Accounts Payable"], bs_col)
    accrued = get_statement_line(balance, ["Accrued Expenses"], bs_col)
    deferred = get_statement_line(balance, ["Deferred Revenue"], bs_col)
    current_debt = get_statement_line(balance, ["Current Debt", "Current Debt And Capital Lease Obligation"], bs_col)
    ltd = get_statement_line(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], bs_col)
    lease = get_statement_line(balance, ["Operating Lease Liability", "Long Term Lease Liability"], bs_col)
    total_assets = get_statement_line(balance, ["Total Assets"], bs_col)
    equity = get_statement_line(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"], bs_col)

    cfo = get_statement_line(cashflow, ["Operating Cash Flow"], cf_col)
    capex_raw = get_statement_line(cashflow, ["Capital Expenditure"], cf_col)
    capex = abs(capex_raw) if capex_raw is not None else None
    fcf = cfo - capex if cfo is not None and capex is not None else None

    rev_ttm = ttm_sum(q_income, ["Total Revenue"])
    ni_ttm = ttm_sum(q_income, ["Net Income"])
    cfo_ttm = ttm_sum(q_cashflow, ["Operating Cash Flow"])
    capex_ttm_raw = ttm_sum(q_cashflow, ["Capital Expenditure"])
    capex_ttm = abs(capex_ttm_raw) if capex_ttm_raw is not None else None
    fcf_ttm = cfo_ttm - capex_ttm if cfo_ttm is not None and capex_ttm is not None else fcf

    debt_total = (current_debt or 0.0) + (ltd or 0.0)
    debt_prev = (
        (get_statement_line(balance, ["Current Debt", "Current Debt And Capital Lease Obligation"], bs_prev) or 0.0)
        + (get_statement_line(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], bs_prev) or 0.0)
    )
    avg_debt = (debt_total + debt_prev) / 2 if (debt_total or debt_prev) else None

    ebitda_ttm = (ebit or 0.0) + (da or 0.0)
    enterprise_value = (market_cap or 0.0) + debt_total - (cash or 0.0)
    nopat = ebit * (1 - DEFAULT_TAX_RATE) if ebit is not None else None

    pe = nm_ratio(price, safe_div(ni_ttm, shares), require_positive_denominator=True)
    pb = nm_ratio(price, safe_div(equity, shares), require_positive_denominator=True)
    ps = nm_ratio(price, safe_div(rev_ttm, shares), require_positive_denominator=True)
    eps_growth = None
    if latest_col is not None and prev_col is not None:
        ni_prev = get_statement_line(income, ["Net Income"], prev_col)
        if ni_prev not in (None, 0):
            eps_growth = (net_income - ni_prev) / abs(ni_prev) if net_income is not None else None
    peg = "N/M"
    if isinstance(pe, float) and eps_growth not in (None, 0):
        peg = pe / (eps_growth * 100)

    metrics = {
        "valuation": {
            "P/E (TTM)": pe,
            "P/B (FY)": pb,
            "P/S (TTM)": ps,
            "PEG": peg if isinstance(peg, float) else "N/M",
            "EV/EBITDA (TTM)": nm_ratio(enterprise_value, ebitda_ttm, require_positive_denominator=True),
            "EV/EBIT (TTM proxy)": nm_ratio(enterprise_value, ebit, require_positive_denominator=True),
            "EV/NOPAT (TTM proxy)": nm_ratio(enterprise_value, nopat, require_positive_denominator=True),
            "Price / Cash Flow (TTM)": nm_ratio(price, safe_div(cfo_ttm, shares), require_positive_denominator=True),
        },
        "profitability": {
            "Gross margin (FY)": nm_percent(gross, rev),
            "Operating margin (FY)": nm_percent(ebit, rev),
            "Net margin (FY)": nm_percent(net_income, rev),
            "ROE (FY)": nm_percent(net_income, equity),
            "ROA (FY)": nm_percent(net_income, total_assets),
            "ROIC (FY)": nm_percent(nopat, (equity or 0.0) + debt_total - (cash or 0.0)),
        },
        "leverage": {
            "Debt / Equity (FY)": nm_ratio(debt_total, equity, require_positive_denominator=True),
            "Debt / EBITDA (TTM proxy)": nm_ratio(debt_total, ebitda_ttm, require_positive_denominator=True),
            "Interest coverage (EBIT/Interest)": nm_ratio(ebit, abs(interest) if interest else None, require_positive_denominator=True),
        },
        "liquidity_efficiency": {
            "Current ratio (FY)": nm_ratio(current_assets, current_liab, require_positive_denominator=True),
            "Quick ratio (FY)": nm_ratio((cash or 0.0) + (ar or 0.0), current_liab, require_positive_denominator=True),
            "Asset turnover (FY)": nm_ratio(rev, total_assets, require_positive_denominator=True),
            "Inventory turnover (FY)": nm_ratio(cogs, inventory, require_positive_denominator=True),
            "Receivables turnover (FY)": nm_ratio(rev, ar, require_positive_denominator=True),
        },
        "free_cash_flow": {
            "FCF (TTM)": fcf_ttm if fcf_ttm is not None else "N/M",
            "FCF yield (TTM)": nm_percent(fcf_ttm, market_cap),
            "FCF margin (TTM)": nm_percent(fcf_ttm, rev_ttm),
        },
    }

    risk_free = get_risk_free_rate()
    beta_5y = get_beta_5y_monthly(ticker)
    cost_of_equity = (risk_free + beta_5y * DEFAULT_ERP) if risk_free is not None and beta_5y is not None else None
    cost_of_debt = safe_div(abs(interest) if interest is not None else None, avg_debt)

    net_debt = debt_total - (cash or 0.0)
    cap_base = (market_cap or 0.0) + max(net_debt, 0.0)
    e_weight = safe_div(market_cap, cap_base) if cap_base else None
    d_weight = (1 - e_weight) if e_weight is not None else None

    wacc = None
    if cost_of_equity is not None and e_weight is not None and d_weight is not None:
        rd = cost_of_debt if cost_of_debt is not None else 0.05
        wacc = (e_weight * cost_of_equity) + (d_weight * rd * (1 - DEFAULT_TAX_RATE))

    dcf = None
    base_fcf = fcf_ttm
    if base_fcf is not None and wacc is not None and wacc > DEFAULT_TERMINAL_GROWTH and shares:
        growth = 0.04
        fcfs = [base_fcf * ((1 + growth) ** yr) for yr in range(1, 6)]
        pv_fcfs = [fcf_i / ((1 + wacc) ** i) for i, fcf_i in enumerate(fcfs, 1)]
        terminal = (fcfs[-1] * (1 + DEFAULT_TERMINAL_GROWTH)) / (wacc - DEFAULT_TERMINAL_GROWTH)
        ev = sum(pv_fcfs) + terminal / ((1 + wacc) ** 5)
        eq = ev - net_debt
        intrinsic = safe_div(eq, shares)

        sensitivity: list[dict[str, Any]] = []
        for w in [wacc - 0.01, wacc + 0.01]:
            row_vals: list[float | None] = []
            for g in [DEFAULT_TERMINAL_GROWTH - 0.01, DEFAULT_TERMINAL_GROWTH, DEFAULT_TERMINAL_GROWTH + 0.01]:
                if w <= g:
                    row_vals.append(None)
                    continue
                terminal_s = (fcfs[-1] * (1 + g)) / (w - g)
                ev_s = sum([f / ((1 + w) ** i) for i, f in enumerate(fcfs, 1)]) + terminal_s / ((1 + w) ** 5)
                row_vals.append(safe_div(ev_s - net_debt, shares))
            sensitivity.append({"wacc": w, "values": row_vals})

        dcf = {
            "base_fcf_ttm": base_fcf,
            "growth_assumption": growth,
            "terminal_growth": DEFAULT_TERMINAL_GROWTH,
            "enterprise_value": ev,
            "equity_value": eq,
            "intrinsic_value_per_share": intrinsic,
            "sensitivity_2x3": sensitivity,
        }

    hist_5y: list[dict[str, Any]] = []
    for col in list(income.columns[:5]):
        yr = col.year if hasattr(col, "year") else str(col)
        yr_rev = get_statement_line(income, ["Total Revenue"], col)
        yr_gp = get_statement_line(income, ["Gross Profit"], col)
        yr_ebit = get_statement_line(income, ["Operating Income", "EBIT"], col)
        yr_ni = get_statement_line(income, ["Net Income"], col)
        yr_cfo = get_statement_line(cashflow, ["Operating Cash Flow"], col)
        yr_capex = get_statement_line(cashflow, ["Capital Expenditure"], col)
        yr_da = get_statement_line(cashflow, ["Depreciation And Amortization", "Depreciation"], col)
        hist_5y.append(
            {
                "FY": str(yr),
                "Revenue": yr_rev,
                "Gross margin": safe_div(yr_gp, yr_rev),
                "Operating margin": safe_div(yr_ebit, yr_rev),
                "Net margin": safe_div(yr_ni, yr_rev),
                "EBITDA": (yr_ebit or 0.0) + (yr_da or 0.0),
                "FCF": (yr_cfo - abs(yr_capex)) if yr_cfo is not None and yr_capex is not None else None,
                "Cash": get_statement_line(balance, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], col),
                "Debt": (
                    (get_statement_line(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], col) or 0.0)
                    + (get_statement_line(balance, ["Current Debt", "Current Debt And Capital Lease Obligation"], col) or 0.0)
                ),
                "Current ratio": safe_div(
                    get_statement_line(balance, ["Current Assets"], col),
                    get_statement_line(balance, ["Current Liabilities"], col),
                ),
            }
        )

    upside = safe_div((dcf["intrinsic_value_per_share"] - price), price) if dcf and dcf.get("intrinsic_value_per_share") is not None else None

    return {
        "ticker": ticker.upper(),
        "as_of": datetime.utcnow().isoformat() + "Z",
        "price_snapshot": {
            "current_price": price,
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
            "beta_5y_monthly": beta_5y,
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
            "Baseline uses latest FY plus prior FY, with TTM for valuation/DCF inputs.",
            "Tax rate fixed at 21% unless company-specific rate is available.",
            "Terminal growth base case is 2% for U.S. issuers.",
            "N/M means non-meaningful (missing or invalid denominator/sign).",
        ],
        "sources": [
            "SEC EDGAR submissions API",
            "Yahoo Finance price history and statements",
            "Kroll Cost of Capital Navigator",
        ],
    }


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze() -> Any:
    body = request.get_json(force=True)
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required"}), 400
    try:
        return jsonify(build_response(ticker))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
