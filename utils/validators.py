"""GSTIN validation, state code extraction, and phone number formatting for DropInvoice.

Assumptions:
- GSTIN follows the 15-character Indian format: 2-digit state code, 10-char PAN,
  1-char entity number, 1-char 'Z' (literal), 1-char check digit.
- The check digit is validated using the government-prescribed modulo-36 algorithm.
- Indian phone numbers may arrive as 10 digits, with 0-prefix, +91-prefix,
  or with the Twilio ``whatsapp:`` prefix.
- Valid GSTIN state codes range from 01 (Jammu & Kashmir) to 38 (Ladakh).
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# GSTIN constants
# ---------------------------------------------------------------------------

# Official GSTIN structural pattern:
# [01-38][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]
GSTIN_REGEX = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
)

# Characters used in the modulo-36 checksum algorithm (0-9 then A-Z)
GSTIN_CHECKSUM_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Valid Indian GST state/UT codes (01 through 38 with gaps)
VALID_STATE_CODES: set[str] = {
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
    "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
    "21", "22", "23", "24", "25", "26", "27", "28", "29", "30",
    "31", "32", "33", "34", "35", "36", "37", "38",
}

# Map state codes to state names for display / debugging
STATE_CODE_MAP: dict[str, str] = {
    "01": "Jammu & Kashmir",    "02": "Himachal Pradesh",
    "03": "Punjab",             "04": "Chandigarh",
    "05": "Uttarakhand",        "06": "Haryana",
    "07": "Delhi",              "08": "Rajasthan",
    "09": "Uttar Pradesh",      "10": "Bihar",
    "11": "Sikkim",             "12": "Arunachal Pradesh",
    "13": "Nagaland",           "14": "Manipur",
    "15": "Mizoram",            "16": "Tripura",
    "17": "Meghalaya",          "18": "Assam",
    "19": "West Bengal",        "20": "Jharkhand",
    "21": "Odisha",             "22": "Chhattisgarh",
    "23": "Madhya Pradesh",     "24": "Gujarat",
    "25": "Daman & Diu",        "26": "Dadra & Nagar Haveli",
    "27": "Maharashtra",        "28": "Andhra Pradesh (Old)",
    "29": "Karnataka",          "30": "Goa",
    "31": "Lakshadweep",        "32": "Kerala",
    "33": "Tamil Nadu",         "34": "Puducherry",
    "35": "Andaman & Nicobar",  "36": "Telangana",
    "37": "Andhra Pradesh",     "38": "Ladakh",
}


# ---------------------------------------------------------------------------
# GSTIN validation
# ---------------------------------------------------------------------------

class InvalidGSTINError(Exception):
    """Raised when a GSTIN string fails structural or checksum validation."""


def validate_gstin(gstin: str) -> bool:
    """Validate a 15-character Indian GSTIN for format and checksum.

    Args:
        gstin: The raw GSTIN string to validate.

    Returns:
        ``True`` if the GSTIN is valid.

    Raises:
        InvalidGSTINError: If the GSTIN fails any validation check.
    """

    normalized = gstin.strip().upper()

    # Check length
    if len(normalized) != 15:
        raise InvalidGSTINError(
            f"GSTIN must be 15 characters, got {len(normalized)}: '{gstin}'"
        )

    # Check structural regex
    if not GSTIN_REGEX.match(normalized):
        raise InvalidGSTINError(
            f"GSTIN does not match the required format: '{gstin}'"
        )

    # Check state code is in the valid set
    state_code = normalized[:2]
    if state_code not in VALID_STATE_CODES:
        raise InvalidGSTINError(
            f"Invalid state code '{state_code}' in GSTIN: '{gstin}'"
        )

    # Validate the check digit using modulo-36 algorithm
    expected_check = _compute_gstin_check_digit(normalized[:14])
    actual_check = normalized[14]
    if actual_check != expected_check:
        raise InvalidGSTINError(
            f"GSTIN check digit mismatch: expected '{expected_check}', "
            f"got '{actual_check}' in '{gstin}'"
        )

    return True


def is_valid_gstin(gstin: str | None) -> bool:
    """Return True if the GSTIN is valid, False otherwise (no exception).

    Convenience wrapper around ``validate_gstin`` for conditional checks.
    """

    if not gstin:
        return False

    try:
        return validate_gstin(gstin)
    except (InvalidGSTINError, Exception):
        return False


def _compute_gstin_check_digit(first_14: str) -> str:
    """Compute the GSTIN check digit using the Indian government's modulo-36 algorithm.

    The algorithm processes the first 14 characters of a GSTIN:
    - Each character is converted to its positional value (0-35).
    - Odd-positioned values are multiplied by 2 and reduced via a specific formula.
    - The sum modulo 36, subtracted from 36 and taken modulo 36 again, gives
      the check digit index.

    Args:
        first_14: The first 14 characters of the GSTIN.

    Returns:
        The expected check digit character (0-9 or A-Z).
    """

    total = 0
    for index, char in enumerate(first_14.upper()):
        # Map character to its numeric position (0-35)
        position = GSTIN_CHECKSUM_CHARS.index(char)

        if index % 2 != 0:
            # Odd-position (1-indexed): multiply by 2 and reduce
            position *= 2
            # Quotient + remainder when divided by 36
            position = (position // 36) + (position % 36)

        total += position

    # Final check digit calculation
    remainder = total % 36
    check_index = (36 - remainder) % 36
    return GSTIN_CHECKSUM_CHARS[check_index]


# ---------------------------------------------------------------------------
# State code extraction
# ---------------------------------------------------------------------------

def extract_state_code(gstin: str | None) -> str | None:
    """Extract the 2-digit Indian GST state code from a GSTIN.

    Args:
        gstin: A GSTIN string (at least 2 characters) or ``None``.

    Returns:
        The 2-digit state code string if valid, else ``None``.
    """

    if not gstin:
        return None

    normalized = str(gstin).strip().upper()
    if len(normalized) < 2 or not normalized[:2].isdigit():
        return None

    code = normalized[:2]
    return code if code in VALID_STATE_CODES else None


def get_state_name(gstin_or_code: str | None) -> str | None:
    """Return the Indian state name for a GSTIN or state code.

    Args:
        gstin_or_code: A full GSTIN or a 2-digit state code.

    Returns:
        The state name, or ``None`` if unrecognized.
    """

    code = extract_state_code(gstin_or_code)
    if code and len(str(gstin_or_code or "").strip()) == 2:
        # Directly passed a state code
        code = str(gstin_or_code).strip().zfill(2)

    return STATE_CODE_MAP.get(code or "") if code else None


# ---------------------------------------------------------------------------
# Phone number formatting
# ---------------------------------------------------------------------------

# Indian mobile numbers: 10 digits starting with 6-9
INDIAN_MOBILE_REGEX = re.compile(r"^[6-9]\d{9}$")


class InvalidPhoneError(Exception):
    """Raised when a phone number cannot be normalized to +91 format."""


def format_phone_number(phone: Any) -> str:
    """Normalize an Indian phone number to E.164 format (+91XXXXXXXXXX).

    Handles common input variations:
    - ``"9876543210"``          → ``"+919876543210"``
    - ``"09876543210"``         → ``"+919876543210"``
    - ``"+919876543210"``       → ``"+919876543210"``
    - ``"919876543210"``        → ``"+919876543210"``
    - ``"whatsapp:+919876543210"`` → ``"+919876543210"``

    Args:
        phone: A phone number string in any common Indian format.

    Returns:
        The phone number in E.164 format (``+91XXXXXXXXXX``).

    Raises:
        InvalidPhoneError: If the number cannot be parsed as a valid
                           Indian mobile number.
    """

    raw = str(phone or "").strip()

    if not raw:
        raise InvalidPhoneError("Phone number is empty.")

    # Strip Twilio's whatsapp: prefix
    if raw.lower().startswith("whatsapp:"):
        raw = raw[len("whatsapp:"):]

    # Remove all non-digit characters except leading +
    cleaned = raw.strip()
    if cleaned.startswith("+"):
        digits = "+" + re.sub(r"[^\d]", "", cleaned[1:])
    else:
        digits = re.sub(r"[^\d]", "", cleaned)

    # Already in +91 format
    if digits.startswith("+91") and len(digits) == 13:
        local = digits[3:]
        if INDIAN_MOBILE_REGEX.match(local):
            return digits
        raise InvalidPhoneError(f"Invalid Indian mobile number: {phone}")

    # "91XXXXXXXXXX" without +
    if digits.startswith("91") and len(digits) == 12:
        local = digits[2:]
        if INDIAN_MOBILE_REGEX.match(local):
            return f"+{digits}"
        raise InvalidPhoneError(f"Invalid Indian mobile number: {phone}")

    # "0XXXXXXXXXX" trunk prefix
    if digits.startswith("0") and len(digits) == 11:
        local = digits[1:]
        if INDIAN_MOBILE_REGEX.match(local):
            return f"+91{local}"
        raise InvalidPhoneError(f"Invalid Indian mobile number: {phone}")

    # Bare 10-digit number
    if len(digits) == 10 and INDIAN_MOBILE_REGEX.match(digits):
        return f"+91{digits}"

    raise InvalidPhoneError(
        f"Cannot normalize '{phone}' to a valid Indian mobile number."
    )


def is_valid_indian_phone(phone: Any) -> bool:
    """Return True if the phone number can be normalized to +91 format.

    Convenience wrapper that catches all exceptions.
    """

    try:
        format_phone_number(phone)
        return True
    except (InvalidPhoneError, Exception):
        return False
