/**
 * StockOps AI Live Operations Dashboard Controller
 * Asynchronous state manager, drag-and-drop uploader, and HITL workflow engine.
 */

// Global state
let currentSelectedFile = null;

// DOM Elements
const kpiTotalValue = document.getElementById('kpi-total-value');
const kpiTotalSkus = document.getElementById('kpi-total-skus');
const kpiTotalUnits = document.getElementById('kpi-total-units');
const kpiAutoCommitted = document.getElementById('kpi-auto-committed');
const kpiFlaggedPending = document.getElementById('kpi-flagged-pending');
const hitlPendingPill = document.getElementById('hitl-pending-pill');

const inventoryTableBody = document.getElementById('inventory-table-body');
const ledgerTableBody = document.getElementById('ledger-table-body');

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const selectedFilename = document.getElementById('selected-filename');
const uploadButton = document.getElementById('upload-button');
const uploadButtonText = document.getElementById('upload-button-text');
const uploadSpinner = document.getElementById('upload-spinner');

const btnSimValid = document.getElementById('btn-sim-valid');
const btnSimAnomaly = document.getElementById('btn-sim-anomaly');
const btnSimUnknown = document.getElementById('btn-sim-unknown');
const btnRefresh = document.getElementById('btn-refresh-telemetry');

const inspectStatusBadge = document.getElementById('inspect-status-badge');
const inspectContent = document.getElementById('inspect-content');
const toastContainer = document.getElementById('toast-container');

// Show floating toast notification
function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  const bgColors = {
    success: 'bg-emerald-950 border-emerald-600 text-emerald-200',
    error: 'bg-rose-950 border-rose-600 text-rose-200',
    warning: 'bg-amber-950 border-amber-600 text-amber-200',
    info: 'bg-slate-900 border-slate-700 text-slate-200'
  };

  toast.className = `p-3.5 rounded-xl border text-xs shadow-2xl backdrop-blur transition-all duration-300 transform translate-y-2 opacity-0 pointer-events-auto flex items-center justify-between gap-3 ${bgColors[type] || bgColors.info}`;
  toast.innerHTML = `
    <span>${message}</span>
    <button onclick="this.parentElement.remove()" class="text-slate-400 hover:text-white">&times;</button>
  `;

  toastContainer.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.remove('translate-y-2', 'opacity-0');
  });

  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-y-2');
    setTimeout(() => toast.remove(), 300);
  }, 4500);
}

// Fetch all dashboard telemetry and re-render tables
async function fetchDashboardState() {
  try {
    const [metricsRes, invRes, ledgerRes] = await Promise.all([
      fetch('/api/v1/metrics'),
      fetch('/api/v1/inventory'),
      fetch('/api/v1/ledger')
    ]);

    if (!metricsRes.ok || !invRes.ok || !ledgerRes.ok) {
      throw new Error('Failed to fetch dashboard endpoints');
    }

    const [metrics, inventory, ledger] = await Promise.all([
      metricsRes.json(),
      invRes.json(),
      ledgerRes.json()
    ]);

    renderKPIs(metrics);
    renderInventoryTable(inventory);
    renderLedgerTable(ledger);

    if (window.lucide) {
      window.lucide.createIcons();
    }
  } catch (err) {
    console.error('Error fetching dashboard state:', err);
    showToast(`Telemetry fetch error: ${err.message}`, 'error');
  }
}

