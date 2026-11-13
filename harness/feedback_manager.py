"""Feedback processing and override management."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.invoice_parser import norm_invoice
from harness.state import StateManager


class FeedbackManager:
    def __init__(self, state_dir: str):
        self.sm = StateManager(state_dir)
        self.state = self.sm.load_feedback_state()

    def process_file(self, feedback_file_path: str) -> None:
        p = Path(feedback_file_path)
        if not p.is_file():
            return
        data = json.loads(p.read_text())
        batch = data.get("after_batch", "")
        items = data.get("items", [])

        existing_items = self.state.setdefault("items", [])
        existing_ids = {it["item_id"] for it in existing_items if "item_id" in it}

        for it in items:
            it_id = it.get("item_id")
            if it_id in existing_ids:
                continue
            it["batch"] = batch
            existing_items.append(it)

            decision = it.get("decision")
            fkey = it.get("finding_key", {})
            cls = fkey.get("class", "")
            inv_id = norm_invoice(fkey.get("invoice_id", ""))
            fact = it.get("fact", {})
            fact_type = fact.get("type", "")
            scope = it.get("scope", {})
            scope_kind = scope.get("kind", "one_off")

            # Store in structured indexes
            if decision == "close":
                closed = self.state.setdefault("closed_keys", [])
                closed.append({"class": cls, "invoice_id": inv_id, "feedback_item_id": it_id, "batch": batch})

            if decision == "reject":
                rejected = self.state.setdefault("rejected_keys", [])
                rejected.append({"class": cls, "invoice_id": inv_id, "feedback_item_id": it_id, "batch": batch})

            if decision == "override_amount":
                val = fact.get("value", {})
                amt = val.get("amount_usd")
                if amt:
                    overrides = self.state.setdefault("overridden_amounts", {})
                    overrides[f"{cls}:{inv_id}"] = {
                        "amount_usd": amt,
                        "feedback_item_id": it_id,
                        "batch": batch,
                    }

            if decision == "one_off_approval":
                approvals = self.state.setdefault("one_off_waivers", {})
                approvals[f"{cls}:{inv_id}"] = {
                    "approved": True,
                    "months": fact.get("value", {}).get("months", []),
                    "feedback_item_id": it_id,
                    "batch": batch,
                }

            if decision == "resolve_conflict" or fact_type == "precedence":
                precs = self.state.setdefault("standing_precedences", [])
                precs.append({
                    "contract_id": fact.get("subject", {}).get("contract_id"),
                    "lane": fact.get("subject", {}).get("lane"),
                    "value": fact.get("value"),
                    "feedback_item_id": it_id,
                    "batch": batch,
                    "valid_from": scope.get("valid_from"),
                    "valid_to": scope.get("valid_to"),
                })

            if fact_type == "sender_authority":
                auths = self.state.setdefault("standing_authorities", [])
                auths.append({
                    "contract_id": scope.get("applies_to", {}).get("contract_id"),
                    "person": fact.get("subject", {}).get("person") or scope.get("applies_to", {}).get("person"),
                    "party": fact.get("subject", {}).get("party"),
                    "categories": fact.get("value", {}).get("categories", []),
                    "valid_from": scope.get("valid_from"),
                    "valid_to": scope.get("valid_to"),
                    "feedback_item_id": it_id,
                    "batch": batch,
                })

            if fact_type == "rate_accepted":
                rates = self.state.setdefault("standing_rates", [])
                rates.append({
                    "contract_id": fact.get("subject", {}).get("contract_id") or scope.get("applies_to", {}).get("contract_id"),
                    "lane": fact.get("subject", {}).get("lane") or scope.get("applies_to", {}).get("lane"),
                    "value": fact.get("value"),
                    "valid_from": scope.get("valid_from"),
                    "valid_to": scope.get("valid_to"),
                    "feedback_item_id": it_id,
                    "batch": batch,
                })

        self.sm.save_feedback_state(self.state)

