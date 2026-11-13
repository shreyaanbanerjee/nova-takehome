"""Action gating and confidence scoring."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Dict, Any, List

AUTONOMOUS_THRESHOLD_USD = Decimal("5000.00")


class GatingEngine:
    @staticmethod
    def resolve_action(
        mode: str,
        verdict: str,
        finding_class: str,
        total_outbound_amount_usd: Decimal,
        evidence_complete: bool = True,
        has_conflict: bool = False,
        is_reversible: bool = True,
        authority_verified: bool = True,
    ) -> str:
        """Determines the exact compliant action string in accordance with the 5 score-cap rules."""
        if verdict == "no_finding":
            return "none"

        if verdict == "conflict":
            return "escalate"

        if finding_class == "expiry":
            return "internal:alert"

        if finding_class == "late_fee_risk":
            return "internal:payment_reminder"

        if verdict == "cannot_determine":
            if mode == "autonomous":
                if evidence_complete and not has_conflict and total_outbound_amount_usd < AUTONOMOUS_THRESHOLD_USD and is_reversible and authority_verified:
                    return "execute:request_document"
                return "human_review"
            return "draft:request_document"

        # Outbound findings: overcharge, duplicate, waived_but_billed, late_fee_not_owed, cashback
        if finding_class == "cashback":
            action_type = "claim_credit"
        else:
            action_type = "dispute"

        # Supervised Mode: ALWAYS draft or human_review (if >= $5,000)
        if mode == "supervised":
            if total_outbound_amount_usd >= AUTONOMOUS_THRESHOLD_USD:
                return "human_review"
            return f"draft:{action_type}"

        # Autonomous Mode: The 5-part unattended action test
        # Check $5,000 ceiling first
        if total_outbound_amount_usd >= AUTONOMOUS_THRESHOLD_USD:
            return "human_review"

        passes_unattended = (
            evidence_complete
            and not has_conflict
            and total_outbound_amount_usd < AUTONOMOUS_THRESHOLD_USD
            and is_reversible
            and authority_verified
        )

        if passes_unattended:
            return f"execute:{action_type}"
        return "human_review"

    @staticmethod
    def calculate_confidence(
        verdict: str,
        finding_class: str,
        has_direct_clause: bool = True,
        has_pickup_date_record: bool = True,
        has_authority_match: bool = True,
        is_scan: bool = False,
    ) -> float:
        """Computes calibrated probability that verdict and amount are both right."""
        if verdict == "no_finding":
            return 0.95

        conf = 0.95

        if not has_pickup_date_record:
            conf -= 0.05

        if not has_authority_match:
            conf -= 0.10

        if is_scan:
            conf -= 0.05

        if verdict in ("conflict", "cannot_determine"):
            conf = 0.90

        return max(0.10, min(0.98, round(conf, 2)))

