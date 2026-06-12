"""ReportLab PDF generator for GST-compliant DropInvoice invoices."""

from __future__ import annotations

import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from invoice.gst_calculator import calculate_gst, tax_breakdown_total, to_float

DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "invoices"
INVOICE_PREFIX = "DROPINV"
PAGE_MARGIN = 36
WATERMARK_TEXT = "GSTIN NOT PROVIDED"


class PDFGenerationError(Exception):
    """Raised when an invoice PDF cannot be generated."""


def generate_invoice_pdf(
    invoice_data: dict[str, Any],
    output_dir: str | Path | None = None,
) -> str:
    """Generate a GST-compliant invoice PDF and return the saved file path."""

    reportlab = load_reportlab()
    enriched_invoice = prepare_invoice_data(invoice_data, output_dir)
    pdf_path = build_pdf_path(enriched_invoice["invoice_number"], output_dir)
    document = reportlab["SimpleDocTemplate"](
        str(pdf_path),
        pagesize=reportlab["A4"],
        rightMargin=PAGE_MARGIN,
        leftMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=PAGE_MARGIN,
        title=enriched_invoice["invoice_number"],
        author="DropInvoice",
    )
    styles = build_styles(reportlab)
    story = build_invoice_story(enriched_invoice, styles, reportlab)

    try:
        document.build(
            story,
            onFirstPage=lambda canvas, doc: draw_page_frame(canvas, doc, enriched_invoice),
            onLaterPages=lambda canvas, doc: draw_page_frame(canvas, doc, enriched_invoice),
        )
    except Exception as exc:
        raise PDFGenerationError(f"Could not generate invoice PDF: {pdf_path}") from exc

    return str(pdf_path)


def generate_pdf(invoice_data: dict[str, Any], output_dir: str | Path | None = None) -> str:
    """Compatibility wrapper for callers that expect a shorter generator name."""

    return generate_invoice_pdf(invoice_data, output_dir)


