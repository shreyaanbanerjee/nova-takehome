"""State persistence management for multi-batch mailbox ingestion."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if is_dataclass(obj):
            return asdict(obj)
        return super().default(obj)


class StateManager:
    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _file(self, name: str) -> Path:
        return self.state_dir / name

    def save_json(self, name: str, data: Any) -> None:
        p = self._file(name)
        p.write_text(json.dumps(data, cls=DecimalEncoder, indent=2))

    def load_json(self, name: str, default: Any = None) -> Any:
        p = self._file(name)
        if not p.is_file():
            return default
        try:
            return json.loads(p.read_text())
        except Exception:
            return default

    def save_documents(self, docs_cache: dict[str, dict]) -> None:
        self.save_json("documents.json", docs_cache)

    def load_documents(self) -> dict[str, dict]:
        return self.load_json("documents.json", {})

    def save_contracts(self, contracts: list[dict]) -> None:
        self.save_json("contracts.json", contracts)

    def load_contracts(self) -> list[dict]:
        return self.load_json("contracts.json", [])

    def save_invoices(self, invoices: list[dict]) -> None:
        self.save_json("invoices.json", invoices)

    def load_invoices(self) -> list[dict]:
        return self.load_json("invoices.json", [])

    def save_tracking(self, tracking: list[dict]) -> None:
        self.save_json("tracking.json", tracking)

    def load_tracking(self) -> list[dict]:
        return self.load_json("tracking.json", [])

    def save_ap_ledger(self, ap_entries: list[dict]) -> None:
        self.save_json("ap_ledger.json", ap_entries)

    def load_ap_ledger(self) -> list[dict]:
        return self.load_json("ap_ledger.json", [])

    def save_emails(self, emails: list[dict]) -> None:
        self.save_json("emails.json", emails)

    def load_emails(self) -> list[dict]:
        return self.load_json("emails.json", [])

    def save_dgd(self, dgd_list: list[dict]) -> None:
        self.save_json("dgd.json", dgd_list)

    def load_dgd(self) -> list[dict]:
        return self.load_json("dgd.json", [])

    def save_feedback_state(self, fb: dict) -> None:
        self.save_json("feedback_state.json", fb)

    def load_feedback_state(self) -> dict:
        return self.load_json("feedback_state.json", {"items": [], "standing": [], "one_off": []})

    def save_report_history(self, history: dict) -> None:
        self.save_json("report_history.json", history)

    def load_report_history(self) -> dict:
        return self.load_json("report_history.json", {})
