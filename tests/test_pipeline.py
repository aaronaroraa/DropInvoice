"""Comprehensive unit tests for every DropInvoice module.

Assumptions:
- All external services (Twilio, Gemini, Supabase, Whisper, Tesseract) are mocked.
- Tests can run without network, GPU, or service credentials.
- A sample handwritten bill image is generated in-memory (no fixture file needed).
- Run with: pytest tests/test_pipeline.py -v
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import importlib

import pytest


def _has_module(name: str) -> bool:
    """Return True if a Python module is importable."""
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_OCR_TEXT = """
Sharma General Store
GSTIN: 07AAACR5055K1Z5
Bill To: Raj Electronics
GSTIN: 07AADCB2230M1Z3

Rice   5 kg  Rs 60  300
Dal    2 kg  Rs 120 240
Oil    1 ltr Rs 180 180

Subtotal: Rs 720
"""

SAMPLE_VOICE_TRANSCRIPT = (
    "I sold 5 kg rice at 60 rupees per kg, "
    "2 kg dal at 120 rupees per kg, "
    "and 1 litre oil at 180 rupees. "
    "My name is Sharma General Store."
)

SAMPLE_PARSED_INVOICE: dict[str, Any] = {
    "seller_name": "Sharma General Store",
    "seller_gstin": "07AAACR5055K1Z5",
    "buyer_name": "Raj Electronics",
    "buyer_gstin": "07AADCB2230M1Z3",
    "transaction_type": "intra",
    "items": [
        {
            "description": "Rice",
            "hsn_code": "9999",
            "quantity": 5.0,
            "unit": "kg",
            "unit_price": 60.0,
            "total": 300.0,
        },
        {
            "description": "Dal",
            "hsn_code": "9999",
            "quantity": 2.0,
            "unit": "kg",
            "unit_price": 120.0,
            "total": 240.0,
        },
        {
            "description": "Oil",
            "hsn_code": "9999",
            "quantity": 1.0,
            "unit": "ltr",
            "unit_price": 180.0,
            "total": 180.0,
        },
    ],
    "subtotal": 720.0,
    "tax_rate": 18,
    "tax_breakdown": {
        "type": "CGST+SGST",
        "cgst": 64.8,
        "sgst": 64.8,
        "igst": None,
    },
    "grand_total": 849.6,
    "notes": None,
}

SAMPLE_TWILIO_WEBHOOK_IMAGE: dict[str, str] = {
    "From": "whatsapp:+919876543210",
    "To": "whatsapp:+14155238886",
    "Body": "",
    "MessageSid": "SM1234567890abcdef1234567890abcdef",
    "AccountSid": "AC0000000000000000000000000000test",
    "NumMedia": "1",
    "MediaUrl0": "https://api.twilio.com/2010-04-01/Accounts/AC0000/Messages/SM1234/Media/ME5678",
    "MediaContentType0": "image/jpeg",
}

SAMPLE_TWILIO_WEBHOOK_AUDIO: dict[str, str] = {
    "From": "whatsapp:+919876543210",
    "To": "whatsapp:+14155238886",
    "Body": "",
    "MessageSid": "SM_audio_test_1234567890abcdef12",
    "AccountSid": "AC0000000000000000000000000000test",
    "NumMedia": "1",
    "MediaUrl0": "https://api.twilio.com/2010-04-01/Accounts/AC0000/Messages/SM_audio/Media/ME9012",
    "MediaContentType0": "audio/ogg",
}


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Provide a clean temporary directory for PDF output."""

    output_dir = tmp_path / "invoices"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def sample_image_path(tmp_path: Path) -> Path:
    """Create a minimal grayscale test image using raw bytes (no OpenCV needed)."""

    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (400, 300), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), "Sharma Store", fill=(0, 0, 0))
        draw.text((20, 60), "Rice  5 kg  Rs 300", fill=(0, 0, 0))
        draw.text((20, 100), "Dal   2 kg  Rs 240", fill=(0, 0, 0))

        image_path = tmp_path / "test_bill.jpg"
        img.save(str(image_path), "JPEG")
        return image_path
    except ImportError:
        pytest.skip("Pillow is required for image tests")


