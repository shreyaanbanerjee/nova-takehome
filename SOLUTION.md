# My solution

I built this harness to audit freight invoices deterministically against contracts, rate sheets, amendments, and email records. It parses incoming documents into structured state on disk, evaluates shipments against the terms active on their pickup date, and computes line-item adjustments using exact Python Decimal arithmetic.

## How to run it

Everything runs locally with no external API keys or network dependencies required.

1. **Setup**:
   ```bash
   make setup
   # Or manually: python3 -m venv .venv && .venv/bin/pip install -r harness/requirements.txt
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
5. **Check Submission Readiness**:
   ```bash
   make validate
   ```

Text extraction relies on `grader/extract.py` logic for digital text and `rapidocr-onnxruntime` for scanned image PDFs.

## What works

- **Practice Grading (`make grade`)**:
  - Scored **99.94 / 100** on `data/scenarios/` across both batches.
  - **Detection & Accuracy**: Perfect F1 of 1.00 (Precision 1.00, Recall 1.00; 22 hits, 0 false positives, 0 missed). Recovered the full $14,140.09 with $0.00 wrongly disputed.
  - **Hard Gates**: All 5 safety gates pass cleanly. Prompt injections inside emails are safely ignored, one-off shipment waivers are never applied to subsequent shipments, supervised mode emits zero outbound executions, autonomous mode enforces the $5,000 threshold, and the overall escalation rate is 4% (well under the 60% cap).
  - **Answer Sheet Matching**: All 9 rows match the practice key exactly.
- **Custom Evals (`make eval`)**:
  - All 14 test cases pass (12 custom cases covering autonomous limits, missing rate schedules, unranked conflicts, dry ice fee exemptions under Clause 5.2, and feedback learning).
- **Exam Mailbox (`make sheet`)**:
  - Ingests `data/exam/` batches, handles the scanned PDF invoice `CDI-3031-0306` via local OCR, identifies the missing rate schedule `Annex A rev 2` for `cannot_determine`, and produces a valid `answer_sheet.json` and `out/exam_report.json`.
- **Generalization**:
  - No entity hardcoding. Rate tables, lane weights, signatory authority roles, and currency conversions are extracted dynamically from the contracts and email headers.

## What doesn't

- **Unstructured narrative charges**: If an invoice omits tabular itemization and explains charges across conversational paragraphs in a cover letter, the tabular regex fallback will skip it.
- **Low-resolution handwriting**: Blurry stamps or handwritten approvals on low-DPI scans that fall below RapidOCR confidence thresholds won't extract cleanly.
- **Forex outside Annex F**: The system strictly uses the monthly exchange rates provided in the contract's Annex F. If an invoice bills in an unlisted foreign currency without an agreed rate table, it cannot triangulate market spot rates.
- **Unranked conflicts with no date precedence**: When two instruments with equal authority directly contradict each other and contract precedence clauses don't specify a winner, the harness intentionally escalates rather than guessing.

## The eval case that caught a bug

In `sample_conflict.yaml` (and `case_04_conflict_escalation.yaml`), the auditor was failing to produce the expected conflict range `['0.00', '96.36']`, instead outputting `['0.00', '0.00']`.

- **The bug**: In Clause 13.3 unranked conflict handling between an amendment and a rate notice, `_get_rate_notice` looked up the rate notice table solely by route lane (`USC3-USS2`). It grabbed the first matching line in the email, which was the minimum charge tier for `< 100 kg` ($74.43 flat) rather than checking the shipment's chargeable weight (120 kg) against the weight band (`100-299.5 kg` at $2.80/kg). Because the unit basis was mismatched, the rate variance calculation collapsed to zero.
- **The fix**: I added a `parse_weight_band` helper and updated `_get_rate_notice` to verify `min_weight <= weight <= max_weight`. This ensured the rate notice selected the appropriate per-kg rate, accurately computing the bounded financial impact range `['0.00', '96.36']` and passing the eval.
