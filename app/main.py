"""FastAPI application for StockOps Autonomous Inventory & Supply Chain Agent."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.agent import evaluate_anomalies, extract_invoice_from_bytes
from app.database import (
    approve_ledger_entry,
    commit_invoice_to_stock,
    get_dashboard_metrics,
    get_inventory_catalog,
    get_stock_ledger,
    init_db,
    reject_ledger_entry,
)
from app.notifier import trigger_anomaly_webhook
from app.schemas import (
    DashboardMetrics,
    ExtractedInvoice,
    InvoiceLineItem,
    LedgerActionResponse,
    ProcessingResult,
)

logger = logging.getLogger("stockops")
logging.basicConfig(level=logging.INFO)

ALLOWED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager initializing database tables and catalog seeds."""
    logger.info("Initializing StockOps SQLite database and catalog...")
    init_db()
    yield
    logger.info("StockOps application shutting down.")


app = FastAPI(
    title="StockOps AI Agent",
    description="Autonomous Multimodal Inventory & Supply Chain Ingestion Agent with Deterministic Guardrails",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", summary="Live Ops Dashboard Web UI", include_in_schema=False)
def serve_dashboard():
    """Serve the single-page Live Operations Web Dashboard."""
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"status": "StockOps AI Agent Online. Static dashboard not yet installed."}



def get_demo_fallback_invoice(filename: str) -> Optional[ExtractedInvoice]:
    """Provide deterministic invoice payload for demo testing when GEMINI_API_KEY is not configured."""
    fn = (filename or "").lower()
    if "valid" in fn:
        return ExtractedInvoice(
            supplier_name="Lumiere Industrial Supply",
            invoice_number="INV-2026-101",
            invoice_date=date(2026, 10, 24),
            currency="USD",
            items=[
                InvoiceLineItem(
                    sku="LED-PNL-6060-40W",
                    description="Backlit LED Panel 60x60cm 40W 6500K",
                    quantity=10,
                    unit_price=14.50,
                    line_total=145.00,
                )
            ],
            subtotal=145.00,
            tax_amount=0.00,
            total_amount=145.00,
            extraction_confidence=0.98,
        )
    elif "anomaly" in fn:
        return ExtractedInvoice(
            supplier_name="Lumiere Industrial Supply",
            invoice_number="INV-2026-102",
            invoice_date=date(2026, 10, 24),
            currency="USD",
            items=[
                InvoiceLineItem(
                    sku="FL-COB-100W-IP65",
                    description="Outdoor Floodlight 100W IP65 Aluminum",
                    quantity=5,
                    unit_price=36.00,
                    line_total=180.00,
                )
            ],
            subtotal=180.00,
            tax_amount=0.00,
            total_amount=180.00,
            extraction_confidence=0.95,
        )
    return None


@app.get("/health", summary="Health Check")
def health_check() -> Dict[str, str]:
    """Return health status of the application."""
    return {"status": "ok"}


@app.get("/api/v1/metrics", response_model=DashboardMetrics, summary="Get Live KPI Dashboard Metrics")
def get_metrics() -> DashboardMetrics:
    """Return aggregated warehouse inventory valuation and ledger status metrics."""
    metrics_dict = get_dashboard_metrics()
    return DashboardMetrics.model_validate(metrics_dict)


@app.get("/api/v1/inventory", summary="Get Current Inventory Catalog")
def get_inventory() -> List[Dict[str, Any]]:
    """Return all stock records from inventory catalog."""
    return get_inventory_catalog()


@app.get("/api/v1/ledger", summary="Get Stock Ingestion Audit Ledger")
def get_ledger() -> List[Dict[str, Any]]:
    """Return all recorded stock ingestion transactions."""
    return get_stock_ledger()