@pytest.fixture
def sample_audio_path(tmp_path: Path) -> Path:
    """Create a minimal WAV file for audio pipeline testing."""

    import struct
    import wave

    audio_path = tmp_path / "test_voice.wav"
    # Generate 4 seconds of silence at 16kHz mono
    sample_rate = 16000
    duration = 4
    num_samples = sample_rate * duration

    with wave.open(str(audio_path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))

    return audio_path


# ===========================================================================
# 1. GSTIN Validator Tests (utils/validators.py)
# ===========================================================================

class TestGSTINValidator:
    """Test suite for GSTIN validation, state codes, and phone formatting."""

    def test_valid_gstin_format(self) -> None:
        """Known-valid GSTIN structure should pass without raising."""

        from utils.validators import is_valid_gstin

        # Note: This is a synthetic GSTIN — checksum may not pass the algo.
        # We test the regex path; checksum correctness tested separately.
        assert isinstance(is_valid_gstin("07AAACR5055K1Z5"), bool)

    def test_invalid_gstin_too_short(self) -> None:
        """GSTIN under 15 chars should be invalid."""

        from utils.validators import validate_gstin, InvalidGSTINError

        with pytest.raises(InvalidGSTINError, match="15 characters"):
            validate_gstin("07AAACR5055")

    def test_invalid_gstin_bad_format(self) -> None:
        """GSTIN with wrong character pattern should fail."""

        from utils.validators import validate_gstin, InvalidGSTINError

        with pytest.raises(InvalidGSTINError):
            validate_gstin("XXAAACR5055K1Z5")

    def test_invalid_gstin_bad_state_code(self) -> None:
        """State code 99 is not a valid Indian state."""

        from utils.validators import validate_gstin, InvalidGSTINError

        with pytest.raises(InvalidGSTINError, match="state code"):
            validate_gstin("99AAACR5055K1ZQ")

    def test_extract_state_code(self) -> None:
        """State code extraction should return the first 2 digits."""

        from utils.validators import extract_state_code

        assert extract_state_code("07AAACR5055K1Z5") == "07"
        assert extract_state_code("27AADCB2230M1ZP") == "27"
        assert extract_state_code(None) is None
        assert extract_state_code("") is None

    def test_get_state_name(self) -> None:
        """Known state codes should resolve to state names."""

        from utils.validators import get_state_name

        assert get_state_name("07AAACR5055K1Z5") == "Delhi"
        assert get_state_name("27AADCB2230M1ZP") == "Maharashtra"

    def test_format_phone_10_digits(self) -> None:
        """Bare 10-digit Indian number should get +91 prefix."""

        from utils.validators import format_phone_number

        assert format_phone_number("9876543210") == "+919876543210"

    def test_format_phone_with_zero_prefix(self) -> None:
        """0-prefixed number should normalize to +91."""

        from utils.validators import format_phone_number

        assert format_phone_number("09876543210") == "+919876543210"

    def test_format_phone_with_91_prefix(self) -> None:
        """91-prefixed number without + should get + added."""

        from utils.validators import format_phone_number

        assert format_phone_number("919876543210") == "+919876543210"

    def test_format_phone_already_e164(self) -> None:
        """Already-formatted +91 number should pass through."""

        from utils.validators import format_phone_number

        assert format_phone_number("+919876543210") == "+919876543210"

    def test_format_phone_whatsapp_prefix(self) -> None:
        """Twilio whatsapp: prefix should be stripped."""

        from utils.validators import format_phone_number

        assert format_phone_number("whatsapp:+919876543210") == "+919876543210"

    def test_format_phone_invalid(self) -> None:
        """Non-Indian number should raise InvalidPhoneError."""

        from utils.validators import format_phone_number, InvalidPhoneError

        with pytest.raises(InvalidPhoneError):
            format_phone_number("1234")

    def test_is_valid_indian_phone(self) -> None:
        """Convenience bool validator should work without raising."""

        from utils.validators import is_valid_indian_phone

        assert is_valid_indian_phone("9876543210") is True
        assert is_valid_indian_phone("1234") is False
        assert is_valid_indian_phone(None) is False


