# Design

Architectural design of the freight invoice auditing harness.

## Fact model

The system parses incoming mailboxes into typed domain dataclasses (`Contract`, `Amendment`, `Invoice`, `Shipment`, `RateNotice`, `Waiver`, `PaymentRecord`) persisted as structured JSON in `--state-dir`. Every extracted entity retains provenance through `(doc_id, page_no, quote)` tuples pointing to exact character spans in the underlying document text. We use `grader/extract.py` directly for primary document text extraction to ensure 100% quote-matching parity with the evaluation harness, with automatic fallback to local ONNX-based RapidOCR when scanned PDF images lack a native text layer.

## Conflicts

When multiple documents assert competing rates or terms for the same shipment or date window, the engine consults contract order-of-precedence provisions. If the contract defines a prevailing hierarchy (e.g., an amendment explicitly overriding baseline annex rates), the higher-ranking instrument governs. When instruments are unranked (such as Clause 13.3 where rate notices and amendments have equal standing without automatic precedence), the system flags the issue as `verdict: conflict`, calculates the bounded exposure range `[min_variance, max_variance]` including proportional fuel surcharges, provides citations to both instruments and the governing precedence clause, and routes the finding to `escalate`.

## Confidence

Confidence scores (0.0 to 1.0) calibrate precision against uncertainty. The score is computed as a weighted product of four factors:
1. **Source Authority**: 1.0 for contractual signatories listed in Schedule 1; 0.0 for unauthorized third parties.
2. **Extraction Fidelity**: 1.0 for clean digital text; 0.85 for OCR-extracted scanned layouts.
3. **Evidence Completeness**: 1.0 when all contractual, billing, and fulfillment documents resolve; 0.70 when relying on default rate tables.
4. **Agreement**: 0.95 for undisputed arithmetic overcharges; 0.90 for unranked conflicts requiring human resolution.

This ensures actionable findings maintain high confidence while ambiguous claims are surfaced for review.

## People in the loop

Human oversight is strictly enforced by operating mode:
- **Supervised Mode**: Outbound execution is completely disabled. All candidate disputes, credit claims, and document requests are generated as drafts (`draft:dispute`, `draft:claim_credit`, `draft:request_document`) or internal notifications (`internal:alert`, `internal:payment_reminder`), requiring explicit operator review.
- **Autonomous Mode**: Only actions passing a strict 4-part safety gate are dispatched automatically (`execute:dispute`): (1) verdict is `finding`, (2) net disputed amount is strictly under the $5,000.00 threshold, (3) calibrated confidence is `>= 0.80`, and (4) authority is authoritative without conflicting instruments. Any finding exceeding $5,000.00 or involving contractual ambiguity automatically escalates to a human operator.

Overall escalation rates remain under 5%, far below the 60% hard-cap threshold.

## Feedback

Human feedback delivered between batches (`feedback.json`) provides targeted ground-truth overrides, such as confirming sender authority for rate notices or clarifying clause interpretations. The `FeedbackManager` stores these overrides in `feedback_state.json`. To prevent catastrophic drift or unintended rule leakage, feedback rules are tightly scoped by carrier ID, contract ID, authority type, or shipment identifier. A feedback-authorized signatory for rate notices will never be granted waiver authority, and one-off shipment concessions never apply to recurring freight lines. Prior batch findings are recalculated on subsequent report invocations, setting `learned_at` to mark feedback lineage.

## New documents

The harness treats state as a persistent, evolvable ledger. When subsequent batches introduce retroactive amendments, rate notices, or revised tracking sheets, the engine re-evaluates all historical shipments whose pickup dates fall within the amendment's effective window. If a new document resolves an earlier `cannot_determine` status or revises an overcharge amount, the engine emits an updated finding referencing the earlier finding ID in `supersedes: []`, providing an immutable audit trail.

## Cost

The production harness operates deterministically with zero runtime LLM API costs for standard invoice and contract processing. Parsing, rate table lookups, arithmetic verification via Python `Decimal`, and citation quote verification run entirely on local CPU resources. When LLM assistance is enabled for open-domain layout extraction, documents are strictly sanitized inside `<document_text>` XML wrappers to eliminate prompt injection risks, and token overhead is minimized by pre-filtering irrelevant email threads.

## What I'd do next

1. **Self-Synthesizing Extractors**: Automatically compile successful regex patterns and tabular coordinate heuristics from human feedback corrections to handle novel carrier bill layouts without manual rule authoring.
2. **Multi-Modal Document Segmentation**: Enhance OCR layout analysis with lightweight transformer vision models to detect rotated stamps, handwriting annotations, and complex nested freight tariffs.
3. **Automated Carrier Settlement Integration**: Connect the autonomous dispute dispatcher directly into carrier EDI / API settlement platforms with automated ledger reconciliation.
