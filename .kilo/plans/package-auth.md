# Auth Package Spec

## Purpose
Client session lifecycle, OTP/PIN flows, secure credential storage, biometric unlock, device identity, and logout/revocation handling.

## Dependencies
`api`, `types`, `validation`, platform secure-storage adapters.

## Phases
- MVP: owner OTP, staff ID/PIN, session restore, secure token storage, logout.
- Stage 2: biometric unlock for authorized sessions and device revocation state.
- Commerce/AI: customer OTP account flow separated from staff authentication.

## Boundaries
Never store tokens in ordinary local storage. Biometric unlock unwraps an existing session, not a replacement for server auth. Handle clock skew, refresh failure, and revoked devices explicitly.

## Validation
Expired/rotated token tests, logout/revocation, failed OTP/PIN throttling, secure-store unavailable fallback, and staff/customer session separation.