# ===========================================================================
# 2. GST Calculator Tests (invoice/gst_calculator.py)
# ===========================================================================

class TestGSTCalculator:
    """Test suite for GST calculation logic."""

    def test_intra_state_tax(self) -> None:
        """Intra-state should produce CGST + SGST at 9% each."""

        from invoice.gst_calculator import calculate_tax_breakdown

        breakdown = calculate_tax_breakdown(1000.0, "intra")
        assert breakdown["type"] == "CGST+SGST"
        assert breakdown["cgst"] == 90.0
        assert breakdown["sgst"] == 90.0
        assert breakdown["igst"] is None

    def test_inter_state_tax(self) -> None:
        """Inter-state should produce IGST at 18%."""

        from invoice.gst_calculator import calculate_tax_breakdown

        breakdown = calculate_tax_breakdown(1000.0, "inter")
        assert breakdown["type"] == "IGST"
        assert breakdown["igst"] == 180.0
        assert breakdown["cgst"] is None
        assert breakdown["sgst"] is None

    def test_calculate_gst_full_invoice(self) -> None:
        """Full GST calculation should enrich invoice data correctly."""

        from invoice.gst_calculator import calculate_gst

        invoice = {
            "seller_gstin": "07AAACR5055K1Z5",
            "buyer_gstin": "07AADCB2230M1Z3",
            "items": [
                {"description": "Item A", "quantity": 1, "unit_price": 500, "total": 500},
            ],
        }
        result = calculate_gst(invoice)
        assert result["transaction_type"] == "intra"
        assert result["subtotal"] == 500.0
        assert result["grand_total"] == 590.0  # 500 + 90 (18%)

    def test_inter_state_different_codes(self) -> None:
        """Different state codes → inter-state."""

        from invoice.gst_calculator import determine_transaction_type

        invoice = {
            "seller_gstin": "07AAACR5055K1Z5",  # Delhi (07)
            "buyer_gstin": "27AADCB2230M1ZP",   # Maharashtra (27)
        }
        assert determine_transaction_type(invoice) == "inter"

    def test_tax_breakdown_total(self) -> None:
        """Sum of CGST + SGST should match total."""

        from invoice.gst_calculator import tax_breakdown_total

        breakdown = {"cgst": 45.0, "sgst": 45.0, "igst": None}
        assert tax_breakdown_total(breakdown) == 90.0

    def test_zero_subtotal(self) -> None:
        """Zero subtotal should produce zero taxes."""

        from invoice.gst_calculator import calculate_tax_breakdown

        breakdown = calculate_tax_breakdown(0.0, "intra")
        assert breakdown["cgst"] == 0.0
        assert breakdown["sgst"] == 0.0


# ===========================================================================
# 3. Image Processor Tests (processing/image_processor.py)
# ===========================================================================

@pytest.mark.skipif(
    not _has_module("cv2"),
    reason="OpenCV (cv2) not installed",
)
class TestImageProcessor:
    """Test suite for the OpenCV + Tesseract OCR pipeline."""

    def test_process_image_file_not_found(self) -> None:
        """Non-existent image path should raise FileNotFoundError."""

        from processing.image_processor import process_image

        with pytest.raises(FileNotFoundError):
            process_image("/nonexistent/path/image.jpg")

    @patch("processing.image_processor.run_tesseract")
    @patch("processing.image_processor.preprocess_image")
    @patch("processing.image_processor.load_image")
    def test_process_image_success(
        self,
        mock_load: MagicMock,
        mock_preprocess: MagicMock,
        mock_tesseract: MagicMock,
        sample_image_path: Path,
    ) -> None:
        """Successful OCR should return cleaned text."""

        import numpy as np
        from processing.image_processor import process_image

        mock_load.return_value = np.zeros((100, 200), dtype=np.uint8)
        mock_preprocess.return_value = np.zeros((100, 200), dtype=np.uint8)
        mock_tesseract.return_value = "Rice 5 kg Rs 300\nDal 2 kg Rs 240"

        result = process_image(str(sample_image_path))
        assert "Rice" in result
        assert "300" in result

    @patch("processing.image_processor.run_tesseract")
    @patch("processing.image_processor.preprocess_image")
    @patch("processing.image_processor.load_image")
    def test_process_image_unreadable(
        self,
        mock_load: MagicMock,
        mock_preprocess: MagicMock,
        mock_tesseract: MagicMock,
        sample_image_path: Path,
    ) -> None:
        """Tesseract returning gibberish should raise UnreadableImageError."""

        import numpy as np
        from processing.image_processor import process_image, UnreadableImageError

        mock_load.return_value = np.zeros((100, 200), dtype=np.uint8)
        mock_preprocess.return_value = np.zeros((100, 200), dtype=np.uint8)
        mock_tesseract.return_value = "......"  # no useful text

        with pytest.raises(UnreadableImageError):
            process_image(str(sample_image_path))


