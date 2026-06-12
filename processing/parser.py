"""Claude and regex-based invoice text parser for DropInvoice."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from invoice.gst_calculator import (
    calculate_tax_breakdown,
    tax_breakdown_total,
)

logger = logging.getLogger("dropinvoice.processing.parser")

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_TAX_RATE = 18.0
DEFAULT_HSN_CODE = "9999"
GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.I)
MONEY_PATTERN = re.compile(r"(?:rs\.?|inr|\u20b9)?\s*(\d+(?:\.\d{1,2})?)", re.I)
ITEM_SKIP_PATTERN = re.compile(r"\b(?:gstin|subtotal|total|grand|cgst|sgst|igst|tax|invoice)\b", re.I)

SYSTEM_PROMPT = """
You extract GST invoice data for DropInvoice from OCR text or voice transcripts.
Return only one valid JSON object with exactly these keys:
seller_name, seller_gstin, buyer_name, buyer_gstin, transaction_type, items,
subtotal, tax_rate, tax_breakdown, grand_total, notes.

Rules:
- transaction_type must be "intra" or "inter".
- items must be a list of objects with description, hsn_code, quantity, unit,
  unit_price, total.
- Use HSN code "9999" when no HSN code is visible.
- Use tax_rate 18.
- Intra-state tax_breakdown is {"type":"CGST+SGST","cgst":x,"sgst":x,"igst":null}.
- Inter-state tax_breakdown is {"type":"IGST","cgst":null,"sgst":null,"igst":x}.
- If GSTIN, buyer, seller, or notes are not present, use null where allowed.
- Do not include markdown, commentary, or extra keys.
""".strip()


class InvoiceParseError(Exception):
    """Raised when invoice text cannot be converted into structured data."""


class GeminiParseError(Exception):
    """Raised when Gemini parsing fails and fallback parsing should run."""


# Alias for backward compatibility
ClaudeParseError = GeminiParseError


def parse_invoice_text(raw_text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse OCR text or a voice transcript into the DropInvoice JSON schema."""

    cleaned_text = clean_input_text(raw_text)
    if not cleaned_text:
        raise InvoiceParseError("Cannot parse empty invoice text.")

    try:
        parsed_data = validate_invoice_data(parse_with_gemini(cleaned_text))
    except Exception as exc:
        logger.warning("Gemini parse failed; using regex fallback: %s", exc)
        parsed_data = validate_invoice_data(parse_with_regex(cleaned_text))

    logger.info("Parsed invoice with %s item(s)", len(parsed_data["items"]))
    return parsed_data


def parse_with_gemini(raw_text: str) -> dict[str, Any]:
    """Send raw invoice text to Google Gemini and return the parsed JSON object."""

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiParseError("GEMINI_API_KEY is not configured.")

    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise GeminiParseError("google-generativeai package is not installed.") from exc

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=get_gemini_model_name(),
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(
            build_user_prompt(raw_text),
            generation_config={"response_mime_type": "application/json"},
        )
        response_text = response.text
    except Exception as exc:
        raise GeminiParseError(f"Gemini API request failed: {exc}") from exc

    if not response_text:
        raise GeminiParseError("Gemini returned an empty response.")

    return load_json_object(response_text)


def get_gemini_model_name() -> str:
    """Return the configured Gemini model name."""

    configured_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    return configured_model or DEFAULT_GEMINI_MODEL


def build_user_prompt(raw_text: str) -> str:
    """Build the Gemini user prompt for invoice extraction."""

    return (
        "Extract the invoice data from this OCR or voice transcript. "
        "Correct obvious OCR spacing and numeric mistakes when context is clear.\n\n"
        f"{raw_text}"
    )


def load_json_object(response_text: str) -> dict[str, Any]:
    """Load the first JSON object found in a model response."""

    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", response_text, re.S | re.I)
    candidate_text = fenced_match.group(1) if fenced_match else response_text
    decoder = json.JSONDecoder()

    for index, character in enumerate(candidate_text):
        if character != "{":
            continue

        try:
            parsed, _ = decoder.raw_decode(candidate_text[index:])
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, dict):
            return parsed

    raise GeminiParseError("Gemini response did not contain a valid JSON object.")


def parse_with_regex(raw_text: str) -> dict[str, Any]:
    """Fallback parser for extracting invoice data without Claude."""

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    gstins = extract_gstins(raw_text)
    items = extract_line_items(lines)
    subtotal = round_money(sum(item["total"] for item in items))
    seller_gstin = gstins[0] if gstins else None
    buyer_gstin = gstins[1] if len(gstins) > 1 else None
    transaction_type = infer_transaction_type(seller_gstin, buyer_gstin)
    tax_breakdown = calculate_tax_breakdown(subtotal, transaction_type)

    return {
        "seller_name": extract_seller_name(lines),
        "seller_gstin": normalize_gstin(seller_gstin),
        "buyer_name": extract_buyer_name(lines),
        "buyer_gstin": normalize_gstin(buyer_gstin),
        "transaction_type": transaction_type,
        "items": items or [build_default_item(raw_text)],
        "subtotal": subtotal,
        "tax_rate": DEFAULT_TAX_RATE,
        "tax_breakdown": tax_breakdown,
        "grand_total": round_money(subtotal + tax_breakdown_total(tax_breakdown)),
        "notes": None,
    }


