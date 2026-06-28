"""ReportLab payslip generator for DropInvoice salary slips.

A salary slip is a distinct document from the GST sales invoice — its own number
series (PAYSLIP-YYYYMM-XXXX), its own earnings/deductions layout, and never any
GST. It reuses the shared ReportLab helpers from invoice.generator for a
consistent look.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from invoice.generator import (
    DEFAULT_OUTPUT_DIR,
    PAGE_MARGIN,
    PDFGenerationError,
    build_styles,
    escape_text,
    format_money,
    load_reportlab,
)

PAYSLIP_PREFIX = "PAYSLIP"


def generate_payslip_pdf(payslip: dict[str, Any], output_dir: str | Path | None = None) -> str:
    """Generate a salary slip PDF and return the saved file path."""

    reportlab = load_reportlab()
    payslip.setdefault("slip_number", next_payslip_number(output_dir))
    payslip.setdefault("date", date.today().isoformat())

    pdf_path = build_payslip_path(payslip["slip_number"], output_dir)
    document = reportlab["SimpleDocTemplate"](
        str(pdf_path),
        pagesize=reportlab["A4"],
        rightMargin=PAGE_MARGIN,
        leftMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=PAGE_MARGIN,
        title=payslip["slip_number"],
        author="DropInvoice",
    )
    styles = build_styles(reportlab)
    story = _build_payslip_story(payslip, styles, reportlab)

    try:
        document.build(story)
    except Exception as exc:
        raise PDFGenerationError(f"Could not generate payslip PDF: {pdf_path}") from exc

    return str(pdf_path)


def build_payslip_path(slip_number: str, output_dir: str | Path | None = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", slip_number).strip("-")
    directory = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe}.pdf"


def next_payslip_number(output_dir: str | Path | None = None) -> str:
    today = date.today()
    year_month = today.strftime("%Y%m")
    directory = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{PAYSLIP_PREFIX}-{year_month}-(\d{{4}})\.pdf$")
    numbers = [
        int(m.group(1))
        for p in directory.glob(f"{PAYSLIP_PREFIX}-{year_month}-*.pdf")
        if (m := pattern.match(p.name))
    ]
    return f"{PAYSLIP_PREFIX}-{year_month}-{max(numbers, default=0) + 1:04d}"


def _build_payslip_story(payslip, styles, reportlab):
    paragraph = reportlab["Paragraph"]
    spacer = reportlab["Spacer"]
    table = reportlab["Table"]
    table_style = reportlab["TableStyle"]
    colors = reportlab["colors"]

    story: list[Any] = []

    # Header
    left = [
        paragraph("DropInvoice", styles["brand"]),
        paragraph(escape_text(payslip.get("employer_name") or "Salary Slip"), styles["muted"]),
    ]
    right = [
        paragraph("SALARY SLIP", styles["invoice_title"]),
        paragraph(f"Slip No: {escape_text(payslip['slip_number'])}", styles["right"]),
        paragraph(f"Date: {escape_text(payslip['date'])}", styles["right"]),
    ]
    header = table([[left, right]], colWidths=[300, 210])
    header.setStyle(table_style([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#D7DEE8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(header)
    story.append(spacer(1, 14))

    # Employee details
    details = [
        paragraph("Employee", styles["section_label"]),
        paragraph(escape_text(payslip.get("employee_name") or "Employee"), styles["normal"]),
        paragraph(f"Designation: {escape_text(payslip.get('designation') or '-')}", styles["small"]),
        paragraph(f"Pay Period: {escape_text(payslip.get('pay_period') or '-')}", styles["small"]),
    ]
    detail_table = table([[details]], colWidths=[510])
    detail_table.setStyle(table_style([
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#D7DEE8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(detail_table)
    story.append(spacer(1, 16))

    # Earnings + Deductions side by side
    earnings_tbl = _component_table(payslip.get("earnings", []), "Earnings",
                                    payslip.get("gross_earnings", 0), styles, reportlab)
    deductions_tbl = _component_table(payslip.get("deductions", []), "Deductions",
                                      payslip.get("total_deductions", 0), styles, reportlab)
    side_by_side = table([[earnings_tbl, deductions_tbl]], colWidths=[255, 255])
    side_by_side.setStyle(table_style([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(side_by_side)
    story.append(spacer(1, 16))

    # Net pay
    net = table(
        [[paragraph("NET PAY", styles["total_label"]),
          paragraph(format_money(payslip.get("net_pay")), styles["total_value"])]],
        colWidths=[150, 110], hAlign="RIGHT",
    )
    net.setStyle(table_style([
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#1F2937")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(net)
    story.append(spacer(1, 18))

    if payslip.get("notes"):
        story.append(paragraph(f"Notes: {escape_text(payslip['notes'])}", styles["small"]))
        story.append(spacer(1, 8))
    story.append(paragraph("Generated by DropInvoice. This payslip does not attract GST.", styles["muted"]))
    return story


def _component_table(components, title, total, styles, reportlab):
    paragraph = reportlab["Paragraph"]
    table = reportlab["Table"]
    table_style = reportlab["TableStyle"]
    colors = reportlab["colors"]

    rows = [[paragraph(title, styles["table_header"]), paragraph("Amount", styles["table_header"])]]
    for entry in components:
        rows.append([
            paragraph(escape_text(entry.get("component") or "Item"), styles["small"]),
            paragraph(format_money(entry.get("amount")), styles["small_right"]),
        ])
    if not components:
        rows.append([paragraph("None", styles["small"]), paragraph(format_money(0), styles["small_right"])])
    rows.append([
        paragraph(f"Total {title}", styles["total_label"]),
        paragraph(format_money(total), styles["total_value"]),
    ])

    comp_table = table(rows, colWidths=[170, 79])
    comp_table.setStyle(table_style([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7DEE8")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F4F7FB")),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return comp_table