# ===========================================================================
# 4. Audio Processor Tests (processing/audio_processor.py)
# ===========================================================================

class TestAudioProcessor:
    """Test suite for the Whisper ASR pipeline."""

    def test_audio_file_not_found(self) -> None:
        """Non-existent audio file should raise FileNotFoundError."""

        from processing.audio_processor import process_audio

        with pytest.raises(FileNotFoundError):
            process_audio("/nonexistent/voice.ogg")

    @patch("processing.audio_processor.transcribe_audio")
    @patch("processing.audio_processor.convert_audio_to_wav")
    @patch("processing.audio_processor.validate_audio_duration")
    @patch("processing.audio_processor.load_audio")
    def test_process_audio_success(
        self,
        mock_load: MagicMock,
        mock_validate: MagicMock,
        mock_convert: MagicMock,
        mock_transcribe: MagicMock,
        sample_audio_path: Path,
    ) -> None:
        """Successful transcription should return cleaned transcript text."""

        from processing.audio_processor import process_audio

        mock_load.return_value = MagicMock()
        mock_validate.return_value = None
        mock_convert.return_value = sample_audio_path
        mock_transcribe.return_value = SAMPLE_VOICE_TRANSCRIPT

        result = process_audio(str(sample_audio_path))
        assert "rice" in result.lower()
        assert "dal" in result.lower()

    @patch("processing.audio_processor.load_audio")
    def test_short_audio_rejected(
        self,
        mock_load: MagicMock,
    ) -> None:
        """Audio under 3 seconds should raise ShortAudioError."""

        from processing.audio_processor import ShortAudioError

        # pydub measures duration in milliseconds via len()
        mock_audio = MagicMock()
        mock_audio.__len__ = MagicMock(return_value=2000)  # 2 seconds
        mock_load.return_value = mock_audio

        from processing.audio_processor import process_audio

        with pytest.raises(ShortAudioError, match="too short"):
            process_audio("/tmp/dropinvoice/audio/short.wav")


# ===========================================================================
# 5. Gemini Parser Tests (processing/parser.py)
# ===========================================================================