def extract_gstins(raw_text: str) -> list[str]:
    """Return unique GSTIN-like values in input order."""

    gstins: list[str] = []
    for match in GSTIN_PATTERN.findall(raw_text.upper()):
        if match not in gstins:
            gstins.append(match)

    return gstins


def extract_line_items(lines: list[str]) -> list[dict[str, Any]]:
    """Extract likely line items from OCR or transcript lines."""

    items: list[dict[str, Any]] = []
    for line in lines:
        item = parse_line_item(line)
        if item:
            items.append(item)

    return items


def parse_line_item(line: str) -> dict[str, Any] | None:
    """Parse one text line into an invoice item when it looks item-like."""

    if ITEM_SKIP_PATTERN.search(line) or GSTIN_PATTERN.search(line):
        return None

    amounts = [to_float(match.group(1)) for match in MONEY_PATTERN.finditer(line)]
    amounts = [amount for amount in amounts if amount is not None]
    if not amounts:
        return None

    description = extract_item_description(line)
    if not description:
        return None

    quantity, unit_price, total = infer_item_numbers(amounts)
    return {
        "description": description,
        "hsn_code": DEFAULT_HSN_CODE,
        "quantity": quantity,
        "unit": "pcs",
        "unit_price": unit_price,
        "total": total,
    }


def extract_item_description(line: str) -> str:
    """Extract an item description before numeric price or quantity text."""

    description = re.split(r"(?:rs\.?|inr|\u20b9)?\s*\d", line, maxsplit=1, flags=re.I)[0]
    description = re.sub(r"[^A-Za-z0-9 &/().,-]+", " ", description)
    description = re.sub(r"\s+", " ", description).strip(" -,:")
    return description or "Item"


def infer_item_numbers(amounts: list[float]) -> tuple[float, float, float]:
    """Infer quantity, unit price, and total from numbers found in a line."""

    if len(amounts) >= 3:
        quantity = amounts[0]
        unit_price = amounts[1]
        total = amounts[-1]
        return round_quantity(quantity), round_money(unit_price), round_money(total)

    if len(amounts) == 2:
        quantity = amounts[0]
        total = amounts[1]
        unit_price = total / quantity if quantity else total
        return round_quantity(quantity), round_money(unit_price), round_money(total)

    total = amounts[0]
    return 1.0, round_money(total), round_money(total)


def extract_seller_name(lines: list[str]) -> str:
    """Infer the seller name from the first non-technical text line."""

    for line in lines:
        if not ITEM_SKIP_PATTERN.search(line) and not GSTIN_PATTERN.search(line):
            return line[:120]

    return "Unknown Seller"


def extract_buyer_name(lines: list[str]) -> str | None:
    """Extract a buyer name from common invoice labels when present."""

    for line in lines:
        match = re.search(r"\b(?:buyer|bill\s*to|customer)\s*[:\-]\s*(.+)", line, re.I)
        if match:
            return match.group(1).strip()[:120] or None

    return None


def build_default_item(raw_text: str) -> dict[str, Any]:
    """Build a single conservative fallback item when no line item is found."""

    total = extract_total_amount(raw_text) or 0.0
    return {
        "description": "Unparsed bill items",
        "hsn_code": DEFAULT_HSN_CODE,
        "quantity": 1.0,
        "unit": "pcs",
        "unit_price": round_money(total),
        "total": round_money(total),
    }


def extract_total_amount(raw_text: str) -> float | None:
    """Extract the final visible amount from total-like text."""

    total_matches = re.findall(
        r"\b(?:grand\s*)?total\s*[:=\-]?\s*(?:rs\.?|inr|\u20b9)?\s*(\d+(?:\.\d{1,2})?)",
        raw_text,
        re.I,
    )
    if total_matches:
        return to_float(total_matches[-1])

    amounts = [to_float(match.group(1)) for match in MONEY_PATTERN.finditer(raw_text)]
    amounts = [amount for amount in amounts if amount is not None]
    return amounts[-1] if amounts else None


