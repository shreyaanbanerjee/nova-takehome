"""Tracking export, AP ledger register, and AWB document parser."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional, List, Dict

from harness.models import TrackingRecord, APEntry
from harness.invoice_parser import norm_invoice, parse_date, clean_num


class TrackingParser:
    @staticmethod
    def parse_tracking_sheet(doc_id: str, pages: list[str]) -> list[TrackingRecord]:
        records: list[TrackingRecord] = []
        for p_idx, page in enumerate(pages, 1):
            lines = page.split("\n")
            for line in lines:
                line_str = line.strip()
                if not line_str or "AWB | Event" in line_str:
                    continue
                parts = [p.strip() for p in line_str.split(" | ")]
                if len(parts) >= 3:
                    awb = parts[0]
                    event = parts[1]
                    ev_date = parse_date(parts[2]) or parts[2]
                    loc = parts[3] if len(parts) > 3 else ""
                    details = parts[4] if len(parts) > 4 else ""
                    # Only accept valid AWB-like strings
                    if re.match(r"^[A-Z]{2}\d{8}", awb):
                        rec = TrackingRecord(
                            awb=awb,
                            event=event,
                            event_date=ev_date,
                            location=loc,
                            details=details,
                            doc_id=doc_id,
                            page=p_idx,
                            raw_quote=line_str,
                        )
                        records.append(rec)
        return records

    @staticmethod
    def parse_ap_register(doc_id: str, pages: list[str]) -> list[APEntry]:
        entries: list[APEntry] = []
        for p_idx, page in enumerate(pages, 1):
            lines = page.split("\n")
            for line in lines:
                line_str = line.strip()
                if not line_str or "Invoice no." in line_str:
                    continue
                parts = [p.strip() for p in line_str.split(" | ")]
                if len(parts) >= 5:
                    inv_id = norm_invoice(parts[0])
                    inv_date = parse_date(parts[1]) or parts[1]
                    due_date_str = parts[2].replace("due", "").strip()
                    due_date = parse_date(due_date_str) or due_date_str
                    amt_str = parts[3].replace("USD", "").replace("INR", "").strip()
                    amt = clean_num(amt_str) or Decimal("0")
                    curr = "INR" if "INR" in parts[3] else "USD"
                    status = parts[4].lower().strip()
                    paid_date = parse_date(parts[5]) if len(parts) > 5 else None

                    entry = APEntry(
                        invoice_id=inv_id,
                        invoice_date=inv_date,
                        due_date=due_date,
                        amount=amt,
                        currency=curr,
                        status=status,
                        paid_date=paid_date,
                        doc_id=doc_id,
                        page=p_idx,
                        raw_quote=line_str,
                    )
                    entries.append(entry)
        return entries

    @staticmethod
    def parse_awb_pdf(doc_id: str, pages: list[str]) -> dict:
        """Parse master AWB details such as total pieces, chargeable weight, and house breakdown."""
        info = {
            "awb": "",
            "total_pieces": None,
            "total_weight": None,
            "houses": {},
            "raw_quotes": [],
            "doc_id": doc_id,
            "page": 1,
        }
        for p_idx, page in enumerate(pages, 1):
            lines = page.split("\n")
            for line in lines:
                line_str = line.strip()
                m_mawb = re.search(r"MASTER\s+AIR\s+WAYBILL\s+([A-Z0-9\-]+)", line_str)
                if m_mawb:
                    info["awb"] = m_mawb.group(1)
                m_totals = re.search(r"Total\s+pieces\s+(\d+)\s*\|\s*Total\s+chargeable\s+weight\s+(\d+(?:\.\d+)?)\s*kg", line_str)
                if m_totals:
                    info["total_pieces"] = int(m_totals.group(1))
                    info["total_weight"] = Decimal(m_totals.group(2))
                    info["raw_quotes"].append(line_str)
                m_house = re.search(r"House\s+([A-Z0-9\-]+):\s*(\d+)\s*pieces,\s*(\d+(?:\.\d+)?)\s*kg", line_str)
                if m_house:
                    h_awb, pcs, wt = m_house.groups()
                    info["houses"][h_awb] = {"pieces": int(pcs), "weight": Decimal(wt)}
                    info["raw_quotes"].append(line_str)
        return info

    @staticmethod
    def parse_dgd_pdf(doc_id: str, pages: list[str]) -> Optional[dict]:
        """Parse Shipper's Declaration for Dangerous Goods (DGD)."""
        full_text = "\n".join(pages)
        if "SHIPPER'S DECLARATION" not in full_text and "DGD-" not in doc_id and "UN1845" not in full_text:
            return None

        m_awb = re.search(r"\b([A-Z]{2}\d{8}(?:-H\d+)?)\b", full_text)
        awb = m_awb.group(1) if m_awb else ""

        for p_idx, page in enumerate(pages, 1):
            for line in page.split("\n"):
                line_str = line.strip()
                if "UN1845" in line_str:
                    is_refrig = "refrigerant only" in line_str.lower()
                    return {
                        "awb": awb,
                        "refrigerant_only": is_refrig,
                        "quote": line_str,
                        "doc_id": doc_id,
                        "page": p_idx,
                    }
        return None
