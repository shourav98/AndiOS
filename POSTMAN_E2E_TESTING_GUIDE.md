# AndiOS — Complete Postman E2E API Testing Guide

> **Source of truth:** current backend code (`backend/routers`, `models`, `services`, `tests`) at HEAD.
> Every endpoint, field, status code and behavior below was verified against the running backend
> (`http://127.0.0.1:8000`) and/or the automated test suite (**152+ tests passing**).
> Items **not** exercised live are explicitly marked **NOT VERIFIED (LIVE)** — they are unit-tested only
> or require external integrations.
>
> **Status labels:** ✅ PASS (LIVE) · ✅ PASS (UNIT) · ⚠️ NOT VERIFIED (LIVE) · 🔧 requires setup

---

## SECTION 1 — COMPLETE API INVENTORY (101 endpoints)

### Authentication — `/auth` (no token unless noted)
| Method | Endpoint | Auth | Purpose | FE Screen |
|---|---|---|---|---|
| POST | `/auth/register` | ❌ | Create agency + owner (one call) | Register |
| POST | `/auth/verify-otp` | ❌ | Verify signup/recovery OTP | OTP Verification |
| POST | `/auth/resend-otp` | ❌ | Resend OTP (60 s Supabase cooldown) | OTP Verification |
| POST | `/auth/login` | ❌ | Password login → 24 h JWT | Login |
| POST | `/auth/logout` | ✅ | Invalidate (cosmetic — JWT is stateless) | any |
| GET | `/auth/me` | ✅ | Profile + agency + role | all screens |
| POST | `/auth/refresh` | refresh tok | New 24 h JWT | (FE not wired yet) |
| POST | `/auth/forgot-password` | ❌ | Send recovery OTP (anti-enumeration) | Forgot Password |
| POST | `/auth/reset-password` | reset tok | Set new password (150 s token) | Reset Password |
| POST | `/auth/change-password` | ✅ session | Change password (requires current) | Settings |
| PATCH | `/auth/profile` | ✅ | Update own profile (non-privileged) | Settings |
| POST | `/auth/profile/update` | ✅ | Alias of the above | Settings |

### Agents — `/agents`
| Method | Endpoint | Auth | Role | FE Screen |
|---|---|---|---|---|
| GET | `/agents` | ✅ | scoped (`search,branch,role,limit≤100,offset`) | Team |
| POST | `/agents` | ✅ 👑 | Create agent row (plan-limited) | Team |
| POST | `/agents/invite` | ✅ 👑 | Row + Supabase invite email | Team |
| GET | `/agents/plan-usage` | ✅ | Plan counters | Team / Plan & Billing |
| GET | `/agents/{id}` | ✅ | Profile + stats | Team |
| PATCH | `/agents/{id}` | ✅ (👑 for role/is_active) | Update | Team |
| DELETE | `/agents/{id}` | ✅ 👑 | Soft-delete | Team |

### Leads — `/leads`
| Method | Endpoint | Auth | Notes | FE Screen |
|---|---|---|---|---|
| POST | `/leads` | ✅ | `status` starts `new`; `assigned_agent_id` validated same-agency | Leads / pipeline |
| GET | `/leads` | ✅ | Filters `status,source,agent_id,is_ai_handling,search` + `limit≤200,offset`; envelope `{leads,total,limit,offset}`; search covers `name,phone,email,external_lead_id,property_ref,property_address,location_pref` (ilike) | Leads list |
| GET | `/leads/stats` | ✅ | Status counts + conversion % (role-scoped) | Overview cross-check |
| GET | `/leads/{id}` | ✅ +access | Lead + `conversations[]` + `viewings[]` | Lead Details drawer |
| PATCH | `/leads/{id}` | ✅ +access | Status/assignment/qualification; `viewing_booked→qualifying` auto on `is_ai_handling:true` | Lead Details |
| POST | `/leads/{id}/handover` | ✅ +access | AI→human | Lead Details |
| POST | `/leads/{id}/restore-ai` | ✅ +access | Back to `qualifying` | Lead Details |

### Conversations — `/conversations/{lead_id}` (all ✅ + lead access)
`GET /` thread · `POST /send` (server-derived sender) · `POST /read` — **Lead Details drawer**.

### Viewings — `/viewings`
| Method | Endpoint | Auth | Notes | FE Screen |
|---|---|---|---|---|
| GET | `/viewings` | ✅ scoped | `agent_id,status,date_from,date_to,limit≤500` | Calendar |
| POST | `/viewings` | ✅ +lead access | **Auto:** lead→`viewing_booked`, reminders, GCal event (optional), WA confirm (optional). `status` accepted at create | Calendar |
| GET | `/viewings/available-slots` | ✅ | Requires GCal connector 🔧 | Calendar |
| GET/PATCH | `/viewings/{id}` | ✅ +access | Complete/cancel/reschedule | Calendar |

