# UI Expansion Specification: StockOps Live Ops Dashboard & Human-in-the-Loop Queue (`stockops-ai-agent`)

## 1. Role & Objective

You are an autonomous Staff Full-Stack AI Engineer working inside the existing `stockops-ai-agent` repository (where the core backend, Pydantic v2 math guardrails, SQLite ledger, and 22 unit/integration tests are already implemented).

Your objective is to build, test, and integrate a production-grade **Live Operations Web Dashboard UI** served directly by FastAPI at `http://localhost:8000/` alongside **Human-in-the-Loop (HITL) Quarantine Resolution APIs**.

**Execution Rule:** Execute this specification phase by phase (UI Phases 1 through 6). Do not move to the next phase until the current phase's `pytest` verification command passes with zero errors and all original 22 tests remain green.

---

## 2. Strict Technical Constraints (DO NOT VIOLATE)

1. **Zero-Node Single-Command Startup:** Serve the frontend UI directly from FastAPI (`GET /` and `/static/*`) so running `uvicorn app.main:app --reload --port 8000` launches both the REST API and the full interactive Web UI on port `8000` without requiring a separate Node.js build server.
2. **Smart Demo Fallback Mode:** If `GEMINI_API_KEY` is missing or set to the placeholder `your_api_key_here`, the 1-click sample simulator and sample invoice uploads (`valid_invoice.png` and `anomaly_invoice.png`) MUST gracefully fall back to deterministic sample payloads so a user or recruiter testing the UI locally without an API key experiences the full pipeline without a `500` crash. When a valid `GEMINI_API_KEY` is configured, use the live Gemini Vision API (`google-genai`).
3. **Preserve Existing Tests:** Do not break any of the existing 22 tests in `evals/test_schemas.py` and `evals/test_pipeline.py`. Add new tests for the UI routes, KPI metrics, and HITL approve/reject endpoints.
4. **Silence Pytest Warnings:** Configure `pyproject.toml` so `pytest -v` runs with zero warnings.

---

## 3. Target Repository Additions

```
stockops-ai-agent/
├── app/
│   ├── main.py                  # Add UI mount, /api/v1/metrics, demo trigger, and HITL approve/reject endpoints
│   ├── database.py              # Add get_dashboard_metrics(), approve_ledger_entry(), reject_ledger_entry()
│   ├── schemas.py               # Add DashboardMetrics and LedgerActionResponse models
│   └── static/
│       ├── index.html           # Industrial Dark-Mode Live Ops Dashboard (Tailwind CSS + Lucide Icons)
│       └── app.js               # Async state manager, drag-and-drop uploader, live ledger & HITL controls
├── evals/
│   └── test_ui_and_hitl.py      # New integration test suite for UI serving, metrics, and HITL workflows
├── pyproject.toml               # Pytest configuration to silence asyncio loop-scope warnings
└── README.md                    # Updated with Web Dashboard documentation and UI routes
```

---

## 4. Phased UI Execution Plan & Verification Gates

### UI PHASE 1: Pytest Configuration & Database Human-in-the-Loop (HITL) Engine

1. Create `pyproject.toml` in the project root to silence `pytest-asyncio` warnings:
   ```toml
   [tool.pytest.ini_options]
   minversion = "8.0"
   testpaths = ["evals"]
   asyncio_mode = "auto"
   asyncio_default_fixture_loop_scope = "function"
   ```
2. Update `app/schemas.py` with new Pydantic v2 response models:
   * `DashboardMetrics`:
     * `total_skus` (`int`)
     * `total_units_in_stock` (`int`)
     * `total_inventory_value` (`float`)
     * `auto_committed_count` (`int`)
     * `flagged_pending_count` (`int`)
     * `approved_override_count` (`int`)
     * `rejected_count` (`int`)
   * `LedgerActionResponse`:
     * `ledger_id` (`int`)
     * `invoice_number` (`str`)
     * `sku` (`str`)
     * `previous_status` (`str`)
     * `new_status` (`str`)
     * `updated_stock` (`int`)
3. Update `app/database.py` with three new functions:
   * `get_dashboard_metrics() -> dict`: Queries `inventory_catalog` and `stock_ledger` to compute `DashboardMetrics` fields (rounding `total_inventory_value` to 2 decimal places).
   * `approve_ledger_entry(ledger_id: int) -> dict`:
     * Fetches the row from `stock_ledger` by `id`. Raises `KeyError` if not found, or `ValueError` if `status != "FLAGGED_FOR_REVIEW"`.
     * Updates `stock_ledger.status` to `"APPROVED_OVERRIDE"`.
     * If the SKU exists in `inventory_catalog`, increments `current_stock` by `quantity_added`. If the SKU was previously unrecognized, inserts it into `inventory_catalog` with `name = f"Imported SKU ({sku})"`, `current_stock = quantity_added`, and `avg_unit_cost = unit_price`.
     * Returns dictionary matching `LedgerActionResponse`.
   * `reject_ledger_entry(ledger_id: int) -> dict`:
     * Fetches the row from `stock_ledger` by `id`. Raises `KeyError` if not found, or `ValueError` if `status != "FLAGGED_FOR_REVIEW"`.
     * Updates `stock_ledger.status` to `"REJECTED"` without modifying `inventory_catalog.current_stock`.
     * Returns dictionary matching `LedgerActionResponse`.
