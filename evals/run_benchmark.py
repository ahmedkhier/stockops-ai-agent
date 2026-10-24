"""Deterministic CLI evaluation benchmark generator for StockOps AI Agent.

Executes 5 critical supply chain and vision extraction scenarios and prints
a portfolio-grade Markdown benchmark scorecard table to stdout.
"""

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import evaluate_anomalies, extract_invoice_from_bytes
from app.database import commit_invoice_to_stock, get_inventory_catalog, init_db
from app.schemas import ExtractedInvoice, InvoiceLineItem, ProcessingResult


def run_all_benchmarks():
    results = []

    # Setup isolated test database for benchmark run
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        bench_db = os.path.join(tmpdir, "benchmark_inventory.db")
        init_db(bench_db)

        # -------------------------------------------------------------
        # Scenario 1: Clean Invoice Auto-Commit
        # -------------------------------------------------------------
        inv1 = ExtractedInvoice(
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
        status1, anomalies1, reason1 = evaluate_anomalies(inv1, db_path=bench_db)
        commit_invoice_to_stock(inv1, status=status1, db_path=bench_db)

        catalog = {r["sku"]: r["current_stock"] for r in get_inventory_catalog(bench_db)}
        s1_pass = (status1 == "AUTO_COMMITTED") and (catalog["LED-PNL-6060-40W"] == 130)
        results.append({
            "scenario": "Scenario 1: Clean Auto-Commit",
            "description": "Standard invoice matching catalog price ($14.50)",
            "expected": "AUTO_COMMITTED & Stock +10",
            "actual": f"{status1} (Stock: {catalog['LED-PNL-6060-40W']})",
            "guardrail": "Catalog Price Match (0% diff)",
            "status": "PASSED" if s1_pass else "FAILED",
        })

        # -------------------------------------------------------------
        # Scenario 2: Math Hallucination Self-Correction
        # -------------------------------------------------------------
        mock_bad = MagicMock()
        mock_bad.text = json.dumps({
            "supplier_name": "Lumiere Industrial Supply",
            "invoice_number": "INV-2026-103",
            "invoice_date": "2026-10-24",
            "currency": "USD",
            "items": [
                {
                    "sku": "DRV-24V-150W-IP67",
                    "description": "Waterproof LED Driver 24V 150W",
                    "quantity": 5,
                    "unit_price": 19.20,
                    "line_total": 120.00,  # Intentional error: 5 * 19.20 = 96.00
                }
            ],
            "subtotal": 120.00,
            "tax_amount": 0.00,
            "total_amount": 120.00,
            "extraction_confidence": 0.95,
        })

        mock_fixed = MagicMock()
        mock_fixed.text = json.dumps({
            "supplier_name": "Lumiere Industrial Supply",
            "invoice_number": "INV-2026-103",
            "invoice_date": "2026-10-24",
            "currency": "USD",
            "items": [
                {
                    "sku": "DRV-24V-150W-IP67",
                    "description": "Waterproof LED Driver 24V 150W",
                    "quantity": 5,
                    "unit_price": 19.20,
                    "line_total": 96.00,  # Fixed arithmetic
                }
            ],
            "subtotal": 96.00,
            "tax_amount": 0.00,
            "total_amount": 96.00,
            "extraction_confidence": 0.98,
        })

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [mock_bad, mock_fixed]

        inv2 = extract_invoice_from_bytes(
            file_bytes=b"dummy-image",
            mime_type="image/png",
            max_retries=2,
            client=mock_client,
        )
        s2_pass = (inv2.items[0].line_total == 96.00) and (mock_client.models.generate_content.call_count == 2)
        results.append({
            "scenario": "Scenario 2: Math Self-Correction",
            "description": "LLM hallucinated line total (5*19.20=120 vs 96.00)",
            "expected": "Self-Correct via Retry (Attempt 2)",
            "actual": f"Recovered line_total=${inv2.items[0].line_total:.2f}",
            "guardrail": "Pydantic v2 @model_validator",
            "status": "PASSED" if s2_pass else "FAILED",
        })

        # -------------------------------------------------------------
        # Scenario 3: +28.57% Price Spike Quarantine
        # -------------------------------------------------------------
        inv3 = ExtractedInvoice(
            supplier_name="Lumiere Industrial Supply",
            invoice_number="INV-2026-102",
            invoice_date=date(2026, 10, 24),
            currency="USD",
            items=[
                InvoiceLineItem(
                    sku="FL-COB-100W-IP65",
                    description="Outdoor Floodlight 100W IP65 Aluminum",
                    quantity=5,
                    unit_price=36.00,  # Catalog avg is 28.00 -> +28.57%
                    line_total=180.00,
                )
            ],
            subtotal=180.00,
            tax_amount=0.00,
            total_amount=180.00,
            extraction_confidence=0.96,
        )
        stock_before_3 = catalog["FL-COB-100W-IP65"]
        status3, anomalies3, reason3 = evaluate_anomalies(inv3, db_path=bench_db)
        commit_invoice_to_stock(inv3, status=status3, db_path=bench_db)
        catalog = {r["sku"]: r["current_stock"] for r in get_inventory_catalog(bench_db)}

        s3_pass = (
            status3 == "FLAGGED_FOR_REVIEW"
            and len(anomalies3) == 1
            and anomalies3[0].deviation_pct == 28.57
            and catalog["FL-COB-100W-IP65"] == stock_before_3
        )
        results.append({
            "scenario": "Scenario 3: Price Spike Detection",
            "description": "FL-COB-100W billed at $36.00 vs $28.00 (+28.57%)",
            "expected": "FLAGGED_FOR_REVIEW & Zero Stock Drift",
            "actual": f"Flagged +{anomalies3[0].deviation_pct:.2f}% (Stock preserved)",
            "guardrail": "Price Spike Threshold (>15.0%)",
            "status": "PASSED" if s3_pass else "FAILED",
        })

        # -------------------------------------------------------------
        # Scenario 4: Unrecognized SKU Quarantine
        # -------------------------------------------------------------
        inv4 = ExtractedInvoice(
            supplier_name="Apex Electrics",
            invoice_number="INV-2026-999",
            invoice_date=date(2026, 10, 24),
            currency="USD",
            items=[
                InvoiceLineItem(
                    sku="UNKNOWN-TRANS-800VA",
                    description="Unregistered Step-down Transformer",
                    quantity=2,
                    unit_price=150.00,
                    line_total=300.00,
                )
            ],
            subtotal=300.00,
            tax_amount=0.00,
            total_amount=300.00,
            extraction_confidence=0.94,
        )
        status4, anomalies4, reason4 = evaluate_anomalies(inv4, db_path=bench_db)
        commit_invoice_to_stock(inv4, status=status4, db_path=bench_db)

        s4_pass = (status4 == "FLAGGED_FOR_REVIEW") and ("Unrecognized SKU" in anomalies4[0].reason)
        results.append({
            "scenario": "Scenario 4: SKU Integrity Gate",
            "description": "Unknown SKU not present in catalog",
            "expected": "FLAGGED_FOR_REVIEW & Quarantined",
            "actual": f"{status4} ({anomalies4[0].sku} unrecognized)",
            "guardrail": "Foreign Key Catalog Verification",
            "status": "PASSED" if s4_pass else "FAILED",
        })

        # -------------------------------------------------------------
        # Scenario 5: Low OCR Confidence Gate
        # -------------------------------------------------------------
        inv5 = ExtractedInvoice(
            supplier_name="Lumiere Industrial Supply",
            invoice_number="INV-2026-BLUR",
            invoice_date=date(2026, 10, 24),
            currency="USD",
            items=[
                InvoiceLineItem(
                    sku="CBL-H07RN-F-3G1.5",
                    description="Heavy Duty Rubber Cable 3x1.5mm (100m Roll)",
                    quantity=1,
                    unit_price=85.00,
                    line_total=85.00,
                )
            ],
            subtotal=85.00,
            tax_amount=0.00,
            total_amount=85.00,
            extraction_confidence=0.74,  # Below 0.85 floor
        )
        status5, anomalies5, reason5 = evaluate_anomalies(inv5, db_path=bench_db)
        commit_invoice_to_stock(inv5, status=status5, db_path=bench_db)

        s5_pass = (status5 == "FLAGGED_FOR_REVIEW") and ("below threshold" in reason5)
        results.append({
            "scenario": "Scenario 5: Vision Confidence Gate",
            "description": "Low scan quality (confidence score 0.74 < 0.85)",
            "expected": "FLAGGED_FOR_REVIEW & Alert Triggered",
            "actual": f"{status5} (confidence 0.74 < 0.85)",
            "guardrail": "Confidence Floor (>=0.85)",
            "status": "PASSED" if s5_pass else "FAILED",
        })

    # Print Markdown Benchmark Scorecard
    print("\n### StockOps Autonomous Agent Evaluation Benchmark Scorecard\n")
    print("| Scenario | Test Case Description | Expected Action | Actual Outcome | Guardrail Enforced | Status |")
    print("| :--- | :--- | :--- | :--- | :--- | :---: |")
    for r in results:
        print(f"| **{r['scenario']}** | {r['description']} | `{r['expected']}` | `{r['actual']}` | {r['guardrail']} | **{r['status']}** |")

    passed_count = sum(1 for r in results if r["status"] == "PASSED")
    total_count = len(results)
    pass_rate = (passed_count / total_count) * 100.0
    print(f"\n**Benchmark Summary:** {passed_count}/{total_count} Passed ({pass_rate:.1f}% Reliability)\n")


if __name__ == "__main__":
    run_all_benchmarks()
