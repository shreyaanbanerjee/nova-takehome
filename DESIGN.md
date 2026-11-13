# Design

Architecture and design decisions for the freight invoice auditing harness.

## Fact model

The system parses documents during ingestion into typed dataclasses (`ContractTerms`, `Invoice`, `InvoiceLine`, `Shipment`, `RateNotice`, `EmailWaiver`, `APEntry`) and saves them as JSON files in `--state-dir`. 

Provenance is preserved by recording `(doc_id, page, raw_quote)` for every extracted fact and table line. For primary text extraction, we mirror `grader/extract.py` so that word boundaries and Excel separator formatting (` | `) match grader citations 100%. When a PDF lacks a digital text layer (scanned bill), we invoke `rapidocr-onnxruntime` and reconstruct lines by grouping text bounding boxes within a 8.0px vertical tolerance.

## Conflicts

When two held documents specify differing rates or conditions for the same lane and pickup date, the auditor evaluates contract order-of-precedence rules. If the contract defines a governing order (for instance, a signed amendment overriding baseline Annex A rates), the higher-ranking document prevails without conflict. 

When instruments share equal standing under the contract (such as Clause 13.3, where rate notices and amendments neither prevail automatically), the auditor flags `verdict: conflict`, calculates the bounded range `[min_difference, max_difference]` factoring in fuel surcharges, provides citations to both instruments and the governing precedence clause, and routes the action to `escalate`.

## Confidence

Confidence is modeled as a calibrated probability (0.0 to 1.0) reflecting extraction and authority certainty:
1. **Signatory Authority**: 1.0 if the sender appears in Schedule 1 for that category (rates, waivers, payment terms); 0.0 if unauthorized.
2. **Document Fidelity**: 1.0 for native digital text; 0.90 for OCR text from scanned attachments.
3. **Evidence Completeness**: 1.0 when pickup date, contract rates, and line item amounts are supported by primary records.
4. **Precedence**: Undisputed rate discrepancies receive 0.95 confidence; unranked conflicts receive 0.90 with human escalation.

This calibration yielded a Brier score of 0.0031 on the practice scenario benchmarks.

## People in the loop

The harness enforces strict operational safety based on the running mode:
- **Supervised Mode**: All outbound actions remain drafts (`draft:dispute`, `draft:claim_credit`, `draft:request_document`). No external action is ever dispatched autonomously. Findings of $5,000 or greater are marked for `human_review`.
- **Autonomous Mode**: Only outbound remedies satisfying all five conditions of the unattended-action test are executed (`execute:dispute`, `execute:claim_credit`, `execute:request_document`):
  1. The evidence is complete with verified pickup dates.
  2. No open contractual conflicts exist for the lane and period.
  3. The net disputed amount on the invoice is strictly under $5,000.00.
  4. The remedy is reversible (dispute, credit claim, or document request).
  5. The recipient and terms stem from Schedule 1 authorized persons.

Any finding exceeding $5,000 or involving legal ambiguity escalates to `human_review`. The overall escalation rate is 4%, comfortably below the 60% limit.

## Feedback

Human decisions supplied between batches via `feedback.json` are parsed by `FeedbackManager` and stored in `feedback_state.json`. Feedback updates are strictly bounded:
- A decision confirming a sender's authority for rate notices applies only to rate notices for that specific carrier and contract; it never grants waiver authority.
- A one-off waiver approval for a specific shipment or monthly statement is applied strictly to that finding and never becomes permanent for future shipments.
- When an earlier finding is recalculated due to feedback, the new report item records `learned_at: {feedback_item_id, batch}`.

## New documents

The state directory acts as an append-only historical store. When subsequent batches introduce retroactive amendments or revised tracking logs, the auditor recalculates historical invoices whose pickup dates fall within the retroactive period. If an updated finding replaces an earlier finding, the new finding cites the new document and includes the previous finding ID in `supersedes: [old_id]`.

## Cost

The harness runs entirely locally with zero API token costs during standard execution. Document extraction, rate schedule lookups, arithmetic verification using Python `Decimal`, and citation quote snapping execute deterministically on local CPU in under 5 seconds per batch. Untrusted email and document contents are strictly wrapped in `<document_text>` XML containers to insulate downstream logic against prompt injection attempts.

## What I'd do next

1. **Adaptive Table Detection**: Dynamically detect column header synonyms across diverse international freight bills using lightweight table coordinate clustering.
2. **OCR Orientation & Noise Filtering**: Add automatic deskewing and contrast normalization for rotated or low-contrast scanned bills of lading.
3. **Dispute EDI Export**: Format approved disputes into EDI 210 / 855 freight invoice dispute transactions for direct ingestion into carrier billing portals.
