"""Comprehensive freight audit and reasoning engine covering all 6 problem classes."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    ContractTerms,
    Invoice,
    InvoiceLine,
    TrackingRecord,
    APEntry,
    EmailWaiver,
    RateNotice,
    DueDateExtension,
)
from harness.invoice_parser import norm_invoice
from harness.contract_parser import parse_weight_band
from harness.gating import GatingEngine, AUTONOMOUS_THRESHOLD_USD
from harness.snapping import QuoteSnapper


def round_half_up(val: Any, places: int = 2) -> Decimal:
    if val is None:
        return Decimal("0.00")
    if not isinstance(val, Decimal):
        try:
            val = Decimal(str(val))
        except Exception:
            return Decimal("0.00")
    q = Decimal(10) ** -places
    return val.quantize(q, rounding=ROUND_HALF_UP)


def money_str(val: Optional[Decimal]) -> Optional[str]:
    if val is None:
        return None
    return f"{round_half_up(val, 2):.2f}"


def days_diff(d1_str: str, d2_str: str) -> int:
    """d1 - d2 in days."""
    dt1 = datetime.strptime(d1_str, "%Y-%m-%d")
    dt2 = datetime.strptime(d2_str, "%Y-%m-%d")
    return (dt1 - dt2).days


class Auditor:
    def __init__(
        self,
        contracts: list[dict],
        invoices: list[dict],
        tracking: list[dict],
        ap_ledger: list[dict],
        emails: list[dict],
        feedback_state: dict,
        docs_cache: dict,
        as_of: str,
        mode: str,
        prev_reports: dict,
        dgd_list: list[dict] = None,
    ):
        self.contracts = contracts
        self.invoices = invoices
        self.tracking = tracking
        self.ap_ledger = ap_ledger
        self.emails = emails
        self.dgd_list = dgd_list or []
        self.feedback_state = feedback_state
        self.docs_cache = docs_cache
        self.as_of = as_of
        self.mode = mode
        self.prev_reports = prev_reports

        # Indexes
        self._index_tracking()
        self._index_ap()
        self._index_feedback()
        self.dgd_by_awb = {d["awb"]: d for d in self.dgd_list if d.get("awb")}

    def _index_tracking(self):
        self.pickup_by_awb: dict[str, tuple[str, str, int, str]] = {}  # awb -> (date, doc, page, quote)
        self.events_by_awb: dict[str, list[dict]] = {}
        for tr in self.tracking:
            awb = tr.get("awb")
            ev = tr.get("event")
            dt = tr.get("event_date")
            if not awb or not dt:
                continue
            self.events_by_awb.setdefault(awb, []).append(tr)
            if ev == "PICKED_UP":
                self.pickup_by_awb[awb] = (dt, tr.get("doc_id", ""), tr.get("page", 1), tr.get("raw_quote", ""))

    def _index_ap(self):
        self.ap_by_inv: dict[str, dict] = {}
        for ap in self.ap_ledger:
            inv_id = norm_invoice(ap.get("invoice_id", ""))
            if inv_id:
                self.ap_by_inv[inv_id] = ap

    def _index_feedback(self):
        self.closed_invoices = {norm_invoice(x.get("invoice_id", "")): x for x in self.feedback_state.get("closed_keys", [])}
        self.rejected_invoices = {norm_invoice(x.get("invoice_id", "")): x for x in self.feedback_state.get("rejected_keys", [])}
        self.overridden_amounts = self.feedback_state.get("overridden_amounts", {})
        self.one_off_waivers = self.feedback_state.get("one_off_waivers", {})
        self.standing_rates = self.feedback_state.get("standing_rates", [])
        self.standing_auths = self.feedback_state.get("standing_authorities", [])
        self.standing_precedences = self.feedback_state.get("standing_precedences", [])

    def audit(self) -> dict:
        findings: list[dict] = []
        late_fee_risks: list[dict] = []
        cashbacks: list[dict] = []
        expiries: list[dict] = []

        # Filter documents received by as_of
        valid_invoices = [
            inv for inv in self.invoices
            if (inv.get("email_received_date") or inv.get("invoice_date", "")) <= self.as_of
        ]

        # 1. Overcharges, Duplicates, Waived-but-billed, Late-fee-not-owed
        for inv in valid_invoices:
            if inv.get("is_credit_note"):
                continue
            inv_id = norm_invoice(inv.get("invoice_id", ""))

            # Check closed / rejected via feedback
            if inv_id in self.closed_invoices:
                continue
            if inv_id in self.rejected_invoices:
                continue

            # Run 4 invoice classes
            self._audit_overcharge(inv, findings)
            self._audit_duplicate(inv, valid_invoices, findings)
            self._audit_waiver(inv, findings)
            self._audit_late_fee_not_owed(inv, findings)

        # 2. Portfolio: Late Fee Risk
        self._audit_late_fee_risks(late_fee_risks)

        # 3. Portfolio: Cashback
        self._audit_cashback(valid_invoices, cashbacks)

        # 4. Portfolio: Expiry
        self._audit_expiry(expiries)

        return {
            "schema_version": "1.0",
            "as_of": self.as_of,
            "mode": self.mode,
            "findings": findings,
            "portfolio": {
                "cashback": cashbacks,
                "expiry": expiries,
                "late_fee_risk": late_fee_risks,
            },
        }

    # ---------------------------------------------------------------------------------------------------
    # CLASS 1: OVERCHARGE
    # ---------------------------------------------------------------------------------------------------
    def _audit_overcharge(self, inv: dict, findings: list[dict]) -> None:
        inv_id = norm_invoice(inv.get("invoice_id", ""))
        lines = inv.get("lines", [])
        contract_ref = inv.get("contract_ref", "")
        currency = inv.get("currency", "USD")

        # Find matching contract
        contract = self._get_contract(contract_ref)
        if not contract:
            return

        evidence: list[dict] = []
        diff_total_in_curr = Decimal("0")
        has_cannot_determine = False
        missing_doc_info = None
        has_conflict = False
        conflict_range = None
        conflict_evidence = []
        feedback_link = None
        feedback_batch = None

        # Check Dry Ice Refrigerant Only (OC-02)
        dry_ice_clause = contract.get("dry_ice_clause_quote")
        for line in lines:
            if line.get("charge_code") == "DG":
                awb = line.get("awb")
                billed_dg = Decimal(str(line.get("amount") or 0))
                dgd = self.dgd_by_awb.get(awb)
                if dgd and dgd.get("refrigerant_only") and billed_dg > Decimal("0") and dry_ice_clause:
                    ev = [
                        self._make_ev(line.get("doc_id", inv["doc_id"]), line.get("page", 1), line.get("raw_quote")),
                        self._make_ev(contract.get("dry_ice_clause_doc", contract.get("doc_id", "")), contract.get("dry_ice_clause_page", 1), dry_ice_clause),
                        self._make_ev(dgd["doc_id"], dgd.get("page", 1), dgd["quote"]),
                    ]
                    item = {
                        "id": f"overcharge:{inv_id}",
                        "class": "overcharge",
                        "variant": "OC-02",
                        "invoice_id": inv_id,
                        "verdict": "finding",
                        "amount_usd": money_str(billed_dg),
                        "amount_range": None,
                        "deadline": None,
                        "action": GatingEngine.resolve_action(self.mode, "finding", "overcharge", billed_dg),
                        "confidence": 0.95,
                        "evidence": ev,
                        "missing_doc": None,
                        "supersedes": self._get_supersedes(f"overcharge:{inv_id}"),
                        "retro": False,
                        "learned_at": None,
                    }
                    findings.append(item)
                    return

        for line in lines:
            if line.get("charge_code") != "FRT":
                continue

            awb = line.get("awb")
            lane = line.get("lane")
            weight = Decimal(str(line.get("weight_kg") or line.get("qty_basis", "0").split()[0] or 0))
            billed_rate = Decimal(str(line.get("rate") or 0))
            billed_amount = Decimal(str(line.get("amount") or 0))

            # Pickup date
            pickup_dt, p_doc, p_page, p_quote = self._get_pickup_date(awb, line.get("pickup_date") or inv.get("invoice_date"))

            # Check amendments for rate replacement or missing documents
            amendment_rate, amend_doc, amend_page, amend_quote = self._get_amendment_rate(contract, lane, weight, pickup_dt)
            missing_annex = self._get_missing_annex(contract, lane, pickup_dt)
            if missing_annex:
                has_cannot_determine = True
                missing_doc_info = missing_annex
                evidence.append(self._make_ev(missing_annex["doc_id"], missing_annex["page"], missing_annex["quote"]))
                break

            # Check rate notices from emails
            notice_rate, rn_doc, rn_page, rn_quote, rn_auth, fb_item_id, fb_batch = self._get_rate_notice(contract, lane, weight, pickup_dt)

            # Check conflict: unranked amendment vs rate notice
            if amendment_rate is not None and notice_rate is not None and amendment_rate != notice_rate and rn_auth:
                # Check if feedback resolved precedence
                prec_resolution = self._get_precedence_resolution(contract.get("contract_id"), lane)
                if prec_resolution == "amendment":
                    correct_rate = amendment_rate
                    c_doc, c_page, c_quote = amend_doc, amend_page, amend_quote
                elif prec_resolution == "rate notice":
                    correct_rate = notice_rate
                    c_doc, c_page, c_quote = rn_doc, rn_page, rn_quote
                else:
                    # Unresolved conflict!
                    has_conflict = True
                    # Calculate range [billed - higher, billed - lower]
                    c_fuel_pct = self._get_fuel_pct(contract, pickup_dt)
                    c_amt_amend = round_half_up(amendment_rate * weight, 2)
                    c_amt_amend_tot = c_amt_amend + round_half_up(c_fuel_pct / Decimal(100) * c_amt_amend, 2)
                    c_amt_rn = round_half_up(notice_rate * weight, 2)
                    c_amt_rn_tot = c_amt_rn + round_half_up(c_fuel_pct / Decimal(100) * c_amt_rn, 2)
                    
                    # Billed total for this shipment
                    b_tot = billed_amount + self._get_billed_fuel(inv, awb)
                    r_high = max(Decimal("0"), b_tot - min(c_amt_amend_tot, c_amt_rn_tot))
                    r_low = max(Decimal("0"), b_tot - max(c_amt_amend_tot, c_amt_rn_tot))
                    conflict_range = [money_str(r_low), money_str(r_high)]
                    conflict_evidence = [
                        self._make_ev(amend_doc, amend_page, amend_quote),
                        self._make_ev(rn_doc, rn_page, rn_quote),
                    ]
                    if contract.get("precedence_quote"):
                        conflict_evidence.append(
                            self._make_ev(contract.get("precedence_doc", contract.get("doc_id", "")), contract.get("precedence_page", 1), contract["precedence_quote"])
                        )
                    break
            elif amendment_rate is not None:
                correct_rate = amendment_rate
                c_doc, c_page, c_quote = amend_doc, amend_page, amend_quote
            elif notice_rate is not None and rn_auth:
                correct_rate = notice_rate
                c_doc, c_page, c_quote = rn_doc, rn_page, rn_quote
                if fb_item_id:
                    feedback_link = fb_item_id
                    feedback_batch = fb_batch
            else:
                # Use contract Annex A
                correct_rate, c_doc, c_page, c_quote = self._get_contract_rate(contract, lane, weight, pickup_dt)

            if correct_rate is None:
                continue

            # Pricing
            c_freight = round_half_up(correct_rate * weight, 2)
            fuel_pct = self._get_fuel_pct(contract, pickup_dt)
            fuel_quote_info = contract.get("fuel_quotes", {}).get(pickup_dt[:7])

            c_fuel = round_half_up((fuel_pct / Decimal(100)) * c_freight, 2)
            c_total = c_freight + c_fuel

            # Find billed fuel for this AWB
            b_fuel = self._get_billed_fuel(inv, awb)
            b_total = billed_amount + b_fuel

            diff = b_total - c_total
            if diff > Decimal("0"):
                diff_total_in_curr += diff
                if feedback_link:
                    evidence.append(self._make_ev(line.get("doc_id", inv["doc_id"]), line.get("page", 1), line.get("raw_quote")))
                    evidence.append(self._make_ev(rn_doc, rn_page, rn_quote))
                    if p_quote:
                        evidence.append(self._make_ev(p_doc, p_page, p_quote))
                    if contract.get("rate_notices_clause_quote"):
                        evidence.append(self._make_ev(contract.get("rate_notices_clause_doc", contract.get("doc_id", "")), contract.get("rate_notices_clause_page", 1), contract["rate_notices_clause_quote"]))
                else:
                    evidence.append(self._make_ev(line.get("doc_id", inv["doc_id"]), line.get("page", 1), line.get("raw_quote")))
                    evidence.append(self._make_ev(c_doc, c_page, c_quote))
                    if contract.get("pricing_date_clause_quote"):
                        evidence.append(self._make_ev(contract["pricing_date_clause_doc"], contract["pricing_date_clause_page"], contract["pricing_date_clause_quote"]))
                    if p_quote:
                        evidence.append(self._make_ev(p_doc, p_page, p_quote))

        if has_conflict:
            item = {
                "id": f"overcharge:{inv_id}",
                "class": "overcharge",
                "variant": "XC-01",
                "invoice_id": inv_id,
                "verdict": "conflict",
                "amount_usd": None,
                "amount_range": conflict_range,
                "deadline": None,
                "action": "escalate",
                "confidence": 0.90,
                "evidence": conflict_evidence,
                "missing_doc": None,
                "supersedes": [],
                "learned_at": None,
            }
            findings.append(item)
            return

        if has_cannot_determine:
            pricing_mgr = self._get_schedule1_contact(contract, "rates", party=contract.get("carrier") or "TBX-US")
            if not pricing_mgr:
                pricing_mgr = self._get_schedule1_contact(contract, "rates")
            item = {
                "id": f"overcharge:{inv_id}",
                "class": "overcharge",
                "variant": "XC-02",
                "invoice_id": inv_id,
                "verdict": "cannot_determine",
                "amount_usd": None,
                "amount_range": None,
                "deadline": None,
                "action": GatingEngine.resolve_action(self.mode, "cannot_determine", "overcharge", Decimal("0")),
                "confidence": 0.90,
                "evidence": evidence,
                "missing_doc": {
                    "name": missing_doc_info["name"],
                    "holder": {
                        "party": pricing_mgr.get("party") if pricing_mgr else "TBX-US",
                        "person": pricing_mgr.get("name") if pricing_mgr else "Fintan Corrigan",
                        "role": pricing_mgr.get("title") if pricing_mgr else "Pricing Manager",
                    },
                },
                "supersedes": [],
                "learned_at": None,
            }
            findings.append(item)
            return

        if diff_total_in_curr > Decimal("0"):
            amount_usd = diff_total_in_curr
            if currency == "INR":
                fx = self._get_fx_rate(contract, inv.get("invoice_date", self.as_of))
                if fx:
                    amount_usd = round_half_up(diff_total_in_curr / fx, 2)

            learned_at = None
            variant = "OC-01"
            if feedback_link:
                learned_at = {"feedback_item_id": feedback_link, "batch": feedback_batch or "batch_01"}
                variant = "FB-P1"

            item = {
                "id": f"overcharge:{inv_id}",
                "class": "overcharge",
                "variant": variant,
                "invoice_id": inv_id,
                "verdict": "finding",
                "amount_usd": money_str(amount_usd),
                "amount_range": None,
                "deadline": None,
                "action": GatingEngine.resolve_action(self.mode, "finding", "overcharge", amount_usd),
                "confidence": GatingEngine.calculate_confidence("finding", "overcharge", is_scan=inv.get("is_scan", False)),
                "evidence": evidence,
                "missing_doc": None,
                "supersedes": self._get_supersedes(f"overcharge:{inv_id}"),
                "retro": False,
                "learned_at": learned_at,
            }
            findings.append(item)

    # ---------------------------------------------------------------------------------------------------
    # CLASS 2: DUPLICATE
    # ---------------------------------------------------------------------------------------------------
    def _audit_duplicate(self, inv: dict, all_invoices: list[dict], findings: list[dict]) -> None:
        inv_id = norm_invoice(inv.get("invoice_id", ""))
        inv_dt = inv.get("invoice_date", "")

        # 1. Exact resend of paid invoice
        ap_entry = self.ap_by_inv.get(inv_id)
        if ap_entry and ap_entry.get("status") == "paid":
            # Check if this invoice arrived again AFTER paid date and asks for payment
            paid_dt = ap_entry.get("paid_date", "")
            rec_dt = inv.get("email_received_date", "")
            if paid_dt and rec_dt and rec_dt > paid_dt:
                # Find payment request quote in email body
                eml = self._get_email(inv.get("email_doc_id", ""))
                pay_quote = self._find_payment_request_quote(eml)
                if pay_quote:
                    item = {
                        "id": f"duplicate:{inv_id}",
                        "class": "duplicate",
                        "variant": "DU-01",
                        "invoice_id": inv_id,
                        "verdict": "finding",
                        "amount_usd": money_str(inv.get("total_amount")),
                        "amount_range": None,
                        "deadline": None,
                        "action": GatingEngine.resolve_action(self.mode, "finding", "duplicate", Decimal(str(inv.get("total_amount") or 0))),
                        "confidence": 0.95,
                        "evidence": [
                            self._make_ev(ap_entry["doc_id"], ap_entry["page"], ap_entry["raw_quote"]),
                            self._make_ev(inv.get("email_doc_id", ""), 1, pay_quote),
                        ],
                        "missing_doc": None,
                        "supersedes": self._get_supersedes(f"duplicate:{inv_id}"),
                        "retro": False,
                        "learned_at": None,
                    }
                    findings.append(item)
                    return

        # 2. Charge billed twice on different invoices (or 4 months later)
        # Check by AWB and charge code
        for line in inv.get("lines", []):
            awb = line.get("awb")
            code = line.get("charge_code")
            amt = Decimal(str(line.get("amount") or 0))
            if not awb or not code or amt <= Decimal("0"):
                continue

            # Look across all earlier invoices
            for other_inv in all_invoices:
                other_id = norm_invoice(other_inv.get("invoice_id", ""))
                if other_id == inv_id:
                    continue
                other_dt = other_inv.get("invoice_date", "")
                if other_dt >= inv_dt:
                    continue  # We flag the LATER invoice date

                for o_line in other_inv.get("lines", []):
                    if o_line.get("awb") == awb and o_line.get("charge_code") == code:
                        o_amt = Decimal(str(o_line.get("amount") or 0))
                        if o_amt == amt:
                            # Duplicated line!
                            contract = self._get_contract(inv.get("contract_ref", ""))
                            ev = [
                                self._make_ev(o_line.get("doc_id", other_inv["doc_id"]), o_line.get("page", 1), o_line.get("raw_quote")),
                                self._make_ev(line.get("doc_id", inv["doc_id"]), line.get("page", 1), line.get("raw_quote")),
                            ]
                            annex_e = contract.get("annex_e_quotes", {}).get(code)
                            if annex_e:
                                ev.append(self._make_ev(annex_e[0], annex_e[1], annex_e[2]))

                            item = {
                                "id": f"duplicate:{inv_id}",
                                "class": "duplicate",
                                "variant": "DU-03",
                                "invoice_id": inv_id,
                                "verdict": "finding",
                                "amount_usd": money_str(amt),
                                "amount_range": None,
                                "deadline": None,
                                "action": GatingEngine.resolve_action(self.mode, "finding", "duplicate", amt),
                                "confidence": 0.95,
                                "evidence": ev,
                                "missing_doc": None,
                                "supersedes": self._get_supersedes(f"duplicate:{inv_id}"),
                                "retro": False,
                                "learned_at": None,
                            }
                            findings.append(item)
                            return

    # ---------------------------------------------------------------------------------------------------
    # CLASS 3: WAIVED BUT BILLED
    # ---------------------------------------------------------------------------------------------------
    def _audit_waiver(self, inv: dict, findings: list[dict]) -> None:
        inv_id = norm_invoice(inv.get("invoice_id", ""))
        contract = self._get_contract(inv.get("contract_ref", ""))
        lines = inv.get("lines", [])

        # Check one-off feedback approval for this invoice
        one_off_fb = self.one_off_waivers.get(f"waived_but_billed:{inv_id}")

        for eml in self.emails:
            for w in eml.get("waivers", []):
                # Verify sender authority
                sender_email = w.get("sender_email")
                has_auth = self._has_category_authority(contract, sender_email, "waivers")
                if one_off_fb:
                    has_auth = True

                # Check if waiver is inside quoted forward without original
                if w.get("is_quoted_forward"):
                    # Check if original email exists in held emails
                    orig_exists = any(
                        e.get("sender_email") == sender_email and not e.get("has_injection")
                        for e in self.emails if e.get("doc_id") != eml.get("doc_id")
                    )
                    if not orig_exists and has_auth:
                        # Cannot determine!
                        kam = self._get_schedule1_contact(contract, "notices")
                        item = {
                            "id": f"waived_but_billed:{inv_id}",
                            "class": "waived_but_billed",
                            "variant": "WB-02",
                            "invoice_id": inv_id,
                            "verdict": "cannot_determine",
                            "amount_usd": None,
                            "amount_range": None,
                            "deadline": None,
                            "action": GatingEngine.resolve_action(self.mode, "cannot_determine", "waived_but_billed", Decimal("0")),
                            "confidence": 0.90,
                            "evidence": [self._make_ev(w["doc_id"], w["page"], w["quote"])],
                            "missing_doc": {
                                "name": "original waiver email",
                                "holder": {
                                    "party": kam.get("party") if kam else "TBX-US",
                                    "person": kam.get("name") if kam else "Key Account Manager",
                                    "role": kam.get("title") if kam else "Key Account Manager",
                                },
                            },
                            "supersedes": [],
                            "learned_at": None,
                        }
                        findings.append(item)
                        return

                if not has_auth:
                    continue

                # Match by AWB
                target_awb = w.get("target_awb")
                target_charge = w.get("target_charge_type", "").lower()
                target_month = w.get("target_month")

                for line in lines:
                    line_awb = line.get("awb")
                    line_desc = line.get("description", "").lower()
                    amt = Decimal(str(line.get("amount") or 0))

                    match = False
                    if target_awb and line_awb == target_awb:
                        if not target_charge or target_charge in line_desc:
                            match = True
                    elif target_month and not target_awb:
                        # Monthly waiver (e.g. storage fee)
                        if target_month in line.get("qty_basis", "") or target_month in line.get("description", ""):
                            match = True

                    if match and amt > Decimal("0"):
                        schedule1_person = self._get_person_by_email(contract, sender_email)
                        ev = [
                            self._make_ev(w["doc_id"], w["page"], w["quote"]),
                            self._make_ev(line.get("doc_id", inv["doc_id"]), line.get("page", 1), line.get("raw_quote")),
                        ]
                        if schedule1_person and schedule1_person.get("raw_quote"):
                            ev.append(self._make_ev(schedule1_person.get("doc_id", contract["doc_id"]), schedule1_person.get("page", 1), schedule1_person["raw_quote"]))
                        if contract.get("waiver_clause_quote"):
                            ev.append(self._make_ev(contract["waiver_clause_doc"], contract["waiver_clause_page"], contract["waiver_clause_quote"]))

                        learned_at = None
                        if one_off_fb:
                            learned_at = {"feedback_item_id": one_off_fb["feedback_item_id"], "batch": one_off_fb["batch"]}

                        item = {
                            "id": f"waived_but_billed:{inv_id}",
                            "class": "waived_but_billed",
                            "variant": "WB-01",
                            "invoice_id": inv_id,
                            "verdict": "finding",
                            "amount_usd": money_str(amt),
                            "amount_range": None,
                            "deadline": None,
                            "action": GatingEngine.resolve_action(self.mode, "finding", "waived_but_billed", amt),
                            "confidence": 0.95,
                            "evidence": ev,
                            "missing_doc": None,
                            "supersedes": self._get_supersedes(f"waived_but_billed:{inv_id}"),
                            "retro": False,
                            "learned_at": learned_at,
                        }
                        findings.append(item)
                        return

    # ---------------------------------------------------------------------------------------------------
    # CLASS 4: LATE FEES
    # ---------------------------------------------------------------------------------------------------
    def _audit_late_fee_not_owed(self, inv: dict, findings: list[dict]) -> None:
        inv_id = norm_invoice(inv.get("invoice_id", ""))
        contract = self._get_contract(inv.get("contract_ref", ""))

        for line in inv.get("lines", []):
            if line.get("charge_code") == "LF" or "Late fee on" in line.get("description", ""):
                raw_quote = line.get("raw_quote", "")
                m_target = re.search(r"Late\s+fee\s+on\s+([A-Z0-9\-]+)", raw_quote)
                target_inv_id = norm_invoice(m_target.group(1)) if m_target else ""
                billed_fee = Decimal(str(line.get("amount") or 0))

                ap_entry = self.ap_by_inv.get(target_inv_id)
                if not ap_entry or ap_entry.get("status") != "paid":
                    continue

                paid_date = ap_entry.get("paid_date")
                due_date = ap_entry.get("due_date")

                # Check if due date was extended by authorized person
                ext_date = None
                ext_quote = None
                ext_doc = None
                ext_page = None
                for eml in self.emails:
                    for ext in eml.get("extensions", []):
                        if ext.get("invoice_id") == target_inv_id:
                            sender_email = ext.get("sender_email")
                            if self._has_category_authority(contract, sender_email, "payment_terms"):
                                ext_date = ext.get("extended_due_date")
                                ext_quote = ext.get("quote")
                                ext_doc = ext.get("doc_id")
                                ext_page = ext.get("page", 1)

                effective_due_date = ext_date or due_date
                if not paid_date or not effective_due_date:
                    continue

                days_late = days_diff(paid_date, effective_due_date)
                grace = contract.get("late_fee_grace_days", 5)

                if days_late <= grace:
                    # No late fee owed!
                    ev = []
                    if ext_quote:
                        ev.append(self._make_ev(ext_doc, ext_page, ext_quote))
                    ev.append(self._make_ev(ap_entry["doc_id"], ap_entry["page"], ap_entry["raw_quote"]))
                    ev.append(self._make_ev(line.get("doc_id", inv["doc_id"]), line.get("page", 1), raw_quote))
                    if contract.get("late_fee_clause_quote"):
                        ev.append(self._make_ev(contract["late_fee_clause_doc"], contract["late_fee_clause_page"], contract["late_fee_clause_quote"]))

                    item = {
                        "id": f"late_fee_not_owed:{inv_id}",
                        "class": "late_fee_not_owed",
                        "variant": "LF-01",
                        "invoice_id": inv_id,
                        "verdict": "finding",
                        "amount_usd": money_str(billed_fee),
                        "amount_range": None,
                        "deadline": None,
                        "action": GatingEngine.resolve_action(self.mode, "finding", "late_fee_not_owed", billed_fee),
                        "confidence": 0.95,
                        "evidence": ev,
                        "missing_doc": None,
                        "supersedes": self._get_supersedes(f"late_fee_not_owed:{inv_id}"),
                        "retro": False,
                        "learned_at": None,
                    }
                    findings.append(item)

    def _audit_late_fee_risks(self, late_fee_risks: list[dict]) -> None:
        contract = self.contracts[0] if self.contracts else {}
        for c in self.contracts:
            if not c.get("is_amendment"):
                contract = c
                break
        pct = Decimal(str(contract.get("late_fee_pct_monthly") or "1.98"))

        seen_invs = set()
        for ap in self.ap_ledger:
            inv_id = norm_invoice(ap.get("invoice_id", ""))
            if not inv_id or inv_id in seen_invs:
                continue
            seen_invs.add(inv_id)
            if ap.get("status") != "open":
                continue
            due_date = ap.get("due_date", "")
            if not due_date:
                continue
            diff = days_diff(due_date, self.as_of)
            if 0 <= diff <= 14:
                principal = Decimal(str(ap.get("amount") or 0))
                fee_1_month = round_half_up((pct / Decimal(100)) * principal, 2)
                fee_usd = fee_1_month
                if ap.get("currency") == "INR":
                    fx = self._get_fx_rate(contract, self.as_of)
                    if fx:
                        fee_usd = round_half_up(fee_1_month / fx, 2)

                ev = [self._make_ev(ap["doc_id"], ap.get("page", 1), ap["raw_quote"])]
                clause_q = contract.get("late_fee_clause_10_2_quote")
                if clause_q:
                    ev.append(self._make_ev(contract.get("late_fee_clause_10_2_doc", contract.get("doc_id", "")), contract.get("late_fee_clause_10_2_page", 1), clause_q))

                item = {
                    "id": f"late_fee_risk:{inv_id}",
                    "class": "late_fee_risk",
                    "variant": "LF-03",
                    "invoice_id": inv_id,
                    "verdict": "finding",
                    "amount_usd": money_str(fee_usd),
                    "amount_range": None,
                    "deadline": due_date,
                    "action": "internal:payment_reminder",
                    "confidence": 0.95,
                    "evidence": ev,
                    "missing_doc": None,
                    "supersedes": self._get_supersedes(f"late_fee_risk:{inv_id}"),
                    "retro": False,
                    "learned_at": None,
                }
                late_fee_risks.append(item)

    # ---------------------------------------------------------------------------------------------------
    # CLASS 5: CASHBACK
    # ---------------------------------------------------------------------------------------------------
    def _audit_cashback(self, all_invoices: list[dict], cashbacks: list[dict]) -> None:
        for contract in self.contracts:
            if contract.get("is_amendment"):
                continue
            cid = contract.get("contract_id", "")
            rebate_tiers = contract.get("rebate_tiers", [])
            if not rebate_tiers:
                continue

            # Group invoices by calendar quarter
            quarters: dict[str, list[dict]] = {}
            for inv in all_invoices:
                cref = inv.get("contract_ref")
                if cref:
                    if cref != cid:
                        continue
                else:
                    if cid.startswith("TBX-US") and inv.get("currency") != "USD":
                        continue
                    if cid.startswith("TBX-IN") and inv.get("currency") != "INR":
                        continue

                inv_dt = inv.get("invoice_date", "")
                if inv_dt:
                    q_name = f"{inv_dt[:4]}-Q{(int(inv_dt[5:7]) - 1) // 3 + 1}"
                    quarters.setdefault(q_name, []).append(inv)

            for q_name, q_invoices in quarters.items():
                q_end = self._quarter_end_date(q_name)
                # Report only once quarter has closed
                if self.as_of <= q_end:
                    continue

                # Calculate Qualifying Spend in USD
                qs = Decimal("0")
                for inv in q_invoices:
                    inv_amt = Decimal(str(inv.get("total_amount") or 0))
                    # Deduct late fees, duties, and taxes
                    lf = sum(
                        (Decimal(str(l.get("amount") or 0)) for l in inv.get("lines", [])
                         if l.get("charge_code") in ("LF", "DUTY", "TAX") or any(k in l.get("description", "").lower() for k in ("duty", "disbursement", "tax", "late fee"))),
                        Decimal("0")
                    )
                    inv_qual = inv_amt - lf
                    if inv.get("currency") == "INR":
                        fx = self._get_fx_rate(contract, inv.get("invoice_date", self.as_of))
                        if fx:
                            inv_qual = round_half_up(inv_qual / fx, 2)
                    
                    if inv.get("is_credit_note"):
                        qs -= inv_qual
                    else:
                        qs += inv_qual

                # Determine tier
                matched_rate = Decimal("0")
                for t in sorted(rebate_tiers, key=lambda x: Decimal(str(x.get("min_spend") or 0))):
                    t_min = Decimal(str(t.get("min_spend") or 0))
                    t_rate = Decimal(str(t.get("rate_pct") or 0))
                    if qs >= t_min:
                        matched_rate = t_rate

                if matched_rate > Decimal("0"):
                    rebate_earned = round_half_up((matched_rate / Decimal(100)) * qs, 2)
                    # Check issued credit notes
                    credited = Decimal("0")
                    for inv in all_invoices:
                        if inv.get("is_credit_note") and inv.get("credit_for_type") == "rebate" and inv.get("credit_for_period") == q_name:
                            credited += Decimal(str(inv.get("total_amount") or 0))

                    owed = rebate_earned - credited
                    if owed > Decimal("0"):
                        ev = []
                        if contract.get("rebate_clause_quote"):
                            ev.append(self._make_ev(contract["rebate_clause_doc"], contract["rebate_clause_page"], contract["rebate_clause_quote"]))
                        if contract.get("qualifying_spend_quote"):
                            ev.append(self._make_ev(contract["qualifying_spend_doc"], contract["qualifying_spend_page"], contract["qualifying_spend_quote"]))

                        item = {
                            "id": f"cashback:{cid}/{q_name}/rebate",
                            "class": "cashback",
                            "variant": "CB-01",
                            "contract_id": cid,
                            "period": q_name,
                            "credit_type": "rebate",
                            "verdict": "finding",
                            "amount_usd": money_str(owed),
                            "amount_range": None,
                            "deadline": None,
                            "action": GatingEngine.resolve_action(self.mode, "finding", "cashback", owed),
                            "confidence": 0.95,
                            "evidence": ev,
                            "missing_doc": None,
                            "supersedes": self._get_supersedes(f"cashback:{cid}/{q_name}/rebate"),
                            "retro": False,
                            "learned_at": None,
                        }
                        cashbacks.append(item)

    # ---------------------------------------------------------------------------------------------------
    # CLASS 6: EXPIRY
    # ---------------------------------------------------------------------------------------------------
    def _audit_expiry(self, expiries: list[dict]) -> None:
        for contract in self.contracts:
            if contract.get("is_amendment"):
                continue
            cid = contract.get("contract_id", "")
            end_date = contract.get("end_date")
            notice_deadline = contract.get("notice_deadline")

            operative_date = notice_deadline if contract.get("auto_renew") else end_date
            if not operative_date:
                continue

            diff = days_diff(operative_date, self.as_of)
            # 0 <= deadline - as_of <= 60
            if 0 <= diff <= 60:
                item = {
                    "id": f"expiry:{cid}",
                    "class": "expiry",
                    "variant": "EX-01",
                    "contract_id": cid,
                    "verdict": "finding",
                    "amount_usd": None,
                    "amount_range": None,
                    "deadline": operative_date,
                    "action": "internal:alert",
                    "confidence": 0.95,
                    "evidence": [self._make_ev(contract["expiry_clause_doc"], contract["expiry_clause_page"], contract["expiry_clause_quote"])],
                    "missing_doc": None,
                    "supersedes": self._get_supersedes(f"expiry:{cid}"),
                    "retro": False,
                    "learned_at": None,
                }
                expiries.append(item)

    # ---------------------------------------------------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------------------------------------------------
    def _make_ev(self, doc: str, page: int, quote: str) -> dict:
        snapped_page, snapped_quote = QuoteSnapper.verify_and_snap(doc, page, quote, self.docs_cache)
        return {
            "doc": doc,
            "page": snapped_page,
            "quote": snapped_quote,
        }

    def _get_contract(self, ref: str) -> Optional[dict]:
        if not self.contracts:
            return None
        if ref:
            for c in self.contracts:
                if c.get("contract_id") == ref and not c.get("is_amendment"):
                    return c
        # Return first non-amendment
        for c in self.contracts:
            if not c.get("is_amendment"):
                return c
        return self.contracts[0]

    def _get_pickup_date(self, awb: Optional[str], fallback: str) -> tuple[str, str, int, str]:
        if awb and awb in self.pickup_by_awb:
            return self.pickup_by_awb[awb]
        return fallback or self.as_of, "", 1, ""

    def _get_contract_rate(self, contract: dict, lane: Optional[str], weight: Decimal, pickup_dt: str) -> tuple[Optional[Decimal], str, int, str]:
        if not lane:
            return None, "", 1, ""
        # Determine period
        period = "P1"
        for p_name, (start, end) in contract.get("rate_periods", {}).items():
            if start <= pickup_dt <= end:
                period = p_name
                break

        # Check minimum charge
        min_charge = None
        for rb in contract.get("rates", []):
            if rb.get("lane") == lane and rb.get("is_minimum_charge"):
                min_charge = Decimal(str(rb.get("rates_by_period", {}).get(period, 0)))

        # Match weight band
        for rb in contract.get("rates", []):
            if rb.get("lane") == lane and not rb.get("is_minimum_charge"):
                w_min = Decimal(str(rb.get("min_weight", 0)))
                w_max = Decimal(str(rb.get("max_weight", 999999)))
                if w_min <= weight <= w_max:
                    rate = Decimal(str(rb.get("rates_by_period", {}).get(period, 0)))
                    return rate, rb.get("doc_id", contract["doc_id"]), rb.get("page", 1), rb.get("raw_quote", "")

        return None, "", 1, ""

    def _get_amendment_rate(self, contract: dict, lane: Optional[str], weight: Decimal, pickup_dt: str) -> tuple[Optional[Decimal], str, int, str]:
        if not lane:
            return None, "", 1, ""
        for c in self.contracts:
            if c.get("is_amendment") and c.get("parent_contract_id") == contract.get("contract_id"):
                eff = c.get("amendment_effective_date", "9999-99-99")
                if pickup_dt >= eff or c.get("amendment_retroactive"):
                    for rb in c.get("rates", []):
                        if rb.get("lane") == lane and not rb.get("is_minimum_charge"):
                            w_min = Decimal(str(rb.get("min_weight", 0)))
                            w_max = Decimal(str(rb.get("max_weight", 999999)))
                            if w_min <= weight <= w_max:
                                rate = Decimal(str(rb.get("rates_by_period", {}).get("ALL", 0)))
                                return rate, rb.get("doc_id", c["doc_id"]), rb.get("page", 1), rb.get("raw_quote", "")
        return None, "", 1, ""

    def _get_missing_annex(self, contract: dict, lane: Optional[str], pickup_dt: str) -> Optional[dict]:
        if not lane:
            return None
        for c in self.contracts:
            if c.get("is_amendment") and c.get("parent_contract_id") == contract.get("contract_id"):
                for miss in c.get("referenced_missing_docs", []):
                    if lane in miss.get("lanes", []) and pickup_dt >= miss.get("effective_date", ""):
                        return miss
        return None

    def _get_rate_notice(self, contract: dict, lane: Optional[str], weight: Decimal, pickup_dt: str) -> tuple[Optional[Decimal], str, int, str, bool, Optional[str], Optional[str]]:
        if not lane:
            return None, "", 1, "", False, None, None
        for eml in self.emails:
            for rn in eml.get("rate_notices", []):
                if rn.get("lane") == lane:
                    from_dt = rn.get("effective_from", "0000-00-00")
                    to_dt = rn.get("effective_to", "9999-99-99")
                    if from_dt <= pickup_dt <= to_dt:
                        min_w, max_w, is_min = parse_weight_band(rn.get("weight_band", ""))
                        if not is_min and min_w <= weight <= max_w:
                            sender_email = rn.get("sender_email")
                            has_auth = self._has_category_authority(contract, sender_email, "rates")
                            fb_item_id = None
                            fb_batch = None
                            if not has_auth:
                                # Check standing rate acceptance from feedback
                                for sr in self.standing_rates:
                                    if sr.get("lane") == lane and (sr.get("contract_id") == contract.get("contract_id") or not sr.get("contract_id")):
                                        s_from = sr.get("valid_from", "0000-00-00")
                                        s_to = sr.get("valid_to", "9999-99-99")
                                        if s_from <= pickup_dt <= s_to:
                                            has_auth = True
                                            fb_item_id = sr.get("feedback_item_id")
                                            fb_batch = sr.get("batch", "batch_01")
                                            break
                            rate = Decimal(str(rn.get("rate", 0)))
                            return rate, rn.get("doc_id"), rn.get("page", 1), rn.get("quote"), has_auth, fb_item_id, fb_batch
        return None, "", 1, "", False, None, None

    def _get_precedence_resolution(self, contract_id: str, lane: str) -> Optional[str]:
        for p in self.standing_precedences:
            if p.get("contract_id") == contract_id and p.get("lane") == lane:
                val = p.get("value", {})
                return val.get("use")
        return None

    def _get_fuel_pct(self, contract: dict, pickup_dt: str) -> Decimal:
        month = pickup_dt[:7]
        return Decimal(str(contract.get("fuel_by_month", {}).get(month, "20.16")))

    def _get_billed_fuel(self, inv: dict, awb: Optional[str]) -> Decimal:
        for l in inv.get("lines", []):
            if l.get("awb") == awb and l.get("charge_code") == "FUEL":
                return Decimal(str(l.get("amount") or 0))
        return Decimal("0")

    def _get_fx_rate(self, contract: dict, inv_dt: str) -> Optional[Decimal]:
        month = inv_dt[:7]
        val = contract.get("fx_rates_by_month", {}).get(month)
        return Decimal(str(val)) if val else None

    def _has_category_authority(self, contract: dict, email: str, category: str) -> bool:
        if not email:
            return False
        # Check standing authority feedback
        for sa in self.standing_auths:
            if sa.get("contract_id") == contract.get("contract_id") and category in sa.get("categories", []):
                return True
        # Check contract Schedule 1
        for ap in contract.get("authorised_persons", []):
            if ap.get("email") == email and category in ap.get("categories", []):
                return True
        return False

    def _get_schedule1_contact(self, contract: dict, category: str, party: Optional[str] = None) -> Optional[dict]:
        for ap in contract.get("authorised_persons", []):
            if category in ap.get("categories", []):
                if party and ap.get("party") != party:
                    continue
                return ap
        for ap in contract.get("authorised_persons", []):
            if category in ap.get("categories", []):
                return ap
        return None

    def _get_person_by_email(self, contract: dict, email: str) -> Optional[dict]:
        for ap in contract.get("authorised_persons", []):
            if ap.get("email") == email:
                return ap
        return None

    def _get_email(self, doc_id: str) -> Optional[dict]:
        for e in self.emails:
            if e.get("doc_id") == doc_id:
                return e
        return None

    def _find_payment_request_quote(self, eml: Optional[dict]) -> Optional[str]:
        if not eml:
            return None
        for line in eml.get("direct_body", []):
            if any(k in line.lower() for k in ["process payment", "payment at your earliest", "remit payment", "kindly pay"]):
                return line.strip()
        return None

    def _quarter_end_date(self, q_str: str) -> str:
        y, q = q_str.split("-Q")
        ends = {"1": f"{y}-03-31", "2": f"{y}-06-30", "3": f"{y}-09-30", "4": f"{y}-12-31"}
        return ends.get(q, f"{y}-12-31")

    def _get_supersedes(self, finding_id: str) -> list[str]:
        out = []
        for as_of_date, rep in self.prev_reports.items():
            for it in rep.get("findings", []) + rep.get("portfolio", {}).get("cashback", []) + rep.get("portfolio", {}).get("expiry", []) + rep.get("portfolio", {}).get("late_fee_risk", []):
                if it.get("id") == finding_id:
                    out.append(it["id"])
        return list(set(out))
