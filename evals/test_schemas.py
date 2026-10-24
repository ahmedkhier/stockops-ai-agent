"""Unit tests for Pydantic v2 schemas and deterministic math guardrails."""

import pytest
from datetime import date
from pydantic import ValidationError
from app.schemas import InvoiceLineItem, ExtractedInvoice, PriceAnomalyFlag, ProcessingResult


def test_valid_invoice_payload_passes():
    """Test 1: Valid invoice payload passes validation cleanly."""
    valid_data = {
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
            },
            {
                "sku": "FL-COB-100W-IP65",
                "description": "Outdoor Floodlight 100W IP65 Aluminum",
                "quantity": 2,
                "unit_price": 28.00,
                "line_total": 56.00,
            },
        ],
        "subtotal": 201.00,
        "tax_amount": 0.00,
        "total_amount": 201.00,
        "extraction_confidence": 0.98,
    }

    invoice = ExtractedInvoice.model_validate(valid_data)
    assert invoice.supplier_name == "Lumiere Industrial Supply"
    assert invoice.invoice_number == "INV-2026-101"
    assert len(invoice.items) == 2
    assert invoice.total_amount == 201.00
    assert invoice.extraction_confidence == 0.98

    # Ensure JSON roundtrip works via Pydantic v2
    json_str = invoice.model_dump_json()
    reloaded = ExtractedInvoice.model_validate_json(json_str)
    assert reloaded == invoice


def test_line_math_mismatch_raises_validation_error():
    """Test 2: Hallucinated line-item multiplication raises ValidationError matching 'Line math mismatch'."""
    invalid_item = {
        "sku": "LED-PNL-6060-40W",
        "description": "Backlit LED Panel 60x60cm 40W 6500K",
        "quantity": 10,
        "unit_price": 14.50,
        "line_total": 175.00,  # 10 * 14.50 is 145.00, not 175.00
    }

    with pytest.raises(ValidationError) as exc_info:
        InvoiceLineItem.model_validate(invalid_item)

    error_str = str(exc_info.value)
    assert "Line math mismatch" in error_str
    assert "LED-PNL-6060-40W" in error_str


def test_subtotal_mismatch_raises_validation_error():
    """Test 3: Mismatched invoice subtotal raises ValidationError matching 'Subtotal mismatch'."""
    invalid_invoice_data = {
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
        "subtotal": 190.00,  # Expected 145.00
        "tax_amount": 0.00,
        "total_amount": 190.00,
        "extraction_confidence": 0.95,
    }

    with pytest.raises(ValidationError) as exc_info:
        ExtractedInvoice.model_validate(invalid_invoice_data)

    error_str = str(exc_info.value)
    assert "Subtotal mismatch" in error_str


def test_total_amount_mismatch_raises_validation_error():
    """Test: Total amount mismatch raises ValidationError matching 'Total amount mismatch'."""
    invalid_total_data = {
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
        "tax_amount": 10.00,
        "total_amount": 200.00,  # Expected 155.00
        "extraction_confidence": 0.95,
    }

    with pytest.raises(ValidationError) as exc_info:
        ExtractedInvoice.model_validate(invalid_total_data)

    error_str = str(exc_info.value)
    assert "Total amount mismatch" in error_str


def test_processing_result_and_price_anomaly_flag():
    """Test: ProcessingResult and PriceAnomalyFlag structure."""
    anomaly = PriceAnomalyFlag(
        sku="FL-COB-100W-IP65",
        extracted_unit_price=36.00,
        historical_avg_price=28.00,
        deviation_pct=28.57,
        reason="Unit price exceeds 15.0% threshold over catalog baseline",
    )

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
        extraction_confidence=0.96,
    )

    result = ProcessingResult(
        status="FLAGGED_FOR_REVIEW",
        invoice=invoice,
        anomalies=[anomaly],
        review_reason="Price anomaly detected: FL-COB-100W-IP65 deviated +28.57%",
    )

    assert result.status == "FLAGGED_FOR_REVIEW"
    assert len(result.anomalies) == 1
    assert result.anomalies[0].sku == "FL-COB-100W-IP65"
    assert result.review_reason is not None

    dumped = result.model_dump()
    assert dumped["status"] == "FLAGGED_FOR_REVIEW"
    assert dumped["anomalies"][0]["deviation_pct"] == 28.57