@app.post(
    "/api/v1/ledger/{ledger_id}/approve",
    response_model=LedgerActionResponse,
    summary="Human-in-the-Loop Override Approval",
)
def approve_ledger(ledger_id: int) -> LedgerActionResponse:
    """Approve a quarantined invoice entry, increment catalog stock, and mark ledger as APPROVED_OVERRIDE."""
    try:
        action_result = approve_ledger_entry(ledger_id)
        return LedgerActionResponse.model_validate(action_result)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@app.post(
    "/api/v1/ledger/{ledger_id}/reject",
    response_model=LedgerActionResponse,
    summary="Human-in-the-Loop Rejection",
)
def reject_ledger(ledger_id: int) -> LedgerActionResponse:
    """Reject a quarantined invoice entry, preserving catalog stock, and mark ledger as REJECTED."""
    try:
        action_result = reject_ledger_entry(ledger_id)
        return LedgerActionResponse.model_validate(action_result)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@app.post(
    "/api/v1/invoices/simulate/{scenario}",
    response_model=ProcessingResult,
    summary="1-Click Instant Scenario Simulator",
)
async def simulate_invoice(scenario: str) -> ProcessingResult:
    """Execute a deterministic invoice simulation scenario for rapid testing and demonstrations."""
    scenario_clean = scenario.lower().strip()

    if scenario_clean == "valid":
        invoice = ExtractedInvoice(
            supplier_name="Lumiere Industrial Supply",
            invoice_number="INV-2026-101",
            invoice_date=date(2026, 10, 24),
            currency="USD",
            items=[
                InvoiceLineItem(
                    sku="LED-PNL-6060-40W",
                    description="Backlit LED Panel 60x60cm 40W 6500K",
                    quantity=10,
                    unit_price=14.50,
                    line_total=145.00,
                )
            ],
            subtotal=145.00,
            tax_amount=0.00,
            total_amount=145.00,
            extraction_confidence=0.98,
        )
    elif scenario_clean == "anomaly":
        invoice = ExtractedInvoice(
            supplier_name="Lumiere Industrial Supply",
            invoice_number="INV-2026-102",
            invoice_date=date(2026, 10, 24),
            currency="USD",
            items=[
                InvoiceLineItem(
                    sku="FL-COB-100W-IP65",
                    description="Outdoor Floodlight 100W IP65 Aluminum",
                    quantity=5,
                    unit_price=36.00,
                    line_total=180.00,
                )
            ],
            subtotal=180.00,
            tax_amount=0.00,
            total_amount=180.00,
            extraction_confidence=0.95,
        )
    elif scenario_clean == "unrecognized_sku":
        invoice = ExtractedInvoice(
            supplier_name="Atlas Power Components",
            invoice_number="INV-2026-103",
            invoice_date=date(2026, 10, 24),
            currency="USD",
            items=[
                InvoiceLineItem(
                    sku="XFMR-DIN-24V-200W",
                    description="DIN-Rail Step-Down Transformer 24V 200W",
                    quantity=4,
                    unit_price=42.00,
                    line_total=168.00,
                )
            ],
            subtotal=168.00,
            tax_amount=0.00,
            total_amount=168.00,
            extraction_confidence=0.94,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown simulation scenario '{scenario}'. Allowed: 'valid', 'anomaly', 'unrecognized_sku'",
        )

    # Cross-check against catalog baselines
    status_str, anomalies, review_reason = evaluate_anomalies(invoice)

    # Commit to database
    commit_invoice_to_stock(invoice, status=status_str)

    result = ProcessingResult(
        status=status_str,  # type: ignore[arg-type]
        invoice=invoice,
        anomalies=anomalies,
        review_reason=review_reason,
    )

    if status_str == "FLAGGED_FOR_REVIEW":
        await trigger_anomaly_webhook(result)

    return result


@app.post(
    "/api/v1/invoices/ingest",
    response_model=ProcessingResult,
    summary="Ingest and Audit Supplier Invoice",
)
async def ingest_invoice(file: UploadFile = File(...)) -> ProcessingResult:
    """Ingest supplier invoice (PNG/JPEG/WebP/PDF), extract line items via Gemini,

    validate math guardrails, check price variance against catalog, commit stock,
    and alert external webhook on anomalies.
    """
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported media type '{file.content_type}'. "
                f"Allowed types: {', '.join(sorted(ALLOWED_MIME_TYPES))}"
            ),
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    api_key = os.getenv("GEMINI_API_KEY", "")
    is_demo_mode = (not api_key) or (api_key.strip() in ("", "your_api_key_here"))

    extracted_invoice: Optional[ExtractedInvoice] = None

    # Smart Demo Fallback: If no API key configured and user uploaded a sample invoice, use deterministic payload
    if is_demo_mode:
        extracted_invoice = get_demo_fallback_invoice(file.filename or "")

    if extracted_invoice is None:
        try:
            # Live multimodal extraction with self-correcting arithmetic loop
            extracted_invoice = extract_invoice_from_bytes(
                file_bytes=file_bytes,
                mime_type=file.content_type,
            )
        except Exception as exc:
            logger.error("Failed to extract invoice: %s", str(exc), exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invoice extraction and guardrail validation failed: {str(exc)}",
            )

    # Cross-check against catalog baselines
    status_str, anomalies, review_reason = evaluate_anomalies(extracted_invoice)

    # Commit to database (audit ledger always, stock increment only if AUTO_COMMITTED)
    commit_invoice_to_stock(extracted_invoice, status=status_str)

    result = ProcessingResult(
        status=status_str,  # type: ignore[arg-type]
        invoice=extracted_invoice,
        anomalies=anomalies,
        review_reason=review_reason,
    )

    # Trigger alert webhook if quarantined
    if status_str == "FLAGGED_FOR_REVIEW":
        await trigger_anomaly_webhook(result)

    return result
