"""OpenCV and Tesseract OCR pipeline for handwritten bill images."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytesseract

logger = logging.getLogger("dropinvoice.processing.image")

MIN_OCR_TEXT_LENGTH = 8
MIN_IMAGE_DIMENSION = 32
TESSERACT_CONFIG = "--oem 3 --psm 6"


class UnreadableImageError(Exception):
    """Raised when the OCR pipeline cannot extract usable text from an image."""


def process_image(image_path: str | Path, metadata: dict[str, Any] | None = None) -> str:
    """Preprocess a bill image, run Tesseract OCR, and return cleaned raw text."""

    path = Path(image_path)
    configure_tesseract()

    image = load_image(path)
    preprocessed = preprocess_image(image)
    raw_text = run_tesseract(preprocessed)
    cleaned_text = clean_ocr_text(raw_text)

    if not is_readable_text(cleaned_text):
        message_sid = metadata.get("message_sid") if metadata else None
        logger.warning("Unreadable image OCR result for %s", message_sid or path)
        raise UnreadableImageError("Couldn't read your bill clearly.")

    logger.info("Extracted %s OCR characters from %s", len(cleaned_text), path)
    return cleaned_text


def configure_tesseract() -> None:
    """Apply an explicit Tesseract binary path when configured."""

    tesseract_cmd = os.getenv("TESSERACT_CMD")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


def load_image(image_path: Path) -> np.ndarray:
    """Load an image from disk and validate that OpenCV decoded it."""

    if not image_path.exists():
        raise FileNotFoundError(f"Image file does not exist: {image_path}")

    image = cv2.imread(str(image_path))
    if image is None:
        raise UnreadableImageError(f"OpenCV could not decode image: {image_path}")

    height, width = image.shape[:2]
    if height < MIN_IMAGE_DIMENSION or width < MIN_IMAGE_DIMENSION:
        raise UnreadableImageError("Image is too small to OCR reliably.")

    return image


def preprocess_image(image: np.ndarray) -> np.ndarray:
    """Run grayscale, denoise, threshold, and deskew preprocessing."""

    grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(grayscale, h=30)
    thresholded = threshold_for_text(denoised)
    deskewed = deskew_image(thresholded)
    return add_padding(deskewed)


def threshold_for_text(grayscale: np.ndarray) -> np.ndarray:
    """Convert grayscale image to high-contrast black text on white background."""

    blurred = cv2.GaussianBlur(grayscale, (5, 5), 0)
    thresholded = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        15,
    )

    # Tesseract performs best when the page background is white and text is dark.
    if np.mean(thresholded) < 127:
        thresholded = cv2.bitwise_not(thresholded)

    return thresholded


def deskew_image(binary_image: np.ndarray) -> np.ndarray:
    """Deskew a thresholded document image using foreground pixel geometry."""

    inverted = cv2.bitwise_not(binary_image)
    coordinates = cv2.findNonZero(inverted)
    if coordinates is None:
        raise UnreadableImageError("No readable foreground text was found.")

    angle = cv2.minAreaRect(coordinates)[-1]
    if angle < -45:
        angle = 90 + angle

    if abs(angle) < 0.1:
        return binary_image

    height, width = binary_image.shape[:2]
    center = (width // 2, height // 2)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)

    return cv2.warpAffine(
        binary_image,
        rotation_matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def add_padding(image: np.ndarray) -> np.ndarray:
    """Add a white border so edge text is not clipped during OCR."""

    return cv2.copyMakeBorder(
        image,
        top=20,
        bottom=20,
        left=20,
        right=20,
        borderType=cv2.BORDER_CONSTANT,
        value=255,
    )


def run_tesseract(image: np.ndarray) -> str:
    """Run Tesseract OCR against a preprocessed image array."""

    try:
        return pytesseract.image_to_string(image, config=TESSERACT_CONFIG)
    except pytesseract.TesseractError as exc:
        raise UnreadableImageError(f"Tesseract OCR failed: {exc}") from exc


def clean_ocr_text(raw_text: str) -> str:
    """Normalize Tesseract output while preserving line item structure."""

    normalized_lines: list[str] = []
    for line in raw_text.splitlines():
        compacted = re.sub(r"[ \t]+", " ", line).strip()
        if compacted:
            normalized_lines.append(compacted)

    cleaned = "\n".join(normalized_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def is_readable_text(text: str) -> bool:
    """Return True when OCR output has enough useful alphanumeric content."""

    alphanumeric_count = sum(character.isalnum() for character in text)
    price_pattern = "(?:rs\\.?|inr|\\u20b9)?\\s*\\d+(?:\\.\\d{1,2})?"
    has_price_like_text = bool(re.search(price_pattern, text, re.I))
    return alphanumeric_count >= MIN_OCR_TEXT_LENGTH and has_price_like_text
