"""Tally (Prime / ERP 9) integration for DropInvoice.

Tally exposes an XML-over-HTTP gateway. When Tally is running as a server
(Help -> Settings -> Connectivity -> Client/Server -> "Tally is acting as Server",
default port 9000), you can POST an "Import Data" envelope to create vouchers
programmatically. This module converts DropInvoice invoices into Tally Sales
Vouchers and pushes them — one invoice or many in a single batch.

Configuration (environment variables):
- TALLY_ENABLED          : "true" to push invoices to Tally (default off).
- TALLY_URL              : Tally gateway URL (default http://localhost:9000).
- TALLY_COMPANY          : Company name in Tally to import into (required to push).
- TALLY_SALES_LEDGER     : Sales ledger name (default "Sales").
- TALLY_DEFAULT_PARTY    : Party ledger for walk-in customers (default "Cash").
- TALLY_CGST_LEDGER / TALLY_SGST_LEDGER / TALLY_IGST_LEDGER : tax ledger names.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

logger = logging.getLogger("dropinvoice.integrations.tally")

DEFAULT_TALLY_URL = "http://localhost:9000"
REQUEST_TIMEOUT_SECONDS = 30


def is_tally_enabled() -> bool:
    """Return True when Tally push is turned on via TALLY_ENABLED."""

    return os.getenv("TALLY_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# Public push API
# ---------------------------------------------------------------------------

def push_invoice_to_tally(invoice_data: dict[str, Any]) -> dict[str, Any]:
    """Push a single invoice to Tally as a Sales Voucher. Best-effort."""

    return push_invoices_to_tally([invoice_data])


def push_invoices_to_tally(invoices: list[dict[str, Any]]) -> dict[str, Any]:
    """Push one or more invoices to Tally in a single Import Data request.

    Returns a result dict with ``ok`` and a ``detail`` message. Never raises —
    Tally connectivity is environment-dependent and must not break the pipeline.
    """

    if not invoices:
        return {"ok": False, "detail": "No invoices to push."}

    company = os.getenv("TALLY_COMPANY", "").strip()
    if not company:
        return {"ok": False, "detail": "TALLY_COMPANY is not configured."}

    try:
        import requests
    except ImportError:
        return {"ok": False, "detail": "requests package is not installed."}

    xml = build_tally_import_xml(invoices, company)
    url = os.getenv("TALLY_URL", DEFAULT_TALLY_URL).strip() or DEFAULT_TALLY_URL

    try:
        response = requests.post(
            url,
            data=xml.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - connectivity is best-effort
        logger.warning("Tally push failed: %s", exc)
        return {"ok": False, "detail": f"Tally request failed: {exc}"}

    body = response.text or ""
    created, errors = _parse_tally_response(body)
    ok = errors == 0 and "<LINEERROR>" not in body
    logger.info("Tally push: created=%s errors=%s", created, errors)
    return {"ok": ok, "detail": body[:500], "created": created, "errors": errors}


# ---------------------------------------------------------------------------
# XML builders
# ---------------------------------------------------------------------------

def build_tally_import_xml(invoices: list[dict[str, Any]], company: str) -> str:
    """Build a Tally 'Import Data' envelope containing one voucher per invoice."""

    messages = "".join(build_sales_voucher_xml(invoice) for invoice in invoices)
    return (
        "<ENVELOPE>"
        "<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>"
        "<BODY><IMPORTDATA>"
        "<REQUESTDESC>"
        "<REPORTNAME>Vouchers</REPORTNAME>"
        f"<STATICVARIABLES><SVCURRENTCOMPANY>{_x(company)}</SVCURRENTCOMPANY></STATICVARIABLES>"
        "</REQUESTDESC>"
        f"<REQUESTDATA>{messages}</REQUESTDATA>"
        "</IMPORTDATA></BODY>"
        "</ENVELOPE>"
    )


def build_sales_voucher_xml(invoice_data: dict[str, Any]) -> str:
    """Build a single Tally Sales Voucher <TALLYMESSAGE> for an invoice."""

    party = (
        invoice_data.get("buyer_name")
        or os.getenv("TALLY_DEFAULT_PARTY", "Cash").strip()
        or "Cash"
    )
    sales_ledger = os.getenv("TALLY_SALES_LEDGER", "Sales").strip() or "Sales"
    invoice_number = str(invoice_data.get("invoice_number") or "")
    voucher_date = _tally_date(invoice_data.get("date"))
    subtotal = _num(invoice_data.get("subtotal"))
    grand_total = _num(invoice_data.get("grand_total")) or subtotal
    narration = invoice_data.get("notes") or "Created by DropInvoice"

    # Party is debited the full amount (negative = debit in Tally's convention).
    entries = [
        _ledger_entry(party, is_deemed_positive=True, amount=-grand_total),
        # Sales ledger credited with the taxable + non-taxable value.
        _ledger_entry(sales_ledger, is_deemed_positive=False, amount=subtotal),
    ]
    entries.extend(_tax_ledger_entries(invoice_data.get("tax_breakdown") or {}))

    return (
        "<TALLYMESSAGE xmlns:UDF=\"TallyUDF\">"
        "<VOUCHER VCHTYPE=\"Sales\" ACTION=\"Create\" OBJVIEW=\"Invoice Voucher View\">"
        f"<DATE>{voucher_date}</DATE>"
        f"<EFFECTIVEDATE>{voucher_date}</EFFECTIVEDATE>"
        "<VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>"
        f"<VOUCHERNUMBER>{_x(invoice_number)}</VOUCHERNUMBER>"
        f"<PARTYLEDGERNAME>{_x(party)}</PARTYLEDGERNAME>"
        f"<BASICBUYERNAME>{_x(party)}</BASICBUYERNAME>"
        "<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>"
        f"<NARRATION>{_x(str(narration))}</NARRATION>"
        f"{''.join(entries)}"
        "</VOUCHER>"
        "</TALLYMESSAGE>"
    )


def _tax_ledger_entries(tax_breakdown: dict[str, Any]) -> list[str]:
    """Build credit ledger entries for CGST/SGST/IGST when present."""

    entries: list[str] = []
    cgst = _num(tax_breakdown.get("cgst"))
    sgst = _num(tax_breakdown.get("sgst"))
    igst = _num(tax_breakdown.get("igst"))

    if igst:
        ledger = os.getenv("TALLY_IGST_LEDGER", "Output IGST").strip() or "Output IGST"
        entries.append(_ledger_entry(ledger, is_deemed_positive=False, amount=igst))
    if cgst:
        ledger = os.getenv("TALLY_CGST_LEDGER", "Output CGST").strip() or "Output CGST"
        entries.append(_ledger_entry(ledger, is_deemed_positive=False, amount=cgst))
    if sgst:
        ledger = os.getenv("TALLY_SGST_LEDGER", "Output SGST").strip() or "Output SGST"
        entries.append(_ledger_entry(ledger, is_deemed_positive=False, amount=sgst))

    return entries


def _ledger_entry(name: str, is_deemed_positive: bool, amount: float) -> str:
    """Build one <ALLLEDGERENTRIES.LIST> entry. Amount sign follows Tally rules."""

    return (
        "<ALLLEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{_x(name)}</LEDGERNAME>"
        f"<ISDEEMEDPOSITIVE>{'Yes' if is_deemed_positive else 'No'}</ISDEEMEDPOSITIVE>"
        f"<AMOUNT>{amount:.2f}</AMOUNT>"
        "</ALLLEDGERENTRIES.LIST>"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_tally_response(body: str) -> tuple[int, int]:
    """Extract the created/error counts from a Tally import response."""

    return _tag_int(body, "CREATED"), _tag_int(body, "ERRORS")


def _tag_int(body: str, tag: str) -> int:
    """Return the integer inside <tag>...</tag>, or 0."""

    import re

    match = re.search(rf"<{tag}>(\d+)</{tag}>", body)
    return int(match.group(1)) if match else 0


def _tally_date(value: Any) -> str:
    """Format a date as Tally's YYYYMMDD."""

    text = str(value or "").strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    return date.today().strftime("%Y%m%d")


def _num(value: Any) -> float:
    """Coerce a value to float, defaulting to 0.0."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _x(text: Any) -> str:
    """Escape text for XML."""

    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
