# Project Specification: StockOps Autonomous Inventory & Supply Chain Agent (`stockops-ai-agent`)

## 1. Role & Objective
You are an autonomous Staff AI Engineer. Your task is to build, test, document, and containerize a production-grade GitHub repository named `stockops-ai-agent`.

The system ingests supplier invoices (PNG/JPEG/PDF), extracts structured line items using the Gemini API (`google-genai`), enforces deterministic arithmetic guardrails via Pydantic v2, cross-checks unit prices against a historical SQLite inventory catalog, auto-commits valid stock, and dispatches webhook alerts (n8n/Slack compatible) when price spikes or low-confidence extractions occur.

**Execution Rule:** Execute this specification phase by phase (Phases 1 through 6). Do not move to the next phase until the current phase's verification command passes with zero errors.

---

## 2. Strict Technical Constraints (DO NOT VIOLATE)
1. **Google GenAI SDK:** Use ONLY the current `google-genai` package (`from google import genai` and `from google.genai import types`). NEVER use the deprecated `google-generativeai` package.
2. **Pydantic v2 Syntax:** Use ONLY Pydantic v2 methods (`model_validate_json`, `model_dump`, `@model_validator(mode="after")`, `ConfigDict`). NEVER use Pydantic v1 `@validator` or `.dict()`.
3. **Offline Testability:** All unit and integration tests in `pytest` MUST pass without requiring a live `GEMINI_API_KEY` by mocking the Gemini client for deterministic pipeline testing, while supporting live API calls when the key is present.
4. **Synthetic Sample Generation:** Include a script using `Pillow` (`PIL`) that generates two realistic synthetic invoice PNGs in `sample_data/` so any developer cloning the repo can test the full vision pipeline immediately.

---

## 3. Target Repository Architecture

~~~text
stockops-ai-agent/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI application & endpoints
│   ├── schemas.py               # Pydantic v2 models & arithmetic validators
│   ├── agent.py                 # Gemini multimodal extraction & self-correction loop
│   ├── database.py              # SQLite inventory catalog & ledger operations
│   └── notifier.py              # Async httpx webhook dispatcher
├── scripts/
│   └── generate_samples.py      # Pillow script to render synthetic test invoices
├── sample_data/
│   ├── valid_invoice.png        # Generated clean invoice matching catalog prices
│   └── anomaly_invoice.png      # Generated invoice with a >15% unit price spike
├── evals/
│   ├── __init__.py
│   ├── test_schemas.py          # Math & schema guardrail tests
│   ├── test_pipeline.py         # End-to-end FastAPI + SQLite + Mocked LLM tests
│   └── run_benchmark.py         # CLI evaluation table generator for README
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
~~~

---

## 4. Phased Execution Plan & Verification Gates

### PHASE 1: Scaffolding & Dependencies
1. Create the directory structure (`app/`, `scripts/`, `sample_data/`, `evals/`, `data/`).
2. Create `.gitignore` (ignore `.env`, `__pycache__/`, `.pytest_cache/`, `data/*.db`, `.venv/`).
3. Create `.env.example` with:
   - `GEMINI_API_KEY=your_api_key_here`
   - `GEMINI_MODEL=gemini-2.5-flash`
   - `PRICE_SPIKE_THRESHOLD_PCT=15.0`
   - `MIN_CONFIDENCE_SCORE=0.85`
   - `ALERT_WEBHOOK_URL=http://n8n:5678/webhook/invoice-anomaly`
4. Create `requirements.txt` with:
   - `fastapi>=0.115.0`
   - `uvicorn[standard]>=0.34.0`
   - `pydantic>=2.10.0`
   - `google-genai>=1.2.0`
   - `python-multipart>=0.0.20`
   - `httpx>=0.28.0`
   - `pillow>=11.0.0`
   - `pytest>=8.3.0`
   - `pytest-asyncio>=0.25.0`
5. **Verification Gate 1:** Install dependencies in the active environment and verify all core packages import cleanly without errors.

---

