# AndiOS BYON — Phase 1 Reconciliation Matrix

Date: 2026-09-19  
Branch: `byon-meta-esu`

This document tracks all Phase 1 corrections against the codebase, specifying their status, affected files, line numbers, and resolution.

---

## Reconciliation Table

| # | Correction Area | Status | File(s) & Line(s) | Description / Resolution |
|---|---|---|---|---|
| **a** | **Graph API Version** | **Changed** | `backend/config.py`:32<br>`backend/.env.example`:44<br>`backend/services/communication/meta_adapter.py`:44<br>`backend/routers/connectors.py`:249-253 | Configured single env setting `META_GRAPH_API_VERSION = "v26.0"`. Verified official Graph API documentation and eliminated all hardcoded `v19.0`/`v22.0` references. |
| **b** | **Tokens & Database Schema** | **Changed** | `backend/database/schema_v11_security.sql`:1-135<br>`backend/utils/crypto.py`:1-190<br>`backend/services/communication/provider_factory.py`:160-185<br>`backend/scripts/backfill_encrypt_tokens.py`:1-150 | Preserved `access_token_enc BYTEA` column; strictly additive migration with duplicate phone precheck and service-role RLS. MultiFernet token encryption with strict key validation, hex BYTEA `gAAAAA` check, legacy plaintext read logging once per account, and double-encryption guards. Standalone idempotent backfill script with pre-write verification and dry-run default. |
| **c** | **Embedded Signup Callback** | **Changed** | `backend/routers/connectors.py`:480-685 | (1) Enforced agent scoping with manager-only override validated against caller's agency; (2) Fail-closed WABA & phone verification with pagination and debug_token checks; (3) Global unique index and conflict precheck returning 409; (4) Authoritative coexistence detection via documented fields with labeled fallback; (5) Stored registration PIN only on actual successful registration; (6) Generic errors with correlation IDs; (7) Upsert by (agency_id, agent_id, channel) updating legacy Twilio rows; (8) Status and disconnect scoped per agent. |
| **d** | **Embedded Signup v4 & Frontend** | **Changed** | `docs/frontend-integration-contract.md`:1-275 | Updated to Facebook Login for Business v4 (`config_id` only, `extras: {}` empty). Coordinated asynchronous race condition between `FB.login` code and postMessage session info. Fixed React stale closures using `useRef`. Documented backend JWT agent derivation. |
| **e** | **Webhook Idempotency & Fast Ack** | **Changed** | `backend/routers/webhooks.py`:290-340, 1030-1045 | Atomic idempotency using `whatsapp_processed_messages` with PK constraint and graceful degradation. Added `_unmark_message_id` on processing failure so delivery retries are not dropped. |
| **f** | **Webhook Event Handlers** | **Changed** | `backend/routers/webhooks.py`:340-420<br>`docs/human-setup-checklist.md`:45-80 | Added handlers for `smb_message_echoes` (human takeover), `history`, `smb_app_state_sync`, `account_update`, and `message_template_status_update`. |
| **g** | **Step D: Templates & Property Finder** | **Changed** | `backend/database/schema_v11_security.sql`:40-60, 95-135<br>`backend/services/communication/template_service.py`:1-145<br>`backend/routers/webhooks.py`:1120-1135 | Added `whatsapp_templates` table. Moved `last_inbound_at` to `leads` table to enforce per-lead 24h window. Scheduled template auto-creation in background tasks. Added Property Finder deduplication. |
| **h** | **Voice BYON & UAE TDRA Rules** | **Changed** | `backend/config.py`:55<br>`docs/plan-review.md`:64-82 | Marked UAE TDRA regulatory interpretations as UNVERIFIED pending legal counsel. Placed Voice BYON behind `ENABLE_VOICE_BYON: bool = False` flag. |
| **i** | **Plan Review & Client-Facing Guide** | **Changed** | `docs/plan-review.md`:85-98 | Updated with documentation citations (URL + quote ≤15 words) and client-facing operational checklist. |
| **j** | **Scheduler Twilio Guard** | **Changed** | `backend/services/scheduler.py`:382-425 | Confirmed and enforced that `poll_provisioned_whatsapp_senders_job` and `recover_stuck_provisioning_job` are strict no-ops when `ENABLE_TWILIO_PROVISIONING=false`. |

---