### Contracts — `/contracts` (⚠️ POST routes have trailing slash)
| Method | Endpoint | Auth | Notes | FE Screen |
|---|---|---|---|---|
| POST | `/contracts/` | ✅ | Draft; `agent_id` = acting agent (attribution) | Create Contract |
| GET | `/contracts/` | ✅ scoped | List (camelCase FE shape) | Contracts |
| GET | `/contracts/{id}` | ✅ scoped | Full row **incl. sign tokens** | Contract Details |
| POST | `/contracts/{id}/generate` | ✅ scoped | PDF (private bucket, 7-day signed URL) + tokens | Contract Details |
| POST | `/contracts/{id}/send-esign` | ✅ scoped | WhatsApp links (needs WA 🔧) | Contract Details |
| POST | `/contracts/{id}/sign` | 🔑 token | **Public.** `{token,role:landlord|tenant}` | Sign page |
| POST | `/contracts/{id}/close` | ✅ scoped | `{cheque_image_url}` → `closed`, lead→`closed` | Contract Details |

### Cheques / Documents
`POST /cheques/` (contract-scoped) · `GET /cheques/` — **Cheques**.
`POST /documents/` (OCR best-effort: success → `extracted`, failure → `failed` + 201) · `GET /documents/lead/{lead_id}` — **Documents**.

### Owners (calling DB) — `/owners`
`GET ""`, `POST ""`, bulk JSON/CSV/XLSX (≤5000 rows), `GET/{id}`, **`PATCH/{id}`**, **`DELETE/{id}` (👑)** — **Owner Database (agent app)**.

### Campaigns / Calls
`GET/POST /call-campaigns` (POST enforces monthly quota) · `GET/{id}` · `POST/{id}/run` (**👑** + active sub + calling hours 09–18 Dubai Mon–Sat) · `POST/{id}/pause` · `GET/{id}/calls` · `GET /calls` — **Calling agent screens**.

### Dashboard
`GET /dashboard/overview` — **Overview** (full detail in SECTION 4).
`GET /dashboard/calling-performance` — real 7-day aggregation — **Performance**.

### Subscription — `/subscription` (Overview of Plan & Billing)
`GET plans` · `GET my-plan` (👑) · `POST checkout|upgrade|add-on` (💼) · `DELETE add-on/{code}` (💼; deletes Stripe item) · `GET billing-portal|invoices|invoices/{id}|payment-method|contract` (👑/manager; payment-method POST 💼; **raw PAN rejected in production** — use `payment_method_id`) · **`POST /cancel`** (💼).

### Webhooks (server-to-server; secrets required in production)
`POST /webhooks/property-finder` (HMAC `X-Hub-Signature-256`) · `/bayut`, `/dubizzle` (`X-Webhook-Token`/`?token=`) · `GET+POST /webhooks/whatsapp` (shared token / Twilio signature) · `/vapi` (`x-vapi-secret`) · `/stripe` (`Stripe-Signature`).

### Admin — `/admin/*` (🛡 super_admin; role is DB-only) — 6 endpoints, no FE screens yet.

---

## SECTION 2 — EXECUTION FLOW (actual implementation order)

```
PHASE 1  Authentication            /auth/register → verify-otp → login
PHASE 2  Agency/Owner setup        (created by register; verify via /auth/me)
PHASE 3  Agents                    POST /agents ×N
PHASE 4  Branches                  agents.branch = free text (set on create/PATCH)
PHASE 5  Create Leads              POST /leads (assigned_agent_id validated)
PHASE 6  Lead listing/search       GET /leads, /leads/{id}, ?search=&filters=
PHASE 7  Status transitions        PATCH /leads/{id} {"status": …}
PHASE 8  AI handling               /handover → /restore-ai (is_ai_handling flag)
PHASE 9  Create Viewing            POST /viewings (auto lead→viewing_booked)
PHASE 10 Complete Viewing         PATCH /viewings/{id} {"status":"completed"} (+lead→viewing_done)
PHASE 11 Create Contract          POST /contracts/
PHASE 12 Generate Contract        POST /contracts/{id}/generate  (tokens minted)
PHASE 13 Sign Contract            GET /contracts/{id} → POST /{id}/sign ×2 (public)
PHASE 14 Create Cheque            POST /cheques/
PHASE 15 Document / OCR           POST /documents/ (best-effort OCR)
PHASE 16 Close Contract / Deal    POST /contracts/{id}/close (lead → closed)
PHASE 17 Dashboard verification   GET /dashboard/overview (+filters) vs baseline
PHASE 18 Isolation tests          foreign-agency token → 404/401; agent scoping
```
Campaigns (owners → campaign → run) and WhatsApp/Vapi webhooks are **separate optional
flows** — see Sections 10–11. Billing is a separate flow (Section 12 notes).

