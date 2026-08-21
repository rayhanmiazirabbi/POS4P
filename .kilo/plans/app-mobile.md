# Mobile App Spec

## Purpose
Expo SDK 57 React Native client for Android/iOS POS, inventory, customer lookup, owner dashboard, scanning, notifications, and offline operation.

## Dependencies
`core`, `api`, `types`, `validation`, `auth`, `permissions`, `medicine`, `inventory`, `sales`, `purchasing`, `customers`, `loyalty`, `reports`, `sync`, `money`, and `design_tokens`.

## Phases
- MVP: authenticated store selection, product search/barcode scan, cart, cash/digital/due payment, receipt view, customer lookup, offline SQLite POS, sync status.
- Stage 2: loyalty, transfers, purchasing, expiry/low-stock workflows, owner dashboard, notifications.
- Commerce/AI: order handling, prescription review queue, invoice capture, voice cart, reorder suggestions.

## Boundaries
Use Expo Router, TanStack Query for server state, Zustand for session/UI state, SQLite for local POS tables, SecureStore for tokens/device credentials, Camera and Notifications through adapters. Domain calculations remain in packages; screens orchestrate them.

## Validation
Test Android phone/tablet and iOS phone/iPad layouts, camera permission denial, airplane-mode sale and replay, duplicate submission, token expiry, printer fallback, and accessibility of cashier flows.
