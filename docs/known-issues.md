# AndiOS — Known Issues & Technical Debt

This document catalogs identified security and architectural issues prioritized for future mitigation.

---

## 1. Google Calendar OAuth: Unsigned State & Potential Tenant Confusion

- **Location**: `backend/routers/connectors.py` (`google_calendar_auth` and `google_calendar_callback`)
- **Severity**: Medium
- **Description**:
  In `GET /connectors/google-calendar/auth`, the OAuth2 `state` parameter is set directly to the caller's raw `agency_id` without an HMAC signature or nonce:
  ```python
  auth_url = get_auth_url(state=agency_id)
  ```
  When Google redirects back to `GET /connectors/google-calendar/callback?code=...&state=<agency_id>`:
  ```python
  agency_id = state
  sb.table("connectors").update({"auth_data": tokens, ...}).eq("agency_id", agency_id).execute()
  ```
  Because the `state` parameter is neither cryptographically signed nor verified against a server-side session nonce:
  1. An attacker could tamper with the `state` parameter during the redirect to inject tokens into another agency's connector record.
  2. If a user has sessions in multiple agencies or clicks a forged redirect link, tenant confusion can occur.
- **Recommended Remediation**:
  Sign the `state` parameter with `itsdangerous` or an HMAC-SHA256 signature using `settings.SECRET_KEY`:
  ```python
  # In auth:
  state = signer.dumps({"agency_id": agency_id, "nonce": secrets.token_hex(16), "exp": time.time() + 600})
  # In callback:
  payload = signer.loads(state, max_age=600)
  agency_id = payload["agency_id"]
  ```
- **Status**: Deferred per consolidated review instructions; tracked for remediation in Phase 2.


---

## 2. Connectors `auth_data` Stored Unencrypted

- **Location**: `connectors` table (`auth_data` JSONB column)
- **Severity**: Low / Informational (Technical Debt)
- **Description**:
  Third-party credentials (e.g. Google Calendar OAuth refresh/access tokens, portal credentials) are persisted as JSONB in the `connectors.auth_data` column without application-layer Fernet encryption.
  The current migration (`schema_v11_security.sql` and `backfill_encrypt_tokens.py`) focuses specifically on securing WhatsApp credentials (`communication_accounts.access_token_enc` and `registration_pin_enc`).
- **Recommended Remediation**:
  In a future phase, migrate `connectors.auth_data` to application-space envelope encryption (e.g. `auth_data_enc BYTEA`) using `utils/crypto.py`.
- **Status**: Out of scope for current BYON Meta Embedded Signup migration; documented for subsequent hardening sprint.

---

## 3. Stale Seed/Test Row in `communication_accounts`

- **Location**: Database table `communication_accounts`
- **Severity**: Low (Operational / Data Hygiene)
- **Description**:
  The live database contains exactly one legacy row in `communication_accounts` with:
  - `agency_id`: `11111111-1111-1111-1111-111111111111`
  - `agent_id`: `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa`
  - `phone_number`: `15556712300` (US test number)
  - `provider`: `meta`
  - `status`: `active`
  - `access_token_enc` / `access_token`: `NULL`
  
  This row is confirmed stale mock/test seed data and does not correspond to any valid agency in the `agencies` table (which only contains production agency `d8798ea7-1b47-40be-ba3e-8e9593871393`).
- **Required Action**:
  A human administrator should manually delete this stale test row from `communication_accounts` before or shortly after this migration ships:
  ```sql
  DELETE FROM communication_accounts 
  WHERE agency_id = '11111111-1111-1111-1111-111111111111' 
    AND id = '<row-id>';
  ```
- **Status**: Documented; automated deletion withheld per safety policy.