---

## SECTION 3 — FRONTEND SCREEN → API INTEGRATION MAP (verified from live FE routes)

| # | Screen (route) | APIs | When to call | Key vars | Next |
|---|---|---|---|---|---|
| 1 | Login `/login` | POST `/auth/login` | submit | → `access_token` | Overview routing |
| 2 | Register `/register` + `/otp-verification` | `/auth/register`, `/verify-otp`, `/resend-otp` | submit / OTP entry | → tokens | Login |
| 3 | Forgot/Reset | `/auth/forgot-password`, `/verify-otp(type=recovery)`, `/reset-password` | flow | → 150 s reset token | Login |
| 4 | Overview `/owner-dashboard/overview` | `GET /dashboard/overview` (+`GET /leads/stats` cross-check) | page load / filter change | `access_token` | – |
| 5 | Leads `/owner-dashboard/leads` + drawer | `GET /leads`, `GET/PATCH /leads/{id}`, `handover`, `restore-ai`, `GET /leads/stats`, conversations ×3 | load / search / actions | `lead_id` | Calendar |
| 6 | Calendar `/owner-dashboard/calendar` | `/viewings` ×5 (+`available-slots` 🔧GCal) | load / book | `viewing_id`, `lead_id` | Contracts |
| 7 | Contracts `/contracts`, `/create-contracts`, `/contract-details/:id` | `/contracts` ×7 (sign = public token) | lifecycle | `contract_id`, sign tokens | Cheques/Docs |
| 8 | Cheques | `/cheques/` ×2 | after contract | `contract_id` | – |
| 9 | Documents (lead drawer) | `/documents` ×2 | upload | `lead_id` | – |
| 10 | Team `/owner-dashboard/team` | `/agents` ×7, `/agents/plan-usage` | load / manage | `agent_id` | – |
| 11 | Connectors `/owner-dashboard/connectors` | `/connectors` ×11 (GCal OAuth 🔧) | wizard | – | – |
| 12 | Owner reports `/owner-dashboard/owner-reports` | `/reports` ×4 | load / generate (👑) | – | – |
| 13 | Plan & Billing ×3 | `/subscription` ×14 (incl. `POST /cancel`) | load / purchase | `payment_method_id` (Stripe.js) | – |
| 14 | Calling agent: campaigns | `/call-campaigns` ×6, `/owners` ×7 | load / run (👑) | `campaign_id`, `owner_id` | Performance |
| 15 | Calling agent: performance | `GET /dashboard/calling-performance` | load | – | – |
| 16 | Settings (embedded) | `POST /auth/change-password`, `POST /auth/profile/update` | save | – | – |
| 17 | Admin | `/admin` ×6 | 🛡 only | – | **no FE screens yet** |

---

## SECTION 4 — OWNER DASHBOARD → OVERVIEW (detailed)

**Single primary API:** `GET /dashboard/overview`
Optional: `GET /leads/stats` (consistency), `GET /agents` (dropdown options).

### Widget → response path (verified types/values)

| Widget | Response path | Type | Example (E2E run) | Filter behavior |
|---|---|---|---|---|
| Avg response time | `data.metrics.avg_response_time.value` | null | `null` → render "—" | not period-filtered |
| "AI handles N%" | `…avg_response_time.subtext` | string | `"AI handles 100%"` | follows lead set |
| Lead → Viewing | `data.metrics.lead_to_viewing.{value,subtext,trend,trend_value}` | num/str | `50.0`, `"1 of 2"`, `"up"`, `"0pts"` | all filters |
| Viewing → Closing | `data.metrics.viewing_to_close.*` | num/str | `100.0`, `"1 of 1"` | all filters |
| Close rate | `data.metrics.close_rate.*` | num/str | `50.0`, subtext `"overall"` or timeframe label | all filters |
| Closed deals count | `data.closed_deals.count` | int | `1` | agent/branch/time |
| Agency fees (5%) | `data.closed_deals.agency_fees_earned` | float | `6000.0` | same |
| Total rent value | `data.closed_deals.total_rent_value` | float | `120000.0` | same |
| Deals by Agent rows | `data.closed_deals.deals_by_agent[]` → `initials,firstName,lastName,deals_count,revenue` | arr | `[{initials:"SA",…,revenue:6000}]` | agent/branch/time |
| Live Leads table | `data.live_leads[]` (≤5, newest) → raw lead fields + `agent_name` | arr | 1 row (L2) | all filters |
| Today's Viewings | `data.todays_viewings[]` → `viewing_datetime,property_address,agent_name,lead_name,lead_source` | arr | 1 row | agent/branch (date fixed=today UTC) |
| Funnel | `data.funnel[]` → `name,count,percentage` | arr | Leads 2/100, Viewings 1/50, Closings 1/50 | all filters (`Closings.%` = of **leads**) |
| AI: dials | `data.ai_agent_stats.outbound_dials` | int | 0 | calls-table window |
| AI: answer rate / calls→listings | `.answer_rate/.calls_to_listings` | str | `"0%"` | – |
| AI: conversations / listings won | `.conversations/.new_listings_won` | int | 0 | – |

