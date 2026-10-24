"""FastAPI application for StockOps Autonomous Inventory & Supply Chain Agent."""

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.agent import evaluate_anomalies, extract_invoice_from_bytes
from app.database import (
    commit_invoice_to_stock,
    get_inventory_catalog,
    get_stock_ledger,
    init_db,
)
from app.notifier import trigger_anomaly_webhook
from app.schemas import ProcessingResult

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


@app.get("/health", summary="Health Check")
def health_check() -> Dict[str, str]:
    """Return health status of the application."""
    return {"status": "ok"}


@app.get("/api/v1/inventory", summary="Get Current Inventory Catalog")
def get_inventory() -> List[Dict[str, Any]]:
    """Return all stock records from inventory catalog."""
    return get_inventory_catalog()


@app.get("/api/v1/ledger", summary="Get Stock Ingestion Audit Ledger")
def get_ledger() -> List[Dict[str, Any]]:
    """Return all recorded stock ingestion transactions."""
    return get_stock_ledger()


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

    try:
        # 1. Multimodal extraction with self-correcting arithmetic loop
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

    # 2. Cross-check against catalog baselines
    status_str, anomalies, review_reason = evaluate_anomalies(extracted_invoice)

    # 3. Commit to database (audit ledger always, stock increment only if AUTO_COMMITTED)
    commit_invoice_to_stock(extracted_invoice, status=status_str)

    result = ProcessingResult(
        status=status_str,  # type: ignore[arg-type]
        invoice=extracted_invoice,
        anomalies=anomalies,
        review_reason=review_reason,
    )

    # 4. Trigger alert webhook if quarantined
    if status_str == "FLAGGED_FOR_REVIEW":
        await trigger_anomaly_webhook(result)

    return result