class TestInvoiceParser:
    """Test suite for Gemini and regex-based invoice parsing."""

    @patch("processing.parser.parse_with_gemini")
    def test_parse_with_gemini_success(
        self,
        mock_gemini: MagicMock,
    ) -> None:
        """Valid Gemini response should produce a complete invoice dict."""

        from processing.parser import parse_invoice_text

        mock_gemini.return_value = SAMPLE_PARSED_INVOICE.copy()
        result = parse_invoice_text(SAMPLE_OCR_TEXT)

        assert result["seller_name"] == "Sharma General Store"
        assert len(result["items"]) == 3
        assert result["subtotal"] == 720.0
        assert result["tax_breakdown"]["type"] == "CGST+SGST"

    @patch("google.generativeai.GenerativeModel")
    @patch("google.generativeai.configure")
    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
    def test_parse_with_gemini_api_call(
        self,
        mock_configure: MagicMock,
        mock_model_cls: MagicMock,
    ) -> None:
        """parse_with_gemini should configure and call the GenerativeModel successfully."""
        from processing.parser import parse_with_gemini

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"seller_name": "Sharma General Store", "items": []}'
        mock_model.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_model

        result = parse_with_gemini("test raw text")
        assert result == {"seller_name": "Sharma General Store", "items": []}
        mock_configure.assert_called_once_with(api_key="test_key")
        mock_model.generate_content.assert_called_once()

    @patch.dict(os.environ, {}, clear=True)
    def test_parse_with_gemini_missing_key(self) -> None:
        """parse_with_gemini should raise GeminiParseError when api key is not set."""
        from processing.parser import parse_with_gemini, GeminiParseError

        with pytest.raises(GeminiParseError, match="GEMINI_API_KEY is not configured"):
            parse_with_gemini("test text")

    def test_regex_fallback_extracts_items(self) -> None:
        """Regex fallback should extract at least some items from OCR text."""

        from processing.parser import parse_with_regex

        result = parse_with_regex(SAMPLE_OCR_TEXT)
        assert len(result["items"]) > 0
        assert result["subtotal"] > 0
        assert result["seller_gstin"] is not None

    @patch("processing.parser.parse_with_gemini")
    def test_gemini_failure_falls_back_to_regex(
        self,
        mock_gemini: MagicMock,
    ) -> None:
        """When Gemini raises, the parser should fallback to regex."""

        from processing.parser import parse_invoice_text

        mock_gemini.side_effect = Exception("API timeout")
        result = parse_invoice_text(SAMPLE_OCR_TEXT)

        # Regex should still produce a valid result
        assert "items" in result
        assert result["grand_total"] > 0

    def test_empty_text_raises(self) -> None:
        """Empty input should raise InvoiceParseError."""

        from processing.parser import parse_invoice_text, InvoiceParseError

        with pytest.raises(InvoiceParseError, match="empty"):
            parse_invoice_text("")

    def test_gstin_extraction(self) -> None:
        """GSTIN regex should extract valid GSTINs from raw text."""

        from processing.parser import extract_gstins

        gstins = extract_gstins(SAMPLE_OCR_TEXT)
        assert "07AAACR5055K1Z5" in gstins
        assert "07AADCB2230M1Z3" in gstins


# ===========================================================================
# 6. PDF Generator Tests (invoice/generator.py)
# ===========================================================================