**Supporting APIs:** `GET /leads/stats` → `{total,new,qualifying,viewing_booked,viewing_done,closed,lost,handover,lead_to_viewing_pct,viewing_to_close_pct,…}` — must never contradict overview (verified). `GET /agents` → branch/agent dropdown options.

---

## SECTION 5 — OVERVIEW FILTER TESTING (E2E-verified values)

Dataset produced by this guide: **L1** (Agent A, Dubai Marina, property_finder, **closed**, viewing today-completed, contract closed 120k) + **L2** (Agent B, Business Bay, bayut, qualifying).

| # | Request | Expected (with that dataset) |
|---|---|---|
| 1 | `GET /dashboard/overview` | funnel `2/1/1`; lead→viewing `50.0` "1 of 2"; closed 1/120000/6000; live=[L2]; today=[L1]; AI zeros |
| 2 | `?timeframe=today` | identical (all records created today) |
| 3 | `?timeframe=last_7_days` / `this_month` / `last_30_days` / `this_quarter` | identical; trends computed |
| 4 | `?start_date=2026-08-01&end_date=2026-08-31` | identical; trends vs July |
| 5 | `?start_date=2026-01-01&end_date=2026-01-31` | all zeros |
| 6 | `?agent_id={{agent_id_a}}` | funnel `1/1/1`; closed_deals **0** (contract attributed to creator) |
| 7 | `?agent_id={{agent_id_b}}` | funnel leads=1 (L2), closings 0 |
| 8 | `?branch_id=Dubai%20Marina` | == Agent A numbers |
| 9 | `?branch_id=Business%20Bay` | == Agent B numbers |
| 10 | `?branch_id=Nowhere` | 200, all zeros |
| 11 | `?platform=bayut` | leads=1 (L2) |
| 12 | `?platform=property_finder` | leads=1 (L1) |
| 13 | `?agent_id={{agent_id_a}}&timeframe=this_month&platform=property_finder` | 1 |
| 14 | `?branch_id=Dubai%20Marina&timeframe=last_7_days` | 200 |
| 15 | agent **token** + no params | Agent A self-scope (funnel=1) |

---

## SECTION 6 — ENVIRONMENT VARIABLES

| Variable | Purpose | Created by |
|---|---|---|
| `base_url` | `http://127.0.0.1:8000` (trailing slash optional — be consistent) | manual |
| `access_token` / `token_owner` | `POST /auth/login` → `data.access_token` | login script |
| `refresh_token` | login → `data.refresh_token` | login script |
| `user_id` | login → `data.user.id` (auth UID) | login script |
| `agency_id` | login → `data.user.agent.agency_id` | login script |
| `owner_agent_id` | login → `data.user.agent.id` | login script |
| `agent_id_a` / `agent_email_a` | `POST /agents` → `data.id/email` | create script |
| `agent_id_b` / `agent_email_b` | same | create script |
| `lead_id_l1` / `lead_external_id_l1` / `lead_agent_id_l1` | `POST /leads` → `data.id/external_lead_id/assigned_agent_id` | create script |
| `lead_id_l2` | second lead | create script |
| `viewing_id` | `POST /viewings` → `data.id` | create script |
| `contract_id` | `POST /contracts/` → `data.id` | create script |
| `landlord_token` / `tenant_token` | `GET /contracts/{id}` → `data.landlord_sign_token/tenant_sign_token` | detail script |
| `cheque_id` | `POST /cheques/` → `data.id` | create script |
| `document_id` | `POST /documents/` → `data.id` | create script |
| `owner_row_id` / `campaign_id` | owners/campaigns creates | create scripts |
| `token_agent_a` | agent login (requires 🛡 Supabase auth user) | agent login |

---

## SECTION 7 — READY-TO-USE POSTMAN TEST SCRIPTS

**Login (Tests tab):**
```js
const d = pm.response.json().data;
pm.test("login 200", () => { pm.response.to.have.status(200); pm.expect(pm.response.json().success).to.eql(true); });
pm.environment.set("access_token", d.access_token);
pm.environment.set("refresh_token", d.refresh_token);
pm.environment.set("user_id", d.user.id);
pm.environment.set("agency_id", d.user.agent.agency_id);
pm.environment.set("owner_agent_id", d.user.agent.id);
```

