# Desktop App Spec

## Purpose
Tauri 2 React/Vite POS for Windows/macOS with reliable offline use and hardware integrations.

## Dependencies
Shared frontend packages, SQLite local store, Tauri commands for printers, scanners, cash drawers, filesystem, and updates.

## Phases
- MVP: login/PIN unlock, keyboard-first POS, local inventory/product cache, sales and receipts, offline sync, printer adapter.
- Stage 2: purchasing, stock operations, branch switching, device management, cash drawer and scanner support.
- Commerce/AI: order preparation, invoice OCR capture, operational alerts and assisted purchasing.

## Boundaries
Keep Rust commands narrow and typed; no sales rules in Rust. Hardware failures must not silently alter sale state. Use signed automatic updates only after explicit policy/configuration.

## Validation
Test Windows and macOS packaging, offline sale/replay, USB/Bluetooth/network printer failures, scanner keyboard input, cash-drawer failure, app restart recovery, and update rollback behavior.