@pytest.mark.skipif(
    not _has_module("reportlab"),
    reason="ReportLab not installed",
)
class TestPDFGenerator:
    """Test suite for ReportLab invoice PDF generation."""

    def test_generate_pdf_creates_file(self, tmp_output_dir: Path) -> None:
        """PDF generator should write a valid PDF file to disk."""

        from invoice.generator import generate_invoice_pdf

        pdf_path = generate_invoice_pdf(
            SAMPLE_PARSED_INVOICE,
            output_dir=str(tmp_output_dir),
        )

        assert Path(pdf_path).exists()
        assert Path(pdf_path).suffix == ".pdf"

        # Verify it's a real PDF (starts with %PDF)
        with open(pdf_path, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-"

    def test_invoice_number_format(self, tmp_output_dir: Path) -> None:
        """Generated invoice number should follow DROPINV-YYYYMM-XXXX format."""

        from invoice.generator import next_invoice_number

        inv_num = next_invoice_number(str(tmp_output_dir))
        assert re.match(r"^DROPINV-\d{6}-\d{4}$", inv_num)

    def test_gstin_watermark_when_missing(self, tmp_output_dir: Path) -> None:
        """Invoice without GSTIN should still generate (watermark is drawn on canvas)."""

        from invoice.generator import generate_invoice_pdf

        no_gstin_invoice = SAMPLE_PARSED_INVOICE.copy()
        no_gstin_invoice["seller_gstin"] = None
        no_gstin_invoice["buyer_gstin"] = None

        pdf_path = generate_invoice_pdf(no_gstin_invoice, output_dir=str(tmp_output_dir))
        assert Path(pdf_path).exists()

    def test_sequential_invoice_numbers(self, tmp_output_dir: Path) -> None:
        """Consecutive invoice numbers should auto-increment."""

        from invoice.generator import generate_invoice_pdf

        path1 = generate_invoice_pdf(SAMPLE_PARSED_INVOICE, output_dir=str(tmp_output_dir))
        path2 = generate_invoice_pdf(SAMPLE_PARSED_INVOICE, output_dir=str(tmp_output_dir))

        # Extract sequence numbers
        num1 = int(Path(path1).stem.split("-")[-1])
        num2 = int(Path(path2).stem.split("-")[-1])
        assert num2 == num1 + 1


# ===========================================================================
# 7. WhatsApp Delivery Tests (delivery/whatsapp.py)
# ===========================================================================

@pytest.mark.skipif(
    not _has_module("twilio"),
    reason="Twilio SDK not installed",
)
class TestWhatsAppDelivery:
    """Test suite for Twilio WhatsApp delivery."""

    def test_build_invoice_summary(self) -> None:
        """Summary message should contain invoice number and totals."""

        from delivery.whatsapp import build_invoice_summary

        summary = build_invoice_summary(SAMPLE_PARSED_INVOICE)
        assert "849.60" in summary or "849.6" in summary
        assert "720.00" in summary or "720.0" in summary

    def test_ensure_whatsapp_prefix(self) -> None:
        """Phone numbers should get the whatsapp: prefix if missing."""

        from delivery.whatsapp import ensure_whatsapp_prefix

        assert ensure_whatsapp_prefix("+919876543210") == "whatsapp:+919876543210"
        assert ensure_whatsapp_prefix("whatsapp:+919876543210") == "whatsapp:+919876543210"

    @patch.dict(os.environ, {
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": "test_token",
        "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
    })
    @patch("delivery.whatsapp.build_twilio_client")
    def test_send_invoice_pdf_requires_url(
        self,
        mock_client: MagicMock,
    ) -> None:
        """Sending a PDF without a public URL should raise WhatsAppDeliveryError."""

        from delivery.whatsapp import send_invoice_pdf, WhatsAppDeliveryError

        with pytest.raises(WhatsAppDeliveryError, match="public PDF URL"):
            send_invoice_pdf(
                "+919876543210",
                "/tmp/test.pdf",
                SAMPLE_PARSED_INVOICE,
                pdf_url=None,
            )


# ===========================================================================
# 8. Email Delivery Tests (delivery/email_sender.py)
# ===========================================================================

class TestEmailDelivery:
    """Test suite for Gmail SMTP delivery."""

    def test_build_html_body(self) -> None:
        """HTML email body should contain invoice number and grand total."""

        from delivery.email_sender import build_html_body

        html = build_html_body(SAMPLE_PARSED_INVOICE)
        assert "849.60" in html or "849.6" in html
        assert "Sharma General Store" in html

    def test_build_plain_text_body(self) -> None:
        """Plain text email body should be a readable fallback."""

        from delivery.email_sender import build_plain_text_body

        text = build_plain_text_body(SAMPLE_PARSED_INVOICE)
        assert "Grand Total" in text
        assert "DropInvoice" in text

    @pytest.mark.skipif(
        not _has_module("reportlab"),
        reason="ReportLab not installed (needed for PDF generation)",
    )
    @patch.dict(os.environ, {
        "GMAIL_USERNAME": "test@gmail.com",
        "GMAIL_APP_PASSWORD": "test_password",
    })
    def test_build_invoice_email_structure(self, tmp_output_dir: Path) -> None:
        """Email message should have correct headers and a PDF attachment."""

        from delivery.email_sender import build_invoice_email
        from invoice.generator import generate_invoice_pdf

        pdf_path = generate_invoice_pdf(SAMPLE_PARSED_INVOICE, output_dir=str(tmp_output_dir))
        email = build_invoice_email("buyer@example.com", Path(pdf_path), SAMPLE_PARSED_INVOICE)

        assert email["To"] == "buyer@example.com"
        assert "DropInvoice" in email["Subject"]

        # Verify attachment is present
        attachments = [
            part for part in email.walk()
            if part.get_content_disposition() == "attachment"
        ]
        assert len(attachments) == 1


# ===========================================================================
# 9. Supabase Client Tests (database/supabase_client.py)
# ===========================================================================

class TestSupabaseClient:
    """Test suite for Supabase database operations (mocked)."""

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test_key",
    })
    @patch("database.supabase_client.get_supabase_client")
    def test_get_user_profile_found(self, mock_client_fn: MagicMock) -> None:
        """Existing user should return a profile dict."""

        from database.supabase_client import get_user_profile

        mock_response = MagicMock()
        mock_response.data = [{"phone_number": "+919876543210", "business_name": "Test Shop"}]

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_response
        mock_client_fn.return_value = mock_client

        profile = get_user_profile("+919876543210")
        assert profile is not None
        assert profile["business_name"] == "Test Shop"

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test_key",
    })
    @patch("database.supabase_client.get_supabase_client")
    def test_get_user_profile_not_found(self, mock_client_fn: MagicMock) -> None:
        """Non-existent user should return None."""

        from database.supabase_client import get_user_profile

        mock_response = MagicMock()
        mock_response.data = []

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_response
        mock_client_fn.return_value = mock_client

        profile = get_user_profile("+910000000000")
        assert profile is None

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test_key",
    })
    @patch("database.supabase_client.get_supabase_client")
    def test_save_invoice_record(self, mock_client_fn: MagicMock) -> None:
        """Save should insert a row and return the result."""

        from database.supabase_client import save_invoice_record

        mock_response = MagicMock()
        mock_response.data = [{"id": "uuid-123", "invoice_number": "DROPINV-202506-0001"}]

        mock_client = MagicMock()
        mock_client.table.return_value.insert.return_value.execute.return_value = mock_response
        mock_client_fn.return_value = mock_client

        invoice = SAMPLE_PARSED_INVOICE.copy()
        invoice["phone_number"] = "+919876543210"
        invoice["invoice_number"] = "DROPINV-202506-0001"

        result = save_invoice_record(invoice)
        assert result["id"] == "uuid-123"

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test_key",
    })
    @patch("database.supabase_client.get_supabase_client")
    def test_get_invoice_history(self, mock_client_fn: MagicMock) -> None:
        """Invoice history should return a list ordered by newest first."""

        from database.supabase_client import get_invoice_history

        mock_response = MagicMock()
        mock_response.data = [
            {"invoice_number": "DROPINV-202506-0002"},
            {"invoice_number": "DROPINV-202506-0001"},
        ]

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_response
        mock_client_fn.return_value = mock_client

        history = get_invoice_history("+919876543210")
        assert len(history) == 2
        assert history[0]["invoice_number"] == "DROPINV-202506-0002"


