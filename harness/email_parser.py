"""Email message parser for waivers, rate notices, extensions, and instructions."""
from __future__ import annotations

import re
from dataclasses import asdict
from decimal import Decimal
from typing import Optional, List, Dict, Tuple

from harness.models import EmailWaiver, RateNotice, DueDateExtension
from harness.invoice_parser import norm_invoice, parse_date, clean_num, MONTHS
from harness.sanitizer import contains_injection_attempt


class EmailParser:
    @staticmethod
    def parse_email(doc_id: str, eml_text: str) -> dict:
        lines = eml_text.split("\n")
        header = {}
        body_lines = []
        in_body = False

        for line in lines:
            if not in_body:
                if line == "":
                    in_body = True
                    continue
                if line.startswith("From: "):
                    header["from"] = line[6:].strip()
                elif line.startswith("To: "):
                    header["to"] = line[4:].strip()
                elif line.startswith("Date: "):
                    header["date"] = line[6:].strip()
                elif line.startswith("Subject: "):
                    header["subject"] = line[9:].strip()
            else:
                body_lines.append(line)

        # Extract sender name and email
        sender_raw = header.get("from", "")
        m_sender = re.match(r"^(.+?)\s*<([\w\.\+\-]+@[\w\.\-]+)>", sender_raw)
        sender_name = m_sender.group(1).strip() if m_sender else sender_raw
        sender_email = m_sender.group(2).strip() if m_sender else ""

        sent_date = header.get("date", "")
        # Normalize date to YYYY-MM-DD
        m_dt = re.search(r"(\d{4}-\d{2}-\d{2})", sent_date)
        date_iso = m_dt.group(1) if m_dt else ""

        # Separate original body from forwarded / quoted text
        direct_body: list[str] = []
        quoted_body: list[str] = []
        is_quoted = False

        for bl in body_lines:
            bl_str = bl.strip()
            if re.match(r"^On\s+\d{4}-\d{2}-\d{2}.+wrote:", bl_str) or bl_str.startswith("From: ") or bl_str.startswith("-----Original Message-----"):
                is_quoted = True
            if is_quoted or bl_str.startswith(">"):
                quoted_body.append(bl_str.lstrip(">").strip())
            else:
                direct_body.append(bl_str)

        full_direct_text = "\n".join(direct_body)
        full_quoted_text = "\n".join(quoted_body)

        has_injection = contains_injection_attempt(full_direct_text)

        # Parse waivers
        waivers = EmailParser._extract_waivers(doc_id, sender_name, sender_email, date_iso, direct_body, is_quoted=False)
        quoted_waivers = EmailParser._extract_waivers(doc_id, sender_name, sender_email, date_iso, quoted_body, is_quoted=True)

        # Parse rate notices
        rate_notices = EmailParser._extract_rate_notices(doc_id, sender_name, sender_email, date_iso, direct_body)

        # Parse due date extensions
        extensions = EmailParser._extract_extensions(doc_id, sender_name, sender_email, date_iso, direct_body)

        return {
            "doc_id": doc_id,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "date": date_iso,
            "subject": header.get("subject", ""),
            "has_injection": has_injection,
            "waivers": [asdict(w) for w in (waivers + quoted_waivers)],
            "rate_notices": [asdict(rn) for rn in rate_notices],
            "extensions": [asdict(de) for de in extensions],
            "direct_body": direct_body,
        }

    @staticmethod
    def _extract_waivers(
        doc_id: str, sender_name: str, sender_email: str, date_iso: str, lines: list[str], is_quoted: bool
    ) -> list[EmailWaiver]:
        waivers: list[EmailWaiver] = []
        for line in lines:
            line_str = line.strip()
            if "waive" in line_str.lower():
                # Detect target AWB
                m_awb = re.search(r"\b([A-Z]{2}\d{8})\b", line_str)
                awb = m_awb.group(1) if m_awb else None

                # Detect target charge type
                charge = None
                for c in [
                    "Delivery fee",
                    "Security surcharge",
                    "Storage",
                    "Screening fee",
                    "Dangerous goods",
                    "Dry ice",
                    "Account fee",
                    "Handling fee",
                    "Fuel surcharge",
                    "Customs",
                    "Disbursement",
                ]:
                    if c.lower() in line_str.lower():
                        charge = c
                        break

                # Detect month (e.g. 2035-05, May 2035)
                m_m = re.search(r"\b(\d{4}-\d{2})\b", line_str)
                month = m_m.group(1) if m_m else None
                if not month:
                    m_named = re.search(r"\b([A-Za-z]+)\s+(\d{4})\b", line_str)
                    if m_named and m_named.group(1).lower()[:3] in MONTHS:
                        m_idx = MONTHS[m_named.group(1).lower()[:3]]
                        month = f"{m_named.group(2)}-{m_idx:02d}"

                scope = "single_shipment"
                if month and not awb:
                    scope = "month"

                w = EmailWaiver(
                    sender_name=sender_name,
                    sender_email=sender_email,
                    doc_id=doc_id,
                    page=1,
                    sent_date=date_iso,
                    target_awb=awb,
                    target_charge_type=charge,
                    target_month=month,
                    scope=scope,
                    is_quoted_forward=is_quoted,
                    quote=line_str,
                )
                waivers.append(w)
        return waivers

    @staticmethod
    def _extract_rate_notices(
        doc_id: str, sender_name: str, sender_email: str, date_iso: str, lines: list[str]
    ) -> list[RateNotice]:
        notices: list[RateNotice] = []
        full_text = "\n".join(lines)
        if "rate notice" not in full_text.lower() and "under clause 3.5" not in full_text.lower():
            return notices

        contract_id = None
        m_cid = re.search(r"Reference:\s*([A-Z0-9\-]+)", full_text)
        if m_cid:
            contract_id = m_cid.group(1).split("/")[0]

        eff_from, eff_to = None, None
        m_dates = re.search(
            r"effective\s+(?:from\s+)?(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}[-\s]+[A-Za-z]{3,}[-\s]+\d{4})\s+(?:to|until|through|-)\s+(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}[-\s]+[A-Za-z]{3,}[-\s]+\d{4})",
            full_text,
            re.IGNORECASE,
        )
        if m_dates:
            eff_from = parse_date(m_dates.group(1))
            eff_to = parse_date(m_dates.group(2))

        # Parse individual rates
        # e.g.: USC3-INS1 45-99.5 kg: 5.03 USD
        # e.g.: USC3-INS1 minimum charge: 90.77 USD
        for line in lines:
            line_str = line.strip()
            m_r = re.search(r"([A-Z0-9]+-[A-Z0-9]+)\s+([^:]+):\s*([\d\.]+)\s*(USD|INR)", line_str)
            if m_r:
                lane, band_str, rate_val, curr = m_r.groups()
                rn = RateNotice(
                    sender_name=sender_name,
                    sender_email=sender_email,
                    doc_id=doc_id,
                    page=1,
                    sent_date=date_iso,
                    contract_id=contract_id,
                    lane=lane,
                    weight_band=band_str.strip(),
                    rate=Decimal(rate_val),
                    currency=curr,
                    effective_from=eff_from,
                    effective_to=eff_to,
                    quote=line_str,
                )
                notices.append(rn)
        return notices

    @staticmethod
    def _extract_extensions(
        doc_id: str, sender_name: str, sender_email: str, date_iso: str, lines: list[str]
    ) -> list[DueDateExtension]:
        exts: list[DueDateExtension] = []
        for line in lines:
            line_str = line.strip()
            # e.g.: We extend the due date of invoice TX-US-2035-0202 to 2035-05-07.
            m = re.search(
                r"extend(?:ed)?\s+the\s+due\s+date\s+of\s+(?:invoice\s+)?([A-Z0-9\-]+)\s+to\s+(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}[-\s]+[A-Za-z]{3,}[-\s]+\d{4})",
                line_str,
                re.IGNORECASE,
            )
            if m:
                inv_id = norm_invoice(m.group(1))
                new_due = parse_date(m.group(2)) or m.group(2)
                de = DueDateExtension(
                    sender_name=sender_name,
                    sender_email=sender_email,
                    doc_id=doc_id,
                    page=1,
                    sent_date=date_iso,
                    invoice_id=inv_id,
                    extended_due_date=new_due,
                    quote=line_str,
                )
                exts.append(de)
        return exts