def prepare_invoice_data(
    invoice_data: dict[str, Any],
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Normalize invoice data, calculate GST, and attach invoice metadata."""

    enriched_invoice = calculate_gst(invoice_data)
    enriched_invoice["invoice_number"] = str(
        invoice_data.get("invoice_number") or next_invoice_number(output_dir)
    )
    enriched_invoice["date"] = str(invoice_data.get("date") or date.today().isoformat())
    return enriched_invoice


def build_pdf_path(invoice_number: str, output_dir: str | Path | None = None) -> Path:
    """Build the output path for an invoice number."""

    safe_invoice_number = re.sub(r"[^A-Za-z0-9_-]+", "-", invoice_number).strip("-")
    directory = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe_invoice_number}.pdf"


def next_invoice_number(output_dir: str | Path | None = None) -> str:
    """Return the next DROPINV-YYYYMM-XXXX invoice number for the output folder."""

    today = date.today()
    year_month = today.strftime("%Y%m")
    directory = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{INVOICE_PREFIX}-{year_month}-(\d{{4}})\.pdf$")
    existing_numbers = []

    for pdf_path in directory.glob(f"{INVOICE_PREFIX}-{year_month}-*.pdf"):
        match = pattern.match(pdf_path.name)
        if match:
            existing_numbers.append(int(match.group(1)))

    next_number = max(existing_numbers, default=0) + 1
    return f"{INVOICE_PREFIX}-{year_month}-{next_number:04d}"


def build_invoice_story(
    invoice_data: dict[str, Any],
    styles: dict[str, Any],
    reportlab: dict[str, Any],
) -> list[Any]:
    """Build the ReportLab flowables used in the invoice PDF."""

    spacer = reportlab["Spacer"]
    story: list[Any] = []
    story.extend(build_header(invoice_data, styles, reportlab))
    story.append(spacer(1, 16))
    story.extend(build_party_sections(invoice_data, styles, reportlab))
    story.append(spacer(1, 18))
    story.append(build_items_table(invoice_data, styles, reportlab))
    story.append(spacer(1, 14))
    story.extend(build_totals_section(invoice_data, styles, reportlab))
    story.extend(build_notes_section(invoice_data, styles, reportlab))
    return story


def build_header(
    invoice_data: dict[str, Any],
    styles: dict[str, Any],
    reportlab: dict[str, Any],
) -> list[Any]:
    """Build the invoice branding and metadata header."""

    paragraph = reportlab["Paragraph"]
    table = reportlab["Table"]
    table_style = reportlab["TableStyle"]
    colors = reportlab["colors"]

    left = [
        paragraph("DropInvoice", styles["brand"]),
        paragraph("WhatsApp-native GST invoicing", styles["muted"]),
    ]
    right = [
        paragraph("TAX INVOICE", styles["invoice_title"]),
        paragraph(f"Invoice No: {invoice_data['invoice_number']}", styles["right"]),
        paragraph(f"Date: {invoice_data['date']}", styles["right"]),
    ]
    header = table([[left, right]], colWidths=[300, 210])
    header.setStyle(
        table_style(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#D7DEE8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return [header]


def build_party_sections(
    invoice_data: dict[str, Any],
    styles: dict[str, Any],
    reportlab: dict[str, Any],
) -> list[Any]:
    """Build seller and buyer detail boxes."""

    paragraph = reportlab["Paragraph"]
    table = reportlab["Table"]
    table_style = reportlab["TableStyle"]
    colors = reportlab["colors"]

    seller_details = [
        paragraph("Seller", styles["section_label"]),
        paragraph(escape_text(invoice_data.get("seller_name") or "Unknown Seller"), styles["normal"]),
        paragraph(f"GSTIN: {display_value(invoice_data.get('seller_gstin'))}", styles["normal"]),
    ]
    buyer_details = [
        paragraph("Buyer", styles["section_label"]),
        paragraph(escape_text(invoice_data.get("buyer_name") or "Unknown Buyer"), styles["normal"]),
        paragraph(f"GSTIN: {display_value(invoice_data.get('buyer_gstin'))}", styles["normal"]),
    ]
    party_table = table([[seller_details, buyer_details]], colWidths=[255, 255])
    party_table.setStyle(
        table_style(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#D7DEE8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7DEE8")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return [party_table]


def build_items_table(
    invoice_data: dict[str, Any],
    styles: dict[str, Any],
    reportlab: dict[str, Any],
) -> Any:
    """Build the HSN line item table."""

    paragraph = reportlab["Paragraph"]
    table = reportlab["Table"]
    table_style = reportlab["TableStyle"]
    colors = reportlab["colors"]

    rows = [
        [
            paragraph("Description", styles["table_header"]),
            paragraph("HSN", styles["table_header"]),
            paragraph("Qty", styles["table_header"]),
            paragraph("Unit", styles["table_header"]),
            paragraph("Rate", styles["table_header"]),
            paragraph("Total", styles["table_header"]),
        ]
    ]
    for item in invoice_data.get("items", []):
        rows.append(
            [
                paragraph(escape_text(item.get("description") or "Item"), styles["small"]),
                paragraph(escape_text(item.get("hsn_code") or "9999"), styles["small"]),
                paragraph(format_quantity(item.get("quantity")), styles["small_right"]),
                paragraph(escape_text(item.get("unit") or "pcs"), styles["small"]),
                paragraph(format_money(item.get("unit_price")), styles["small_right"]),
                paragraph(format_money(item.get("total")), styles["small_right"]),
            ]
        )

    items_table = table(rows, colWidths=[205, 56, 50, 48, 75, 76], repeatRows=1)
    items_table.setStyle(
        table_style(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7DEE8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return items_table


def build_totals_section(
    invoice_data: dict[str, Any],
    styles: dict[str, Any],
    reportlab: dict[str, Any],
) -> list[Any]:
    """Build subtotal, tax breakdown, and grand total tables."""

    table = reportlab["Table"]
    table_style = reportlab["TableStyle"]
    colors = reportlab["colors"]
    paragraph = reportlab["Paragraph"]
    tax_breakdown = invoice_data["tax_breakdown"]
    rows = [
        ["Subtotal", format_money(invoice_data["subtotal"])],
        ["Tax Type", tax_breakdown["type"]],
    ]

    if tax_breakdown["type"] == "IGST":
        rows.append(["IGST 18%", format_money(tax_breakdown.get("igst"))])
    else:
        rows.append(["CGST 9%", format_money(tax_breakdown.get("cgst"))])
        rows.append(["SGST 9%", format_money(tax_breakdown.get("sgst"))])

    rows.extend(
        [
            ["Total Tax", format_money(tax_breakdown_total(tax_breakdown))],
            [paragraph("Grand Total", styles["total_label"]), paragraph(format_money(invoice_data["grand_total"]), styles["total_value"])],
        ]
    )
    totals_table = table(rows, colWidths=[140, 120], hAlign="RIGHT")
    totals_table.setStyle(
        table_style(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#D7DEE8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E3E8EF")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F4F7FB")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return [totals_table]


def build_notes_section(
    invoice_data: dict[str, Any],
    styles: dict[str, Any],
    reportlab: dict[str, Any],
) -> list[Any]:
    """Build optional invoice notes and footer text."""

    paragraph = reportlab["Paragraph"]
    spacer = reportlab["Spacer"]
    story: list[Any] = [spacer(1, 18)]

    if invoice_data.get("notes"):
        story.append(paragraph(f"Notes: {escape_text(invoice_data['notes'])}", styles["small"]))
        story.append(spacer(1, 8))

    story.append(paragraph("Generated by DropInvoice for WhatsApp invoice automation.", styles["muted"]))
    return story


def draw_page_frame(canvas: Any, document: Any, invoice_data: dict[str, Any]) -> None:
    """Draw page footer and GSTIN watermark when needed."""

    canvas.saveState()

    if should_draw_gstin_watermark(invoice_data):
        draw_watermark(canvas, document)

    canvas.setFont("Helvetica", 8)
    canvas.setFillColorRGB(0.35, 0.39, 0.45)
    canvas.drawRightString(559, 22, f"Page {document.page}")
    canvas.restoreState()


def draw_watermark(canvas: Any, document: Any) -> None:
    """Draw a translucent GSTIN missing watermark across the page."""

    canvas.saveState()
    canvas.setFillAlpha(0.12)
    canvas.setFillColorRGB(0.65, 0.10, 0.10)
    canvas.setFont("Helvetica-Bold", 46)
    canvas.translate(300, 430)
    canvas.rotate(35)
    canvas.drawCentredString(0, 0, WATERMARK_TEXT)
    canvas.restoreState()


def should_draw_gstin_watermark(invoice_data: dict[str, Any]) -> bool:
    """Return True when seller or buyer GSTIN is missing."""

    return not invoice_data.get("seller_gstin") or not invoice_data.get("buyer_gstin")


def build_styles(reportlab: dict[str, Any]) -> dict[str, Any]:
    """Build paragraph styles used by the invoice PDF."""

    get_sample_style_sheet = reportlab["getSampleStyleSheet"]
    paragraph_style = reportlab["ParagraphStyle"]
    colors = reportlab["colors"]
    styles = get_sample_style_sheet()

    return {
        "brand": paragraph_style(
            "DropInvoiceBrand",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=28,
            textColor=colors.HexColor("#111827"),
        ),
        "invoice_title": paragraph_style(
            "InvoiceTitle",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            alignment=2,
            textColor=colors.HexColor("#111827"),
        ),
        "section_label": paragraph_style(
            "SectionLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#4B5563"),
        ),
        "normal": paragraph_style("NormalText", parent=styles["Normal"], fontSize=10, leading=14),
        "small": paragraph_style("SmallText", parent=styles["Normal"], fontSize=8.5, leading=11),
        "small_right": paragraph_style(
            "SmallRight",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=11,
            alignment=2,
        ),
        "right": paragraph_style("RightText", parent=styles["Normal"], fontSize=9, leading=12, alignment=2),
        "muted": paragraph_style(
            "MutedText",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#6B7280"),
        ),
        "table_header": paragraph_style(
            "TableHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=colors.white,
        ),
        "total_label": paragraph_style(
            "TotalLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
        ),
        "total_value": paragraph_style(
            "TotalValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            alignment=2,
        ),
    }


def load_reportlab() -> dict[str, Any]:
    """Import ReportLab symbols lazily and return them as a dependency map."""

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise PDFGenerationError("reportlab is not installed.") from exc

    return {
        "A4": A4,
        "Paragraph": Paragraph,
        "ParagraphStyle": ParagraphStyle,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
        "Table": Table,
        "TableStyle": TableStyle,
        "colors": colors,
        "getSampleStyleSheet": getSampleStyleSheet,
    }


def format_money(value: Any) -> str:
    """Format a value as an INR amount using ASCII text."""

    numeric_value = to_float(value) or 0.0
    return f"INR {numeric_value:,.2f}"


def format_quantity(value: Any) -> str:
    """Format item quantity with compact decimal output."""

    numeric_value = to_float(value) or 0.0
    if numeric_value.is_integer():
        return str(int(numeric_value))

    return f"{numeric_value:.3f}".rstrip("0").rstrip(".")


def display_value(value: Any) -> str:
    """Return a display string for optional invoice values."""

    return escape_text(value) if value else "Not provided"


def escape_text(value: Any) -> str:
    """Escape text for safe insertion into ReportLab Paragraph markup."""

    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