4. **Verification Gate 1:** Create `evals/test_ui_and_hitl.py` with unit tests verifying `get_dashboard_metrics`, `approve_ledger_entry` (confirming stock increments upon approval), and `reject_ledger_entry` (confirming stock remains unchanged). Run `pytest -v`.

---

### UI PHASE 2: FastAPI HITL Endpoints, KPI Route & 1-Click Sample Simulator

1. Update `app/main.py` with the following endpoints:
   * `GET /api/v1/metrics` -> returns `DashboardMetrics` via `get_dashboard_metrics()`.
   * `POST /api/v1/ledger/{ledger_id}/approve` -> calls `approve_ledger_entry(ledger_id)`. Returns `404` on `KeyError`, `400` on `ValueError`, or `200` with `LedgerActionResponse`.
   * `POST /api/v1/ledger/{ledger_id}/reject` -> calls `reject_ledger_entry(ledger_id)`. Returns `404` on `KeyError`, `400` on `ValueError`, or `200` with `LedgerActionResponse`.
   * `POST /api/v1/invoices/simulate/{scenario}`:
     * Accepts `scenario` in `("valid", "anomaly", "unrecognized_sku")`.
     * Runs a deterministic simulation through `evaluate_anomalies()`, `commit_invoice_to_stock()`, and `trigger_anomaly_webhook()` so users can test the full pipeline from the Web UI with a single click—even before configuring a live `GEMINI_API_KEY`:
       * `"valid"`: `INV-2026-101`, `Lumiere Industrial Supply`, 10x `LED-PNL-6060-40W` @ $\$14.50 = \$145.00$, confidence $0.98$.
       * `"anomaly"`: `INV-2026-102`, `Lumiere Industrial Supply`, 5x `FL-COB-100W-IP65` @ $\$36.00 = \$180.00$ ($+28.57\%$ price spike over $\$28.00$), confidence $0.95$.
       * `"unrecognized_sku"`: `INV-2026-103`, `Atlas Power Components`, 4x `XFMR-DIN-24V-200W` @ $\$42.00 = \$168.00$ (unmapped catalog SKU), confidence $0.94$.
   * Update `POST /api/v1/invoices/ingest`:
     * If `GEMINI_API_KEY` is missing or equals `"your_api_key_here"` AND the uploaded filename matches `valid_invoice.png` or `anomaly_invoice.png`, automatically use the corresponding deterministic `ExtractedInvoice` payload so local file-upload testing works seamlessly offline.
2. **Verification Gate 2:** Add API integration tests in `evals/test_ui_and_hitl.py` testing `/api/v1/metrics`, `/api/v1/invoices/simulate/valid`, `/api/v1/invoices/simulate/anomaly`, `/api/v1/ledger/{id}/approve`, and `/api/v1/ledger/{id}/reject`. Run `pytest -v`.

---

### UI PHASE 3: Industrial Dark-Mode Live Ops Dashboard (`app/static/index.html`)

