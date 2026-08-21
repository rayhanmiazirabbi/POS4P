# Prescriptions Backend Spec

## Purpose
Prescription metadata, secure file storage references, review workflow, pharmacist decisions, and order linkage.

## Dependencies
`organizations`, `stores`, `customers`, `orders`, object storage, `audit`, optional `ai`.

## Phases
- Commerce: upload, pending/approved/rejected/needs-clarification states, review notes, pharmacist authorization.
- AI: OCR/product extraction as an untrusted suggestion requiring review.
- Future: regulatory retention and clinical workflows, explicitly out of current scope.

## Data/API
Owns `prescriptions`, `prescription_files`, `prescription_reviews`; stores object keys, not binary files in PostgreSQL. Signed URLs are short-lived and scoped.

## Validation
Unauthorized file access, status transitions, replacement/deletion retention, malware/content checks, order gating, and audit completeness.
