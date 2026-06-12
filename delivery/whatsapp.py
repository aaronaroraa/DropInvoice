"""Twilio WhatsApp delivery helpers for DropInvoice invoices."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from invoice.gst_calculator import tax_breakdown_total, to_float

logger = logging.getLogger("dropinvoice.delivery.whatsapp")


class WhatsAppDeliveryError(Exception):
    """Raised when WhatsApp invoice delivery cannot be completed."""


def send_invoice_pdf(
    to_number: str,
    pdf_path: str | Path,
    invoice_data: dict[str, Any],
    pdf_url: str | None = None,
) -> str:
    """Send an invoice PDF as WhatsApp media and return Twilio message SID."""

    media_url = pdf_url or str(invoice_data.get("pdf_url") or "")
    if not media_url.startswith(("http://", "https://")):
        raise WhatsAppDeliveryError("Twilio media delivery requires a public PDF URL.")

    client = build_twilio_client()
    from_number = get_required_env("TWILIO_WHATSAPP_FROM")
    normalized_to_number = ensure_whatsapp_prefix(to_number)
    message_body = build_invoice_summary(invoice_data)

    try:
        message = client.messages.create(
            from_=from_number,
            to=normalized_to_number,
            body=message_body,
            media_url=[media_url],
        )
    except Exception as exc:
        raise WhatsAppDeliveryError(f"Could not send WhatsApp invoice to {to_number}") from exc

    logger.info("Sent WhatsApp invoice %s to %s", invoice_data.get("invoice_number"), to_number)
    return str(message.sid)


def send_summary_message(to_number: str, invoice_data: dict[str, Any]) -> str:
    """Send a text-only WhatsApp invoice summary and return Twilio message SID."""

    client = build_twilio_client()
    from_number = get_required_env("TWILIO_WHATSAPP_FROM")

    try:
        message = client.messages.create(
            from_=from_number,
            to=ensure_whatsapp_prefix(to_number),
            body=build_invoice_summary(invoice_data),
        )
    except Exception as exc:
        raise WhatsAppDeliveryError(f"Could not send WhatsApp summary to {to_number}") from exc

    return str(message.sid)


def build_invoice_summary(invoice_data: dict[str, Any]) -> str:
    """Build a compact WhatsApp invoice summary message."""

    invoice_number = invoice_data.get("invoice_number") or "your invoice"
    grand_total = format_money(invoice_data.get("grand_total"))
    subtotal = format_money(invoice_data.get("subtotal"))
    tax_breakdown = invoice_data.get("tax_breakdown") or {}
    total_tax = format_money(tax_breakdown_total(tax_breakdown))

    return (
        f"Your GST invoice {invoice_number} is ready.\n"
        f"Subtotal: {subtotal}\n"
        f"GST: {total_tax}\n"
        f"Grand Total: {grand_total}"
    )


def build_twilio_client() -> Any:
    """Build a Twilio REST client from environment credentials."""

    account_sid = get_required_env("TWILIO_ACCOUNT_SID")
    auth_token = get_required_env("TWILIO_AUTH_TOKEN")

    try:
        from twilio.rest import Client
    except ImportError as exc:
        raise WhatsAppDeliveryError("twilio package is not installed.") from exc

    return Client(account_sid, auth_token)


def ensure_whatsapp_prefix(phone_number: str) -> str:
    """Return a phone number formatted for Twilio WhatsApp messaging."""

    normalized = str(phone_number).strip()
    if normalized.startswith("whatsapp:"):
        return normalized

    return f"whatsapp:{normalized}"


def get_required_env(name: str) -> str:
    """Return a required environment variable or raise a delivery error."""

    value = os.getenv(name)
    if not value:
        raise WhatsAppDeliveryError(f"{name} is required for WhatsApp delivery.")

    return value


def format_money(value: Any) -> str:
    """Format a numeric value as an INR amount for WhatsApp text."""

    numeric_value = to_float(value) or 0.0
    return f"INR {numeric_value:,.2f}"