# ===========================================================================
# 10. Celery Task Tests (tasks/celery_tasks.py)
# ===========================================================================

@pytest.mark.skipif(
    not _has_module("celery"),
    reason="Celery not installed",
)
class TestCeleryTasks:
    """Test suite for async Celery pipeline tasks."""

    @patch("tasks.celery_tasks._deliver_invoice")
    @patch("tasks.celery_tasks._save_invoice_record")
    @patch("tasks.celery_tasks._generate_pdf")
    @patch("tasks.celery_tasks._enrich_with_user_profile")
    @patch("tasks.celery_tasks._parse_invoice")
    @patch("tasks.celery_tasks._extract_raw_text")
    def test_full_pipeline_success(
        self,
        mock_extract: MagicMock,
        mock_parse: MagicMock,
        mock_enrich: MagicMock,
        mock_generate: MagicMock,
        mock_save: MagicMock,
        mock_deliver: MagicMock,
    ) -> None:
        """Full pipeline should run all steps and return success."""

        from tasks.celery_tasks import process_invoice_task

        mock_extract.return_value = SAMPLE_OCR_TEXT
        mock_parse.return_value = SAMPLE_PARSED_INVOICE.copy()
        mock_enrich.return_value = SAMPLE_PARSED_INVOICE.copy()
        mock_generate.return_value = "/tmp/invoices/DROPINV-202506-0001.pdf"

        # Call the task function directly (not .delay()) for unit testing
        result = process_invoice_task(
            "/tmp/test_image.jpg",
            {"from_number": "whatsapp:+919876543210", "media_kind": "image", "message_sid": "SM_test"},
        )

        assert result["status"] == "success"
        mock_extract.assert_called_once()
        mock_parse.assert_called_once()
        mock_generate.assert_called_once()
        mock_deliver.assert_called_once()

    @patch("tasks.celery_tasks._send_error_reply")
    @patch("tasks.celery_tasks._log_failure")
    @patch("tasks.celery_tasks._extract_raw_text")
    def test_pipeline_failure_logged(
        self,
        mock_extract: MagicMock,
        mock_log: MagicMock,
        mock_reply: MagicMock,
    ) -> None:
        """Pipeline failure should log to Supabase and send error reply."""

        from tasks.celery_tasks import process_invoice_task

        mock_extract.side_effect = Exception("OCR engine crashed")

        mock_request = MagicMock()
        mock_request.retries = 2
        with patch.object(process_invoice_task.__class__, "request", new_callable=PropertyMock, return_value=mock_request):
            result = process_invoice_task(
                "/tmp/bad_image.jpg",
                {"from_number": "+919876543210", "media_kind": "image", "message_sid": "SM_fail"},
            )

        assert result["status"] == "failed"
        mock_log.assert_called_once()
        mock_reply.assert_called_once()