// Render Top KPI Cards
function renderKPIs(metrics) {
  kpiTotalValue.textContent = `$${Number(metrics.total_inventory_value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  kpiTotalSkus.textContent = metrics.total_skus || 0;
  kpiTotalUnits.textContent = Number(metrics.total_units_in_stock || 0).toLocaleString();
  kpiAutoCommitted.textContent = metrics.auto_committed_count || 0;
  kpiFlaggedPending.textContent = metrics.flagged_pending_count || 0;

  if (metrics.flagged_pending_count > 0) {
    hitlPendingPill.textContent = `${metrics.flagged_pending_count} PENDING`;
    hitlPendingPill.classList.remove('hidden');
  } else {
    hitlPendingPill.classList.add('hidden');
  }
}

// Render Master Inventory Catalog Table
function renderInventoryTable(items) {
  if (!items || items.length === 0) {
    inventoryTableBody.innerHTML = `<tr><td colspan="5" class="px-4 py-6 text-center text-slate-500">No catalog items found.</td></tr>`;
    return;
  }

  inventoryTableBody.innerHTML = items.map(item => {
    const valuation = Number(item.current_stock * item.avg_unit_cost).toFixed(2);
    return `
      <tr class="hover:bg-slate-900/60 transition">
        <td class="px-4 py-3 font-semibold text-slate-200 mono">${escapeHtml(item.sku)}</td>
        <td class="px-4 py-3 text-slate-300 font-medium">${escapeHtml(item.name)}</td>
        <td class="px-4 py-3 text-right">
          <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-bold mono ${item.current_stock > 30 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'}">
            ${item.current_stock}
          </span>
        </td>
        <td class="px-4 py-3 text-right mono text-slate-300">$${Number(item.avg_unit_cost).toFixed(2)}</td>
        <td class="px-4 py-3 text-right mono text-slate-100 font-bold">$${valuation}</td>
      </tr>
    `;
  }).join('');
}

// Render Immutable Audit Ledger & HITL Action Queue
function renderLedgerTable(entries) {
  if (!entries || entries.length === 0) {
    ledgerTableBody.innerHTML = `<tr><td colspan="7" class="px-4 py-6 text-center text-slate-500">No ledger transactions recorded yet.</td></tr>`;
    return;
  }

  ledgerTableBody.innerHTML = entries.map(entry => {
    let statusBadge = '';
    let actionButtons = '-';

    if (entry.status === 'AUTO_COMMITTED') {
      statusBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/15 border border-emerald-500/30 text-emerald-400">AUTO-COMMITTED</span>`;
    } else if (entry.status === 'FLAGGED_FOR_REVIEW') {
      statusBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/15 border border-amber-500/30 text-amber-400 animate-pulse">QUARANTINED</span>`;
      actionButtons = `
        <div class="flex items-center justify-center gap-1.5">
          <button onclick="resolveLedgerAnomaly(${entry.id}, 'approve')" class="px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-[10px] transition shadow">
            Approve
          </button>
          <button onclick="resolveLedgerAnomaly(${entry.id}, 'reject')" class="px-2 py-1 rounded bg-rose-600 hover:bg-rose-500 text-white font-medium text-[10px] transition shadow">
            Reject
          </button>
        </div>
      `;
    } else if (entry.status === 'APPROVED_OVERRIDE') {
      statusBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-blue-500/15 border border-blue-500/30 text-blue-400">OVERRIDE APPROVED</span>`;
    } else if (entry.status === 'REJECTED') {
      statusBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/15 border border-rose-500/30 text-rose-400">REJECTED</span>`;
    }

    return `
      <tr class="hover:bg-slate-900/60 transition ${entry.status === 'FLAGGED_FOR_REVIEW' ? 'bg-amber-950/20' : ''}">
        <td class="px-3 py-3 mono text-slate-500">#${entry.id}</td>
        <td class="px-3 py-3 mono text-slate-300 font-medium">${escapeHtml(entry.invoice_number)}</td>
        <td class="px-3 py-3 mono text-slate-200">${escapeHtml(entry.sku)}</td>
        <td class="px-3 py-3 text-right mono text-slate-300 font-semibold">+${entry.quantity_added}</td>
        <td class="px-3 py-3 text-right mono text-slate-300">$${Number(entry.unit_price).toFixed(2)}</td>
        <td class="px-3 py-3 text-center">${statusBadge}</td>
        <td class="px-3 py-3 text-center">${actionButtons}</td>
      </tr>
    `;
  }).join('');
}

