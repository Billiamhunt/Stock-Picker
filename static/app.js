const form = document.getElementById('ticker-form');
const statusEl = document.getElementById('status');
const outputEl = document.getElementById('output');

const fmtNum = (v) => (v === null || v === undefined || Number.isNaN(v) ? 'N/M' : Number(v).toLocaleString(undefined, {maximumFractionDigits: 2}));
const fmtPct = (v) => (v === null || v === undefined || Number.isNaN(v) ? 'N/M' : `${(v*100).toFixed(2)}%`);

function renderKV(title, obj) {
  let rows = Object.entries(obj || {}).map(([k, v]) => `<tr><th>${k}</th><td>${typeof v === 'number' ? fmtNum(v) : v ?? 'N/M'}</td></tr>`).join('');
  return `<h3>${title}</h3><table class="table">${rows}</table>`;
}

function renderMetricTable(title, obj) {
  let rows = Object.entries(obj || {}).map(([k,v]) => `<tr><th>${k}</th><td>${typeof v === 'number' ? fmtNum(v) : v}</td></tr>`).join('');
  return `<h3>${title}</h3><table class="table">${rows}</table>`;
}

function renderHistoricalTable(rows) {
  if (!rows?.length) return '<p>N/M</p>';
  const headers = Object.keys(rows[0]);
  const head = headers.map(h => `<th>${h}</th>`).join('');
  const body = rows.map(r => `<tr>${headers.map(h => {
    const v = r[h];
    if (typeof v === 'number' && h.toLowerCase().includes('margin')) return `<td>${fmtPct(v)}</td>`;
    if (typeof v === 'number') return `<td>${fmtNum(v)}</td>`;
    return `<td>${v ?? 'N/M'}</td>`;
  }).join('')}</tr>`).join('');
  return `<h3>5-year historical table</h3><table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderDCF(dcf) {
  if (!dcf) return '<h3>DCF summary and sensitivity</h3><p>N/M (insufficient data)</p>';
  const sensHead = '<tr><th>WACC \\ g</th><th>1%</th><th>2%</th><th>3%</th></tr>';
  const sensRows = dcf.sensitivity.map(r => `<tr><th>${fmtPct(r.wacc)}</th>${r.values.map(v => `<td>${fmtNum(v)}</td>`).join('')}</tr>`).join('');
  return `
  <h3>DCF summary and sensitivity</h3>
  <table class="table">
    <tr><th>Base FCF (TTM)</th><td>${fmtNum(dcf.base_fcf_ttm)}</td></tr>
    <tr><th>Growth assumption</th><td>${fmtPct(dcf.growth_assumption)}</td></tr>
    <tr><th>Terminal growth</th><td>${fmtPct(dcf.terminal_growth)}</td></tr>
    <tr><th>Enterprise value</th><td>${fmtNum(dcf.enterprise_value)}</td></tr>
    <tr><th>Equity value</th><td>${fmtNum(dcf.equity_value)}</td></tr>
    <tr><th>Intrinsic value per share</th><td>${fmtNum(dcf.intrinsic_value_per_share)}</td></tr>
  </table>
  <h4>Sensitivity (2 x 3 requested grid represented as 3x3 with low/base/high WACC and g)</h4>
  <table class="table">${sensHead}${sensRows}</table>`;
}

function renderOutput(d) {
  Plotly.newPlot('chart', [{x: d.chart.dates, y: d.chart.close, mode:'lines', name:d.ticker}], {title:`${d.ticker} Price History`});

  const snapshot = d.price_snapshot;
  const sections = [];
  sections.push('<h2>Price & snapshot</h2>');
  sections.push(renderKV('Snapshot', {
    'Current price': fmtNum(snapshot.current_price),
    'Market cap': fmtNum(snapshot.market_cap),
    'Fiscal year end': snapshot.fiscal_year_end,
    'Latest 10-K': snapshot.latest_10k ? `<a href="${snapshot.latest_10k.url}" target="_blank">${snapshot.latest_10k.filing_date}</a>` : 'N/M',
    'Latest 10-Q': snapshot.latest_10q ? `<a href="${snapshot.latest_10q.url}" target="_blank">${snapshot.latest_10q.filing_date}</a>` : 'N/M',
    'Upside/downside vs intrinsic': fmtPct(snapshot.upside_downside_vs_intrinsic)
  }));

  sections.push('<h2>Valuation metrics</h2>' + renderMetricTable('', d.metrics.valuation));
  sections.push('<h2>Profitability</h2>' + renderMetricTable('', d.metrics.profitability));
  sections.push('<h2>Leverage</h2>' + renderMetricTable('', d.metrics.leverage));
  sections.push('<h2>Liquidity & efficiency</h2>' + renderMetricTable('', d.metrics.liquidity_efficiency));
  sections.push('<h2>Free cash flow</h2>' + renderMetricTable('', d.metrics.free_cash_flow));
  sections.push('<h2>WACC build</h2>' + renderKV('', {
    'Risk-free rate (10Y)': fmtPct(d.wacc.risk_free_rate_10y),
    'Equity risk premium (Kroll)': fmtPct(d.wacc.equity_risk_premium_kroll),
    'Kroll source': `<a href="${d.wacc.kroll_source}" target="_blank">Source</a>`,
    'Beta (5Y monthly proxy)': fmtNum(d.wacc.beta_5y_monthly),
    'Cost of equity (CAPM)': fmtPct(d.wacc.cost_of_equity_capm),
    'Cost of debt': fmtPct(d.wacc.cost_of_debt),
    'Tax rate': fmtPct(d.wacc.tax_rate),
    'Equity weight': fmtPct(d.wacc.equity_weight),
    'Debt weight': fmtPct(d.wacc.debt_weight),
    'WACC': fmtPct(d.wacc.wacc),
    'Formula': d.wacc.formula
  }));
  sections.push(renderDCF(d.dcf));
  sections.push(renderHistoricalTable(d.historical_5y));
  sections.push(`<h3>Assumptions</h3><ul>${d.assumptions.map(a=>`<li>${a}</li>`).join('')}</ul>`);
  sections.push(`<h3>Data source citations</h3><ul>${d.sources.map(s=>`<li>${s}</li>`).join('')}</ul>`);
  sections.push(`<h3>Investor conclusion</h3><p>Business quality, balance-sheet risk, cash-flow durability, and major risks should be judged from the metrics above. Treat N/M flags conservatively and require a margin of safety when intrinsic value is sensitive to WACC/terminal growth.</p>`);
  outputEl.innerHTML = sections.join('');
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const ticker = document.getElementById('ticker').value.trim();
  statusEl.textContent = 'Running filing-first analysis...';
  outputEl.innerHTML = '';
  try {
    const res = await fetch('/api/analyze', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ticker})});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'failed');
    renderOutput(data);
    statusEl.textContent = `Completed at ${new Date(data.as_of).toLocaleString()}`;
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  }
});
