"""Gemini multimodal vision extraction, self-correction retry loop, and anomaly audit."""

import json
import logging
import os
from typing import List, Optional, Tuple
from pydantic import ValidationError
from google import genai
from google.genai import types

from app.database import get_historical_unit_cost
from app.schemas import ExtractedInvoice, PriceAnomalyFlag

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an autonomous invoice intelligence specialist for StockOps.
Analyze the provided invoice image or document. Extract all supplier details, invoice metadata, 
individual line items (SKU, description, quantity, unit_price, line_total), subtotal, tax_amount, 
total_amount, and an overall extraction_confidence score between 0.0 and 1.0.

STRICT ARITHMETIC RULES:
1. Every line item's line_total MUST equal quantity * unit_price within 0.05 tolerance.
2. The subtotal MUST equal the sum of all item line_totals within 0.10 tolerance.
3. The total_amount MUST equal subtotal + tax_amount within 0.10 tolerance.
Ensure clean JSON output conforming strictly to the requested schema.
"""


def get_genai_client(api_key: Optional[str] = None) -> genai.Client:
    """Return a configured google-genai Client."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    return genai.Client(api_key=key)


def extract_invoice_from_bytes(
    file_bytes: bytes,
    mime_type: str,
    max_retries: int = 2,
    client: Optional[genai.Client] = None,
    model_name: Optional[str] = None,
) -> ExtractedInvoice:
    """Extract structured invoice data with Gemini multimodal API and self-correct on validation error."""
    ai_client = client or get_genai_client()
    target_model = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    media_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    contents: List[object] = [
        media_part,
        "Extract the invoice data from this document accurately following all arithmetic guardrails.",
    ]

    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedInvoice,
            system_instruction=SYSTEM_PROMPT,
        )

        try:
            response = ai_client.models.generate_content(
                model=target_model,
                contents=contents,
                config=config,
            )

            raw_text = response.text or ""
            # Parse with Pydantic v2
            invoice = ExtractedInvoice.model_validate_json(raw_text)
            return invoice

        except (ValidationError, json.JSONDecodeError, ValueError) as err:
            last_error = err
            logger.warning(
                "Invoice extraction attempt %d failed validation: %s",
                attempt + 1,
                str(err),
            )
            if attempt < max_retries:
                # Append correction prompt for self-healing loop
                retry_prompt = (
                    f"CRITICAL CORRECTION REQUIRED:\n"
                    f"Your previous extraction failed deterministic validation with error:\n"
                    f"{str(err)}\n\n"
                    f"Previous invalid JSON:\n{getattr(response, 'text', '')}\n\n"
                    f"Recalculate all quantities, unit prices, line totals, subtotal, and total amount. "
                    f"Ensure line_total == quantity * unit_price and subtotal == sum(line_totals). "
                    f"Return the corrected JSON conforming to the schema."
                )
                contents.append(retry_prompt)
            else:
                raise last_error

    if last_error:
        raise last_error
    raise RuntimeError("Failed to extract invoice after maximum retries.")


def evaluate_anomalies(
    invoice: ExtractedInvoice, db_path: Optional[str] = None
) -> Tuple[str, List[PriceAnomalyFlag], Optional[str]]:
    """Evaluate extracted invoice against catalog baselines, price spike threshold, and confidence floor."""
    price_threshold_pct = float(os.getenv("PRICE_SPIKE_THRESHOLD_PCT", "15.0"))
    min_confidence_score = float(os.getenv("MIN_CONFIDENCE_SCORE", "0.85"))

    anomalies: List[PriceAnomalyFlag] = []
    quarantine_reasons: List[str] = []

    # 1. Check confidence floor
    if invoice.extraction_confidence < min_confidence_score:
        quarantine_reasons.append(
            f"Extraction confidence ({invoice.extraction_confidence:.2f}) "
            f"is below threshold ({min_confidence_score:.2f})"
        )

    # 2. Check each line item against catalog
    for item in invoice.items:
        historical_cost = get_historical_unit_cost(item.sku, db_path=db_path)

        if historical_cost is None:
            # Unrecognized SKU
            flag = PriceAnomalyFlag(
                sku=item.sku,
                extracted_unit_price=item.unit_price,
                historical_avg_price=0.0,
                deviation_pct=100.0,
                reason=f"Unrecognized SKU '{item.sku}' not found in inventory catalog",
            )
            anomalies.append(flag)
            quarantine_reasons.append(f"Unrecognized SKU '{item.sku}'")
        else:
            # Calculate price deviation
            deviation_pct = round(
                ((item.unit_price - historical_cost) / historical_cost) * 100.0, 2
            )
            if deviation_pct > price_threshold_pct:
                flag = PriceAnomalyFlag(
                    sku=item.sku,
                    extracted_unit_price=item.unit_price,
                    historical_avg_price=historical_cost,
                    deviation_pct=deviation_pct,
                    reason=(
                        f"Unit price ${item.unit_price:.2f} exceeds historical cost "
                        f"${historical_cost:.2f} by +{deviation_pct:.2f}% "
                        f"(threshold: {price_threshold_pct:.1f}%)"
                    ),
                )
                anomalies.append(flag)
                quarantine_reasons.append(
                    f"Price spike on {item.sku} (+{deviation_pct:.2f}%)"
                )

    if anomalies or quarantine_reasons:
        return (
            "FLAGGED_FOR_REVIEW",
            anomalies,
            "; ".join(quarantine_reasons),
        )

    return ("AUTO_COMMITTED", [], None)
