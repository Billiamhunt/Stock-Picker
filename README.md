# Stock Procedure Analyzer

A Flask web app that accepts a stock ticker and returns:

- interactive historical price chart
- SEC EDGAR links for latest 10-K and 10-Q
- core financial statements (FY + TTM)
- valuation/operating metrics
- WACC build and 5-year DCF with sensitivity
- 5-year historical summary table

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:5000`.
