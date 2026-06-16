"""GST calculation utilities for DropInvoice invoices."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from invoice.gst_rates import (
    DEFAULT_GST_RATE,
    resolve_item_gst_rate,
)


class GSTCalculationError(Exception):
    """Raised when invoice data cannot be used for GST calculation."""


def calculate_gst(invoice_data: dict[str, Any]) -> dict[str, Any]:
    """Return invoice data with per-item GST rates, mixed-rate tax, and totals.

    Each item's GST rate is resolved from its HSN code / description / suggested
    rate via the embedded GST engine, so bills with mixed slabs (e.g. 0% atta +
    5% spices + 18% soap) are taxed correctly. The aggregate ``tax_breakdown``
    keeps its CGST/SGST/IGST shape for backward compatibility, and a rate-wise
    ``rate_summary`` is added for GST-compliant reporting.
    """

    normalized_invoice = deepcopy(invoice_data)
    items = normalized_invoice.get("items")
    if not isinstance(items, list) or not items:
        raise GSTCalculationError("Invoice data must include at least one item.")

    transaction_type = determine_transaction_type(normalized_invoice)

    # Bucket taxable amounts by resolved GST rate, tagging each item with its rate.
    taxable_by_rate: dict[float, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        taxable = get_item_total(item)
        rate = resolve_item_gst_rate(
            item.get("hsn_code"),
            item.get("description"),
            item.get("gst_rate"),
        )
        item["gst_rate"] = rate
        taxable_by_rate[rate] = taxable_by_rate.get(rate, 0.0) + taxable

    subtotal = round_money(sum(taxable_by_rate.values()))
    rate_summary, total_cgst, total_sgst, total_igst = _summarize_rates(
        taxable_by_rate, transaction_type
    )

    if transaction_type == "inter":
        tax_breakdown = {
            "type": "IGST",
            "cgst": None,
            "sgst": None,
            "igst": round_money(total_igst),
        }
    else:
        tax_breakdown = {
            "type": "CGST+SGST",
            "cgst": round_money(total_cgst),
            "sgst": round_money(total_sgst),
            "igst": None,
        }

    total_tax = round_money(total_cgst + total_sgst + total_igst)
    representative_rate = (
        max(taxable_by_rate, key=lambda r: taxable_by_rate[r])
        if taxable_by_rate
        else DEFAULT_GST_RATE
    )

    normalized_invoice["transaction_type"] = transaction_type
    normalized_invoice["subtotal"] = subtotal
    normalized_invoice["tax_rate"] = int(representative_rate)
    normalized_invoice["tax_breakdown"] = tax_breakdown
    normalized_invoice["rate_summary"] = rate_summary
    normalized_invoice["grand_total"] = round_money(subtotal + total_tax)

    return normalized_invoice


def _summarize_rates(
    taxable_by_rate: dict[float, float],
    transaction_type: str,
) -> tuple[list[dict[str, Any]], float, float, float]:
    """Build a rate-wise GST summary and aggregate CGST/SGST/IGST totals."""

    normalized_type = normalize_transaction_type(transaction_type)
    rate_summary: list[dict[str, Any]] = []
    total_cgst = total_sgst = total_igst = 0.0

    for rate in sorted(taxable_by_rate):
        taxable = round_money(taxable_by_rate[rate])
        tax = round_money(taxable * rate / 100)

        if normalized_type == "inter":
            rate_summary.append({
                "rate": rate, "taxable": taxable,
                "cgst": None, "sgst": None, "igst": tax, "total_tax": tax,
            })
            total_igst += tax
        else:
            half = round_money(tax / 2)
            rate_summary.append({
                "rate": rate, "taxable": taxable,
                "cgst": half, "sgst": half, "igst": None,
                "total_tax": round_money(half * 2),
            })
            total_cgst += half
            total_sgst += half

    return rate_summary, total_cgst, total_sgst, total_igst


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
