"""Pydantic v2 schemas and deterministic math guardrails for invoice processing."""

from datetime import date
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class InvoiceLineItem(BaseModel):
    sku: str = Field(..., description="Stock Keeping Unit identifier")
    description: str = Field(..., description="Product description")
    quantity: int = Field(..., gt=0, description="Quantity of items billed")
    unit_price: float = Field(..., ge=0.0, description="Billed unit price")
    line_total: float = Field(..., ge=0.0, description="Billed total for this line item")

    @model_validator(mode="after")
    def validate_line_total(self) -> "InvoiceLineItem":
        expected_total = round(self.quantity * self.unit_price, 2)
        stated_total = round(self.line_total, 2)
        if abs(expected_total - stated_total) > 0.05:
            raise ValueError(
                f"Line math mismatch for SKU {self.sku}: "
                f"expected {self.quantity} * {self.unit_price} = {expected_total:.2f}, "
                f"but got {self.line_total:.2f}"
            )
        return self


class ExtractedInvoice(BaseModel):
    supplier_name: str = Field(..., description="Name of the vendor/supplier")
    invoice_number: str = Field(..., description="Unique invoice reference number")
    invoice_date: date = Field(..., description="Date invoice was issued")
    currency: str = Field(default="USD", description="3-letter currency code")
    items: List[InvoiceLineItem] = Field(..., min_length=1, description="List of line items")
    subtotal: float = Field(..., ge=0.0, description="Sum of line items before tax")
    tax_amount: float = Field(default=0.0, ge=0.0, description="Tax or VAT amount")
    total_amount: float = Field(..., ge=0.0, description="Total billed invoice amount")
    extraction_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score of multimodal extraction [0.0, 1.0]"
    )

    @model_validator(mode="after")
    def validate_invoice_totals(self) -> "ExtractedInvoice":
        calculated_subtotal = round(sum(item.line_total for item in self.items), 2)
        stated_subtotal = round(self.subtotal, 2)
        if abs(calculated_subtotal - stated_subtotal) > 0.10:
            raise ValueError(
                f"Subtotal mismatch: sum of line items ({calculated_subtotal:.2f}) "
                f"does not match stated subtotal ({stated_subtotal:.2f})"
            )

        expected_total = round(self.subtotal + self.tax_amount, 2)
        stated_total = round(self.total_amount, 2)
        if abs(expected_total - stated_total) > 0.10:
            raise ValueError(
                f"Total amount mismatch: subtotal ({self.subtotal:.2f}) + tax ({self.tax_amount:.2f}) = "
                f"{expected_total:.2f}, but got total {stated_total:.2f}"
            )

        return self


class PriceAnomalyFlag(BaseModel):
    sku: str = Field(..., description="Stock Keeping Unit")
    extracted_unit_price: float = Field(..., description="Billed unit price on invoice")
    historical_avg_price: float = Field(..., description="Historical average unit cost in catalog")
    deviation_pct: float = Field(..., description="Percentage price deviation from catalog")
    reason: str = Field(..., description="Detailed explanation of the anomaly")


class ProcessingResult(BaseModel):
    status: Literal["AUTO_COMMITTED", "FLAGGED_FOR_REVIEW"] = Field(
        ..., description="Processing status of the invoice"
    )
    invoice: ExtractedInvoice = Field(..., description="Validated structured invoice")
    anomalies: List[PriceAnomalyFlag] = Field(
        default_factory=list, description="List of price or catalog anomalies identified"
    )
    review_reason: Optional[str] = Field(
        default=None, description="Reason invoice was quarantined for review, if any"
    )


class DashboardMetrics(BaseModel):
    total_skus: int = Field(..., description="Total distinct active SKUs in catalog")
    total_units_in_stock: int = Field(..., description="Total sum of stock units in warehouse")
    total_inventory_value: float = Field(..., description="Total valuation of catalog inventory in USD")
    auto_committed_count: int = Field(..., description="Count of auto-committed invoices")
    flagged_pending_count: int = Field(..., description="Count of quarantined entries awaiting review")
    approved_override_count: int = Field(..., description="Count of human-approved overrides")
    rejected_count: int = Field(..., description="Count of rejected entries")


class LedgerActionResponse(BaseModel):
    ledger_id: int = Field(..., description="Primary key identifier of the ledger entry")
    invoice_number: str = Field(..., description="Invoice identifier reference")
    sku: str = Field(..., description="Stock Keeping Unit involved")
    previous_status: str = Field(..., description="Status of the ledger entry before action")
    new_status: str = Field(..., description="Updated status of the ledger entry")
    updated_stock: int = Field(..., description="Current stock level in catalog after action")

