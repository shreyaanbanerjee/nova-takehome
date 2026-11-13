"""Document reader and text extraction engine.
Replicates grader/extract.py for exact quote matching, with OCR fallback for scanned PDFs.
"""
from __future__ import annotations

import io
import os
import re
from datetime import date, datetime
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Optional, Tuple, List

import openpyxl
import pymupdf

SEP = " | "


def norm(s: str) -> str:
    """Normalize string by collapsing all whitespace."""
    return " ".join(s.split())


# ---- PDF Extraction -----------------------------------------------------------------------------------
def _reading_order(page) -> str:
    words = page.get_text("words")
    words.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines: list[list] = []
    for w in words:
        mid = (w[1] + w[3]) / 2
        if lines and abs(lines[-1][0] - mid) <= 2.5:
            lines[-1][1].append(w)
        else:
            lines.append([mid, [w]])
    return "\n".join(" ".join(x[4] for x in sorted(ws, key=lambda w: w[0])) for _, ws in lines)


def pdf_pages(data: bytes) -> list[str]:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return [_reading_order(p) for p in doc]


def pdf_is_image_only(data: bytes) -> bool:
    """True when a PDF has no text layer (scanned document)."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return all(not p.get_text().strip() for p in doc)


def ocr_pdf_pages(data: bytes) -> list[str]:
    """Extract text from scanned PDFs using RapidOCR with vertical box grouping,
    with Claude Vision fallback if API key is provided."""
    pages_text: list[str] = []
    
    # Try RapidOCR
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                pix = page.get_pixmap()
                img_bytes = pix.tobytes("png")
                result, _ = ocr(img_bytes)
                if not result:
                    pages_text.append("")
                    continue
                boxes = []
                for box, text, score in result:
                    yc = (box[0][1] + box[2][1]) / 2
                    xc = box[0][0]
                    boxes.append((yc, xc, text))
                boxes.sort(key=lambda b: (b[0], b[1]))
                lines: list[list] = []
                for yc, xc, text in boxes:
                    if lines and abs(lines[-1][0] - yc) <= 8.0:
                        lines[-1][1].append((xc, text))
                    else:
                        lines.append([yc, [(xc, text)]])
                reconstructed = []
                for yc, line_boxes in lines:
                    line_boxes.sort(key=lambda b: b[0])
                    reconstructed.append(" ".join(b[1] for b in line_boxes))
                pages_text.append("\n".join(reconstructed))
    except Exception as e:
        pages_text = []

    # If OCR produced nothing and Anthropic API key is available, fallback to Claude Vision
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not any(pages_text) and api_key:
        try:
            import base64
            from anthropic import Anthropic
            base_url = os.environ.get("ANTHROPIC_BASE_URL")
            client = Anthropic(api_key=api_key, base_url=base_url)
            vision_pages = []
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                for page in doc:
                    pix = page.get_pixmap()
                    b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
                    msg = client.messages.create(
                        model="claude-3-5-sonnet-20241022",
                        max_tokens=4096,
                        messages=[{
                            "role": "user",
                            "content": [
                                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                                {"type": "text", "text": "Transcribe all text from this scanned freight invoice exactly line by line, preserving numbers, table headers, rates, and totals."}
                            ]
                        }]
                    )
                    content = "".join(b.text for b in msg.content if getattr(b, "text", None))
                    vision_pages.append(content)
            if any(vision_pages):
                pages_text = vision_pages
        except Exception:
            pass

    return pages_text


# ---- XLSX Extraction ----------------------------------------------------------------------------------
def _cell_text(cell) -> str:
    v = cell.value
    if v is None:
        return ""
    fmt = cell.number_format or "General"
    prefix = ""
    m = re.match(r'^"([^"]*)"(.*)$', fmt)
    if m:
        prefix, fmt = m.group(1), m.group(2)
    if isinstance(v, datetime):
        return prefix + v.date().isoformat()
    if isinstance(v, date):
        return prefix + v.isoformat()
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        dp = len(fmt.split(".")[1]) if "." in fmt else 0
        return prefix + f"{Decimal(str(v)):.{dp}f}"
    return prefix + str(v)


def xlsx_pages(data: bytes) -> list[str]:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    pages = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows():
            cells = [t for t in (_cell_text(c) for c in row) if t != ""]
            if cells:
                rows.append(SEP.join(cells))
        pages.append("\n".join(rows))
    return pages


# ---- Email Extraction ---------------------------------------------------------------------------------
def parse_eml(data: bytes):
    return BytesParser(policy=policy.default).parsebytes(data)


def email_lines(msg) -> list[str]:
    name, addr = parseaddr(str(msg["From"]))
    out = [f"From: {name} <{addr}>", f"To: {msg['To']}"]
    if msg["Cc"]:
        out.append(f"Cc: {msg['Cc']}")
    dt = parsedate_to_datetime(str(msg["Date"]))
    out += [f"Date: {dt.strftime('%Y-%m-%dT%H:%M:%SZ')}", f"Subject: {msg['Subject']}", ""]
    body = msg.get_body(preferencelist=("plain",))
    out += body.get_content().replace("\r\n", "\n").rstrip("\n").split("\n") if body else []
    names = [p.get_filename() for p in msg.iter_attachments()]
    if names:
        out.append("Attachments: " + ", ".join(names))
    return out


def attachments(data: bytes) -> list[tuple[str, bytes]]:
    return [(p.get_filename() or "", p.get_content()) for p in parse_eml(data).iter_attachments()]


# ---- Generic Reader ----------------------------------------------------------------------------------
def pages_of(data: bytes, ext: str) -> tuple[list[str], bool]:
    """Returns (pages_text, is_scan)."""
    ext = ext.lower().lstrip(".")
    if ext == "pdf":
        is_scan = pdf_is_image_only(data)
        if is_scan:
            return ocr_pdf_pages(data), True
        return pdf_pages(data), False
    if ext == "xlsx":
        return xlsx_pages(data), False
    if ext == "eml":
        return ["\n".join(email_lines(parse_eml(data)))], False
    raise ValueError(f"Unsupported extension: .{ext}")


def found(pages: list[str], page: int, quote: str) -> bool:
    """Matches the exact quote checking logic of grader/extract.py."""
    return 1 <= page <= len(pages) and norm(quote) in norm(pages[page - 1])

