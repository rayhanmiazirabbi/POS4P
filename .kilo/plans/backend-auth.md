# Auth Backend Spec

## Purpose
OTP owner authentication, staff ID/PIN authentication, sessions, device authorization, refresh/revocation, and rate limits.

## Dependencies
`organizations`, `stores`, `users`, `audit`; PostgreSQL and secure OTP provider abstraction.

## Phases
- MVP: OTP issuance/verification, staff PIN, session/token lifecycle, device registration.
- Stage 2: biometric-compatible session policy, passkeys/password option.
- Commerce: customer OTP accounts isolated from staff privileges.

## Data/API
Owns auth challenges, sessions, refresh/revocation state, and device auth status. Expose scoped login/refresh/logout and current-user endpoints. Hash PIN/password with Argon2id; never log secrets.

## Validation
Rate limits, replayed OTP, expired sessions, revoked device, tenant/store selection, brute-force protection, and direct object authorization.