1. Create `app/static/index.html` using Tailwind CSS CDN (`https://cdn.tailwindcss.com`) and Lucide Icons (`https://unpkg.com/lucide@latest`) with a clean, high-contrast slate/emerald/amber/rose industrial command-center aesthetic:
   * **Header Bar:**
     * Project title (`StockOps AI // Autonomous Supply Chain & Inventory Guard`), live API health indicator badge (`ONLINE`), and a `"Refresh Telemetry"` button.
   * **Top KPI Telemetry Ribbon (4 Cards):**
     * **Total Catalog Value:** Formatted currency (`#kpi-total-value`) + total SKU count (`#kpi-total-skus`).
     * **Total Units in Stock:** Live sum of warehouse units (`#kpi-total-units`).
     * **Auto-Committed Invoices:** Count of clean ledger transactions (`#kpi-auto-committed`) with emerald badge.
     * **Quarantined Anomalies:** Count of `FLAGGED_FOR_REVIEW` items (`#kpi-flagged-pending`) with amber/rose alert indicator.
   * **Left Column — Multimodal Invoice Ingestion & 1-Click Simulator:**
     * **Drag-and-Drop Upload Zone (`#dropzone`):** Supports clicking or dragging `PNG`, `JPEG`, `WEBP`, or `PDF` invoices, showing selected filename and an `"Extract & Audit Invoice"` submit button.
     * **Instant Demo Simulator Bar:** Three buttons for zero-setup testing:
       1. `"Simulate Clean Invoice (+10 Panels)"` -> triggers `POST /api/v1/invoices/simulate/valid`
       2. `"Simulate +28.5% Price Spike"` -> triggers `POST /api/v1/invoices/simulate/anomaly`
       3. `"Simulate Unknown SKU"` -> triggers `POST /api/v1/invoices/simulate/unrecognized_sku`
     * **Live Extraction & Guardrail Inspector Card (`#inspection-panel`):**
       * Shows the most recently processed invoice's metadata (`supplier_name`, `invoice_number`, `invoice_date`, `extraction_confidence` progress bar, Pydantic Math Check badge `VERIFIED`, and status pill `AUTO_COMMITTED` vs `FLAGGED_FOR_REVIEW`).
       * Lists extracted line items and renders high-visibility alert boxes for every `PriceAnomalyFlag` (`sku`, `extracted_unit_price` vs `historical_avg_price`, and `+deviation_pct%`).
   * **Right Column — Master Inventory Catalog & Audit Ledger Queue:**
     * **Master Inventory Catalog Table (`#inventory-table-body`):** Displays `SKU`, `Product Name`, `Current Stock` (with visual badge), `Historical Avg Cost`, and `Total Line Valuation`.
     * **Immutable Audit Ledger & Quarantine Queue (`#ledger-table-body`):** Displays `ID`, `Invoice #`, `SKU`, `Qty`, `Unit Price`, `Status Badge`, and **Human-in-the-Loop Action Buttons**:
       * Whenever a row has `status === "FLAGGED_FOR_REVIEW"`, render interactive **"Approve"** (emerald) and **"Reject"** (rose) buttons directly inside the row.
2. Mount `app/static` in `app/main.py` and serve `app/static/index.html` at `GET /` using `FileResponse`.
3. **Verification Gate 3:** Add a test in `evals/test_ui_and_hitl.py` asserting `GET /` returns HTTP `200` and contains `"StockOps AI"`. Run `pytest -v`.

---

### UI PHASE 4: Interactive Frontend Controller (`app/static/app.js`)

1. Implement `app/static/app.js` with clean, modular `async/await` logic:
   * `fetchDashboardState()`: Calls `/api/v1/metrics`, `/api/v1/inventory`, and `/api/v1/ledger` in parallel (`Promise.all`) and updates the DOM tables and KPI cards without page reloads.
   * `handleFileUpload(file)`: Sends `FormData` to `POST /api/v1/invoices/ingest`, displays a loading spinner during extraction, renders the returned `ProcessingResult` in the Guardrail Inspector Card, shows a toast notification, and refreshes the dashboard state.
   * `runSimulation(scenario)`: Calls `POST /api/v1/invoices/simulate/{scenario}`, renders the `ProcessingResult` in the Guardrail Inspector Card, and refreshes all tables.
   * `resolveLedgerAnomaly(ledgerId, action)`: Calls `POST /api/v1/ledger/{ledgerId}/{action}` (`"approve"` or `"reject"`), shows a confirmation toast, and immediately refreshes KPIs, inventory stock levels, and ledger status badges.
2. **Verification Gate 4:** Verify `GET /static/app.js` returns HTTP `200` via FastAPI `TestClient`. Run `pytest -v`.

---

### UI PHASE 5: End-to-End Integration & Benchmark Verification

1. In `evals/test_ui_and_hitl.py`, write a comprehensive end-to-end lifecycle test (`test_full_ui_hitl_quarantine_and_approval_lifecycle`) that:
   * Calls `POST /api/v1/invoices/simulate/anomaly` and verifies `FL-COB-100W-IP65` is `FLAGGED_FOR_REVIEW` and its catalog stock remains at `45`.
   * Checks `GET /api/v1/metrics` and verifies `flagged_pending_count == 1`.
   * Calls `POST /api/v1/ledger/{ledger_id}/approve` on the quarantined entry.
   * Verifies `FL-COB-100W-IP65` stock in `GET /api/v1/inventory` is now incremented from `45` to `50`, and `flagged_pending_count == 0` while `approved_override_count == 1`.
2. **Verification Gate 5:** Run `pytest -v` and `python evals/run_benchmark.py` and confirm 100% pass rate with zero warnings.

---

### UI PHASE 6: Docker & README Documentation Update

1. Update `Dockerfile` to ensure `app/static` is copied into the container image.
2. Update `README.md` to showcase:
   * How to launch and access the **Live Ops Web Dashboard** at `http://localhost:8000/`.
   * The new Human-in-the-Loop API endpoints (`GET /api/v1/metrics`, `POST /api/v1/ invoices/simulate/{scenario}`, `POST /api/v1/ledger/{id}/approve`, `POST /api/v1/ledger/{id}/reject`).
   * Updated test count in the verification section.
3. **Final Verification Gate:** Run `pytest -v` across the entire test suite and ensure 100% of tests pass with zero warnings.