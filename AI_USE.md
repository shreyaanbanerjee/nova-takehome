# AI use

Documentation of minimal AI assistance during the assignment.

## Tools

Cursor editor with Claude 3.5 Sonnet, used as an IDE autocomplete and syntax assistant.

## How you worked with them

Per GoComet's instructions on minimal AI use, I kept AI involvement strictly limited to low-level syntax and boilerplate assistance:
- **Design & Architecture**: I read the problem specifications (`GLOSSARY.md`, `README.md`, `grader/score.py`) and designed the domain models, state persistence, and audit pipeline by hand.
- **AI Queries**: I queried the AI assistant mainly for tricky Python regex edge cases (e.g., non-greedy multiline matching across arbitrary line wraps) and RapidOCR ONNX runtime image-to-text integration.
- **Verification**: All arithmetic calculations, precedence rules, and quote snapping were verified and debugged manually using deterministic test scripts and Python assertions.

## Written without AI

- Core architecture, state serialization, and batch ingestion pipeline in `harness/`.
- All freight auditing algorithms for overcharges, duplicate billings, authorized waivers, late fee calculations, qualifying spend volume rebates, and contract expiration alerts.
- The 5 safety hard-cap guardrails ($5,000 autonomous execution threshold, supervised mode outbound lock, one-off waiver isolation, and escalation ceiling).
- All 12 evaluation test scenarios in `evals/cases/` and documentation in `SOLUTION.md` and `DESIGN.md`.

## Prompt log

1. **Regex for Line Items**:
   > "Help construct a Python regex to match whitespace-delimited freight invoice lines with columns: item_no, tracking_number, pickup_date, description, lane, units, rate, net_amount."
2. **RapidOCR Integration**:
   > "How to pass a PyMuPDF page pixmap directly into rapidocr-onnxruntime as a PNG byte buffer without saving to disk?"
3. **JSON Schema Draft 2020-12**:
   > "How to validate a python dictionary against a JSON schema file using the jsonschema library with Draft202012Validator?"
