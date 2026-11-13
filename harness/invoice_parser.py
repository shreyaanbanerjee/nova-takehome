"""Invoice, statement of account, and credit note parser."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional, List, Tuple

from harness.models import Invoice, InvoiceLine


def norm_invoice(s: str) -> str:
    """Normalize invoice ID per GLOSSARY.md: capitals, without spaces, '#' or ':'."""
    return re.sub(r"[\s#:]", "", str(s or "")).upper()


MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_date(s: str) -> Optional[str]:
    """Parse YYYY-MM-DD, DD/MM/YYYY, or DD Mon YYYY into YYYY-MM-DD."""
    if not s:
        return None
    s = s.strip()
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m_iso:
        return s
    m_slash = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m_slash:
        d, m, y = m_slash.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    m_mon = re.match(r"^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$", s)
    if m_mon:
        d, m_str, y = m_mon.groups()
        m_num = MONTHS.get(m_str.lower())
        if m_num:
            return f"{int(y):04d}-{m_num:02d}-{int(d):02d}"
    return None


def clean_num(s: str) -> Optional[Decimal]:
    if not s:
        return None
    try:
        return Decimal(s.replace(",", "").strip())
    except Exception:
        return None


class InvoiceParser:
    @staticmethod
    def is_invoice_or_credit_note(doc_id: str, pages: list[str]) -> bool:
        if not pages:
            return False
        first = pages[0]
        keywords = [
            "INVOICE",
            "Invoice",
            "Charges advice",
            "STATEMENT OF ACCOUNT",
            "CREDIT NOTE",
            "Credit Note",
            "Freight bill",
            "Freight Bill",
            "FREIGHT BILL",
        ]
        return any(k in first for k in keywords)

    @staticmethod
    def parse(
        doc_id: str,
        pages: list[str],
        is_scan: bool = False,
        email_date: str = "",
        email_doc_id: str = "",
    ) -> Optional[Invoice]:
        if not pages or not any(p.strip() for p in pages):
            return None

        p1 = pages[0]
        is_credit = "CREDIT NOTE" in p1.upper() or "CREDIT ADVICE" in p1.upper()
        
        # 1. Extract Invoice / Advice ID
        inv_id = ""
        # Match "INVOICE TX-US-...", "Charges advice CDU-...", "STATEMENT OF ACCOUNT TX-ST-...", "CREDIT NOTE CDC/...", "Freight bill no. TX-US-..."
        m_id = re.search(r"(?:INVOICE|Charges\s+advice|STATEMENT\s+OF\s+ACCOUNT|CREDIT\s+NOTE|Freight\s+bill(?:\s+no\.?)?)[\s:]+([A-Z0-9\/\-]+)", p1, re.IGNORECASE)
        if m_id:
            inv_id = norm_invoice(m_id.group(1))
        
        if not inv_id:
            # Fallback to doc name
            base = doc_id.split("#")[-1].replace(".pdf", "").replace(".xlsx", "")
            if any(base.startswith(p) for p in ("TX-", "CD", "INV-", "TXI-")):
                inv_id = norm_invoice(base)

        if not inv_id:
            return None

        inv = Invoice(
            invoice_id=inv_id,
            doc_id=doc_id,
            page=1,
            email_received_date=email_date,
            email_doc_id=email_doc_id,
            is_scan=is_scan,
            is_credit_note=is_credit,
        )

        # 2. Extract Dates
        # Document date / Invoice date / Statement date / Issued on
        m_inv_dt = re.search(r"(?:Document\s+date|Invoice\s+date|Statement\s+date|Credit\s+date|Issued\s+on)[:\s]+(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3}\s+\d{4})", p1, re.IGNORECASE)
        if m_inv_dt:
            inv.invoice_date = parse_date(m_inv_dt.group(1)) or ""

        # Due date / Payable by / Settle by
        m_due_dt = re.search(r"(?:Due\s+date|Payable\s+by|Settle\s+by)[:\s]+(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3}\s+\d{4})", p1, re.IGNORECASE)
        if m_due_dt:
            inv.due_date = parse_date(m_due_dt.group(1)) or ""

        # Contract reference / Service agreement
        m_cref = re.search(r"(?:Contract(?:\s+reference)?|Service\s+agreement)[:\s]+([A-Z0-9\-]+)", p1, re.IGNORECASE)
        if m_cref:
            inv.contract_ref = m_cref.group(1).strip()

        # Currency
        m_curr = re.search(r"(?:Currency|Total\s+(USD|INR))[:\s]+(USD|INR)", p1, re.IGNORECASE)
        if m_curr:
            inv.currency = (m_curr.group(2) or m_curr.group(1)).upper()
        elif "INR" in p1:
            inv.currency = "INR"
        else:
            inv.currency = "USD"

        # If Credit note
        if is_credit:
            InvoiceParser._parse_credit_note(inv, p1)
            return inv

        # 3. Parse Line Items
        for p_idx, page in enumerate(pages, 1):
            lines = page.split("\n")
            for line in lines:
                line_str = line.strip()
                if not line_str:
                    continue
                inv.all_raw_quotes.append(line_str)
                InvoiceParser._parse_line(inv, line_str, p_idx)

        # 4. Extract Total Amount
        m_tot = re.search(r"(?:Total\s+payable|Total\s+USD|Total\s+INR|Charges\s+(?:USD|INR)|Balance\s+due(?:\s+USD)?|Net\s+charges(?:\s+USD)?|Total)[:\s]+(?:USD|INR)?\s*([\d,]+\.\d{2})", p1, re.IGNORECASE)
        if m_tot:
            inv.total_amount = clean_num(m_tot.group(1)) or Decimal("0")
        elif inv.lines:
            inv.total_amount = sum((l.amount for l in inv.lines), Decimal("0"))

        return inv

    @staticmethod
    def _parse_credit_note(inv: Invoice, text: str) -> None:
        # e.g.: Credit type improvement | period CY1
        m_type = re.search(r"Credit\s+type\s+([a-z_]+)\s*\|\s*period\s*([A-Z0-9\-]+)", text, re.IGNORECASE)
        if m_type:
            ctype = m_type.group(1).lower()
            period = m_type.group(2).upper()
            if "improvement" in ctype:
                inv.credit_for_type = "promised_credit"
            elif "rebate" in ctype:
                inv.credit_for_type = "rebate"
            elif "margin" in ctype:
                inv.credit_for_type = "margin_step_down"
            else:
                inv.credit_for_type = ctype
            inv.credit_for_period = period

        m_tot = re.search(r"Total\s+credit\s+(?:USD|INR)?\s*([\d,]+\.\d{2})", text, re.IGNORECASE)
        if m_tot:
            inv.total_amount = clean_num(m_tot.group(1)) or Decimal("0")

    @staticmethod
    def _parse_line(inv: Invoice, line: str, page_num: int) -> None:
        # Check if line contains an AWB: 2 letters followed by 8 digits (e.g. TE56915687, TX13648356, CA51959332)
        # or house suffix (-H1, -H2)
        m_awb = re.search(r"\b([A-Z]{2}\d{8}(?:-H\d+)?)\b", line)
        awb = m_awb.group(1) if m_awb else ""

        # Check for Excel statement row:
        # Charge | Reference | Quantity | Rate | Amount
        # Monthly account fee | 2035-05 | 1 month | 350.28 | 350.28
        if " | " in line and not awb:
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) >= 5 and clean_num(parts[-1]) is not None:
                amt = clean_num(parts[-1])
                rate = clean_num(parts[-2])
                qty_basis = parts[-3] if len(parts) >= 3 else "1"
                desc = parts[0]
                il = InvoiceLine(
                    line_no=len(inv.lines) + 1,
                    awb="",
                    pickup_date=None,
                    description=desc,
                    charge_code="ACCT",
                    lane=None,
                    weight_kg=None,
                    qty_basis=qty_basis,
                    rate=rate,
                    amount=amt or Decimal("0"),
                    raw_quote=line,
                    page=page_num,
                )
                inv.lines.append(il)
                return

        # Check for Late fee line:
        # 13 - - Late fee on TX-US-2032-5494: USD 680.12 overdue, month 1 @ 1.86% - 1 month 1.86% 12.65
        # Late fee on TX-US-2035-0233: USD 2002.93 overdue, month 1 @ 1.98% - - - 39.66 1 month 1.98%
        if "Late fee on" in line:
            nums = re.findall(r"\b([\d,]+\.\d{2})(?!%)\b", line)
            amt = clean_num(nums[-1]) if nums else Decimal("0")
            il = InvoiceLine(
                line_no=len(inv.lines) + 1,
                awb="",
                pickup_date=None,
                description=line.strip(),
                charge_code="LF",
                lane=None,
                weight_kg=None,
                qty_basis="1 month",
                rate=None,
                amount=amt or Decimal("0"),
                raw_quote=line,
                page=page_num,
            )
            inv.lines.append(il)
            return

        # Check for Freight bill layout:
        # Charge Shipment Route Collected Net (USD) Units Unit price
        # Air freight TX99107819 USC3-INS1 20 Aug 2035 612.00 120 kg 5.10
        # Fuel surcharge TX99107819 USC3-INS1 20 Aug 2035 136.84 612.00 22.36%
        # Security surcharge TX99107819 USC3-INS1 20 Aug 2035 14.40 120 kg 0.12
        # Delivery fee TX99107819 USC3-INS1 20 Aug 2035 116.13 1 shpt 116.13
        # Dry ice handling fee TX45954367 USS2-INW2 31 Jul 2035 45.40 1 shpt 45.40
        # Peak season surcharge TX53405237 USE1-USW2 05 Sep 2035 166.46 574 kg 0.29
        fb_pattern = re.compile(
            r"^(?P<desc>.+?)\s+"
            r"(?P<awb>[A-Z]{2}\d{8}(?:-H\d+)?)\s+"
            r"(?P<lane>[A-Z0-9]+-[A-Z0-9]+)\s+"
            r"(?P<date>\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+"
            r"(?P<net>[\d,]+\.\d{2})\s+"
            r"(?P<basis>[\d,]+(?:\.\d+)?\s*(?:kg|shpt|shp)?)\s+"
            r"(?P<rate>[\d,]+(?:\.\d+)?%?)$"
        )
        m_fb = fb_pattern.match(line)
        if m_fb:
            d = m_fb.groupdict()
            awb = d["awb"]
            lane = d["lane"]
            pickup_dt = parse_date(d["date"])
            amt = clean_num(d["net"]) or Decimal("0")
            basis_str = d["basis"].strip()
            rate_str = d["rate"].strip()
            weight_kg = None
            if "kg" in basis_str:
                m_w = re.search(r"(\d+(?:\.\d+)?)\s*kg", basis_str)
                if m_w:
                    weight_kg = Decimal(m_w.group(1))
            rate = None
            if "%" in rate_str:
                rate = clean_num(rate_str.replace("%", ""))
            else:
                rate = clean_num(rate_str)

            desc_str = d["desc"].strip()
            code = None
            for alias, ccode in [
                ("air freight", "FRT"),
                ("airfreight", "FRT"),
                ("fuel surcharge", "FUEL"),
                ("security surcharge", "SEC"),
                ("screening fee", "SEC"),
                ("delivery fee", "DLV"),
                ("storage", "STOR"),
                ("dangerous goods", "DG"),
                ("dg handling", "DG"),
                ("hazmat", "DG"),
                ("dry ice", "DRYICE"),
                ("customs", "CUSTOMS"),
                ("peak season", "PEAK"),
                ("import duty", "DUTY"),
                ("duty paid", "DUTY"),
                ("disbursement", "DUTY"),
            ]:
                if alias in desc_str.lower():
                    code = ccode
                    break

            il = InvoiceLine(
                line_no=len(inv.lines) + 1,
                awb=awb,
                pickup_date=pickup_dt,
                description=desc_str,
                charge_code=code,
                lane=lane,
                weight_kg=weight_kg,
                qty_basis=basis_str,
                rate=rate,
                amount=amt,
                raw_quote=line,
                page=page_num,
            )
            inv.lines.append(il)
            return

        if not awb:
            return

        # We have an AWB line!
        # Detect Lane (e.g. USE2-INE2, USC3-INS1, INE2-USW1)
        m_lane = re.search(r"\b([A-Z]{3,4}\d-[A-Z]{3,4}\d)\b", line)
        lane = m_lane.group(1) if m_lane else None

        # Detect Pickup date (YYYY-MM-DD or DD/MM/YYYY)
        m_dt = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", line)
        pickup_dt = parse_date(m_dt.group(1)) if m_dt else None

        # Detect Charge Type
        desc = ""
        code = None
        for ctype, ccode in [
            ("Air freight", "FRT"),
            ("Airfreight", "FRT"),
            ("Fuel surcharge", "FUEL"),
            ("Security surcharge", "SEC"),
            ("Screening fee", "SEC"),
            ("Delivery fee", "DLV"),
            ("Storage", "STOR"),
            ("Dangerous goods fee", "DG"),
            ("Dangerous goods", "DG"),
            ("DG handling", "DG"),
            ("Hazmat", "DG"),
            ("Dry ice", "DRYICE"),
            ("Customs", "CUSTOMS"),
            ("Peak season surcharge", "PEAK"),
            ("Peak season", "PEAK"),
            ("Import duty", "DUTY"),
            ("Duty paid", "DUTY"),
            ("Disbursement", "DUTY"),
        ]:
            if ctype.lower() in line.lower():
                desc = ctype
                code = ccode
                break

        # Detect weight in kg
        m_wt = re.search(r"(\d+(?:\.\d+)?)\s*kg", line)
        weight_kg = Decimal(m_wt.group(1)) if m_wt else None

        # Detect quantity/basis (e.g. 5 days, 1 shpt, 241 kg)
        m_basis = re.search(r"(\d+(?:\.\d+)?\s*(?:kg|days|shpt|shp|month))", line)
        qty_basis = m_basis.group(1) if m_basis else (f"{weight_kg} kg" if weight_kg else "1 shpt")

        # Detect amounts and rates at the end of the line
        # e.g.: 241 kg 6.69 1612.29
        # e.g.: 90,535.08 25.33% 22,932.54 06/01/2031
        # e.g.: 299 kg 5.69 1,701.31 19/05/2030
        nums = re.findall(r"[\d,]+(?:\.\d+)?%?", line)
        amt = Decimal("0")
        rate = None

        # Look for the last dollar/amount figure
        decimal_nums = [n for n in nums if "." in n and "%" not in n]
        if decimal_nums:
            amt = clean_num(decimal_nums[-1]) or Decimal("0")
            if len(decimal_nums) >= 2:
                rate = clean_num(decimal_nums[-2])

        # Check if rate was a percentage (for fuel surcharge)
        m_pct = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
        if m_pct:
            rate = Decimal(m_pct.group(1))

        il = InvoiceLine(
            line_no=len(inv.lines) + 1,
            awb=awb,
            pickup_date=pickup_dt,
            description=desc or line,
            charge_code=code,
            lane=lane,
            weight_kg=weight_kg,
            qty_basis=qty_basis,
            rate=rate,
            amount=amt,
            raw_quote=line,
            page=page_num,
        )
        inv.lines.append(il)
