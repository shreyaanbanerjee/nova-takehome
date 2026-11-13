"""Generalized freight invoice auditing harness.
Implements ingest, report, and feedback commands according to GLOSSARY.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Ensure harness directory is in python sys.path for isolated execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.extract import pages_of, parse_eml, attachments, email_lines
from harness.contract_parser import ContractParser
from harness.invoice_parser import InvoiceParser
from harness.tracking_parser import TrackingParser
from harness.email_parser import EmailParser
from harness.feedback_manager import FeedbackManager
from harness.auditor import Auditor
from harness.state import StateManager

DEFAULT_STATE = ".state"


def ingest(batch_dir: str, state_dir: str) -> None:
    sm = StateManager(state_dir)
    bpath = Path(batch_dir)
    batch_name = bpath.name

    docs_cache = sm.load_documents()
    contracts = sm.load_contracts()
    invoices = sm.load_invoices()
    tracking = sm.load_tracking()
    ap_ledger = sm.load_ap_ledger()
    emails = sm.load_emails()
    dgd_list = sm.load_dgd()

    eml_files = sorted(bpath.glob("*.eml"))

    for eml_file in eml_files:
        eml_bytes = eml_file.read_bytes()
        eml_doc_id = f"{batch_name}/{eml_file.name}"

        # 1. Read email
        eml_msg = parse_eml(eml_bytes)
        eml_text_lines = email_lines(eml_msg)
        eml_full_text = "\n".join(eml_text_lines)
        docs_cache[eml_doc_id] = {"pages": [eml_full_text], "is_scan": False}

        parsed_eml = EmailParser.parse_email(eml_doc_id, eml_full_text)
        emails.append(parsed_eml)
        email_date = parsed_eml.get("date", "")

        # 2. Read attachments
        att_list = attachments(eml_bytes)
        for att_name, att_bytes in att_list:
            if not att_name:
                continue
            att_doc_id = f"{eml_doc_id}#{att_name}"
            ext = att_name.rsplit(".", 1)[-1].lower()

            try:
                pages, is_scan = pages_of(att_bytes, ext)
            except Exception:
                continue

            docs_cache[att_doc_id] = {"pages": pages, "is_scan": is_scan}
            # Also register bare name if not ambiguous
            if att_name not in docs_cache:
                docs_cache[att_name] = {"pages": pages, "is_scan": is_scan}

            # Parse Contract / Amendment
            if ContractParser.is_contract_or_amendment(att_doc_id, pages):
                ct = ContractParser.parse(att_doc_id, pages)
                contracts.append(asdict(ct))

            # Parse Tracking Export
            elif "tracking" in att_name.lower():
                tr_recs = TrackingParser.parse_tracking_sheet(att_doc_id, pages)
                for tr in tr_recs:
                    tracking.append(asdict(tr))

            # Parse AP Register
            elif "ap-register" in att_name.lower() or "register" in att_name.lower():
                ap_entries = TrackingParser.parse_ap_register(att_doc_id, pages)
                for ap in ap_entries:
                    ap_ledger.append(asdict(ap))

            # Parse AWB PDF
            elif att_name.startswith("AWB-") or "waybill" in att_name.lower():
                awb_info = TrackingParser.parse_awb_pdf(att_doc_id, pages)
                # If master AWB has an execution date, record as tracking
                if awb_info.get("awb"):
                    # Can be referenced for pieces / splitting
                    pass

            # Parse DGD PDF
            elif "dgd" in att_name.lower() or "declaration" in att_name.lower():
                dgd_info = TrackingParser.parse_dgd_pdf(att_doc_id, pages)
                if dgd_info:
                    dgd_list.append(dgd_info)

            # Parse Invoice / Credit Note / Statement
            elif InvoiceParser.is_invoice_or_credit_note(att_doc_id, pages):
                inv = InvoiceParser.parse(
                    att_doc_id,
                    pages,
                    is_scan=is_scan,
                    email_date=email_date,
                    email_doc_id=eml_doc_id,
                )
                if inv:
                    invoices.append(asdict(inv))

    sm.save_documents(docs_cache)
    sm.save_contracts(contracts)
    sm.save_invoices(invoices)
    sm.save_tracking(tracking)
    sm.save_ap_ledger(ap_ledger)
    sm.save_emails(emails)
    sm.save_dgd(dgd_list)


def feedback(feedback_file: str, state_dir: str) -> None:
    fm = FeedbackManager(state_dir)
    fm.process_file(feedback_file)


def report(as_of: str, mode: str, state_dir: str) -> dict:
    sm = StateManager(state_dir)
    docs_cache = sm.load_documents()
    contracts = sm.load_contracts()
    invoices = sm.load_invoices()
    tracking = sm.load_tracking()
    ap_ledger = sm.load_ap_ledger()
    emails = sm.load_emails()
    dgd_list = sm.load_dgd()
    feedback_state = sm.load_feedback_state()
    prev_reports = sm.load_report_history()

    auditor = Auditor(
        contracts=contracts,
        invoices=invoices,
        tracking=tracking,
        ap_ledger=ap_ledger,
        emails=emails,
        dgd_list=dgd_list,
        feedback_state=feedback_state,
        docs_cache=docs_cache,
        as_of=as_of,
        mode=mode,
        prev_reports=prev_reports,
    )

    result = auditor.audit()

    # Save to report history for future supersedes tracking
    prev_reports[as_of] = result
    sm.save_report_history(prev_reports)

    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="harness.py", description="Freight invoice audit harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="read one batch folder into the state dir")
    p.add_argument("batch_dir")
    p.add_argument("--state-dir", default=DEFAULT_STATE)
    p = sub.add_parser("report", help="print report.json for a date")
    p.add_argument("--as-of", required=True, help="YYYY-MM-DD; only documents received by then count")
    p.add_argument("--mode", choices=["supervised", "autonomous"], default="supervised")
    p.add_argument("--state-dir", default=DEFAULT_STATE)
    p = sub.add_parser("feedback", help="take in a person's decisions (feedback.json)")
    p.add_argument("feedback_file")
    p.add_argument("--state-dir", default=DEFAULT_STATE)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "ingest":
            ingest(a.batch_dir, a.state_dir)
        elif a.cmd == "report":
            json.dump(report(a.as_of, a.mode, a.state_dir), sys.stdout, indent=1)
            sys.stdout.write("\n")
        else:
            feedback(a.feedback_file, a.state_dir)
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
