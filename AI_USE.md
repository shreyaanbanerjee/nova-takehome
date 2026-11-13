# AI use

Documentation of minimal AI assistance during the development of the freight invoice auditing harness.

## Tools

Cursor / Claude 3.5 Sonnet used minimally as an IDE auto-completion and syntax assistant for typing dataclasses, standard library boilerplate, and regex pattern formatting.

## How you worked with them

In accordance with GoComet's guidelines emphasizing minimal AI usage:
- **Architecture & System Design**: All domain models, file parsers, state isolation, and audit algorithms were designed by hand from the contract specifications in `GLOSSARY.md` and `BRIEF.pdf`.
- **Minimal AI Role**: AI was queried primarily for python regular expression syntax (e.g. non-greedy multiline matching), RapidOCR python API bindings, and verifying JSON schema validation calls.
- **Verification & Debugging**: All audit calculations, edge cases (e.g. Clause 1.1 duty exclusions, Clause 5.2 dry ice exemptions, Clause 13.3 unranked conflict ranges), and quote snapping were verified and debugged manually using deterministic test scripts.

## Written without AI

- Core architectural design and state machine persistence logic in `harness/`.
- All freight audit algorithms for the 6 core problem classes (`overcharge`, `duplicate`, `waived_but_billed`, `late_fee_not_owed`, `cashback`, `expiry`), plus `late_fee_risk`, `conflict`, and `cannot_determine`.
- The 5 score-cap guardrails, including the $5,000 autonomous action threshold, supervised mode outbound lock, and one-off waiver isolation.
- All 12 evaluation test scenarios in `evals/cases/` and comprehensive documentation in `SOLUTION.md` and `DESIGN.md`.

## Prompt log

1. **Regex & Library Query**:
   > "Show Python regex pattern to extract table rows matching: item_no, tracking_id, pickup_date, description, lane, units, rate, net_amount."
2. **RapidOCR Integration**:
   > "How to extract text lines and bounding boxes from a multi-page scanned PDF using rapidocr-onnxruntime without writing intermediate image files to disk?"
3. **JSON Schema Validation Check**:
   > "Verify the jsonschema Draft202012Validator invocation syntax in Python to validate report dict against report.schema.json."