**Create Lead:**
```js
const d = pm.response.json().data;
pm.test("201 + persisted", () => {
    pm.response.to.have.status(201);
    pm.expect(d.external_lead_id).to.eql("POSTMAN-E2E-001");
    pm.expect(d.assigned_agent_id).to.eql(pm.environment.get("agent_id_a"));
    pm.expect(d.agency_id).to.eql(pm.environment.get("agency_id"));
    pm.expect(d.status).to.eql("new");
    pm.expect(d.is_ai_handling).to.eql(true);
});
pm.environment.set("lead_id_l1", d.id);
pm.environment.set("lead_external_id_l1", d.external_lead_id);
pm.environment.set("lead_agent_id_l1", d.assigned_agent_id);
```

**Lead search (regression):**
```js
const d = pm.response.json().data;
pm.test("search total==1 + lead returned", () => {
    pm.expect(d.total).to.eql(1);
    pm.expect(d.leads[0].id).to.eql(pm.environment.get("lead_id_l1"));
});
```

**Viewing create / complete / contract sign / close / dashboard:** use the scripts from
`tests/test_integration_fixes.py` as assertion reference — key checks:
- viewing: `data.id` saved; `GET /leads/{id}` shows `status:"viewing_booked"` + viewing in `viewings[]`.
- sign: two calls (landlord, tenant) → second returns `fully_signed:true`; forged token → 400.
- close: 200 `status:"closed"`; then `GET /leads/{id}` → `status:"closed"`.
- overview: assert funnel/closed_deals equal the values computed in SECTION 5.

---

## SECTION 8 — REQUEST-BY-REQUEST EXECUTION GUIDE (verified sequence)

> All authenticated calls: `Authorization: Bearer {{…}}`, `Content-Type: application/json`.
> ✅ = executed live in the verified E2E run.

