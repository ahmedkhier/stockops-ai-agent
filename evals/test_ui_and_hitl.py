"""Integration and unit tests for UI serving, dashboard metrics, and Human-in-the-Loop (HITL) workflows."""

import pytest
from app.database import (
    approve_ledger_entry,
    commit_invoice_to_stock,
    get_dashboard_metrics,
    get_inventory_catalog,
    get_stock_ledger,
    init_db,
    reject_ledger_entry,
)
from app.schemas import DashboardMetrics, ExtractedInvoice, InvoiceLineItem, LedgerActionResponse
from datetime import date


@pytest.fixture
def hitl_db(tmp_path):
    """Fixture providing an isolated temporary database for UI and HITL testing."""
    db_file = str(tmp_path / "test_hitl.db")
    init_db(db_file)
    return db_file


def test_get_dashboard_metrics_initial(hitl_db):
    """Verify get_dashboard_metrics returns correct initial catalog statistics."""
    metrics_dict = get_dashboard_metrics(hitl_db)
    metrics = DashboardMetrics.model_validate(metrics_dict)

    # Catalog initially has 4 items
    assert metrics.total_skus == 4
    # 120 + 45 + 80 + 15 = 260
    assert metrics.total_units_in_stock == 260
    # 120*14.50 (1740) + 45*28.00 (1260) + 80*19.20 (1536) + 15*85.00 (1275) = 5811.00
    assert metrics.total_inventory_value == 5811.00
    assert metrics.auto_committed_count == 0
    assert metrics.flagged_pending_count == 0
    assert metrics.approved_override_count == 0
    assert metrics.rejected_count == 0


