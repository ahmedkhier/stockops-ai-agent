"""Asynchronous HTTP webhook notifier for stock and invoice anomalies."""

import logging
import os
from typing import Optional
import httpx
from app.schemas import ProcessingResult

logger = logging.getLogger(__name__)


async def trigger_anomaly_webhook(
    result: ProcessingResult, webhook_url: Optional[str] = None
) -> bool:
    """Dispatch an asynchronous alert webhook to an external endpoint (e.g. n8n or Slack).

    Returns True if successfully delivered (HTTP 2xx), or False if failed/skipped.
    Network and endpoint errors are logged gracefully without raising exceptions.
    """
    url = webhook_url or os.getenv("ALERT_WEBHOOK_URL")
    if not url or url.strip() == "":
        logger.info("No ALERT_WEBHOOK_URL configured. Skipping webhook dispatch.")
        return False

    payload = {
        "event": "INVOICE_ANOMALY_DETECTED",
        "status": result.status,
        "review_reason": result.review_reason,
        "invoice_number": result.invoice.invoice_number,
        "supplier_name": result.invoice.supplier_name,
        "total_amount": result.invoice.total_amount,
        "extraction_confidence": result.invoice.extraction_confidence,
        "anomalies": [anomaly.model_dump() for anomaly in result.anomalies],
        "full_invoice": result.invoice.model_dump(mode="json"),
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.info("Successfully delivered anomaly webhook to %s (status %d)", url, response.status_code)
            return True
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Webhook endpoint %s returned HTTP error: %d %s",
            url,
            exc.response.status_code,
            exc.response.text,
        )
        return False
    except httpx.RequestError as exc:
        logger.warning("Failed to deliver webhook to %s due to network error: %s", url, str(exc))
        return False
    except Exception as exc:
        logger.warning("Unexpected error during webhook dispatch to %s: %s", url, str(exc))
        return False