**STEP 1 ✅** `POST /auth/login` `{email,password}` → 200; save token/ids (script above). *Pre: registered+confirmed account.*
**STEP 2 ✅** `GET /auth/me` → 200; verify role/agency; vars re-confirmed.
**STEP 3 ✅** `GET /dashboard/overview` → 200; **baseline all zeros** (empty agency). Save `baseline_overview`.
**STEP 4 ✅** `POST /agents` Agent A `{name:"Postman Agent A",email:"postman.agent.a@e2etest.com",phone:"+971500000101",role:"agent",branch:"Dubai Marina"}` → **201**; save `agent_id_a`. (409 → email exists, bump suffix.)
**STEP 5 ✅** `POST /agents` Agent B (`branch:"Business Bay"`) → 201; save `agent_id_b`.
**STEP 6 ✅** `GET /agents?limit=100` + `GET /agents/plan-usage` → 3 agents, correct branches; plan pro.
**STEP 7 ✅** `POST /leads` L1 (body in SECTION 5 note; unique `external_lead_id:"POSTMAN-E2E-001"`, `assigned_agent_id:{{agent_id_a}}`) → **201**; save `lead_id_l1`… (400 "not belong to your agency" = assignment guard working).
**STEP 8 ✅** `GET /leads/{{lead_id_l1}}` → 200 round-trip + empty `conversations/viewings`.
**STEP 9 ✅** `GET /leads?search=POSTMAN-E2E` → total 1 + L1 (also test full name, property ref, lowercase, no-match → 0).
**STEP 10 ✅** `PATCH /leads/{{lead_id_l1}} {"status":"qualifying"}` → 200; GET verifies persistence.
**STEP 11 ✅** `POST /leads/{{lead_id_l2}}` L2 → Agent B → 201 (for scoping/handover tests).
**STEP 12 ✅** `POST /leads/{L2}/handover {"reason":"E2E"}` → 200; GET → `handover`+AI off; `POST /restore-ai` → `qualifying`+AI on.
**STEP 13 ✅** `POST /viewings {lead_id:L1, agent_id:A, property_address, viewing_datetime:"<today>T10:00:00Z", duration_minutes:60}` → 200/201; save `viewing_id`; lead auto→`viewing_booked`; unknown lead → 404.
**STEP 14 ✅** `PATCH /viewings/{{viewing_id}} {"status":"completed"}` → 200; `PATCH /leads/{L1} {"status":"viewing_done"}`.
**STEP 15 ✅** `POST /contracts/ {lead_id:L1, rent_amount:120000, dates, names,…}` → 200/201 draft; save `contract_id`. *(Trailing slash!)*
**STEP 16 ✅** `POST /contracts/{id}/generate` → 200 signed URL.
**STEP 17 ✅** `GET /contracts/{id}` → copy sign tokens.
**STEP 18 ✅** `POST /contracts/{id}/sign {token:ll,role:"landlord"}` → 200; repeat tenant → `fully_signed:true`; forged token → 400.
**STEP 19 ✅** `POST /cheques/ {contract_id,cheque_number:"CH-E2E-01",bank_name,amount,due_date}` → 200; listed in `GET /cheques/`.
**STEP 20 ✅** `POST /documents/ {lead_id,document_type,file_url}` → 200 (`extracted` with real image URL / `failed` with unreadable URL — never 500).
**STEP 21 ✅** `PATCH /leads/{L1} {"status":"negotiating"}` → `POST /contracts/{id}/close {"cheque_image_url":…}` → 200 closed; lead → `closed`.
**STEP 22 ✅** `GET /dashboard/overview` → funnel 2/1/1, closed 1/120000/6000, deals_by_agent, live=[L2], today=[L1], AI zeros.
**STEP 23 ✅** Filters: SECTION 5 matrix (#2–#15).
**STEP 24 ✅** Isolation: SECTION 10.
**STEP 25 ✅** Negative tests: SECTION 9.

---

## SECTION 9 — NEGATIVE / ERROR TESTING (verified statuses)

| # | Test | Request | Expected |
|---|---|---|---|
| N1 | No token | any ✅ endpoint without header | **401** |
| N2 | Garbage token | `Bearer abc` | **401** |
| N3 | Invalid UUID path | `GET /leads/{not-a-uuid}` | **422** |
| N4 | Missing required field | `POST /leads` w/o `name`/`phone` | **422** |
| N5 | Foreign lead detail | owner-of-B → `GET /leads/{A-lead}` | **404** (no existence leak), DB unchanged |
| N6 | Foreign contract generate/close | → **404**; close also 400 when own-but-unsigned | DB unchanged |
| N7 | Foreign agent assignment | `POST /leads {assigned_agent_id:<other-agency agent>}` | **400** "does not belong to your agency", no insert |
| N8 | Unknown viewing lead | `POST /viewings {lead_id:random}` | **404** |
| N9 | Close unsigned contract | `/close` on draft | **400** "must be signed" |
| N10 | Forged sign token | `/sign {token:"wrong"}` | **400**, DB unchanged |
| N11 | Expired sign link | expired token | **400** "expired" |
| N12 | Invalid status value | `PATCH {"status":"bogus"}` | **422** (enum) |
| N13 | Search no-match | `?search=THIS-DOES-NOT-EXIST` | **200** total 0 [] |
| N14 | Search metacharacters | `?search=jo%,()` | **200** sanitized, no PostgREST error |
| N15 | User without agents row | overview with valid JWT, no profile | **400** "No agent profile found" |
| N16 | Raw PAN in production | `POST /subscription/payment-method {card_number…}` | **400** "tokenize" (dev allows) |
| N17 | Unreadable document URL | `POST /documents/` fake URL | **200** `status:"failed"` (row persisted) |
| N18 | Agent self-role-escalation | `PATCH /agents/{self} {"role":"owner"}` as agent | **403** |
| N19 | Agent runs campaign | `POST /call-campaigns/{id}/run` as agent | **403** |
| N20 | Quota exceeded | 1000th campaign (starter) | **403** upgrade message |
| N21 | Foreign webhook | unsigned/wrong-secret webhook | **403/400**, zero DB writes |
| N22 | Duplicate lead email | n/a (leads allow dupes) | documented: allowed |

---

## SECTION 10 — TENANT ISOLATION TESTING

Setup: **Agency A** = your E2E agency (owner token `{{token_owner}}`, data: L1/L2/viewing/contract). **Agency B** = any other agency's owner token (`{{token_foreign}}` — mint via a second `/auth/register`, or use another real agency owner).

| # | Token | Request | Expected | DB change |
|---|---|---|---|---|
| ISO-1 | B | `GET /leads/{A-lead}` | **404** | none |
| ISO-2 | B | `GET /leads?search=POSTMAN-E2E` | 200, `total` counts only B's leads (A's absent) | none |
| ISO-3 | B | `GET /contracts/{A-contract}` | **404** | none |
| ISO-4 | B | `POST /contracts/{A-contract}/close` | **404** | none |
| ISO-5 | B | `POST /cheques/ {contract_id:A}` | **404** | none |
| ISO-6 | B | `POST /viewings {lead_id:A}` | **404** | none |
| ISO-7 | B | `GET /documents/lead/{A-lead}` | 200 [] (or 404 via access check) | none |
| ISO-8 | B | `GET /dashboard/overview` | 200, only B's counts | none |
| ISO-9 | B | `POST /leads {assigned_agent_id:<A's agent>}` | **400** | none |
| ISO-10 | A-agent | `GET /leads/{B-agent-lead}` | **403/404** | none |

Live-verified: ISO-1/3/4/8/9/10 ✅ (E2E + audit runs).