// Render Live Guardrail Inspector Card
function renderInspectionResult(result) {
  const invoice = result.invoice;
  const isAuto = result.status === 'AUTO_COMMITTED';

  inspectStatusBadge.innerHTML = isAuto
    ? `<span class="px-2 py-0.5 rounded text-xs font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">AUTO_COMMITTED</span>`
    : `<span class="px-2 py-0.5 rounded text-xs font-bold bg-amber-500/20 text-amber-400 border border-amber-500/30">FLAGGED_FOR_REVIEW</span>`;

  const confidencePct = Math.round((invoice.extraction_confidence || 0) * 100);

  let anomaliesHtml = '';
  if (result.anomalies && result.anomalies.length > 0) {
    anomaliesHtml = `
      <div class="p-3 rounded-lg bg-rose-950/40 border border-rose-800/60 space-y-1.5">
        <p class="text-xs font-bold text-rose-400 flex items-center gap-1.5">
          <i data-lucide="alert-octagon" class="w-4 h-4"></i>
          Price Variance & SKU Anomalies Detected:
        </p>
        <ul class="text-[11px] text-rose-200/90 space-y-1 pl-5 list-disc">
          ${result.anomalies.map(a => `
            <li>
              <span class="mono font-semibold">${escapeHtml(a.sku)}</span>:
              Billed at <span class="mono font-bold">$${a.extracted_unit_price.toFixed(2)}</span> vs Catalog <span class="mono">$${a.historical_avg_price.toFixed(2)}</span>
              ${a.deviation_pct ? `(+<span class="font-bold">${a.deviation_pct}%</span>)` : ''} &mdash; ${escapeHtml(a.reason)}
            </li>
          `).join('')}
        </ul>
      </div>
    `;
  }

  inspectContent.innerHTML = `
    <!-- Top Metadata Grid -->
    <div class="grid grid-cols-2 gap-2 text-xs p-3 rounded-lg bg-slate-950/60 border border-slate-800">
      <div>
        <p class="text-[10px] uppercase text-slate-500">Supplier Name</p>
        <p class="font-semibold text-slate-200 truncate">${escapeHtml(invoice.supplier_name)}</p>
      </div>
      <div>
        <p class="text-[10px] uppercase text-slate-500">Invoice Number</p>
        <p class="mono font-semibold text-slate-200">${escapeHtml(invoice.invoice_number)}</p>
      </div>
      <div>
        <p class="text-[10px] uppercase text-slate-500">Invoice Date</p>
        <p class="mono text-slate-300">${escapeHtml(String(invoice.invoice_date))}</p>
      </div>
      <div>
        <p class="text-[10px] uppercase text-slate-500">Total Billed</p>
        <p class="mono font-bold text-blue-400">$${Number(invoice.total_amount).toFixed(2)} ${invoice.currency}</p>
      </div>
    </div>

    <!-- Confidence & Math Guardrail Badges -->
    <div class="flex items-center justify-between text-xs p-2.5 rounded-lg bg-slate-950/40 border border-slate-800">
      <div class="flex items-center gap-2">
        <span class="text-slate-400 text-[11px]">Vision Confidence:</span>
        <div class="w-20 bg-slate-800 rounded-full h-2 overflow-hidden">
          <div class="bg-blue-500 h-2 rounded-full" style="width: ${confidencePct}%"></div>
        </div>
        <span class="mono font-bold text-slate-200 text-[11px]">${confidencePct}%</span>
      </div>
      <div class="flex items-center gap-1.5 text-emerald-400 font-semibold text-[11px]">
        <i data-lucide="check-check" class="w-4 h-4"></i>
        <span>Pydantic v2 Math: VERIFIED</span>
      </div>
    </div>

    <!-- Anomalies Box (if any) -->
    ${anomaliesHtml}

    <!-- Line Items Table -->
    <div class="border border-slate-800 rounded-lg overflow-hidden">
      <table class="w-full text-left text-[11px]">
        <thead class="bg-slate-950/80 text-slate-400 uppercase text-[9px] border-b border-slate-800">
          <tr>
            <th class="px-2.5 py-1.5">SKU</th>
            <th class="px-2.5 py-1.5 text-right">Qty</th>
            <th class="px-2.5 py-1.5 text-right">Unit Price</th>
            <th class="px-2.5 py-1.5 text-right">Total</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-800/60">
          ${invoice.items.map(i => `
            <tr>
              <td class="px-2.5 py-1.5 mono font-medium text-slate-300">${escapeHtml(i.sku)}</td>
              <td class="px-2.5 py-1.5 text-right mono text-slate-400">${i.quantity}</td>
              <td class="px-2.5 py-1.5 text-right mono text-slate-400">$${Number(i.unit_price).toFixed(2)}</td>
              <td class="px-2.5 py-1.5 text-right mono font-semibold text-slate-200">$${Number(i.line_total).toFixed(2)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;

  if (window.lucide) {
    window.lucide.createIcons();
  }
}

// Handle File Drag and Drop & Selection
dropzone.addEventListener('click', () => fileInput.click());

dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.classList.add('border-blue-500', 'bg-slate-900');
});