def test_approve_ledger_entry_increments_stock(hitl_db):
    """Verify that approving a FLAGGED_FOR_REVIEW ledger entry increments stock."""
    # Commit a flagged invoice
    flagged_inv = ExtractedInvoice(
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
    commit_invoice_to_stock(flagged_inv, status="FLAGGED_FOR_REVIEW", db_path=hitl_db)

    # Initial stock before approval: 45
    ledger_entries = get_stock_ledger(hitl_db)
    assert len(ledger_entries) == 1
    ledger_id = ledger_entries[0]["id"]
    assert ledger_entries[0]["status"] == "FLAGGED_FOR_REVIEW"

    # Approve
    action_dict = approve_ledger_entry(ledger_id, db_path=hitl_db)
    resp = LedgerActionResponse.model_validate(action_dict)
    assert resp.ledger_id == ledger_id
    assert resp.previous_status == "FLAGGED_FOR_REVIEW"
    assert resp.new_status == "APPROVED_OVERRIDE"
    assert resp.updated_stock == 50  # 45 + 5 = 50

    # Verify catalog stock was updated
    catalog = {r["sku"]: r["current_stock"] for r in get_inventory_catalog(hitl_db)}
    assert catalog["FL-COB-100W-IP65"] == 50

    # Verify metrics updated
    metrics = DashboardMetrics.model_validate(get_dashboard_metrics(hitl_db))
    assert metrics.flagged_pending_count == 0
    assert metrics.approved_override_count == 1


def test_approve_unrecognized_sku_registers_in_catalog(hitl_db):
    """Verify approving a flagged entry with an unknown SKU adds it to the catalog."""
    unknown_inv = ExtractedInvoice(
        supplier_name="Specialty Components Inc",
        invoice_number="INV-2026-999",
        invoice_date=date(2026, 10, 24),
        currency="USD",
        items=[
            InvoiceLineItem(
                sku="CUSTOM-MODULE-48V",
                description="Custom DC Power Supply Module",
                quantity=8,
                unit_price=110.00,
                line_total=880.00,
            )
        ],
        subtotal=880.00,
        tax_amount=0.00,
        total_amount=880.00,
        extraction_confidence=0.92,
    )
    commit_invoice_to_stock(unknown_inv, status="FLAGGED_FOR_REVIEW", db_path=hitl_db)
    ledger_entries = get_stock_ledger(hitl_db)
    ledger_id = ledger_entries[0]["id"]

    # Approve unknown SKU
    action_dict = approve_ledger_entry(ledger_id, db_path=hitl_db)
    resp = LedgerActionResponse.model_validate(action_dict)
    assert resp.new_status == "APPROVED_OVERRIDE"
    assert resp.updated_stock == 8

    # Catalog now has 5 SKUs
    catalog = {r["sku"]: r for r in get_inventory_catalog(hitl_db)}
    assert "CUSTOM-MODULE-48V" in catalog
    assert catalog["CUSTOM-MODULE-48V"]["current_stock"] == 8
    assert catalog["CUSTOM-MODULE-48V"]["avg_unit_cost"] == 110.00


def test_reject_ledger_entry_preserves_stock(hitl_db):
    """Verify that rejecting a flagged entry updates status without modifying stock."""
    flagged_inv = ExtractedInvoice(
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
    commit_invoice_to_stock(flagged_inv, status="FLAGGED_FOR_REVIEW", db_path=hitl_db)
    ledger_id = get_stock_ledger(hitl_db)[0]["id"]

    # Reject
    action_dict = reject_ledger_entry(ledger_id, db_path=hitl_db)
    resp = LedgerActionResponse.model_validate(action_dict)
    assert resp.new_status == "REJECTED"
    assert resp.updated_stock == 45  # Stock untouched

    catalog = {r["sku"]: r["current_stock"] for r in get_inventory_catalog(hitl_db)}
    assert catalog["FL-COB-100W-IP65"] == 45

    metrics = DashboardMetrics.model_validate(get_dashboard_metrics(hitl_db))
    assert metrics.flagged_pending_count == 0
    assert metrics.rejected_count == 1


def test_approve_reject_invalid_conditions(hitl_db):
    """Verify KeyError on non-existent ID and ValueError on non-flagged entry."""
    with pytest.raises(KeyError):
        approve_ledger_entry(99999, db_path=hitl_db)

    with pytest.raises(KeyError):
        reject_ledger_entry(99999, db_path=hitl_db)

    # Commit an auto-committed entry
    clean_inv = ExtractedInvoice(
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
    commit_invoice_to_stock(clean_inv, status="AUTO_COMMITTED", db_path=hitl_db)
    clean_id = get_stock_ledger(hitl_db)[0]["id"]

    with pytest.raises(ValueError):
        approve_ledger_entry(clean_id, db_path=hitl_db)

    with pytest.raises(ValueError):
        reject_ledger_entry(clean_id, db_path=hitl_db)


# ---------------------------------------------------------
# Phase 2 FastAPI HITL & Simulation Endpoints Tests
# ---------------------------------------------------------

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def hitl_client(hitl_db, monkeypatch):
    """Fixture providing TestClient pointed at the isolated HITL database."""
    monkeypatch.setenv("SQLITE_DB_PATH", hitl_db)
    init_db(hitl_db)
    with TestClient(app) as client:
        yield client


def test_api_get_metrics(hitl_client):
    """Test GET /api/v1/metrics returns dashboard metrics."""
    res = hitl_client.get("/api/v1/metrics")
    assert res.status_code == 200
    data = res.json()
    assert data["total_skus"] == 4
    assert data["total_units_in_stock"] == 260
    assert data["total_inventory_value"] == 5811.00


def test_api_simulate_valid(hitl_client):
    """Test POST /api/v1/invoices/simulate/valid increments stock."""
    res = hitl_client.post("/api/v1/invoices/simulate/valid")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "AUTO_COMMITTED"
    assert data["invoice"]["invoice_number"] == "INV-2026-101"

    # Verify stock of LED-PNL-6060-40W incremented from 120 to 130
    cat_res = hitl_client.get("/api/v1/inventory")
    cat = {i["sku"]: i["current_stock"] for i in cat_res.json()}
    assert cat["LED-PNL-6060-40W"] == 130


def test_api_simulate_anomaly_and_hitl_workflow(hitl_client):
    """Test POST /api/v1/invoices/simulate/anomaly flags for review and allows approval."""
    # 1. Simulate anomaly
    res = hitl_client.post("/api/v1/invoices/simulate/anomaly")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "FLAGGED_FOR_REVIEW"
    assert len(data["anomalies"]) == 1
    assert data["anomalies"][0]["deviation_pct"] == 28.57

    # Stock of FL-COB-100W-IP65 unchanged at 45
    cat = {i["sku"]: i["current_stock"] for i in hitl_client.get("/api/v1/inventory").json()}
    assert cat["FL-COB-100W-IP65"] == 45

    # 2. Check metrics
    m = hitl_client.get("/api/v1/metrics").json()
    assert m["flagged_pending_count"] == 1

    # 3. Fetch ledger entry ID
    ledger = hitl_client.get("/api/v1/ledger").json()
    ledger_id = ledger[0]["id"]

    # 4. Approve via HITL endpoint
    appr_res = hitl_client.post(f"/api/v1/ledger/{ledger_id}/approve")
    assert appr_res.status_code == 200
    appr_data = appr_res.json()
    assert appr_data["new_status"] == "APPROVED_OVERRIDE"
    assert appr_data["updated_stock"] == 50

    # Stock now 50
    cat = {i["sku"]: i["current_stock"] for i in hitl_client.get("/api/v1/inventory").json()}
    assert cat["FL-COB-100W-IP65"] == 50


def test_api_simulate_unrecognized_sku_and_reject(hitl_client):
    """Test POST /api/v1/invoices/simulate/unrecognized_sku and reject via HITL endpoint."""
    res = hitl_client.post("/api/v1/invoices/simulate/unrecognized_sku")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "FLAGGED_FOR_REVIEW"
    assert "Unrecognized SKU" in data["anomalies"][0]["reason"]

    ledger = hitl_client.get("/api/v1/ledger").json()
    ledger_id = ledger[0]["id"]

    # Reject via HITL endpoint
    rej_res = hitl_client.post(f"/api/v1/ledger/{ledger_id}/reject")
    assert rej_res.status_code == 200
    rej_data = rej_res.json()
    assert rej_data["new_status"] == "REJECTED"


def test_api_simulate_invalid_scenario(hitl_client):
    """Test invalid scenario returns 400 Bad Request."""
    res = hitl_client.post("/api/v1/invoices/simulate/non_existent_scenario")
    assert res.status_code == 400


def test_api_ingest_smart_demo_fallback(hitl_client, monkeypatch):
    """Test POST /api/v1/invoices/ingest uses demo fallback when GEMINI_API_KEY is placeholder."""
    monkeypatch.setenv("GEMINI_API_KEY", "your_api_key_here")

    # Ingest valid sample PNG name
    res = hitl_client.post(
        "/api/v1/invoices/ingest",
        files={"file": ("valid_invoice.png", b"dummy-png-bytes", "image/png")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "AUTO_COMMITTED"
    assert data["invoice"]["invoice_number"] == "INV-2026-101"


# ---------------------------------------------------------
# Phase 3 & 4 Static UI Serving Tests
# ---------------------------------------------------------

def test_serve_dashboard_ui_html(hitl_client):
    """Verify GET / returns HTTP 200 and contains StockOps AI title."""
    res = hitl_client.get("/")
    assert res.status_code == 200
    assert "StockOps AI" in res.text
    assert "text/html" in res.headers.get("content-type", "")


def test_serve_static_app_js(hitl_client):
    """Verify GET /static/app.js returns HTTP 200."""
    res = hitl_client.get("/static/app.js")
    assert res.status_code == 200
    assert "StockOps AI" in res.text


# ---------------------------------------------------------
# Phase 5 End-to-End HITL Quarantine & Approval Lifecycle
# ---------------------------------------------------------

def test_full_ui_hitl_quarantine_and_approval_lifecycle(hitl_client):
    """End-to-end test simulating anomaly, inspecting quarantine, and approving via HITL."""
    # 1. Simulate anomaly
    sim_res = hitl_client.post("/api/v1/invoices/simulate/anomaly")
    assert sim_res.status_code == 200
    sim_data = sim_res.json()
    assert sim_data["status"] == "FLAGGED_FOR_REVIEW"

    # Catalog stock preserved at 45
    cat1 = {i["sku"]: i["current_stock"] for i in hitl_client.get("/api/v1/inventory").json()}
    assert cat1["FL-COB-100W-IP65"] == 45

    # 2. Check metrics: 1 pending
    m1 = hitl_client.get("/api/v1/metrics").json()
    assert m1["flagged_pending_count"] == 1
    assert m1["approved_override_count"] == 0

    # 3. Fetch quarantined ledger ID
    ledger = hitl_client.get("/api/v1/ledger").json()
    quarantined = [entry for entry in ledger if entry["status"] == "FLAGGED_FOR_REVIEW"]
    assert len(quarantined) >= 1
    quarantined_id = quarantined[0]["id"]

    # 4. Human-in-the-Loop overrides & approves
    appr_res = hitl_client.post(f"/api/v1/ledger/{quarantined_id}/approve")
    assert appr_res.status_code == 200
    appr_data = appr_res.json()
    assert appr_data["new_status"] == "APPROVED_OVERRIDE"
    assert appr_data["updated_stock"] == 50

    # 5. Check catalog stock incremented from 45 to 50
    cat2 = {i["sku"]: i["current_stock"] for i in hitl_client.get("/api/v1/inventory").json()}
    assert cat2["FL-COB-100W-IP65"] == 50

    # 6. Check metrics updated: pending 0, approved 1
    m2 = hitl_client.get("/api/v1/metrics").json()
    assert m2["flagged_pending_count"] == 0
    assert m2["approved_override_count"] == 1


