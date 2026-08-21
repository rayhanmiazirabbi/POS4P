# AI Backend Spec

## Purpose
Optional asynchronous AI service layer for invoice OCR, catalogue matching, voice POS parsing, reorder/expiry predictions, reporting tools, and anomaly detection.

## Dependencies
`catalog`, `products`, `inventory`, `purchasing`, `sales`, `reports`, `prescriptions`, `audit`, `outbox`, object storage, model provider adapters.

## Phases
- Stage 2: invoice extraction and catalogue candidate matching with user confirmation.
- Stage 4: voice cart, reorder/expiry suggestions, safe natural-language report tools, anomaly alerts.
- Out of scope: diagnosis, prescribing, dosage decisions, or unrestricted SQL.

## Data/API
Owns AI job/request/result metadata, model/version/confidence, user confirmations, and prompt/tool audit references. Models call allow-listed structured tools and receive least-privilege data.

## Validation
Provider timeout/retry, prompt injection, tenant leakage, low-confidence routing, deterministic tool authorization, human confirmation gates, and output auditability.
