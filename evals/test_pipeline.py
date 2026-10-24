"""Comprehensive pipeline, database, agent, and FastAPI integration test suite for StockOps AI Agent."""

import json
import os
import sqlite3
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import httpx
import pytest

from app.agent import (
    evaluate_anomalies,
    extract_invoice_from_bytes,
    extract_invoice_via_openrouter,
    get_active_llm_provider,
)
from app.database import (
    commit_invoice_to_stock,
    get_historical_unit_cost,
    get_inventory_catalog,
    get_stock_ledger,
    init_db,
)
from app.main import app
from app.notifier import trigger_anomaly_webhook
from app.schemas import ExtractedInvoice, InvoiceLineItem, PriceAnomalyFlag, ProcessingResult


@pytest.fixture
def test_db(tmp_path):
    """Fixture providing an isolated temporary SQLite database."""
    db_file = str(tmp_path / "test_inventory.db")
    init_db(db_file)
    return db_file


@pytest.fixture
def api_client(test_db, monkeypatch):
    """Fixture providing a FastAPI TestClient configured with an isolated test database."""
    monkeypatch.setenv("SQLITE_DB_PATH", test_db)
    init_db(test_db)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------
# Phase 3 Database Tests
# ---------------------------------------------------------

def test_init_db_seeds_catalog(test_db):
    """Test that init_db correctly populates initial catalog items."""
    catalog = get_inventory_catalog(test_db)
    assert len(catalog) == 4

    skus = {row["sku"]: row for row in catalog}
    assert "LED-PNL-6060-40W" in skus
    assert skus["LED-PNL-6060-40W"]["current_stock"] == 120
    assert skus["LED-PNL-6060-40W"]["avg_unit_cost"] == 14.50

    cost = get_historical_unit_cost("FL-COB-100W-IP65", test_db)
    assert cost == 28.00

    cost_unknown = get_historical_unit_cost("NON-EXISTENT-SKU", test_db)
    assert cost_unknown is None


def test_commit_invoice_auto_committed_increments_stock(test_db):
    """Test that commit_invoice_to_stock increments stock when status is AUTO_COMMITTED."""
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

    commit_invoice_to_stock(invoice, status="AUTO_COMMITTED", db_path=test_db)

    # Check updated stock: original was 120, now should be 130
    catalog = {row["sku"]: row for row in get_inventory_catalog(test_db)}
    assert catalog["LED-PNL-6060-40W"]["current_stock"] == 130

    # Check stock ledger
    ledger = get_stock_ledger(test_db)
    assert len(ledger) == 1
    assert ledger[0]["invoice_number"] == "INV-2026-101"
    assert ledger[0]["sku"] == "LED-PNL-6060-40W"
    assert ledger[0]["quantity_added"] == 10
    assert ledger[0]["status"] == "AUTO_COMMITTED"


def test_commit_invoice_flagged_for_review_preserves_stock(test_db):
    """Test that commit_invoice_to_stock preserves stock when status is FLAGGED_FOR_REVIEW."""
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

    commit_invoice_to_stock(invoice, status="FLAGGED_FOR_REVIEW", db_path=test_db)

    # Stock must remain unchanged (original was 45)
    catalog = {row["sku"]: row for row in get_inventory_catalog(test_db)}
    assert catalog["FL-COB-100W-IP65"]["current_stock"] == 45

    # Stock ledger must record the quarantined entry
    ledger = get_stock_ledger(test_db)
    assert len(ledger) == 1
    assert ledger[0]["invoice_number"] == "INV-2026-102"
    assert ledger[0]["sku"] == "FL-COB-100W-IP65"
    assert ledger[0]["quantity_added"] == 5
    assert ledger[0]["status"] == "FLAGGED_FOR_REVIEW"


# ---------------------------------------------------------
# Phase 4 Agent & Self-Correction Loop Tests
# ---------------------------------------------------------

