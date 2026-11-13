"""Domain models and data structures for the freight audit harness."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AuthorisedPerson:
    name: str
    title: str = ""
    party: str = ""
    categories: list[str] = field(default_factory=list)
    email: str = ""
    raw_quote: str = ""
    doc_id: str = ""
    page: int = 1


@dataclass
class RateBand:
    lane: str
    weight_band: str
    min_weight: Decimal = Decimal("0")
    max_weight: Decimal = Decimal("999999")
    is_minimum_charge: bool = False
    rates_by_period: dict[str, Decimal] = field(default_factory=dict)
    raw_quote: str = ""
    doc_id: str = ""
    page: int = 1


@dataclass
class AccessorialRate:
    code: str
    name: str
    fee: Decimal
    basis: str  # e.g. "per shipment", "per storage day", "per chargeable kg"
    currency: str = "USD"
    raw_quote: str = ""
    doc_id: str = ""
    page: int = 1


@dataclass
class CashbackTier:
    min_spend: Decimal
    rate_pct: Decimal  # e.g. 5.0 for 5%


@dataclass
class ContractTerms:
    contract_id: str
    doc_id: str
    page: int = 1
    shipper: str = ""
    carrier: str = ""
    contract_date: str = ""
    initial_term_months: Optional[int] = None
    end_date: Optional[str] = None
    auto_renew: bool = False
    notice_days: int = 60
    notice_deadline: Optional[str] = None
    currency: str = "USD"
    # Precedence & unranked instruments
    unranked_rate_notices: bool = True
    precedence_quote: str = ""
    precedence_doc: str = ""
    precedence_page: int = 1
    # Clauses
    pricing_date_clause_quote: str = ""
    pricing_date_clause_doc: str = ""
    pricing_date_clause_page: int = 1
    rate_notices_clause_quote: str = ""
    rate_notices_clause_doc: str = ""
    rate_notices_clause_page: int = 1
    waiver_clause_quote: str = ""
    waiver_clause_doc: str = ""
    waiver_clause_page: int = 1
    late_fee_clause_quote: str = ""
    late_fee_clause_doc: str = ""
    late_fee_clause_page: int = 1
    late_fee_clause_10_2_quote: str = ""
    late_fee_clause_10_2_doc: str = ""
    late_fee_clause_10_2_page: int = 1
    rebate_clause_quote: str = ""
    rebate_clause_doc: str = ""
    rebate_clause_page: int = 1
    qualifying_spend_quote: str = ""
    qualifying_spend_doc: str = ""
    qualifying_spend_page: int = 1
    dry_ice_clause_quote: str = ""
    dry_ice_clause_doc: str = ""
    dry_ice_clause_page: int = 1
    expiry_clause_quote: str = ""
    expiry_clause_doc: str = ""
    expiry_clause_page: int = 1
    # Tables
    rate_periods: dict[str, tuple[str, str]] = field(default_factory=dict)  # P1 -> (start, end)
    rates: list[RateBand] = field(default_factory=list)
    fuel_by_month: dict[str, Decimal] = field(default_factory=dict)  # '2030-04' -> Decimal('23.54')
    fuel_quotes: dict[str, tuple[str, int, str]] = field(default_factory=dict)  # month -> (doc, page, quote)
    accessorials: dict[str, AccessorialRate] = field(default_factory=dict)  # code -> AccessorialRate
    charge_code_aliases: dict[str, str] = field(default_factory=dict)  # alias.lower() -> code (from Annex E)
    annex_e_quotes: dict[str, tuple[str, int, str]] = field(default_factory=dict)  # code -> (doc, page, quote)
    fx_rates_by_month: dict[str, Decimal] = field(default_factory=dict)  # '2030-01' -> Decimal('89.63')
    fx_quotes: dict[str, tuple[str, int, str]] = field(default_factory=dict)  # month -> (doc, page, quote)
    # Late fee terms
    late_fee_pct_monthly: Decimal = Decimal("1.86")
    late_fee_grace_days: int = 5
    late_fee_compounds: bool = False
    # Cashback terms
    rebate_tiers: list[CashbackTier] = field(default_factory=list)
    margin_step_down_spend: Optional[Decimal] = None
    margin_step_down_pct: Optional[Decimal] = None
    annual_improvement_credit: Optional[Decimal] = None
    # Authorized contacts
    authorised_persons: list[AuthorisedPerson] = field(default_factory=list)
    # Amendment specifics
    is_amendment: bool = False
    parent_contract_id: Optional[str] = None
    amendment_no: Optional[str] = None
    amendment_effective_date: Optional[str] = None
    amendment_retroactive: bool = False
    referenced_missing_docs: list[dict] = field(default_factory=list)
    missing_annexes: list[dict] = field(default_factory=list)


@dataclass
class InvoiceLine:
    line_no: int
    awb: str
    pickup_date: Optional[str]
    description: str
    charge_code: Optional[str]  # e.g. "FRT", "FUEL", "SEC", "DLV", "LF", "STOR"
    lane: Optional[str]
    weight_kg: Optional[Decimal]
    qty_basis: str
    rate: Optional[Decimal]
    amount: Decimal
    raw_quote: str = ""
    page: int = 1


@dataclass
class Invoice:
    invoice_id: str
    doc_id: str
    page: int = 1
    email_received_date: str = ""
    email_doc_id: str = ""
    invoice_date: str = ""
    due_date: str = ""
    currency: str = "USD"
    party_carrier: str = ""
    party_shipper: str = ""
    contract_ref: str = ""
    lines: list[InvoiceLine] = field(default_factory=list)
    total_amount: Decimal = Decimal("0")
    total_usd: Decimal = Decimal("0")
    is_credit_note: bool = False
    credit_for_invoice_id: Optional[str] = None
    credit_for_awb: Optional[str] = None
    credit_for_period: Optional[str] = None
    credit_for_type: Optional[str] = None  # rebate, margin, etc.
    is_scan: bool = False
    scan_raw_text: str = ""
    all_raw_quotes: list[str] = field(default_factory=list)


@dataclass
class TrackingRecord:
    awb: str
    event: str  # PICKED_UP, ARRIVED_FACILITY, DELIVERED, etc.
    event_date: str
    location: str = ""
    details: str = ""
    doc_id: str = ""
    page: int = 1
    raw_quote: str = ""


@dataclass
class APEntry:
    invoice_id: str
    invoice_date: str
    due_date: str
    amount: Decimal
    currency: str = "USD"
    status: str = "open"  # paid, scheduled, open
    paid_date: Optional[str] = None
    doc_id: str = ""
    page: int = 1
    raw_quote: str = ""


@dataclass
class EmailWaiver:
    sender_name: str
    sender_email: str
    doc_id: str
    page: int = 1
    sent_date: str = ""
    target_awb: Optional[str] = None
    target_charge_type: Optional[str] = None  # e.g. "Delivery fee", "Security surcharge"
    target_month: Optional[str] = None
    scope: str = "single_shipment"  # single_shipment, month, standing
    condition: Optional[str] = None
    is_quoted_forward: bool = False
    quote: str = ""


@dataclass
class RateNotice:
    sender_name: str
    sender_email: str
    doc_id: str
    page: int = 1
    sent_date: str = ""
    contract_id: Optional[str] = None
    lane: Optional[str] = None
    weight_band: Optional[str] = None
    rate: Optional[Decimal] = None
    currency: str = "USD"
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    quote: str = ""
    is_quoted_forward: bool = False


@dataclass
class DueDateExtension:
    sender_name: str
    sender_email: str
    doc_id: str
    page: int = 1
    sent_date: str = ""
    invoice_id: str = ""
    extended_due_date: str = ""
    quote: str = ""


@dataclass
class DangerousGoodsDeclaration:
    awb: str
    refrigerant_only: bool = False
    quote: str = ""
    doc_id: str = ""
    page: int = 1