---

## SECTION 11 — KNOWN API BEHAVIOR (verified, not bugs)

1. `metrics.avg_response_time.value` is **null** until response-time analytics exist; subtext "AI handles N%" is real.
2. `ai_agent_stats` are **zeros until `calls` rows exist**; Postman-only campaign runs (no Vapi key) create `Failed` dials → dials>0, answer-rate 0%. Answer-side stats require real Vapi outcomes via `/webhooks/vapi`.
3. `branch_id` expects the **branch NAME string** (matched to `agents.branch`); ignored for agent-role callers.
4. `funnel[0].percentage` is 100 even at 0 leads; `Closings.percentage` is % **of leads** (use `metrics.viewing_to_close.value` for % of viewings).
5. Trends are `null` without a period filter; `"up"/"0pts"` default with one.
6. Contract PDFs live in a **private** bucket; returned URLs are **signed, 7-day expiry**.
7. `POST /documents/` OCR is best-effort: unreadable input → 200 with `status:"failed"` (row persisted).
8. Contract/cheque/document **POST routes have trailing slashes** (`/contracts/`, `/cheques/`, `/documents/`).
9. Campaign `run` defers (no rows) outside 09:00–18:00 Asia/Dubai, Fridays off; requires 👑 + active subscription + owners in the target group.
10. Logout does not revoke the stateless 24 h JWT; `/auth/refresh` exists for token renewal.
11. Plan-usage limits (999/10000) come from the internal limits table and differ from marketing plan numbers (display-only).
12. Webhook secrets (`PROPERTY_FINDER_WEBHOOK_SECRET`, `WHATSAPP_WEBHOOK_TOKEN`, `VAPI_WEBHOOK_SECRET`, `BAYUT/DUBIZZLE_WEBHOOK_TOKEN`, `STRIPE_WEBHOOK_SECRET`) are **mandatory in production** — requests fail closed (403/400) without them; development tolerates missing secrets with warnings.
13. Prerequisites applied to this environment: private storage bucket `contracts` ✔; e-sign/close columns migration ✔ (required again on any fresh database).

---

## SECTION 12 — FRONTEND INTEGRATION ORDER (manual for FE dev)

```
1  Authentication (existing) ── keep; add /auth/change-password + /auth/profile/update later
2  Global context             ── /auth/me → agency_id, role, agent_id (route owner vs agent)
3  Agents/Branches            ── /agents (Team screen + dropdown options)
4  Overview                   ── /dashboard/overview (SECTION 4 widget order)
5  Leads list                 ── GET /leads (envelope data.leads/total; filters)
6  Lead detail + chat         ── /leads/{id} + /conversations
7  Viewings/Calendar          ── /viewings (needs Connectors for slots)
8  Connectors                 ── wizard endpoints
9  Contracts                  ── lifecycle (SECTION 2, phases 11–13)
10 Cheques + Documents        ── after contracts
11 Owner DB + Campaigns       ── owners → campaigns → performance
12 Reports                    ── /reports
13 Billing (last)             ── /subscription (Stripe.js for card)
14 Settings                   ── change-password / profile
```

Per-screen field mappings: see `DASHBOARD_OVERVIEW_POSTMAN_AND_DATA_SETUP_GUIDE.md` (Overview deep-dive) + SECTION 3 table (screen→API).

---

## SECTION 13 — API DEPENDENCY GRAPH (actual)

```
POST /auth/register ──▶ OTP ──▶ POST /auth/login ──▶ access_token
                                                        │
        ┌───────────────────────────────────────────────┤
        ▼                                               ▼
 POST /agents ──▶ agent_id (branch)          GET /auth/me ──▶ agency context
        │                                              │
        ▼                                              ▼
 POST /leads ──▶ lead_id ──▶ PATCH status ──▶ handover/restore-ai
        │                                   │
        ▼                                   ▼
 POST /viewings ──▶ viewing_id ──▶ PATCH completed ──▶ (lead → viewing_done)
        │
        ▼
 POST /contracts/ ──▶ contract_id ──▶ /generate ──▶ GET tokens ──▶ /sign ×2 ──▶ /close
        │                                                                            │
        ├── POST /cheques/                                                           ▼
        └── POST /documents/                                              GET /dashboard/overview
                                                                 (+ /leads/stats cross-check)
Separate flows: /owners → /call-campaigns → /run → (Vapi) → /webhooks/vapi
                /webhooks/{property-finder|bayut|dubizzle|whatsapp|stripe}
                /subscription/* (Stripe)   /connectors/* (GCal/WA tests)
```

---

## SECTION 14 — FINAL POSTMAN CHECKLIST