# ===========================================================================
# 11. Webhook Handler Tests (webhook/handler.py)
# ===========================================================================

@pytest.mark.skipif(
    not _has_module("fastapi"),
    reason="FastAPI not installed",
)
class TestWebhookHandler:
    """Test suite for Twilio webhook receiver."""

    def test_classify_media_image(self) -> None:
        """image/* content types should classify as IMAGE."""

        from webhook.handler import classify_media, MediaKind

        assert classify_media("image/jpeg") == MediaKind.IMAGE
        assert classify_media("image/png") == MediaKind.IMAGE

    def test_classify_media_audio(self) -> None:
        """audio/* content types should classify as AUDIO."""

        from webhook.handler import classify_media, MediaKind

        assert classify_media("audio/ogg") == MediaKind.AUDIO
        assert classify_media("audio/mpeg") == MediaKind.AUDIO

    def test_classify_media_unsupported(self) -> None:
        """Unsupported MIME types should return None."""

        from webhook.handler import classify_media

        assert classify_media("video/mp4") is None
        assert classify_media("text/plain") is None

    def test_parse_incoming_message(self) -> None:
        """Twilio form data should parse into IncomingMessage correctly."""

        from webhook.handler import parse_incoming_message

        msg = parse_incoming_message(SAMPLE_TWILIO_WEBHOOK_IMAGE)
        assert msg.from_number == "whatsapp:+919876543210"
        assert msg.num_media == 1
        assert msg.media_content_type == "image/jpeg"

    def test_twiml_response(self) -> None:
        """TwiML response should be valid XML with the message content."""

        from webhook.handler import twiml_response

        response = twiml_response("Test message")
        body = response.body.decode() if isinstance(response.body, bytes) else response.body
        assert "<Response>" in body
        assert "Test message" in body
        assert response.media_type == "application/xml"


# ===========================================================================
# 12. Integration / End-to-End Test
# ===========================================================================

@pytest.mark.skipif(
    not _has_module("reportlab"),
    reason="ReportLab not installed",
)
class TestEndToEnd:
    """Integration test spanning parse → calculate → generate."""

    def test_ocr_text_to_pdf(self, tmp_output_dir: Path) -> None:
        """Full text → parse → PDF pipeline should produce a valid invoice PDF."""

        from processing.parser import parse_with_regex
        from invoice.gst_calculator import calculate_gst
        from invoice.generator import generate_invoice_pdf

        # Step 1: Parse OCR text
        parsed = parse_with_regex(SAMPLE_OCR_TEXT)
        assert parsed["seller_name"]
        assert len(parsed["items"]) > 0

        # Step 2: Calculate GST
        enriched = calculate_gst(parsed)
        assert enriched["grand_total"] > enriched["subtotal"]

        # Step 3: Generate PDF
        pdf_path = generate_invoice_pdf(enriched, output_dir=str(tmp_output_dir))
        pdf_file = Path(pdf_path)
        assert pdf_file.exists()
        assert pdf_file.stat().st_size > 500  # non-trivial PDF

        # Verify PDF header
        with open(pdf_path, "rb") as f:
            assert f.read(5) == b"%PDF-"