### PHASE 2: Pydantic v2 Schemas & Deterministic Math Guardrails (TDD)
1. Implement `app/schemas.py`:
   - `InvoiceLineItem`:
     - Fields: `sku` (`str`), `description` (`str`), `quantity` (`int`, `gt=0`), `unit_price` (`float`, `ge=0.0`), `line_total` (`float`, `ge=0.0`).
     - Add `@model_validator(mode="after")` verifying `abs(round(self.quantity * self.unit_price, 2) - round(self.line_total, 2)) <= 0.05`. Raise a descriptive `ValueError` containing the SKU and expected vs. stated math on mismatch.
   - `ExtractedInvoice`:
     - Fields: `supplier_name` (`str`), `invoice_number` (`str`), `invoice_date` (`date`), `currency` (`str`, default `"USD"`), `items` (`List[InvoiceLineItem]`, `min_length=1`), `subtotal` (`float`, `ge=0.0`), `tax_amount` (`float`, default `0.0`, `ge=0.0`), `total_amount` (`float`, `ge=0.0`), `extraction_confidence` (`float`, `ge=0.0`, `le=1.0`).
     - Add `@model_validator(mode="after")` verifying that the sum of `item.line_total` matches `subtotal` within `0.10` tolerance, and `subtotal + tax_amount` matches `total_amount` within `0.10` tolerance.
   - `PriceAnomalyFlag`:
     - Fields: `sku` (`str`), `extracted_unit_price` (`float`), `historical_avg_price` (`float`), `deviation_pct` (`float`), `reason` (`str`).
   - `ProcessingResult`:
     - Fields: `status` (`str`: `"AUTO_COMMITTED"` or `"FLAGGED_FOR_REVIEW"`), `invoice` (`ExtractedInvoice`), `anomalies` (`List[PriceAnomalyFlag]` = `[]`), `review_reason` (`Optional[str]` = `None`).
2. Implement `evals/test_schemas.py`:
   - Test 1: Valid invoice payload passes validation cleanly.
   - Test 2: Hallucinated line-item multiplication raises `ValidationError` matching `"Line math mismatch"`.
   - Test 3: Mismatched invoice subtotal raises `ValidationError` matching `"Subtotal mismatch"`.
3. **Verification Gate 2:** Run `pytest evals/test_schemas.py` and confirm 100% pass rate before proceeding.

---

### PHASE 3: SQLite Inventory Ledger & Historical Price Engine
1. Implement `app/database.py`:
   - Read database path dynamically from `os.getenv("SQLITE_DB_PATH", "data/inventory.db")` so tests can isolate temporary databases.
   - Create `init_db()` which creates two tables:
     - `inventory_catalog (sku TEXT PRIMARY KEY, name TEXT NOT NULL, current_stock INTEGER NOT NULL DEFAULT 0, avg_unit_cost REAL NOT NULL)`
     - `stock_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_number TEXT NOT NULL, sku TEXT NOT NULL, quantity_added INTEGER NOT NULL, unit_price REAL NOT NULL, status TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)`
   - Seed `inventory_catalog` (`INSERT OR IGNORE`) with 4 realistic electrical/lighting SKUs:
     - `("LED-PNL-6060-40W", "Backlit LED Panel 60x60cm 40W 6500K", 120, 14.50)`
     - `("FL-COB-100W-IP65", "Outdoor Floodlight 100W IP65 Aluminum", 45, 28.00)`
     - `("DRV-24V-150W-IP67", "Waterproof LED Driver 24V 150W", 80, 19.20)`
     - `("CBL-H07RN-F-3G1.5", "Heavy Duty Rubber Cable 3x1.5mm (100m Roll)", 15, 85.00)`
   - Implement `get_historical_unit_cost(sku: str) -> Optional[float]`.
   - Implement `commit_invoice_to_stock(invoice: ExtractedInvoice, status: str) -> None`:
     - Always insert an audit row into `stock_ledger`.
     - Only increment `current_stock` in `inventory_catalog` when `status == "AUTO_COMMITTED"`.
2. **Verification Gate 3:** Add database tests in `evals/test_pipeline.py` verifying catalog seeding, stock increments on `AUTO_COMMITTED`, and stock preservation when `FLAGGED_FOR_REVIEW`. Run `pytest`.

---