def test_extract_invoice_self_correction_recovers_from_bad_math():
    """Verify that if Attempt 1 produces bad math, the retry loop self-corrects on Attempt 2."""
    mock_bad_response = MagicMock()
    mock_bad_response.text = json.dumps({
        "supplier_name": "Lumiere Industrial Supply",
        "invoice_number": "INV-2026-101",
        "invoice_date": "2026-10-24",
        "currency": "USD",
        "items": [
            {
                "sku": "LED-PNL-6060-40W",
                "description": "Backlit LED Panel 60x60cm 40W 6500K",
                "quantity": 10,
                "unit_price": 14.50,
                "line_total": 175.00,
            }
        ],
        "subtotal": 175.00,
        "tax_amount": 0.00,
        "total_amount": 175.00,
        "extraction_confidence": 0.95,
    })

    mock_good_response = MagicMock()
    mock_good_response.text = json.dumps({
        "supplier_name": "Lumiere Industrial Supply",
        "invoice_number": "INV-2026-101",
        "invoice_date": "2026-10-24",
        "currency": "USD",
        "items": [
            {
                "sku": "LED-PNL-6060-40W",
                "description": "Backlit LED Panel 60x60cm 40W 6500K",
                "quantity": 10,
                "unit_price": 14.50,
                "line_total": 145.00,
            }
        ],
        "subtotal": 145.00,
        "tax_amount": 0.00,
        "total_amount": 145.00,
        "extraction_confidence": 0.98,
    })

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        mock_bad_response,
        mock_good_response,
    ]

    extracted = extract_invoice_from_bytes(
        file_bytes=b"fake-image-bytes",
        mime_type="image/png",
        max_retries=2,
        client=mock_client,
    )

    assert extracted.invoice_number == "INV-2026-101"
    assert extracted.items[0].line_total == 145.00
    assert mock_client.models.generate_content.call_count == 2


def test_evaluate_anomalies_clean_invoice_auto_commits(test_db):
    """Verify that an invoice with valid SKUs and non-spiking prices is AUTO_COMMITTED."""
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

    status, anomalies, reason = evaluate_anomalies(invoice, db_path=test_db)
    assert status == "AUTO_COMMITTED"
    assert len(anomalies) == 0
    assert reason is None


def test_evaluate_anomalies_flags_price_spike(test_db):
    """Verify that a >15% price spike is flagged for review."""
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

    status, anomalies, reason = evaluate_anomalies(invoice, db_path=test_db)
    assert status == "FLAGGED_FOR_REVIEW"
    assert len(anomalies) == 1
    assert anomalies[0].sku == "FL-COB-100W-IP65"
    assert anomalies[0].deviation_pct == 28.57
    assert "Price spike on FL-COB-100W-IP65" in reason


def test_evaluate_anomalies_flags_unrecognized_sku(test_db):
    """Verify that an item not found in catalog is quarantined."""
    invoice = ExtractedInvoice(
        supplier_name="Unknown Vendor",
        invoice_number="INV-UNKNOWN-01",
        invoice_date=date(2026, 10, 24),
        currency="USD",
        items=[
            InvoiceLineItem(
                sku="UNKNOWN-PART-XYZ",
                description="Mysterious electronic component",
                quantity=1,
                unit_price=50.00,
                line_total=50.00,
            )
        ],
        subtotal=50.00,
        tax_amount=0.00,
        total_amount=50.00,
        extraction_confidence=0.95,
    )

    status, anomalies, reason = evaluate_anomalies(invoice, db_path=test_db)
    assert status == "FLAGGED_FOR_REVIEW"
    assert len(anomalies) == 1
    assert "Unrecognized SKU" in anomalies[0].reason


def test_evaluate_anomalies_flags_low_confidence(test_db):
    """Verify that extraction confidence below 0.85 is quarantined."""
    invoice = ExtractedInvoice(
        supplier_name="Lumiere Industrial Supply",
        invoice_number="INV-BLURRY-01",
        invoice_date=date(2026, 10, 24),
        currency="USD",
        items=[
            InvoiceLineItem(
                sku="LED-PNL-6060-40W",
                description="Backlit LED Panel 60x60cm 40W 6500K",
                quantity=1,
                unit_price=14.50,
                line_total=14.50,
            )
        ],
        subtotal=14.50,
        tax_amount=0.00,
        total_amount=14.50,
        extraction_confidence=0.72,
    )

    status, anomalies, reason = evaluate_anomalies(invoice, db_path=test_db)
    assert status == "FLAGGED_FOR_REVIEW"
    assert "below threshold" in reason


