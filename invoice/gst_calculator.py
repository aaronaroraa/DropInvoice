"""GST calculation utilities for DropInvoice invoices."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_GST_RATE = 18.0


class GSTCalculationError(Exception):
    """Raised when invoice data cannot be used for GST calculation."""


def calculate_gst(invoice_data: dict[str, Any]) -> dict[str, Any]:
    """Return invoice data with transaction type, subtotal, tax, and grand total."""

    normalized_invoice = deepcopy(invoice_data)
    items = normalized_invoice.get("items")
    if not isinstance(items, list) or not items:
        raise GSTCalculationError("Invoice data must include at least one item.")

    subtotal = get_invoice_subtotal(normalized_invoice)
    transaction_type = determine_transaction_type(normalized_invoice)
    tax_breakdown = calculate_tax_breakdown(subtotal, transaction_type)

    normalized_invoice["transaction_type"] = transaction_type
    normalized_invoice["subtotal"] = round_money(subtotal)
    normalized_invoice["tax_rate"] = int(DEFAULT_GST_RATE)
    normalized_invoice["tax_breakdown"] = tax_breakdown
    normalized_invoice["grand_total"] = round_money(subtotal + tax_breakdown_total(tax_breakdown))

    return normalized_invoice


def calculate_gst_breakdown(invoice_data: dict[str, Any]) -> dict[str, Any]:
    """Return the complete GST tax breakdown for parsed invoice data."""

    subtotal = get_invoice_subtotal(invoice_data)
    transaction_type = determine_transaction_type(invoice_data)
    return calculate_tax_breakdown(subtotal, transaction_type)


def determine_transaction_type(invoice_data: dict[str, Any]) -> str:
    """Determine intra/inter-state transaction type from GSTIN state codes."""

    seller_state_code = extract_state_code(invoice_data.get("seller_gstin"))
    buyer_state_code = extract_state_code(invoice_data.get("buyer_gstin"))

    if seller_state_code and buyer_state_code and seller_state_code != buyer_state_code:
        return "inter"

    if seller_state_code and buyer_state_code:
        return "intra"

    explicit_type = str(invoice_data.get("transaction_type") or "").strip().lower()
    if explicit_type in {"intra", "inter"}:
        return explicit_type

    return "intra"


def calculate_tax_breakdown(subtotal: float, transaction_type: str) -> dict[str, Any]:
    """Calculate CGST+SGST for intra-state or IGST for inter-state invoices."""

    normalized_type = normalize_transaction_type(transaction_type)
    total_tax = round_money(subtotal * DEFAULT_GST_RATE / 100)

    if normalized_type == "inter":
        return {
            "type": "IGST",
            "cgst": None,
            "sgst": None,
            "igst": total_tax,
        }

    split_tax = round_money(total_tax / 2)
    return {
        "type": "CGST+SGST",
        "cgst": split_tax,
        "sgst": split_tax,
        "igst": None,
    }


def get_invoice_subtotal(invoice_data: dict[str, Any]) -> float:
    """Return invoice subtotal from explicit subtotal or item totals."""

    explicit_subtotal = to_float(invoice_data.get("subtotal"))
    if explicit_subtotal is not None and explicit_subtotal > 0:
        return round_money(explicit_subtotal)

    item_total = 0.0
    for item in invoice_data.get("items", []):
        if not isinstance(item, dict):
            continue

        item_total += get_item_total(item)

    return round_money(item_total)


def get_item_total(item: dict[str, Any]) -> float:
    """Return a line item total from total or quantity multiplied by unit price."""

    explicit_total = to_float(item.get("total"))
    if explicit_total is not None:
        return round_money(explicit_total)

    quantity = to_float(item.get("quantity")) or 1.0
    unit_price = to_float(item.get("unit_price")) or 0.0
    return round_money(quantity * unit_price)


def extract_state_code(gstin: Any) -> str | None:
    """Return the two-digit Indian GST state code from a GSTIN."""

    if gstin is None:
        return None

    normalized_gstin = str(gstin).strip().upper()
    if len(normalized_gstin) < 2 or not normalized_gstin[:2].isdigit():
        return None

    return normalized_gstin[:2]


def normalize_transaction_type(transaction_type: str) -> str:
    """Normalize transaction type and reject unsupported values."""

    normalized_type = str(transaction_type or "").strip().lower()
    if normalized_type not in {"intra", "inter"}:
        raise GSTCalculationError("Transaction type must be 'intra' or 'inter'.")

    return normalized_type


def tax_breakdown_total(tax_breakdown: dict[str, Any]) -> float:
    """Return the total GST amount from a tax breakdown dictionary."""

    return round_money(
        (to_float(tax_breakdown.get("cgst")) or 0.0)
        + (to_float(tax_breakdown.get("sgst")) or 0.0)
        + (to_float(tax_breakdown.get("igst")) or 0.0)
    )


def to_float(value: Any) -> float | None:
    """Convert numeric input into a float when possible."""

    if value is None:
        return None

    if isinstance(value, int | float):
        return float(value)

    cleaned = "".join(character for character in str(value) if character.isdigit() or character in ".-")
    if cleaned in {"", ".", "-", "-."}:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def round_money(value: float) -> float:
    """Round monetary values to two decimal places."""

    return round(float(value), 2)