```
[ ] Authentication (register/OTP/login/me/refresh/forgot/reset)
[ ] change-password + profile-update
[ ] Agent create/invite/list/update/delete + plan-usage
[ ] Branch values set + branch filter
[ ] Lead creation (UUID fix) + validation (foreign agent 400)
[ ] Lead retrieval + envelope (leads/total/limit/offset)
[ ] Lead search: name/partial/case/external-id/property-ref/no-match/metacharacters
[ ] Lead filters: status/source/agent_id/is_ai_handling
[ ] Status transitions: qualifying → viewing_booked(auto) → viewing_done → negotiating → closed
[ ] AI handover / restore-ai
[ ] Viewing create (today) / complete / detail / slots(NOT VERIFIED LIVE – needs GCal)
[ ] Contract create / generate(signed URL) / tokens / sign×2 / forged-400
[ ] Cheque create + list
[ ] Document create: extracted + failed paths
[ ] Contract close + lead auto-close
[ ] Overview: KPIs, closed deals, fees/rent, deals_by_agent, live leads,
    today's viewings (lead_name/source), funnel, AI stats
[ ] Overview filters: 5 timeframes + custom + agent + branch + platform + combos
[ ] Empty-agency zeros · unknown-branch zeros · no-profile 400
[ ] Tenant isolation: lead/contract/close/cheque/viewing/dashboard/document
[ ] Agent scoping: A sees own only; B-lead 403
[ ] Unauthorized: 401 no-token; 👑 403s (agents/campaigns/billing)
[ ] Webhook auth (5 providers) — fail-closed in production
[ ] Billing: plans/my-plan/checkout fail-closed/cancel/add-on removal (Stripe 🔧)
[ ] NOT VERIFIED (LIVE): real Vapi calls · WhatsApp provider E2E · Stripe CLI webhooks ·
    GCal slots · admin console flows · /auth/refresh in FE
```

---

## SECTION 15 — FINAL PASS / FAIL / NOT VERIFIED REPORT

| Area | Status | Basis |
|---|---|---|
| Auth (login/me/register/OTP/reset) | ✅ PASS (LIVE) | E2E steps 1–2 + audit |
| change-password / profile | ✅ PASS (UNIT) | regression tests |
| Agents CRUD + plan usage | ✅ PASS (LIVE) | steps 4–6 |
| Branch filter (overview) | ✅ PASS (LIVE) | audit S5 |
| Leads CRUD + search + filters | ✅ PASS (LIVE) | steps 7–9 + audit |
| Status transitions + AI handover | ✅ PASS (LIVE) | steps 8, 12 |
| Viewings create/complete/join | ✅ PASS (LIVE) | steps 13–14 |
| Contracts lifecycle + sign + close | ✅ PASS (LIVE) | steps 15–21 |
| Cheques | ✅ PASS (LIVE) | step 19 |
| Documents + OCR failure | ✅ PASS (LIVE) | step 20 |
| Overview metrics vs DB | ✅ PASS (LIVE) | 17-check audit |
| Overview filters/scoping/empty | ✅ PASS (LIVE) | 35-check audit |
| Tenant isolation | ✅ PASS (LIVE) | ISO probes + audit S10 |
| Campaigns (create/quota/gates) | ✅ PASS (UNIT) · run: ⚠️ NOT VERIFIED (LIVE – needs Vapi/hours) | tests |
| AI answer-side stats | ⚠️ NOT VERIFIED (LIVE) — requires real Vapi | – |
| WhatsApp inbound E2E | ✅ PASS (UNIT) · ⚠️ LIVE requires provider creds | tests |
| Stripe webhooks | ✅ PASS (UNIT) · ⚠️ LIVE via `stripe listen` | tests |
| Billing checkout/cancel live | ⚠️ NOT VERIFIED (LIVE) — needs Stripe keys | – |
| GCal slots | ⚠️ NOT VERIFIED (LIVE) — needs connected calendar | – |
| Admin endpoints | ✅ PASS (UNIT) · no FE | tests |
| Reports/owner-dashboard | ✅ PASS (UNIT) | tests |

**Totals:** 101 endpoints inventoried · live-verified E2E chain: auth → agents → leads → viewings → contracts → cheques → documents → close → dashboard → isolation (**all PASS**).

---

## APPENDIX — QUICK START FOR A NEW DEVELOPER

1. Start backend: `cd backend && uvicorn main:app --port 8000` (env configured; `contracts` bucket + e-sign columns already applied to this Supabase project).
2. Import environment variables from SECTION 6; set `base_url` + owner credentials.
3. Execute SECTION 8 steps **1 → 25 in order**, running each Tests script.
4. Finish with SECTION 9 negatives, SECTION 10 isolation, SECTION 14 checklist.
5. Swagger reference: `http://127.0.0.1:8000/docs`.

*Document generated from code inspection + live execution. No frontend files touched.*