dropzone.addEventListener('dragleave', () => {
  dropzone.classList.remove('border-blue-500', 'bg-slate-900');
});

dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('border-blue-500', 'bg-slate-900');
  if (e.dataTransfer.files && e.dataTransfer.files[0]) {
    selectFile(e.dataTransfer.files[0]);
  }
});

fileInput.addEventListener('change', (e) => {
  if (e.target.files && e.target.files[0]) {
    selectFile(e.target.files[0]);
  }
});

function selectFile(file) {
  currentSelectedFile = file;
  selectedFilename.textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
}

// Upload & Audit Button Handler
uploadButton.addEventListener('click', async () => {
  if (!currentSelectedFile) {
    showToast('Please select or drop an invoice file first.', 'warning');
    return;
  }
  await handleFileUpload(currentSelectedFile);
});

async function handleFileUpload(file) {
  setLoading(true);
  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/v1/invoices/ingest', {
      method: 'POST',
      body: formData
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Upload failed with HTTP ${res.status}`);
    }

    const result = await res.json();
    renderInspectionResult(result);

    if (result.status === 'AUTO_COMMITTED') {
      showToast(`Invoice ${result.invoice.invoice_number} auto-committed! Stock updated.`, 'success');
    } else {
      showToast(`Invoice ${result.invoice.invoice_number} quarantined for review!`, 'warning');
    }

    await fetchDashboardState();
  } catch (err) {
    console.error('Invoice upload error:', err);
    showToast(`Extraction error: ${err.message}`, 'error');
  } finally {
    setLoading(false);
  }
}

// Run 1-Click Simulation
async function runSimulation(scenario) {
  setLoading(true);
  try {
    const res = await fetch(`/api/v1/invoices/simulate/${scenario}`, {
      method: 'POST'
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Simulation failed with HTTP ${res.status}`);
    }

    const result = await res.json();
    renderInspectionResult(result);

    if (result.status === 'AUTO_COMMITTED') {
      showToast(`Simulated clean invoice: Auto-committed +10 units!`, 'success');
    } else {
      showToast(`Simulated ${scenario}: Quarantined into HITL review queue!`, 'warning');
    }

    await fetchDashboardState();
  } catch (err) {
    console.error('Simulation error:', err);
    showToast(`Simulation error: ${err.message}`, 'error');
  } finally {
    setLoading(false);
  }
}

// Human-in-the-Loop Override Approval / Rejection
async function resolveLedgerAnomaly(ledgerId, action) {
  try {
    const res = await fetch(`/api/v1/ledger/${ledgerId}/${action}`, {
      method: 'POST'
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Failed to ${action} ledger entry`);
    }

    const actionResp = await res.json();
    if (action === 'approve') {
      showToast(`Entry #${ledgerId} approved! Catalog updated to ${actionResp.updated_stock} units.`, 'success');
    } else {
      showToast(`Entry #${ledgerId} rejected. Catalog stock preserved.`, 'info');
    }

    await fetchDashboardState();
  } catch (err) {
    console.error(`Error resolving ledger entry ${ledgerId}:`, err);
    showToast(`Action failed: ${err.message}`, 'error');
  }
}

// Wire Simulator Buttons
btnSimValid.addEventListener('click', () => runSimulation('valid'));
btnSimAnomaly.addEventListener('click', () => runSimulation('anomaly'));
btnSimUnknown.addEventListener('click', () => runSimulation('unrecognized_sku'));
btnRefresh.addEventListener('click', () => {
  fetchDashboardState();
  showToast('Telemetry refreshed from SQLite.', 'info');
});

function setLoading(isLoading) {
  if (isLoading) {
    uploadButton.disabled = true;
    uploadButtonText.textContent = 'Extracting via Gemini Vision...';
    uploadSpinner.classList.remove('hidden');
  } else {
    uploadButton.disabled = false;
    uploadButtonText.textContent = 'Extract & Audit Invoice';
    uploadSpinner.classList.add('hidden');
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Initial Telemetry Load on Page Load
document.addEventListener('DOMContentLoaded', () => {
  fetchDashboardState();
});