# ---------------------------------------------------------
# Phase 4 Webhook Notifier Tests
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_anomaly_webhook_success():
    """Verify that trigger_anomaly_webhook posts JSON payload and returns True on 200 OK."""
    result = ProcessingResult(
        status="FLAGGED_FOR_REVIEW",
        invoice=ExtractedInvoice(
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
        ),
        anomalies=[
            PriceAnomalyFlag(
                sku="FL-COB-100W-IP65",
                extracted_unit_price=36.00,
                historical_avg_price=28.00,
                deviation_pct=28.57,
                reason="Unit price exceeds 15.0% threshold",
            )
        ],
        review_reason="Price spike on FL-COB-100W-IP65 (+28.57%)",
    )

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        delivered = await trigger_anomaly_webhook(
            result, webhook_url="http://fake-webhook-receiver/alert"
        )
        assert delivered is True
        mock_post.assert_called_once()
        sent_payload = mock_post.call_args[1]["json"]
        assert sent_payload["event"] == "INVOICE_ANOMALY_DETECTED"
        assert sent_payload["invoice_number"] == "INV-2026-102"


@pytest.mark.asyncio
async def test_trigger_anomaly_webhook_handles_network_error_gracefully():
    """Verify that network errors do not raise unhandled exceptions and return False."""
    result = ProcessingResult(
        status="FLAGGED_FOR_REVIEW",
        invoice=ExtractedInvoice(
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
        ),
        anomalies=[],
        review_reason="Test error resilience",
    )

    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")):
        delivered = await trigger_anomaly_webhook(
            result, webhook_url="http://unreachable-n8n:5678/webhook"
        )
        assert delivered is False


# ---------------------------------------------------------
# Phase 5 FastAPI Endpoint Tests
# ---------------------------------------------------------

def test_api_health_endpoint(api_client):
    """Verify GET /health returns 200 OK and status ok."""
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_inventory_endpoint(api_client):
    """Verify GET /api/v1/inventory returns catalog items."""
    response = api_client.get("/api/v1/inventory")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 4
    skus = [i["sku"] for i in items]
    assert "LED-PNL-6060-40W" in skus


def test_api_ledger_endpoint(api_client):
    """Verify GET /api/v1/ledger returns stock ledger records."""
    response = api_client.get("/api/v1/ledger")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_api_ingest_invalid_media_type(api_client):
    """Verify POST /api/v1/invoices/ingest rejects invalid MIME types with 400 Bad Request."""
    response = api_client.post(
        "/api/v1/invoices/ingest",
        files={"file": ("invoice.txt", b"plain text content", "text/plain")},
    )
    assert response.status_code == 400
    assert "Unsupported media type" in response.json()["detail"]


def test_api_ingest_empty_file(api_client):
    """Verify POST /api/v1/invoices/ingest rejects empty uploads with 400 Bad Request."""
    response = api_client.post(
        "/api/v1/invoices/ingest",
        files={"file": ("invoice.png", b"", "image/png")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_api_ingest_clean_invoice_end_to_end(api_client):
    """Verify POST /api/v1/invoices/ingest successfully extracts, validates, and auto-commits."""
    mock_extracted = ExtractedInvoice(
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

    with patch("app.main.extract_invoice_from_bytes", return_value=mock_extracted):
        response = api_client.post(
            "/api/v1/invoices/ingest",
            files={"file": ("valid_invoice.png", b"fake-png-bytes", "image/png")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "AUTO_COMMITTED"
        assert len(data["anomalies"]) == 0
        assert data["invoice"]["invoice_number"] == "INV-2026-101"

    # Verify inventory stock was incremented
    inv_response = api_client.get("/api/v1/inventory")
    catalog = {i["sku"]: i["current_stock"] for i in inv_response.json()}
    assert catalog["LED-PNL-6060-40W"] == 130


def test_api_ingest_anomaly_invoice_quarantined_and_webhook_alerted(api_client):
    """Verify POST /api/v1/invoices/ingest flags price spikes and alerts webhook."""
    mock_extracted = ExtractedInvoice(
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
        extraction_confidence=0.96,
    )

    with patch("app.main.extract_invoice_from_bytes", return_value=mock_extracted):
        with patch("app.main.trigger_anomaly_webhook", new_callable=AsyncMock) as mock_webhook:
            response = api_client.post(
                "/api/v1/invoices/ingest",
                files={"file": ("anomaly_invoice.png", b"fake-png-bytes", "image/png")},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "FLAGGED_FOR_REVIEW"
            assert len(data["anomalies"]) == 1
            assert data["anomalies"][0]["deviation_pct"] == 28.57
            mock_webhook.assert_called_once()

    # Stock must NOT be incremented (stays at 45)
    inv_response = api_client.get("/api/v1/inventory")
    catalog = {i["sku"]: i["current_stock"] for i in inv_response.json()}
    assert catalog["FL-COB-100W-IP65"] == 45


# ---------------------------------------------------------
# OpenRouter API Key & Vision Pipeline Tests
# ---------------------------------------------------------

def test_get_active_llm_provider_detection(monkeypatch):
    """Verify provider detection for Gemini, explicit OpenRouter, and sk-or- prefix."""
    # 1. Default fallback to Gemini
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    provider, key, model = get_active_llm_provider()
    assert provider == "gemini"
    assert model == "gemini-2.5-flash"

    # 2. Explicit OPENROUTER_API_KEY
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-my-openrouter-key")
    provider, key, model = get_active_llm_provider()
    assert provider == "openrouter"
    assert key == "sk-or-v1-my-openrouter-key"
    assert "gemini" in model

    # 3. GEMINI_API_KEY containing an OpenRouter key starting with sk-or-
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-or-v1-pasted-into-gemini-key")
    provider, key, model = get_active_llm_provider()
    assert provider == "openrouter"
    assert key == "sk-or-v1-pasted-into-gemini-key"


def test_extract_invoice_via_openrouter_mocked():
    """Verify that extract_invoice_via_openrouter parses valid JSON responses."""
    mock_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "supplier_name": "Lumiere Industrial Supply",
                        "invoice_number": "INV-2026-101",
                        "invoice_date": "2026-10-24",
                        "currency": "USD",
                        "items": [
                            {
                                "sku": "LED-PNL-6060-40W",
                                "description": "Backlit LED Panel 60x60cm 40W 6500K",
                                "quantity": 10,
                                "unit_price": 14.50,
                                "line_total": 145.00,
                            }
                        ],
                        "subtotal": 145.00,
                        "tax_amount": 0.00,
                        "total_amount": 145.00,
                        "extraction_confidence": 0.98,
                    })
                }
            }
        ]
    }

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = mock_payload
    mock_client.post.return_value = mock_resp

    extracted = extract_invoice_via_openrouter(
        file_bytes=b"sample-png-bytes",
        mime_type="image/png",
        api_key="sk-or-v1-test-key",
        model_name="google/gemini-2.5-flash",
        http_client=mock_client,
    )

    assert extracted.invoice_number == "INV-2026-101"
    assert extracted.items[0].line_total == 145.00
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args[1]
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk-or-v1-test-key"
    assert "data:image/png;base64," in json.dumps(call_kwargs["json"])