def validate_invoice_data(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize parsed data to the required invoice JSON schema."""

    if not isinstance(data, dict):
        raise InvoiceParseError("Parsed invoice data must be a JSON object.")

    seller_gstin = normalize_gstin(data.get("seller_gstin"))
    buyer_gstin = normalize_gstin(data.get("buyer_gstin"))
    transaction_type = normalize_transaction_type(
        data.get("transaction_type"),
        seller_gstin,
        buyer_gstin,
    )
    items = normalize_items(data.get("items"))
    subtotal = to_float(data.get("subtotal"))
    if subtotal is None or subtotal <= 0:
        subtotal = round_money(sum(item["total"] for item in items))

    tax_breakdown = calculate_tax_breakdown(subtotal, transaction_type)
    grand_total = to_float(data.get("grand_total"))
    if grand_total is None or grand_total <= 0:
        grand_total = round_money(subtotal + tax_breakdown_total(tax_breakdown))

    return {
        "seller_name": normalize_string(data.get("seller_name"), "Unknown Seller"),
        "seller_gstin": seller_gstin,
        "buyer_name": normalize_optional_string(data.get("buyer_name")),
        "buyer_gstin": buyer_gstin,
        "transaction_type": transaction_type,
        "items": items,
        "subtotal": round_money(subtotal),
        "tax_rate": int(DEFAULT_TAX_RATE),
        "tax_breakdown": tax_breakdown,
        "grand_total": round_money(grand_total),
        "notes": normalize_optional_string(data.get("notes")),
    }


def normalize_items(raw_items: Any) -> list[dict[str, Any]]:
    """Normalize raw item data into required line item dictionaries."""

    if not isinstance(raw_items, list):
        raise InvoiceParseError("Parsed invoice data must include an items list.")

    normalized_items = [normalize_item(item) for item in raw_items if isinstance(item, dict)]
    if not normalized_items:
        raise InvoiceParseError("Parsed invoice data must include at least one item.")

    return normalized_items


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize one parsed line item."""

    quantity = to_float(item.get("quantity")) or 1.0
    unit_price = to_float(item.get("unit_price"))
    total = to_float(item.get("total"))

    if total is None and unit_price is not None:
        total = quantity * unit_price
    if unit_price is None and total is not None:
        unit_price = total / quantity if quantity else total
    if total is None or unit_price is None:
        raise InvoiceParseError("Each item must include unit_price or total.")

    return {
        "description": normalize_string(item.get("description"), "Item"),
        "hsn_code": normalize_string(item.get("hsn_code"), DEFAULT_HSN_CODE),
        "quantity": round_quantity(quantity),
        "unit": normalize_string(item.get("unit"), "pcs"),
        "unit_price": round_money(unit_price),
        "total": round_money(total),
    }


def normalize_transaction_type(
    raw_transaction_type: Any,
    seller_gstin: str | None,
    buyer_gstin: str | None,
) -> str:
    """Normalize or infer the transaction type."""

    if seller_gstin and buyer_gstin:
        return infer_transaction_type(seller_gstin, buyer_gstin)

    transaction_type = str(raw_transaction_type or "").strip().lower()
    if transaction_type in {"intra", "inter"}:
        return transaction_type

    return infer_transaction_type(seller_gstin, buyer_gstin)


def infer_transaction_type(seller_gstin: str | None, buyer_gstin: str | None) -> str:
    """Infer intra/inter-state transaction type from GSTIN state codes."""

    if seller_gstin and buyer_gstin and seller_gstin[:2] != buyer_gstin[:2]:
        return "inter"

    return "intra"


def normalize_string(value: Any, default: str) -> str:
    """Normalize a required string value with a fallback default."""

    if value is None:
        return default

    normalized = re.sub(r"\s+", " ", str(value)).strip()
    return normalized or default


def normalize_optional_string(value: Any) -> str | None:
    """Normalize an optional string value."""

    if value is None:
        return None

    normalized = re.sub(r"\s+", " ", str(value)).strip()
    if normalized.lower() in {"", "none", "null", "n/a", "na"}:
        return None

    return normalized


def normalize_gstin(value: Any) -> str | None:
    """Normalize an optional GSTIN value to uppercase."""

    normalized = normalize_optional_string(value)
    return normalized.upper() if normalized else None


def to_float(value: Any) -> float | None:
    """Convert numbers and numeric strings into floats."""

    if value is None:
        return None

    if isinstance(value, int | float):
        return float(value)

    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    if cleaned in {"", ".", "-", "-."}:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def round_money(value: float) -> float:
    """Round monetary values to two decimals."""

    return round(float(value), 2)


def round_quantity(value: float) -> float:
    """Round quantity values while keeping fractional quantities possible."""

    return round(float(value), 3)


def clean_input_text(raw_text: str) -> str:
    """Normalize raw OCR or transcript text before parsing."""

    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(raw_text).splitlines()]
    useful_lines = [line for line in lines if line]
    return "\n".join(useful_lines).strip()
