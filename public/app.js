/* Property Yield Calculator — frontend logic */

const API = window.location.port === '5500' || window.location.protocol === 'file:'
  ? 'http://localhost:3000'
  : '';

// ── Helpers ──────────────────────────────────────────────────────────────────

const fmt = (n, decimals = 0) =>
  n == null ? '—' : n.toLocaleString('en-DE', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });

const fmtEur = (n, decimals = 0) =>
  n == null ? '—' : `€\u202F${fmt(n, decimals)}`;

const fmtPct = (n) =>
  n == null ? '—' : `${fmt(n, 2)}\u202F%`;

const $ = (id) => document.getElementById(id);

// ── Input collection ─────────────────────────────────────────────────────────

function getInputs() {
  const g = (id) => {
    const v = parseFloat($(id)?.value);
    return isNaN(v) ? null : v;
  };
  return {
    purchase_price:        g('purchase_price'),
    monthly_rent:          g('monthly_rent'),
    hausgeld:              g('hausgeld') ?? 0,
    maintenance:           g('maintenance') ?? 0,
    property_size_m2:      g('property_size_m2') ?? 0,
    down_payment_pct:      (g('down_payment_pct') ?? 20) / 100,
    interest_rate:         (g('interest_rate') ?? 4) / 100,
    loan_term_years:       Math.round(g('loan_term_years') ?? 30),
    grunderwerbsteuer_pct: (g('grunderwerbsteuer_pct') ?? 3.5) / 100,
    notar_pct:             (g('notar_pct') ?? 1.5) / 100,
    makler_pct:            (g('makler_pct') ?? 3.57) / 100,
  };
}

// ── Client-side calculation (mirrors api/index.py) ───────────────────────────

function calcMortgage(principal, annualRate, years) {
  const r = annualRate / 12;
  const n = years * 12;
  if (r === 0) return principal / n;
  return principal * (r * Math.pow(1 + r, n)) / (Math.pow(1 + r, n) - 1);
}

function analyze(d) {
  if (!d.purchase_price || !d.monthly_rent) return null;

  const annualRent = d.monthly_rent * 12;
  const monthlyMaint = d.maintenance;
  const annualMaint = monthlyMaint * 12;
  const annualHausgeld = d.hausgeld * 12;

  const grossYield = (annualRent / d.purchase_price) * 100;
  const netAnnualIncome = annualRent - annualHausgeld - annualMaint;
  const netYield = (netAnnualIncome / d.purchase_price) * 100;

  const downPayment = d.purchase_price * d.down_payment_pct;
  const loanAmount = d.purchase_price - downPayment;
  const monthlyMortgage = calcMortgage(loanAmount, d.interest_rate, d.loan_term_years);

  const cashflow = d.monthly_rent - monthlyMortgage - d.hausgeld - monthlyMaint;
  const annualCashflow = cashflow * 12;

  const grt = d.purchase_price * d.grunderwerbsteuer_pct;
  const notar = d.purchase_price * d.notar_pct;
  const makler = d.purchase_price * d.makler_pct;
  const totalPurchaseCosts = grt + notar + makler;
  const totalInvestment = downPayment + totalPurchaseCosts;

  const roiYears = annualCashflow > 0 ? Math.round((totalInvestment / annualCashflow) * 10) / 10 : null;
  const breakEvenRent = monthlyMortgage + d.hausgeld + monthlyMaint;

  return {
    gross_yield: grossYield,
    net_yield: netYield,
    monthly_mortgage: monthlyMortgage,
    monthly_maintenance: monthlyMaint,
    monthly_cashflow: cashflow,
    annual_cashflow: annualCashflow,
    is_positive_cashflow: cashflow > 0,
    purchase_costs: { grunderwerbsteuer: grt, notar, makler, total: totalPurchaseCosts },
    total_investment: totalInvestment,
    loan_amount: loanAmount,
    down_payment: downPayment,
    roi_years: roiYears,
    break_even_rent: breakEvenRent,
    interest_rate: d.interest_rate,
    loan_term_years: d.loan_term_years,
  };
}

// ── Render results ────────────────────────────────────────────────────────────

