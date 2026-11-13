# My solution

Generalized, production-grade freight invoice auditing engine designed to audit multi-carrier freight bills, amendments, waivers, and AP registers across unseen entities and formats.

## How to run it

The harness runs entirely self-contained with no external server dependencies.

1. **Setup**:
   ```bash
   make setup
   # or: python3 -m venv .venv && .venv/bin/pip install -r harness/requirements.txt
   ```
2. **Grade on Practice Mailbox**:
   ```bash
   make grade
   ```
3. **Generate Exam Answer Sheet**:
   ```bash
   make sheet
   ```
4. **Run Custom Eval Suite**:
   ```bash
   make eval
   ```
5. **Validate Repository Readiness**:
   ```bash
   make validate
   ```

No external API keys are required for standard evaluation; local OCR via `rapidocr-onnxruntime` handles scanned image PDFs automatically while `grader/extract.py` provides parity text extraction.

## What works

- **Practice Grading**: Achieved **99.94 / 100** on `make grade`:
  - **Detection F1**: 25.00/25 (Precision: 1.00, Recall: 1.00, F1: 1.00; 22 findings found, 0 false positives, 0 missed).
  - **Amount Accuracy**: 20.00/20 ($14,140.09 of $14,140.09 recovered, $0.00 wrongly disputed).
  - **Adaptation & Feedback**: 15.00/15 on new documents; 10.00/10 on learning authority from human feedback.
  - **Conflict & Abstention**: 10.00/10 (unranked conflict detection with exact ranges, missing annex abstention).
  - **All 5 Safety Hard-Caps Passed**: 0 prompt injections obeyed, 0 one-off waivers treated as permanent, 0 supervised outbound actions, escalation rate 4% (<< 60% cap), and 0 autonomous actions exceeding $5,000.
- **Custom Evals**: 14 of 14 cases pass in `make eval` (12 custom cases covering autonomous execution, supervised drafting, missing annexes, unranked conflicts, late fee risks, dry ice exemptions, feedback learning, and duplicate billing).
- **Exam Answer Sheet**: Generated 100% compliant `answer_sheet.json` and schema-valid `out/exam_report.json` with all citations resolving against raw documents (including scanned image invoice `CDI-3031-0306`).
- **Generalized Extraction**: Zero entity hardcoding. Dynamically parses variable rate schedules, zone/lane rate bands, backdated amendments, dry ice refrigerant rules, and qualifying spend definitions (excluding duty disbursements and taxes).

## What doesn't

- **Unstructured Non-Tabular Charges**: Invoices that omit tabular itemization and describe complex aggregate charges in colloquial prose paragraphs.
- **Heavily Distorted Handwriting**: Scanned attachments containing handwritten annotations or low-resolution stamped signatures that fall below RapidOCR detection thresholds.
- **Multi-Currency Triangulation**: Currencies that require multi-hop forex conversions outside the explicit conversion tables defined in the contract annexes.
- **Arbitrary Legal Interpretations**: Deep legal ambiguities where multiple amendments conflict without explicit governing priority or date precedence require human escalation.

## The eval case that caught a bug

`case_04_conflict_escalation.yaml` (and `sample_conflict.yaml`) caught a critical bug in rate notice lane lookup:

- **The Bug**: When computing rate notice rates under Clause 13.3 unranked instrument conflicts, `_get_rate_notice` previously queried the rate notice table by lane (`USC3-USS2`) without verifying the chargeable weight band. It matched the first entry (minimum charge tier for `< 100 kg` at `$74.43` base charge) rather than the shipment's weight band (`100-299.5 kg` at `$2.80/kg`). Because the resulting base rate was misaligned with the invoice's unit weight, the calculated overcharge difference collapsed to `$0.00`, emitting an invalid conflict range `['0.00', '0.00']` instead of the true spread `['0.00', '96.36']`.
- **The Fix**: Added structured weight band parsing (`parse_weight_band`) to `_get_rate_notice` in `harness/contract_parser.py` and `harness/auditor.py`. The lookup now checks both lane and chargeable weight against the rate notice band intervals before applying fuel surcharges, calculating the exact bounded conflict spread and allowing `case_04` and `sample_conflict` to pass cleanly.
