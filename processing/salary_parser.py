"""Salary payslip parser for DropInvoice — kept separate from sales invoices.

Turns a text message or a photo into a structured payslip (earnings, deductions,
net pay). This is a distinct document type from the GST sales invoice so the two
never get jumbled. Falls back to a simple "name + amount paid" payslip when only
that is given.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("dropinvoice.processing.salary")

SALARY_SCHEMA_KEYS = (
    "employee_name", "designation", "employer_name", "pay_period",
    "earnings", "deductions", "gross_earnings", "total_deductions", "net_pay", "notes",
)

SALARY_SYSTEM_PROMPT = """
You are DropInvoice's payroll assistant for India. You ALWAYS return exactly one
valid JSON payslip object — never refuse, never ask, never return empty. Convert
the input (a typed message or a payslip photo) into this structure with EXACTLY
these keys:
employee_name, designation, employer_name, pay_period, earnings, deductions,
gross_earnings, total_deductions, net_pay, notes.

Rules:
- earnings: list of {"component": str, "amount": number} (e.g. Basic, HRA,
  Conveyance, Special Allowance, Bonus, Overtime). If only a single total salary
  is given, use one earning {"component": "Salary", "amount": <amount>}.
- deductions: list of {"component": str, "amount": number} (e.g. PF, ESI,
  Professional Tax, TDS, Advance, Loan). Use an empty list if none.
- gross_earnings = sum of all earnings. total_deductions = sum of all deductions.
  net_pay = gross_earnings - total_deductions.
- Expand Indian shorthand: 1k=1000, 1.5k=1500, 1L/1 lakh=100000, 1cr=10000000.
- A payslip has NO GST. Never add tax.
- Keep the employee's name exactly as written. Use null for unknown
  designation / employer_name / pay_period / notes.
- If the input is unclear, still return a payslip: one earning "Salary" with the
  amount you can find (or 0), and explain in "notes". Output only the JSON object.
""".strip()

SALARY_USER_PROMPT = (
    "Extract this into a single payslip JSON object matching the required schema. "
    "Handle handwriting and Indian shorthand. Do not invent values you cannot see."
)


class SalaryParseError(Exception):
    """Raised when salary text cannot be converted into a payslip."""


def parse_salary_text(raw_text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse a typed salary message into a validated payslip."""

    from processing.parser import expand_indian_shorthand

    cleaned = expand_indian_shorthand(str(raw_text or "")).strip()
    if not cleaned:
        raise SalaryParseError("Cannot parse empty salary text.")

    try:
        data = _gemini_salary(_text_request(cleaned))
    except Exception as exc:
        logger.warning("Gemini salary parse failed; using regex fallback: %s", exc)
        data = _regex_salary(cleaned)

    return validate_salary_data(data)


def parse_salary_from_image(image_path: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse a payslip image into a validated payslip via Gemini Vision."""

    import mimetypes
    from pathlib import Path

    image_bytes = Path(image_path).read_bytes()
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    data = _gemini_salary([SALARY_USER_PROMPT, {"mime_type": mime_type, "data": image_bytes}])
    return validate_salary_data(data)


def _text_request(cleaned: str) -> list[Any]:
    return [f"{SALARY_USER_PROMPT}\n\n{cleaned}"]


def _gemini_salary(contents: list[Any]) -> dict[str, Any]:
    """Call Gemini with the salary system prompt and return the parsed JSON."""

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SalaryParseError("GEMINI_API_KEY is not configured.")

    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise SalaryParseError("google-generativeai is not installed.") from exc

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name, system_instruction=SALARY_SYSTEM_PROMPT)
    response = model.generate_content(
        contents, generation_config={"response_mime_type": "application/json"}
    )
    text = response.text
    if not text:
        raise SalaryParseError("Gemini returned an empty payslip.")
    return _load_json(text)


def _regex_salary(text: str) -> dict[str, Any]:
    """Very small fallback: pull a name and the largest amount as the salary."""

    amounts = [float(m) for m in re.findall(r"\d+(?:\.\d+)?", text)]
    salary = max(amounts) if amounts else 0.0
    name = re.sub(r"[\d,]", "", text)
    name = re.sub(r"\b(salary|payslip|wages|tankhwa|for|of|rs|inr)\b", " ", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" -,:") or "Employee"
    return {
        "employee_name": name[:80],
        "earnings": [{"component": "Salary", "amount": salary}],
        "deductions": [],
        "notes": "Parsed from a simple message.",
    }


def validate_salary_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize parsed data into the payslip schema. Never fails."""

    if not isinstance(data, dict):
        data = {}

    earnings = _normalize_components(data.get("earnings"))
    deductions = _normalize_components(data.get("deductions"))
    if not earnings:
        earnings = [{"component": "Salary", "amount": _money(data.get("net_pay")) or 0.0}]

    gross = _money(data.get("gross_earnings"))
    if gross is None or gross <= 0:
        gross = round(sum(item["amount"] for item in earnings), 2)
    total_deductions = _money(data.get("total_deductions"))
    if total_deductions is None or total_deductions < 0:
        total_deductions = round(sum(item["amount"] for item in deductions), 2)
    net_pay = _money(data.get("net_pay"))
    if net_pay is None:
        net_pay = round(gross - total_deductions, 2)

    return {
        "employee_name": _text(data.get("employee_name"), "Employee"),
        "designation": _opt_text(data.get("designation")),
        "employer_name": _opt_text(data.get("employer_name")),
        "pay_period": _opt_text(data.get("pay_period")),
        "earnings": earnings,
        "deductions": deductions,
        "gross_earnings": round(gross, 2),
        "total_deductions": round(total_deductions, 2),
        "net_pay": round(net_pay, 2),
        "notes": _opt_text(data.get("notes")),
    }


def _normalize_components(raw: Any) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            amount = _money(entry.get("amount"))
            if amount is None:
                continue
            components.append({
                "component": _text(entry.get("component"), "Item"),
                "amount": round(amount, 2),
            })
    return components


def _load_json(text: str) -> dict[str, Any]:
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S | re.I)
    candidate = match.group(1) if match else text
    decoder = json.JSONDecoder()
    for index, char in enumerate(candidate):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(candidate[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise SalaryParseError("No valid JSON object in payslip response.")


def _money(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(cleaned) if cleaned not in {"", ".", "-"} else None
    except ValueError:
        return None


def _text(value: Any, default: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or default


def _opt_text(value: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or None
