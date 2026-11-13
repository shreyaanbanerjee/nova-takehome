"""Contract, amendment, and Annex parsing engine."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Tuple

from harness.models import (
    ContractTerms,
    RateBand,
    AccessorialRate,
    CashbackTier,
    AuthorisedPerson,
)
from harness.invoice_parser import parse_date


def parse_weight_band(band_str: str) -> tuple[Decimal, Decimal, bool]:
    """Parse weight band like '0-44.5 kg', '100-299.5 kg', '300+ kg', 'minimum charge'."""
    b = band_str.lower().strip()
    if "minimum charge" in b or "min" in b:
        return Decimal("0"), Decimal("0"), True
    m_plus = re.search(r"(\d+(?:\.\d+)?)\s*\+\s*kg", b)
    if m_plus:
        return Decimal(m_plus.group(1)), Decimal("999999"), False
    m_range = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*kg", b)
    if m_range:
        return Decimal(m_range.group(1)), Decimal(m_range.group(2)), False
    return Decimal("0"), Decimal("999999"), False


class ContractParser:
    @staticmethod
    def is_contract_or_amendment(doc_id: str, pages: list[str]) -> bool:
        if not pages:
            return False
        first_page = pages[0]
        return any(
            k in first_page
            for k in [
                "Master Services Agreement",
                "Amendment No.",
                "Amendment reference",
                "Agreement No.",
            ]
        )

    @staticmethod
    def parse(doc_id: str, pages: list[str]) -> ContractTerms:
        full_text = "\n\n".join(pages)
        is_amendment = "Amendment No." in pages[0] or "Amendment reference" in pages[0]
        
        # Extract Contract ID
        contract_id = ""
        m_cid = re.search(r"Agreement\s+No\.\s+([A-Z0-9\-]+)", pages[0])
        if m_cid:
            contract_id = m_cid.group(1).strip()
        if not contract_id:
            m_ref = re.search(r"Amendment\s+reference\s+([A-Z0-9\-]+(?:/[A-Z0-9\-]+)?)", pages[0])
            if m_ref:
                contract_id = m_ref.group(1).strip()
        if not contract_id:
            m_cid2 = re.search(r"(?:Master\s+Services\s+Agreement\s+\(Express\s+Air\))\s+([A-Z0-9\-]+)", pages[0])
            if m_cid2:
                contract_id = m_cid2.group(1).strip()
        if not contract_id:
            # Fallback from filename
            base = doc_id.split("#")[-1].replace(".pdf", "").replace("MSA-", "")
            contract_id = base

        ct = ContractTerms(contract_id=contract_id, doc_id=doc_id)
        ct.is_amendment = is_amendment

        if is_amendment:
            ContractParser._parse_amendment(ct, doc_id, pages)
        else:
            ContractParser._parse_msa(ct, doc_id, pages)

        return ct

    @staticmethod
    def _parse_msa(ct: ContractTerms, doc_id: str, pages: list[str]) -> None:
        # Search page 1 & 2 for Shipper & Carrier
        p1 = pages[0]
        m_ship = re.search(r"(?:between|\(1\))\s+([A-Z][A-Za-z0-9\s,\.]+?)\s*(?:\(|whose|\n)", p1)
        if m_ship:
            ct.shipper = m_ship.group(1).strip()
        m_carr = re.search(r"(?:and|\(2\))\s+([A-Z][A-Za-z0-9\s,\.]+?)\s*(?:\(|whose|\n)", p1)
        if m_carr:
            ct.carrier = m_carr.group(1).strip()
            
        m_dt = re.search(r"Dated\s+(\d{4}-\d{2}-\d{2})", p1)
        if m_dt:
            ct.contract_date = m_dt.group(1)

        for p_idx, page in enumerate(pages, 1):
            # Term & Initial term
            m_term = re.search(r"2\.2\s+Initial term\.\s+The initial term is\s+(\d+)\s+months,\s+ending on\s+(\d{4}-\d{2}-\d{2})\.", page)
            if m_term:
                ct.initial_term_months = int(m_term.group(1))
                ct.end_date = m_term.group(2)
                ct.expiry_clause_quote = m_term.group(0)
                ct.expiry_clause_doc = doc_id
                ct.expiry_clause_page = p_idx

            m_auto = re.search(r"2\.3\s+Auto-renewal\.\s+(This Agreement (?:does not|renews) automatically[^\.\n]*\.)", page)
            if m_auto:
                ct.auto_renew = "does not" not in m_auto.group(1)
                if not ct.expiry_clause_quote:
                    ct.expiry_clause_quote = m_auto.group(0)
                    ct.expiry_clause_doc = doc_id
                    ct.expiry_clause_page = p_idx

            # Notice deadline calculation
            if ct.end_date:
                # Default notice is 60 days before end_date
                try:
                    end_dt = datetime.strptime(ct.end_date, "%Y-%m-%d")
                    ct.notice_deadline = (end_dt - timedelta(days=ct.notice_days)).strftime("%Y-%m-%d")
                except Exception:
                    pass

            # Pricing date clause
            m_pricing = re.search(r"3\.2\s+Pricing date\.\s+Each shipment is priced at the rates, surcharges and fees in force on its Pickup Date\.", page)
            if m_pricing:
                ct.pricing_date_clause_quote = m_pricing.group(0)
                ct.pricing_date_clause_doc = doc_id
                ct.pricing_date_clause_page = p_idx

            # Rate notices clause 3.5
            m_rn_clause = re.search(r"3\.5\s+Rate notices\.\s+(The Carrier may change Annex A rates by written notice from an Authorised Person \(rates\)[^\.\n]*?\.)", page)
            if m_rn_clause:
                ct.rate_notices_clause_quote = m_rn_clause.group(0)
                ct.rate_notices_clause_doc = doc_id
                ct.rate_notices_clause_page = p_idx

            # Waiver clause
            m_waiver = re.search(r"11\.1\s+Waiver authority\.\s+A charge may be waived only in writing by a person listed in Schedule 1 for waivers\.\s+Schedule 1 is exhaustive\.", page)
            if m_waiver:
                ct.waiver_clause_quote = m_waiver.group(0)
                ct.waiver_clause_doc = doc_id
                ct.waiver_clause_page = p_idx

            # Dry ice clause
            m_di = re.search(r"5\.2\s+Dry ice\.\s+(Dry ice\s+\(UN1845\).*?does not attract the Dangerous Goods Fee\.)", page, re.DOTALL)
            if m_di:
                ct.dry_ice_clause_quote = m_di.group(0)
                ct.dry_ice_clause_doc = doc_id
                ct.dry_ice_clause_page = p_idx

            # Late fee clause
            m_lf = re.search(r"10\.5\s+Extension of due date\.\s+A due date may be extended by written notice from the Carrier's Authorised Person \(payment terms\);\s+the extended date replaces the due date for clause 10\.2\.", page)
            if m_lf:
                ct.late_fee_clause_quote = m_lf.group(0)
                ct.late_fee_clause_doc = doc_id
                ct.late_fee_clause_page = p_idx

            m_lf102 = re.search(r"10\.2\s+Late fee\.\s+(Undisputed amounts unpaid more than.*?(?:base_i per clause 10\.3\.))", page, re.DOTALL)
            if m_lf102:
                ct.late_fee_clause_10_2_quote = m_lf102.group(0)
                ct.late_fee_clause_10_2_doc = doc_id
                ct.late_fee_clause_10_2_page = p_idx
            
            # Late fee rate & compounding
            m_rate = re.search(r"fee_i\s*=\s*(\d+(?:\.\d+)?)\s*%\s*x\s*base_i", page)
            if m_rate:
                ct.late_fee_pct_monthly = Decimal(m_rate.group(1))
            if "10.3 Compounding." in page:
                ct.late_fee_compounds = "added to the overdue balance" in page

            # Qualifying Spend & Rebate clause
            m_qs = re.search(r"1\.1\s+Qualifying Spend\.\s+(Qualifying Spend means.*?(?:month of the invoice date\.|per invoice at the Annex F rate[^\n\.]*\.))", page, re.DOTALL)
            if m_qs:
                ct.qualifying_spend_quote = m_qs.group(0).strip()
                ct.qualifying_spend_doc = doc_id
                ct.qualifying_spend_page = p_idx

            m_rebate = re.search(r"7\.1\s+Volume rebate\.\s+(The Carrier shall credit a rebate on each Quarter\'s Qualifying Spend \(QS\) at the tier reached:.*?(?:Quarter\.|period\.))", page, re.DOTALL)
            if m_rebate:
                ct.rebate_clause_quote = m_rebate.group(0).strip()
                ct.rebate_clause_doc = doc_id
                ct.rebate_clause_page = p_idx
                # Parse tiers: e.g. QS >= USD 55,000.00: 5%; QS >= USD 91,000.00: 7.5%; QS >= USD 150,000.00: 10%
                for m_t in re.finditer(r"QS\s*>=\s*(?:USD|INR)\s*([\d,]+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*%", m_rebate.group(0)):
                    spend = Decimal(m_t.group(1).replace(",", ""))
                    rate = Decimal(m_t.group(2))
                    ct.rebate_tiers.append(CashbackTier(min_spend=spend, rate_pct=rate))

            # Unranked instruments / Precedence clause
            m_unranked = re.search(r"13\.3\s+Unranked instruments\.\s+(Where a rate notice under clause 3\.5 and an amendment state different rates for the same\s+lane and\s+period, the parties shall resolve the difference jointly; neither prevails automatically\.)", page, re.DOTALL)
            if m_unranked:
                ct.unranked_rate_notices = True
                ct.precedence_quote = m_unranked.group(0).strip()
                ct.precedence_doc = doc_id
                ct.precedence_page = p_idx

            # Annex A: Express rates
            if "Annex A: Express rates" in page:
                ContractParser._parse_annex_a(ct, doc_id, p_idx, page)

            # Annex C: Fuel surcharge
            if "Annex C: Fuel surcharge" in page:
                ContractParser._parse_annex_c(ct, doc_id, p_idx, page)

            # Annex D: Accessorials
            if "Annex D: Accessorials" in page:
                ContractParser._parse_annex_d(ct, doc_id, p_idx, page)

            # Annex E: Charge codes
            if "Annex E: Charge codes" in page:
                ContractParser._parse_annex_e(ct, doc_id, p_idx, page)

            # Annex F: Exchange rates
            if "Annex F: Exchange rates" in page:
                ContractParser._parse_annex_f(ct, doc_id, p_idx, page)

            # Schedule 1: Authorised Persons
            if "Schedule 1: Authorised Persons" in page or "Schedule 1 Authorised Persons" in page:
                ContractParser._parse_schedule_1(ct, doc_id, p_idx, page)

    @staticmethod
    def _parse_annex_a(ct: ContractTerms, doc_id: str, p_idx: int, page: str) -> None:
        lines = page.split("\n")
        # Periods: Period P1: pickups 2030-04-02 to 2030-10-01
        for line in lines:
            m_p = re.search(
                r"Period\s+(P\d+):\s*pickups\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}[-\s]+[A-Za-z]{3,}[-\s]+\d{4})\s*(?:to|through|-)\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}[-\s]+[A-Za-z]{3,}[-\s]+\d{4})",
                line,
                re.IGNORECASE,
            )
            if m_p:
                d1 = parse_date(m_p.group(2))
                d2 = parse_date(m_p.group(3))
                if d1 and d2:
                    ct.rate_periods[m_p.group(1)] = (d1, d2)

        # Lane Weight band P1 rate [P2 rate] [P3 rate]
        # e.g.: USC1-INW1 minimum charge 96.00 96.00
        # e.g.: USC1-INW1 0-44.5 kg 9.68 10.32
        for line in lines:
            line_str = line.strip()
            # Match lane code (e.g. USE2-INE2, USC1-INW1)
            m_row = re.match(
                r"^([A-Z0-9]+-[A-Z0-9]+)\s+(minimum charge|\d+(?:\.\d+)?-\d+(?:\.\d+)?\s*kg|\d+\+\s*kg)\s+([\d\.]+)(?:\s+([\d\.]+))?(?:\s+([\d\.]+))?$",
                line_str,
            )
            if m_row:
                lane = m_row.group(1)
                band_str = m_row.group(2)
                p1_rate = m_row.group(3)
                p2_rate = m_row.group(4)
                p3_rate = m_row.group(5)
                min_w, max_w, is_min = parse_weight_band(band_str)
                rates_dict = {"P1": Decimal(p1_rate)}
                if p2_rate:
                    rates_dict["P2"] = Decimal(p2_rate)
                if p3_rate:
                    rates_dict["P3"] = Decimal(p3_rate)
                rb = RateBand(
                    lane=lane,
                    weight_band=band_str,
                    min_weight=min_w,
                    max_weight=max_w,
                    is_minimum_charge=is_min,
                    rates_by_period=rates_dict,
                    raw_quote=line_str,
                    doc_id=doc_id,
                    page=p_idx,
                )
                ct.rates.append(rb)

    @staticmethod
    def _parse_annex_c(ct: ContractTerms, doc_id: str, p_idx: int, page: str) -> None:
        # e.g.: 2030-04 23.54%
        for line in page.split("\n"):
            line_str = line.strip()
            m = re.match(r"^(\d{4}-\d{2})\s+(\d+(?:\.\d+)?)\s*%$", line_str)
            if m:
                month, pct = m.groups()
                ct.fuel_by_month[month] = Decimal(pct)
                ct.fuel_quotes[month] = (doc_id, p_idx, line_str)

    @staticmethod
    def _parse_annex_d(ct: ContractTerms, doc_id: str, p_idx: int, page: str) -> None:
        # e.g.: DG Dangerous goods fee 155.65 USD per shipment
        # e.g.: SEC Security surcharge 0.16 USD per chargeable kg
        for line in page.split("\n"):
            line_str = line.strip()
            m = re.match(r"^([A-Z0-9]+)\s+(.+?)\s+([\d\.]+)\s+(USD|INR)\s+(per\s+.+)$", line_str)
            if m:
                code, name, fee, curr, basis = m.groups()
                ar = AccessorialRate(
                    code=code,
                    name=name,
                    fee=Decimal(fee),
                    basis=basis,
                    currency=curr,
                    raw_quote=line_str,
                    doc_id=doc_id,
                    page=p_idx,
                )
                ct.accessorials[code] = ar

    @staticmethod
    def _parse_annex_e(ct: ContractTerms, doc_id: str, p_idx: int, page: str) -> None:
        # e.g.: Code SEC: Security surcharge; also billed as: Screening fee, Screening fee (adj.), Security screening
        for line in page.split("\n"):
            line_str = line.strip()
            m = re.search(r"Code\s+([A-Z0-9]+):\s*([^;]+);\s*also billed as:\s*(.+)", line_str)
            if m:
                code = m.group(1)
                primary = m.group(2).strip()
                aliases = [a.strip() for a in m.group(3).split(",")]
                ct.charge_code_aliases[primary.lower()] = code
                for a in aliases:
                    ct.charge_code_aliases[a.lower()] = code
                ct.annex_e_quotes[code] = (doc_id, p_idx, line_str)

    @staticmethod
    def _parse_annex_f(ct: ContractTerms, doc_id: str, p_idx: int, page: str) -> None:
        # e.g.: 2030-01 89.63
        for line in page.split("\n"):
            line_str = line.strip()
            m = re.match(r"^(\d{4}-\d{2})\s+([\d\.]+)$", line_str)
            if m:
                month, fx = m.groups()
                ct.fx_rates_by_month[month] = Decimal(fx)
                ct.fx_quotes[month] = (doc_id, p_idx, line_str)

    @staticmethod
    def _parse_schedule_1(ct: ContractTerms, doc_id: str, p_idx: int, page: str) -> None:
        # e.g.: Joaquin Rathbone, Key Account Manager, TBX-US: notices, billing_disputes <joaquin.rathbone@tarrowby.example>
        for line in page.split("\n"):
            line_str = line.strip()
            m = re.search(r"^([A-Z][A-Za-z\s\-\']+?),\s*([^:]+?),\s*([A-Z0-9\-]+):\s*([a-z_,\s]+)\s*<([\w\.\+\-]+@[\w\.\-]+)>", line_str)
            if m:
                name = m.group(1).strip()
                title = m.group(2).strip()
                party = m.group(3).strip()
                cats = [c.strip() for c in m.group(4).split(",")]
                email = m.group(5).strip()
                ap = AuthorisedPerson(
                    name=name,
                    title=title,
                    party=party,
                    categories=cats,
                    email=email,
                    raw_quote=line_str,
                    doc_id=doc_id,
                    page=p_idx,
                )
                ct.authorised_persons.append(ap)

    @staticmethod
    def _parse_amendment(ct: ContractTerms, doc_id: str, pages: list[str]) -> None:
        full_text = "\n\n".join(pages)
        p1 = pages[0]
        # Parent contract ID
        m_parent = re.search(r"Master\s+Services\s+Agreement\s+\(Express\s+Air\)\s+([A-Z0-9\-]+)", p1)
        if m_parent:
            ct.parent_contract_id = m_parent.group(1).strip()
        else:
            m_parent2 = re.search(r"Amendment\s+reference\s+([A-Z0-9\-]+)/AMD", p1)
            if m_parent2:
                ct.parent_contract_id = m_parent2.group(1).strip()

        # Effective from
        m_eff = re.search(r"Effective from\s+(\d{4}-\d{2}-\d{2})", p1)
        if m_eff:
            ct.amendment_effective_date = m_eff.group(1)

        # Check for retroactive clause
        if "retroactive" in full_text.lower():
            ct.amendment_retroactive = True

        for p_idx, page in enumerate(pages, 1):
            lines = page.split("\n")
            # Parse replacement rates:
            # Lane Weight band Rate Currency
            # USC3-USS2 minimum charge 82.82 USD
            # USC3-USS2 0-44.5 kg 3.43 USD
            for line in lines:
                line_str = line.strip()
                m_row = re.match(r"^([A-Z0-9]+-[A-Z0-9]+)\s+(minimum charge|\d+(?:\.\d+)?-\d+(?:\.\d+)?\s*kg|\d+\+\s*kg)\s+([\d\.]+)\s+(USD|INR)$", line_str)
                if m_row:
                    lane, band_str, rate, curr = m_row.groups()
                    min_w, max_w, is_min = parse_weight_band(band_str)
                    rb = RateBand(
                        lane=lane,
                        weight_band=band_str,
                        min_weight=min_w,
                        max_weight=max_w,
                        is_minimum_charge=is_min,
                        rates_by_period={"ALL": Decimal(rate)},
                        raw_quote=line_str,
                        doc_id=doc_id,
                        page=p_idx,
                    )
                    ct.rates.append(rb)

            # Check for referenced missing documents
            # e.g.: 2. The Annex A rates for lane(s) USS2-INN1 are deleted and replaced as follows: Annex A rev 2 (attached) applies from 2035-06-04 to lane(s) USS2-INN1.
            m_miss = re.search(
                r"replaced as follows:\s*([^(\n]+?)\s*\(attached\)\s*applies from\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}[-\s]+[A-Za-z]{3,}[-\s]+\d{4})\s*to lane\(s\)\s*([A-Z0-9\-,\s]+)",
                page,
                re.IGNORECASE,
            )
            if m_miss:
                doc_name = m_miss.group(1).strip()
                eff_date = parse_date(m_miss.group(2).strip()) or m_miss.group(2).strip()
                lanes = [l.strip() for l in m_miss.group(3).split(",")]
                ct.referenced_missing_docs.append({
                    "name": doc_name,
                    "effective_date": eff_date,
                    "lanes": lanes,
                    "quote": m_miss.group(0),
                    "doc_id": doc_id,
                    "page": p_idx,
                })
                for l in lanes:
                    ct.missing_annexes.append({
                        "name": doc_name,
                        "lane": l,
                        "effective_from": eff_date,
                        "quote": m_miss.group(0),
                        "doc_id": doc_id,
                        "page": p_idx,
                    })