### PHASE 4: Multimodal Agent, Self-Correction Loop & Webhook Dispatcher
1. Implement `app/agent.py`:
   - `extract_invoice_from_bytes(file_bytes: bytes, mime_type: str, max_retries: int = 2) -> ExtractedInvoice`:
     - Uses `genai.Client(api_key=os.getenv("GEMINI_API_KEY"))`.
     - Calls `client.models.generate_content` with `response_mime_type="application/json"` and `response_schema=ExtractedInvoice`.
     - Parses response with `ExtractedInvoice.model_validate_json(response.text)`.
     - If `ValidationError` is raised, appends the validation traceback to the prompt and retries up to `max_retries` times so the LLM self-corrects arithmetic mistakes.
   - `evaluate_anomalies(invoice: ExtractedInvoice) -> Tuple[str, List[PriceAnomalyFlag], Optional[str]]`:
     - Flags if `invoice.extraction_confidence < MIN_CONFIDENCE_SCORE`.
     - Flags if any item `sku` is missing from `inventory_catalog`.
     - Flags if `((item.unit_price - historical_cost) / historical_cost) * 100 > PRICE_SPIKE_THRESHOLD_PCT`.
     - Returns `("FLAGGED_FOR_REVIEW", anomalies, review_reason)` if any flags exist, else `("AUTO_COMMITTED", [], None)`.
2. Implement `app/notifier.py`:
   - `trigger_anomaly_webhook(result: ProcessingResult) -> None`:
     - Sends an async POST request via `httpx.AsyncClient(timeout=10.0)` to `ALERT_WEBHOOK_URL` if configured. Logs warnings gracefully on network errors without crashing the request.
3. **Verification Gate 4:** Add unit tests in `evals/test_pipeline.py` mocking the Gemini client to verify:
   - Self-correction retry loop recovers when attempt 1 returns bad math and attempt 2 returns fixed math.
   - Price spike detection accurately flags a >15% price increase.
   - Run `pytest` and verify all tests pass.

---

### PHASE 5: FastAPI Application, Synthetic Invoices & Evaluation Benchmark
1. Implement `app/main.py`:
   - Use FastAPI `lifespan` context manager to call `init_db()`.
   - `GET /health` -> returns `{"status": "ok"}`.
   - `GET /api/v1/inventory` -> returns all rows from `inventory_catalog`.
   - `GET /api/v1/ledger` -> returns all rows from `stock_ledger`.
   - `POST /api/v1/invoices/ingest` -> accepts `UploadFile`, validates `content_type` (`image/png`, `image/jpeg`, `image/webp`, `application/pdf`), extracts invoice, evaluates anomalies, commits to database, triggers webhook if flagged, and returns `ProcessingResult`.
2. Implement `scripts/generate_samples.py`:
   - Uses `PIL.Image` and `PIL.ImageDraw` to generate two clean invoice PNG images in `sample_data/`:
     - `sample_data/valid_invoice.png`: Billed by "Lumiere Industrial Supply", Invoice `INV-2026-101`, 10x `LED-PNL-6060-40W` @ $14.50 = $145.00.
     - `sample_data/anomaly_invoice.png`: Billed by "Lumiere Industrial Supply", Invoice `INV-2026-102`, 5x `FL-COB-100W-IP65` @ $36.00 = $180.00 (+28.57% spike over $28.00 catalog price).
3. Implement `evals/run_benchmark.py`:
   - Executes 5 deterministic evaluation scenarios (Clean Invoice Auto-Commit, Math Hallucination Self-Correction, +28.57% Price Spike Detection, Unrecognized SKU Quarantine, Low OCR Confidence Gate) and prints a Markdown benchmark scorecard table to stdout.
4. **Verification Gate 5:**
   - Run `python scripts/generate_samples.py` and verify PNGs are created.
   - Add FastAPI `TestClient` integration tests to `evals/test_pipeline.py` and run `pytest`.
   - Run `python evals/run_benchmark.py` and capture the Markdown table output.

---

### PHASE 6: Dockerization & Portfolio-Grade README
1. Create `Dockerfile` using `python:3.12-slim`, installing `requirements.txt`, copying the repository files, and launching `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
2. Create `docker-compose.yml` with `stockops-agent` (port `8000:8000`) and `n8n` (`docker.n8n.io/n8nio/n8n:latest`, port `5678:5678`).
3. Create `README.md` with:
   - Badges for Python 3.12, FastAPI, Pydantic v2, Google Gemini API, and Docker.
   - Executive overview and Mermaid architecture diagram showing the Ingress -> Gemini Vision -> Pydantic Math Guardrail (Self-Healing Loop) -> SQLite Price Audit -> Webhook Alert pipeline.
   - One-command setup instructions (`docker compose up --build`) and `curl` test commands using `sample_data/valid_invoice.png` and `sample_data/anomaly_invoice.png`.
   - The exact Markdown table generated by `python evals/run_benchmark.py`.
4. **Final Verification Gate:** Run `pytest -v` across the entire repository and ensure 100% of tests pass with zero errors.