def test_extract_invoice_via_openrouter_self_correction():
    """Verify OpenRouter self-corrects bad math across retry attempts."""
    # Attempt 1: Line total math error
    bad_resp = MagicMock()
    bad_resp.status_code = 200
    bad_resp.raise_for_status = MagicMock()
    bad_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "supplier_name": "Lumiere Industrial Supply",
                        "invoice_number": "INV-2026-101",
                        "invoice_date": "2026-10-24",
                        "currency": "USD",
                        "items": [
                            {
                                "sku": "LED-PNL-6060-40W",
                                "description": "Backlit LED Panel 60x60cm 40W 6500K",
                                "quantity": 10,
                                "unit_price": 14.50,
                                "line_total": 180.00,  # Invalid line total
                            }
                        ],
                        "subtotal": 180.00,
                        "tax_amount": 0.00,
                        "total_amount": 180.00,
                        "extraction_confidence": 0.95,
                    })
                }
            }
        ]
    }

    # Attempt 2: Fixed arithmetic
    good_resp = MagicMock()
    good_resp.status_code = 200
    good_resp.raise_for_status = MagicMock()
    good_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "supplier_name": "Lumiere Industrial Supply",
                        "invoice_number": "INV-2026-101",
                        "invoice_date": "2026-10-24",
                        "currency": "USD",
                        "items": [
                            {
                                "sku": "LED-PNL-6060-40W",
                                "description": "Backlit LED Panel 60x60cm 40W 6500K",
                                "quantity": 10,
                                "unit_price": 14.50,
                                "line_total": 145.00,
                            }
                        ],
                        "subtotal": 145.00,
                        "tax_amount": 0.00,
                        "total_amount": 145.00,
                        "extraction_confidence": 0.98,
                    })
                }
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.post.side_effect = [bad_resp, good_resp]

    extracted = extract_invoice_via_openrouter(
        file_bytes=b"sample-png-bytes",
        mime_type="image/png",
        api_key="sk-or-v1-test-key",
        model_name="google/gemini-2.5-flash",
        max_retries=2,
        http_client=mock_client,
    )

    assert extracted.invoice_number == "INV-2026-101"
    assert extracted.items[0].line_total == 145.00
    assert mock_client.post.call_count == 2