function render(r, inputs) {
  if (!r) {
    $('results').hidden = true;
    return;
  }

  $('results').hidden = false;

  // Verdict
  const verdict = $('verdict');
  verdict.className = 'verdict ' + (r.is_positive_cashflow ? 'positive' : 'negative');
  $('verdictAmount').textContent = fmtEur(r.monthly_cashflow, 2);
  $('verdictBadge').textContent = r.is_positive_cashflow
    ? '✓ Positive Cash Flow'
    : '✗ Negative Cash Flow';

  // Metrics
  $('grossYield').textContent      = fmtPct(r.gross_yield);
  $('netYield').textContent        = fmtPct(r.net_yield);
  $('monthlyMortgage').textContent = fmtEur(r.monthly_mortgage, 0);
  $('roiYears').textContent        = r.roi_years ? `${fmt(r.roi_years, 1)} yrs` : '∞';

  // Breakdown
  $('brRent').textContent        = `+ ${fmtEur(inputs.monthly_rent, 0)}`;
  $('brMortgage').textContent    = `− ${fmtEur(r.monthly_mortgage, 0)}`;
  $('brHausgeld').textContent    = `− ${fmtEur(inputs.hausgeld, 0)}`;
  $('brMaintenance').textContent = `− ${fmtEur(r.monthly_maintenance, 0)}`;
  const cfEl = $('brCashflow');
  cfEl.textContent = `${r.monthly_cashflow >= 0 ? '+' : ''} ${fmtEur(r.monthly_cashflow, 2)}`;
  cfEl.style.color = r.is_positive_cashflow ? 'var(--positive)' : 'var(--negative)';

  $('breakEvenNote').textContent =
    `Break-even rent: ${fmtEur(r.break_even_rent, 0)} / month`;

  // Purchase costs
  $('pcDownPayment').textContent       = fmtEur(r.down_payment, 0);
  $('pcGrunderwerbsteuer').textContent = fmtEur(r.purchase_costs.grunderwerbsteuer, 0);
  $('pcNotar').textContent             = fmtEur(r.purchase_costs.notar, 0);
  $('pcMakler').textContent            = fmtEur(r.purchase_costs.makler, 0);
  $('pcTotal').textContent             = fmtEur(r.total_investment, 0);

  // Loan strip
  $('loanAmount').textContent = fmtEur(r.loan_amount, 0);
  $('loanRate').textContent   = fmtPct(r.interest_rate * 100);
  $('loanTerm').textContent   = `${r.loan_term_years} years`;
}

// ── Live calculation on input ─────────────────────────────────────────────────

function recalc() {
  const inputs = getInputs();
  const result = analyze(inputs);
  render(result, inputs);
}

const NUMERIC_IDS = [
  'purchase_price', 'monthly_rent', 'hausgeld', 'maintenance', 'property_size_m2',
  'down_payment_pct', 'interest_rate', 'loan_term_years',
  'grunderwerbsteuer_pct', 'notar_pct', 'makler_pct',
];

NUMERIC_IDS.forEach(id => {
  const el = $(id);
  if (el) el.addEventListener('input', recalc);
});

// ── Scraping ──────────────────────────────────────────────────────────────────

function setFieldValue(id, value, source) {
  const el = $(id);
  if (!el || value == null) return;
  el.value = value;
  const badge = $(`badge_${id}`);
  if (badge) {
    badge.textContent = source === 'scraped' ? 'Scraped' : 'Manual';
    badge.className = `field-badge ${source}`;
    badge.hidden = false;
  }
}

function showStatus(msg, type = 'ok') {
  const el = $('scrapeStatus');
  el.textContent = msg;
  el.className = `scrape-status ${type}`;
  el.hidden = false;
}

function hideStatus() {
  $('scrapeStatus').hidden = true;
}

$('scrapeBtn').addEventListener('click', async () => {
  const url = $('urlInput').value.trim();
  if (!url) return;

  const btn = $('scrapeBtn');
  btn.disabled = true;
  btn.querySelector('.btn-text').hidden = true;
  btn.querySelector('.btn-spinner').hidden = false;
  hideStatus();

  try {
    const res = await fetch(`${API}/api/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (data.error) {
      showStatus(`⚠ ${data.error}`, 'err');
      return;
    }

    let fieldsFound = 0;
    const mapping = {
      purchase_price:   'purchase_price',
      monthly_rent:     'monthly_rent',
      hausgeld:         'hausgeld',
      property_size_m2: 'property_size_m2',
    };

    for (const [key, fieldId] of Object.entries(mapping)) {
      if (data[key] != null) {
        setFieldValue(fieldId, data[key], 'scraped');
        fieldsFound++;
      }
    }

    if (data.title || data.address) {
      $('propertyMeta').hidden = false;
      if (data.title)   $('metaTitle').textContent   = data.title;
      if (data.address) $('metaAddress').textContent = data.address;
    }

    if (fieldsFound === 0) {
      showStatus('⚠ No data found automatically — please fill in the fields manually.', 'warn');
    } else {
      showStatus(`✓ ${fieldsFound} field${fieldsFound > 1 ? 's' : ''} auto-filled. Check and complete any missing values.`, 'ok');
      recalc();
    }
  } catch (err) {
    if (err.message.includes('fetch') || err.message.includes('Failed')) {
      showStatus('⚠ API not reachable — fill in the fields manually.', 'warn');
    } else {
      showStatus(`Error: ${err.message}`, 'err');
    }
  } finally {
    btn.disabled = false;
    btn.querySelector('.btn-text').hidden = false;
    btn.querySelector('.btn-spinner').hidden = true;
  }
});

$('urlInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') $('scrapeBtn').click();
});

recalc();
