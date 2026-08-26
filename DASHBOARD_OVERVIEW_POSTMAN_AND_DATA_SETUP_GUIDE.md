# AndiOS — Dashboard Overview: Backend API Testing & Data Setup Guide

> **Scope:** Backend/API only (Postman + Supabase). No frontend changes.
> **Source of truth:** actual codebase at HEAD (routers, services, `database/*.sql`).
> **Goal:** take a fresh test account, generate real data through real APIs, and make
> `GET {{base_url}}/dashboard/overview` return meaningful, non-zero values.
>
> Legend: ✅ available now · 🔧 requires setup/config · 🧩 requires external integration ·
> ❌ NOT IMPLEMENTED (no API) · 🛡 Supabase Dashboard / DB step (not Postman)

---

## PART 0 — Response envelope (read first)

Every endpoint returns:

```json
{ "success": true, "status": 200, "message": "...", "data": { ... } }
```

Errors use the same shape with `"success": false` and the HTTP status mirrored in `status`.
Auth failures: **401**. Role failures: **403**. Missing/foreign resources: **404**.
Validation: **422** (Pydantic) or **400**.

---

## PART 1 — ACTUAL DATA FLOW (as implemented)

```
POST /auth/register
      ├─ creates Supabase Auth user
      ├─ creates agencies row          (plan: starter)
      └─ creates agents row (owner)    ← the registering user IS the owner agent
            ↓
POST /auth/verify-otp (type=signup)      ← email OTP from Supabase
      ↓
POST /auth/login                         ← 24h custom JWT (Authorization: Bearer)
      ↓
POST /agents  /agents/invite             ← team members (agents.branch = "branch")
      ↓
POST /leads                              ← status always starts "new"
      ↓
PATCH /leads/{id}                        ← status lifecycle / assignment
      ↓
POST /viewings                           ← AUTO-sets lead → viewing_booked
      │                                    + schedules reminders (follow_ups)
      │                                    + optional Google Calendar event
      │                                    + optional WhatsApp confirmation
PATCH /viewings/{id}  (status=completed) ← viewing_done source for funnel
      ↓
POST /contracts                          ← draft
POST /contracts/{id}/generate            ← PDF + e-sign tokens (status: generated)
POST /contracts/{id}/send-esign          ← WhatsApp links (status: sent)
POST /contracts/{id}/sign   (PUBLIC)     ← landlord + tenant (status: signed)
POST /contracts/{id}/close               ← fee cheque (status: closed, lead → closed)
      ↓
POST /owners → POST /call-campaigns → POST /call-campaigns/{id}/run
      │                                   (Vapi dials → calls rows → AI stats)
POST /webhooks/vapi                      ← outcomes/transcripts (x-vapi-secret)
      ↓
GET /dashboard/overview                  ← reads leads, viewings, contracts, calls
```

Entities **not** required by Overview: documents, cheques, payments/invoices,
owner-reports, connectors (optional enhancers for viewings), WhatsApp (optional).

---

## PART 2 — COMPLETE API TABLE (Overview-relevant)

| # | Purpose | Method | Endpoint | Auth | Role | Body / Params | Prereq | DB write | Dashboard field fed |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Register agency+owner | POST | `/auth/register` | ❌ | – | `{agency_name, full_name, email, password}` | – | auth.users + agencies + agents | (identity) |
| 2 | Verify signup OTP | POST | `/auth/verify-otp` | ❌ | – | `{email, token, type:"signup"}` | #1 | auth confirm | (identity) |
| 3 | Resend OTP | POST | `/auth/resend-otp` | ❌ | – | `{email, type:"signup"}` | #1 | – | – |
| 4 | Login | POST | `/auth/login` | ❌ | – | `{email, password}` | #1/#2 | app_metadata sync | **token** |
| 5 | Who am I | GET | `/auth/me` | ✅ | any | – | #4 | – | agency_id / agent_id vars |
| 6 | Refresh token | POST | `/auth/refresh` | refresh tok | any | `{refresh_token}` | #4 | – | token |
| 7 | Add agent (row) | POST | `/agents` | ✅ | 👑 owner/manager | `{name,email,phone?,role?,branch?}` | #4, plan limit | agents | deals_by_agent, branch filter |
| 8 | Invite agent (email) | POST | `/agents/invite` | ✅ | 👑 | `{email,name,role}` | #4 | agents + Supabase invite | – |
| 9 | Update agent (branch!) | PATCH | `/agents/{id}` | ✅ | 👑 (self: limited) | `{branch?,phone?,…}` | #7 | agents | branch filter |
| 10 | Deactivate agent | DELETE | `/agents/{id}` | ✅ | 👑 | – | #7 | agents.is_active=false | – |
| 11 | Plan usage | GET | `/agents/plan-usage` | ✅ | any | – | #4 | – | – |
| 12 | Create lead | POST | `/leads` | ✅ | any | `LeadCreate` (below) | #4 | leads (status=new) | funnel, live_leads |
| 13 | List leads | GET | `/leads` | ✅ | scoped | `status,source,agent_id,search,limit≤200,offset` | #12 | – | – |
| 14 | Lead detail | GET | `/leads/{id}` | ✅ +owner-of-lead | scoped | – | #12 | – | – |
| 15 | Update lead | PATCH | `/leads/{id}` | ✅ +lead access | scoped | `LeadUpdate` (below) | #12 | leads | all funnel stages |
| 16 | Handover | POST | `/leads/{id}/handover` | ✅ +lead access | scoped | `{reason, agent_id?}` | #12 | leads | – |
| 17 | Restore AI | POST | `/leads/{id}/restore-ai` | ✅ +lead access | scoped | – | #12 | leads | – |
| 18 | Lead stats | GET | `/leads/stats` | ✅ | scoped | – | #12 | – | cross-check |
| 19 | Create viewing | POST | `/viewings` | ✅ +lead access | any | `ViewingCreate` (below) | #12 | viewings + follow_ups + lead→viewing_booked | todays_viewings, funnel |
| 20 | List viewings | GET | `/viewings` | ✅ | scoped | `agent_id,status,date_from,date_to,limit≤500` | #19 | – | – |
| 21 | Update viewing | PATCH | `/viewings/{id}` | ✅ +viewing access | scoped | `ViewingUpdate` | #19 | viewings | completed viewings |
| 22 | Viewing detail | GET | `/viewings/{id}` | ✅ +viewing access | scoped | – | #19 | – | – |
| 23 | Free slots | GET | `/viewings/available-slots` | ✅ | any | `date_from,date_to,agent_id?` | GCal 🔧 | – | – |
| 24 | Create contract | POST | `/contracts` | ✅ | any | `ContractCreate` (below) | – (lead optional) | contracts (draft) | closed_deals |
| 25 | List contracts | GET | `/contracts` | ✅ | scoped | – | #24 | – | – |
| 26 | Contract detail | GET | `/contracts/{id}` | ✅ | scoped | – | #24 | – | **returns e-sign tokens** |
| 27 | Generate PDF | POST | `/contracts/{id}/generate` | ✅ | scoped | – | #24 | status=generated, tokens, PDF | – |
| 28 | Send e-sign | POST | `/contracts/{id}/send-esign` | ✅ | scoped | – | #27 + WA 🔧 | status=sent | – |
| 29 | **Sign (public)** | POST | `/contracts/{id}/sign` | 🔑 sign token | landlord/tenant | `{token, role}` | #27 | signed_at / status=signed | closed_deals |
| 30 | Close contract | POST | `/contracts/{id}/close` | ✅ | scoped | `{cheque_image_url}` | #29 both | status=closed, lead→closed | **closed_deals** |
| 31 | Create owner (marketing DB) | POST | `/owners` | ✅ | any | `OwnerCreate` | #4 | owners | campaign targets |
| 32 | Bulk owners | POST | `/owners/bulk-upload|-csv|-xlsx` | ✅ | any | file/list ≤5000 | #4 | owners | – |
| 33 | Update owner | PATCH | `/owners/{id}` | ✅ | any | `OwnerUpdate` | #31 | owners | – |
| 34 | Delete owner | DELETE | `/owners/{id}` | ✅ | 👑 | – | #31 | owners | – |
| 35 | Create campaign | POST | `/call-campaigns` | ✅ | any + quota | `{campaign_name, group, from_time?, to_time?}` | #31 owners in group | call_campaigns | – |
| 36 | Run campaign | POST | `/call-campaigns/{id}/run` | ✅ | 👑 + active sub | – | #35, calling hours 🔧 | **calls rows** | ai_agent_stats |
| 37 | Pause campaign | POST | `/call-campaigns/{id}/pause` | ✅ | any | – | #36 | call_campaigns | – |
| 38 | Campaign calls | GET | `/call-campaigns/{id}/calls` | ✅ | scoped | – | #36 | – | – |
| 39 | Call logs | GET | `/calls` | ✅ | scoped | `campaign_id?,status?` | #36 | – | – |
| 40 | Vapi outcomes | POST | `/webhooks/vapi` | 🔑 `x-vapi-secret` | – | Vapi payload | #36 | calls/owners/campaigns | ai answer-side |
| 41 | Stripe webhook | POST | `/webhooks/stripe` | 🔑 Stripe-Signature | – | Stripe event | Stripe 🔧 | subscriptions/invoices | – |
| 42 | WhatsApp inbound | POST | `/webhooks/whatsapp` | 🔑 provider token/sig | – | provider payload | lead w/ phone 🔧 | conversations/leads | – (not Overview) |
| 43 | Send WA reply | POST | `/conversations/{lead_id}/send` | ✅ +lead access | any | `{message_body}` | #12, WA 🔧 | conversations | – |
| 44 | Change password | POST | `/auth/change-password` | ✅ session | any | `{current_password,new_password}` | #4 | auth | – |
| 45 | Update profile | PATCH | `/auth/profile` | ✅ | any | `{name?,phone?,branch?,…}` | #4 | agents | branch filter (self) |

**NOT AVAILABLE (verified absent):** branch CRUD API (branch = free-text `agents.branch`);
`DELETE /leads/{id}`; direct `POST /calls` (rows only via campaign run); admin APIs require a
`super_admin` role that no endpoint can mint (DB-only) — none needed for Overview.

---

## PART 3 — POSTMAN ENVIRONMENT SETUP (STEP 0)

Create environment `AndiOS-Local`. Variables:

| Variable | How obtained |
|---|---|
| `base_url` | e.g. `http://localhost:8000` (no trailing slash) |
| `email_owner` / `password_owner` | your chosen test credentials |
| `email_agent` / `password_agent` | second user (see Part 5.3) |
| `token_owner` | set automatically by login test script |
| `token_agent` | set automatically by agent login |
| `user_id` | `data.user_id` from `/auth/me` (auth UID) |
| `agency_id` | `data.agent.agency_id` from `/auth/me` |
| `owner_agent_id` | `data.agent.id` from `/auth/me` |
| `agent_id` | id of the 2nd agent (from `GET /agents`) |
| `branch_1` | literal branch NAME, e.g. `Dubai Marina` |
| `branch_2` | e.g. `Business Bay` |
| `lead_id_1..lead_id_6` | `data.id` from each `POST /leads` (script sets it) |
| `viewing_id` | `data.id` from `POST /viewings` |
| `contract_id` | `data.id` from `POST /contracts` |
| `landlord_token` / `tenant_token` | from `GET /contracts/{id}` (fields `landlord_sign_token` / `tenant_sign_token`) |
| `owner_row_id` | `data.id` from `POST /owners` |
| `campaign_id` | `data.id` from `POST /call-campaigns` |

> ⚠️ `branch_id` on `/dashboard/overview` is **the branch NAME string** (matched against
> `agents.branch`), not a UUID. There is no branch table.

Recommended login **Tests script** (stores everything real):

```js
const d = pm.response.json().data;
pm.environment.set("token_owner", d.access_token);
pm.environment.set("user_id", d.user.id);
if (d.user?.agent) {
  pm.environment.set("owner_agent_id", d.user.agent.id);
  pm.environment.set("agency_id", d.user.agent.agency_id);
}
```

---

## PART 4 — AUTHENTICATION SETUP

### 4.1 Register (creates Agency + Owner in one call) ✅
```
POST {{base_url}}/auth/register
{ "agency_name": "QA Test Agency",
  "full_name": "Test Owner",
  "email": "{{email_owner}}",
  "password": "TestPass123!" }
```
→ 201 `{data:{user_id, agency:{id,name,slug}, agent}}`. Supabase sends a 6-digit OTP email.
*If SMTP is not configured the fallback auto-confirms the account (dev behavior).*

### 4.2 Verify OTP ✅
```
POST {{base_url}}/auth/verify-otp
{ "email": "{{email_owner}}", "token": "123456", "type": "signup" }
```
→ 200 with `access_token` (24h JWT). `type` must be `signup` here, `recovery` for resets.

### 4.3 Login ✅
```
POST {{base_url}}/auth/login
{ "email": "{{email_owner}}", "password": "{{password_owner}}" }
```
→ 200 `data.access_token` (+ `refresh_token`, `supabase_token`, `user.agent`). Run the
Part-3 script here. Negative: wrong password → 401; unknown email → identical 401.

### 4.4 Authorization header
Collection-level: **Bearer Token** `{{token_owner}}` (per-request override with
`{{token_agent}}`).

### 4.5 Verify identity/agency ✅
```
GET {{base_url}}/auth/me
```
→ `data.user_id`, `data.email`, `data.agent{id,name,role,agency_id,branch,
agencies{id,name,subscription_plan}}`. Confirm `agency_id` is the agency you seeded.

### 4.6 Owner vs agent behavior (server-enforced)
- Owner/manager: agency-wide data; can create agents/campaigns; run campaigns.
- Agent: leads/viewings auto-narrowed to `assigned_agent_id`/`agent_id`; **403** on
  agent-create, campaign-run, owner-delete, role changes, billing GETs.

### 4.7 Agent token (two supported paths)
- **Path A (Postman-only friendly):** create the agents row via `POST /agents` (👑), then
  🛡 in **Supabase Dashboard → Authentication → Add user** create the auth user with the
  **same email** and a password. `POST /auth/login` then works — the backend hydrates
  agency/role from the `agents` row by email.
- **Path B:** `POST /agents/invite` sends a Supabase invite whose landing page
  (`/auth/accept-invite`) is **not built in the FE yet** — fine for backend testing only
  if you complete the invite via Supabase Dashboard.

---

## PART 5 — AGENCY / OWNER / AGENT / BRANCH

| Entity | Created by | Notes |
|---|---|---|
| Agency | `POST /auth/register` only | ✅ no separate API. Admin alt: `POST /admin/agencies` (🛡 super_admin, DB-only role) |
| Owner | same call (role=owner agent) | `agents.role="owner"` |
| Agent row | `POST /agents` (👑) | `AgentCreate {name, email, phone?, role:agent|senior_agent|manager|owner, calendar_id?, whatsapp_number?, branch?}` |
| Agent auth user | 🛡 Supabase Dashboard (or invite + FE page) | **NO public API sets an agent password** |
| Branch | **No table/API** — free-text `agents.branch` | Set via `POST /agents`, `PATCH /agents/{id}`, or `PATCH /auth/profile` (self) |

Verify: `GET /agents` → rows include `branch`; `GET /agents/plan-usage` → plan counters.

---

## PART 6 — MINIMUM REALISTIC DATASET (code-derived)

| Entity | Target | Why (drives) |
|---|---|---|
| Agency/Owner | 1 | identity |
| Branches | 2 distinct `branch` strings | branch filter |
| Agents | 3 (1 owner + 2 agents; 2 in branch_1, 1 in branch_2) | deals_by_agent, filters |
| Leads | 6 (see Part 7 mix) | funnel/live_leads/close_rate |
| Viewings | 4: **1 today (scheduled)**, 2 completed (1 today-dated completed also fine), 1 future | todays_viewings, viewing→closing |
| Closed leads | ≥1 (`closed`) | close_rate, closings funnel |
| Contracts | 2 → 1 `closed`, 1 `signed` | closed_deals (count/fees/rent/by-agent) |
| Owners | 3 in one `property_group` | campaign prerequisite |
| Campaign run | 1 (during calling hours) | `ai_agent_stats.outbound_dials` |
| Calls with outcomes | requires Vapi 🔧 (Part 10) | answer_rate/conversations/listings |

---

## PART 7 — LEAD LIFECYCLE (actual statuses)

`new → qualifying → viewing_booked → viewing_done → negotiating → closed`
(side states: `lost`, `handover`). `viewing_booked` is set **automatically** by viewing
creation; everything else is `PATCH /leads/{id} {"status": "…"}`.

`POST /leads` body (status always starts `new`):
```json
{ "name": "Jane Doe", "phone": "+971501110011", "email": "jane@x.com",
  "source": "bayut", "property_ref": "MRN-001",
  "property_address": "Marina Gate 2, Dubai Marina",
  "bedrooms": 2, "budget_min": 90000, "budget_max": 120000,
  "location_pref": "Dubai Marina", "purpose": "rent",
  "assigned_agent_id": "{{agent_id}}" }
```
`source` enum: `property_finder | bayut | dubizzle | direct | referral`.
`PATCH` body fields: `name,email,status,assigned_agent_id,bedrooms,budget_min,budget_max,
location_pref,is_ai_handling,notes`.

**Recommended seed sequence**

| Lead | Path | Dashboard effect |
|---|---|---|
| L1 | new → qualifying → viewing_booked(auto) → viewing_done → negotiating → **closed** | closings funnel, close_rate, (contract links to it) |
| L2 | new → qualifying → viewing_booked(auto) → viewing_done | viewing_to_close denominator |
| L3 | new → qualifying → viewing_booked(auto) | funnel viewings |
| L4 | new → qualifying | funnel |
| L5 | new (assigned agent 2) | live_leads variety |
| L6 | new → **handover** (`/handover {reason}`) then `/restore-ai` | live_leads |

Assign across **both branches’ agents** so the branch filter shows different numbers.
All `PATCH`/detail calls as an **agent token** must target that agent’s own leads (403/404 otherwise).

---

## PART 8 — VIEWING SETUP

Create (auto: lead→`viewing_booked`, reminders, optional GCal/WA — **works without both**):
```
POST {{base_url}}/viewings
{ "lead_id": "{{lead_id_1}}", "agent_id": "{{agent_id}}",
  "property_address": "Marina Gate 2, Dubai Marina",
  "viewing_datetime": "2026-08-26T10:00:00Z",
  "duration_minutes": 60 }
```
→ 201 `data{id, status:"scheduled", viewing_datetime, google_event_id:null,…}`.
Save `viewing_id`.

**Timezone rule (important):** the Overview “Today’s viewings” bucket matches
`viewing_datetime` starting with the **UTC date string of the server**. Schedule
“today’s” viewing with today’s **UTC** date (e.g. `…T10:00:00Z` on the current UTC day).
A Dubai-evening slot may roll to the next UTC day — if Today’s list looks empty, that’s why.

| Goal | How |
|---|---|
| Today’s viewing (appears in `todays_viewings`) | create with today UTC datetime (or PATCH datetime to today) |
| Future viewing | future ISO datetime (excluded from Today, counted in funnel if completed later) |
| **Completed viewing** | `PATCH /viewings/{id} {"status":"completed"}` — **or** create with `"status":"completed"` directly |
| Attach feedback | `PATCH {"feedback_received":"Loved the layout"}` (or at create) |
| Reassign agent | `PATCH {"agent_id":"{{agent_id}}"}` |

Dashboard fields per row (verified): `lead_name`, `lead_source` (joined from lead),
`property_address`, `viewing_datetime`, `agent_name`, plus raw viewing fields.

---

## PART 9 — CONTRACT / CLOSED-DEAL SETUP

Dashboard counts contracts with status **`signed`, `active`, or `closed`**
(`draft`/`generated`/`sent`/`cancelled` are ignored). `closed` is the terminal state after
the fee cheque. Minimum Postman-only path (no WhatsApp needed):

```
1) POST /contracts
   { "lead_id": "{{lead_id_1}}", "property_unit": "Apt 101",
     "area_community": "Downtown", "start_date": "2026-09-01",
     "end_date": "2027-08-31", "rent_amount": 120000,
     "security_deposit": 10000, "number_of_cheques": 4,
     "owner_name": "Landlord X", "tenant_name": "{{lead name}}" }
   → 201 data.id = {{contract_id}} (status: draft)

2) POST /contracts/{{contract_id}}/generate
   → 200 {data:{url}} (status: generated, 7-day signed PDF URL, tokens minted)

3) GET /contracts/{{contract_id}}
   → copy data.landlord_sign_token / tenant_sign_token → env vars

4) POST /contracts/{{contract_id}}/sign        (PUBLIC — no Bearer)
   { "token": "{{landlord_token}}", "role": "landlord" }   → 200
   repeat with tenant token/role                            → status: signed

5) POST /contracts/{{contract_id}}/close       (Bearer)
   { "cheque_image_url": "https://example.com/cheque.jpg" }
   → 200 (status: closed; associated lead → closed)
```
Second contract: stop after step 4 (`signed`) to exercise the non-closed counted state.
`cheque_image_url` is stored as-is (any https string). `rent_amount` drives
`total_rent_value`; fees = 5 % of rent; `deals_by_agent` groups by `agent_id`
(set `agent_id` implicitly? — contracts take `created_by` from your token’s `agent_id`;
to attribute deals to specific agents, create contracts with those agents’ tokens).

---

## PART 10 — AI / CALLING STATISTICS (`ai_agent_stats`)

Source: **`calls` table only** (`agency_id`, `status_value`, per row).

| Field | Computed from calls rows |
|---|---|
| `outbound_dials` | row count (period-filtered) |
| `answer_rate` | % rows with `status_value` ∈ {listing-won, callback-booked, interested, not-interested} |
| `conversations` | same answered count |
| `calls_to_listings` | listing-won ÷ answered |
| `new_listings_won` | listing-won count |

**Creation path:** `POST /owners` (targets) → `POST /call-campaigns {campaign_name,
group:"<property_group>", from_time, to_time}` → `POST /call-campaigns/{id}/run` (👑).
Run inserts `calls` rows per dialed owner.

**Constraints (all code-enforced):**
- 👑 only for run; subscription must be active (403 otherwise); monthly campaign quota enforced.
- Calling hours: **09:00–18:00 Asia/Dubai, Friday excluded** — outside the window the run
  defers and creates **no rows**.
- With **no `VAPI_API_KEY`**: dials are recorded as `Failed` → `outbound_dials > 0` but
  answer-side stats stay 0 %. **Postman alone cannot produce answered outcomes.**
- With a real Vapi key: real calls are placed (costs money) and outcomes arrive via
  `POST /webhooks/vapi` (header `x-vapi-secret`) which fills `status_value`,
  transcript, recording → full stats.
- Manual alternative: insert/update `calls` rows in 🛡 Supabase (clearly a DB step — the
  system itself never fabricates these values).

---

## PART 11 — WHATSAPP AUTOMATION (optional; **not required for Overview**)

- Providers: `360dialog` (shared-secret header `X-Webhook-Token` / `?token=`) or `Twilio`
  (`X-Twilio-Signature` validated). Inbound: `POST /webhooks/whatsapp`; verification:
  `GET /webhooks/whatsapp`.
- Inbound **only matches existing leads** (normalized full-number match first, unique-suffix
  fallback, ambiguous → safely skipped). New numbers are ignored.
- Effects: conversations rows, AI qualification reply (OpenAI), lead field updates,
  handover detection. **Zero effect on Overview numbers** — treat as a separate E2E test:
  1. Configure provider creds in backend env (fail-closed in production without them).
  2. Create a lead with a real/test phone.
  3. POST a signed/authorized inbound payload “from” that phone.
  4. Expect 200; verify `conversations` rows + AI reply row; lead qualification fields update.

---

## PART 12 — FILTER TESTING (`GET /dashboard/overview`)

| # | Query | Expect to change |
|---|---|---|
| 1 | *(none)* | everything, all-time |
| 2 | `timeframe=today` | counts → today only; `todays_viewings` unaffected (always today) |
| 3 | `timeframe=last_7_days` | last 7 days incl. previous-period trends |
| 4 | `timeframe=this_month` | current calendar month |
| 5 | `timeframe=last_30_days` | rolling 30 |
| 6 | `timeframe=this_quarter` | quarter; close_rate subtext says “overall, this qtr” |
| 7 | `start_date=2026-08-01&end_date=2026-08-31` | custom window (trends = preceding equal window) |
| 8 | `agent_id={{agent_id}}` | owner-only narrowing; agent token ignores and self-scopes |
| 9 | `branch_id={{branch_1}}` | **branch NAME string**; narrows via agents in that branch |
| 10 | `platform=bayut` | substring match on lead source |
| 11 | 8+2 | intersection |
| 12 | 9+2 | intersection |
| 13 | 8+10 | intersection |
| 14 | 8+9+10+2 | full intersection |
| 15 | `branch_id=NonExistent` | 200 with all-zero/empty (sentinel filter) |
| 16 | agent token + `branch_id` | agent self-scope wins (branch ignored for non-owners) |

---

## PART 13 — DASHBOARD FIELD → DATA MAP

| UI field | Response path | DB source | Minimum data | Seed request |
|---|---|---|---|---|
| Avg response time | `metrics.avg_response_time.value` | *(not yet computed)* | – | **null today** — render placeholder |
| “AI handles N%” | `…avg_response_time.subtext` | leads.is_ai_handling | ≥1 lead | #12 |
| Lead→viewing % | `metrics.lead_to_viewing.value` | leads(status viewing_booked+) ÷ leads | ≥1 viewing_booked lead | #19 auto |
| “X of Y” | `…lead_to_viewing.subtext` | same | – | – |
| Viewing→closing % | `metrics.viewing_to_close.value` | closed leads ÷ completed viewings | ≥1 completed viewing + 1 closed lead | #21 + #15 |
| Close rate | `metrics.close_rate.value` | closed leads ÷ leads | ≥1 closed lead | #15 |
| Trends ↑↓ | `metrics.*.trend/trend_value` | previous equal period | data in two consecutive windows | date your seeds across a boundary |
| Closed deals count | `closed_deals.count` | contracts signed/active/closed | ≥1 | #24+#27+#29(+30) |
| Agency fees | `closed_deals.agency_fees_earned` | Σ rent×5 % | rent_amount on those contracts | #24 body |
| Total rent value | `closed_deals.total_rent_value` | Σ rent | – | – |
| Per-agent deals | `closed_deals.deals_by_agent[]` | contracts grouped by agent_id | contracts under ≥2 agents | create with different agent tokens |
| Live leads rows | `live_leads[]` | leads not closed/lost (top 5, newest) | ≥1 active lead | #12 |
| Lead name / “2BR - Area” | `live_leads[].name` / `bedrooms`+`property_address` | leads | fill bedrooms+address | #12 body |
| Platform dot | `live_leads[].source` | leads.source | enum value | #12 body |
| Status badge | `live_leads[].status` | leads.status | lifecycle | #15 |
| Agent column | `live_leads[].agent_name` | agents join | assigned_agent_id set | #12/#15 |
| Age | `live_leads[].created_at/updated_at` | leads | – | – |
| Today’s viewings rows | `todays_viewings[]` | viewings dated today (UTC) | ≥1 today viewing | #19 |
| Client name | `todays_viewings[].lead_name` | leads via join | lead exists | auto |
| Platform tag | `todays_viewings[].lead_source` | leads via join | lead.source | #12 |
| Property / time / agent | `.property_address` / `.viewing_datetime` / `.agent_name` | viewings/agents | – | #19 |
| Funnel counts | `funnel[].count` | leads/viewings | – | #12/#19/#15 |
| Funnel %s | `funnel[].percentage` | derived | – | – |
| AI: dials | `ai_agent_stats.outbound_dials` | calls rows | campaign run | #36 |
| AI: answer/conversations | `.answer_rate/.conversations` | answered outcomes | Vapi 🔧 or 🛡 DB | Part 10 |
| AI: calls→listings / listings won | `.calls_to_listings/.new_listings_won` | listing-won outcomes | same | Part 10 |

---

## PART 14 — “ALL ZEROS” TROUBLESHOOTING (ordered)

1. **Token** — `GET /auth/me` with the same token. Confirmed 200? Whose `email`?
2. **User** — `data.user_id` matches a Supabase auth user?
3. **Agency ID** — `data.agent.agency_id` = the agency you seeded? *(Real case seen: token
   for empty agency `847ac5dd…` while data lived under `d8798ea7…`.)*
4. **Agent ID** — `data.agent.id` non-null?
5. **Role** — `data.agent.role` (agent ⇒ self-scoped data only).
6. **Agency records** — 🛡 `select count(*) from leads where agency_id='…'`
7. **Lead records** — same query; also `GET /leads` (scoped view).
8. **Assignment** — `select assigned_agent_id, count(*) … group by 1` (NULLs never appear
   under an agent token).
9. **Statuses** — `group by status` (all-`new` ⇒ funnel viewings/closings stay 0).
10. **Viewings** — count for agency.
11. **Viewing dates** — `select viewing_datetime…` — “today” = **UTC date prefix**.
12. **Contracts** — count (0 ⇒ closed_deals 0 is *correct*).
13. **Contract statuses** — `group by status` (`draft` never counts; needs signed/active/closed).
14. **Calls** — `select count(*) from calls where agency_id='…'`.
15. **Campaign** — status `Running`? created within quota? run returned 200?
16. **Integrations** — Vapi key set? calling-hours window? (deferred runs create no rows).
17. **Filters** — re-send with **no** query params; then re-add one at a time (Part 12 #15).
18. **Timezone** — UTC-vs-Dubai date mismatch for “today” items (Part 8 rule).

---

## PART 15 — POSTMAN MASTER EXECUTION ORDER

```
01 SET env (base_url, emails/passwords, branch names)
02 POST /auth/register                      (owner+agency)          → 201
03 POST /auth/verify-otp                    (signup OTP)            → 200 tokens
04 POST /auth/login                         → 200; script stores {{token_owner}},
                                              {{owner_agent_id}}, {{agency_id}}, {{user_id}}
05 GET  /auth/me                            → verify agency/role
06 POST /agents  (agent A, branch={{branch_1}})                     → 201 {{agent_id}}
07 POST /agents  (agent B, branch={{branch_2}})                     → 201
08 🛡 Supabase: create auth users for A/B emails (+password)         → enables logins
09 POST /auth/login (agent A)               → stores {{token_agent}}
10 GET  /agents?limit=100                   → verify rows/branches
11 POST /leads ×6 (mixed sources, agents, bedrooms/areas)           → 201 (save ids)
12 PATCH leads per Part-7 table (statuses)                          → 200 each
13 POST /viewings (today, lead L1, agent A)                          → 201 {{viewing_id}}
14 POST /viewings (future, lead L3)                                  → 201
15 PATCH /viewings/{{viewing_id}} {"status":"completed"}             → 200
16 POST /contracts (lead L1, rent 120000)                            → 201 {{contract_id}}
17 POST /contracts/{id}/generate                                     → 200 url
18 GET  /contracts/{id}                      → copy sign tokens
19 POST /contracts/{id}/sign  (landlord, then tenant) — no Bearer → 200 ×2
20 POST /contracts/{id}/close {"cheque_image_url": …}                → 200 (lead→closed)
21 POST /contracts #2 → generate → sign ×2 (leave signed)            → counted state
22 POST /owners ×3 (same property_group "Marina Owners")             → 201
23 POST /call-campaigns {"campaign_name":"QA","group":"Marina Owners"} → 201 {{campaign_id}}
24 POST /call-campaigns/{id}/run  (👑 token, within 9–18 Dubai, Mon–Sat)
                                              → 200; calls rows created
25 GET  /dashboard/overview (no filters)     → funnel/live_leads/closed_deals non-zero
26 GET  …?timeframe=this_month | last_7_days | custom range
27 GET  …?agent_id=… ; ?branch_id={{branch_1}} ; ?platform=bayut ; combos
28 GET  … with {{token_agent}}               → self-scoped numbers
29 GET  … with branch_id=NonExistent         → all-zero (expected)
30 Cross-tenant probe: foreign token on /leads/{id}, /contracts/{id}/generate
                                            → 404 (isolation intact)
31 Re-run 25 and diff against 26–30 snapshots
```

---

## PART 16 — “READY FOR FRONTEND” CHECKLIST

```
[ ] Register → OTP → login works; tokens stored
[ ] /auth/me returns correct agency_id + role
[ ] Owner token: agency-wide data visible
[ ] Agent token: self-scoped only (403s on 👑 actions)
[ ] ≥2 agents with distinct branch values
[ ] ≥6 leads across statuses incl. closed
[ ] Every lead has assigned_agent_id (or intentionally NULL)
[ ] ≥1 viewing dated TODAY (UTC)
[ ] ≥1 completed viewing
[ ] ≥2 contracts (1 closed, 1 signed)
[ ] Closed contract's lead flipped to closed
[ ] calls rows exist (campaign run) — dials > 0
[ ] Answer-side AI stats: real (Vapi) or consciously zero
[ ] /dashboard/overview: funnel + live_leads + closed_deals non-zero
[ ] todays_viewings row exposes lead_name + lead_source + agent_name
[ ] Filters: timeframe / agent / branch / platform / combos verified
[ ] branch filter uses branch NAME string
[ ] Foreign-token probes → 404/empty (isolation)
[ ] avg_response_time handled as null (placeholder in UI later)
[ ] No fabricated values anywhere in responses
```

---

## APPENDIX — ARCHITECTURE / DATA-FLOW DIAGRAM

```
Supabase Auth ──▶ JWT(sub,email) ──▶ verify_token ──▶ agents(email) ──▶ agency_id/role/agent_id
                                                                          │
   agencies ◀── register ─────────────────────────────────────────────────┘
      │ agency_id on every tenant row
      ├─ agents(branch) ─────────────▶ branch filter (overview)
      ├─ leads(status, assigned_agent_id, source, created_at)
      │     ▲ status=viewing_booked         │ assigned_agent_id
      │     │                               ▼
      ├─ viewings(lead_id FK, agent_id, viewing_datetime, status)
      │     │  POST auto: lead→viewing_booked · follow_ups · GCal? · WhatsApp?
      │     └─ completed ──▶ viewing_to_close denominator
      ├─ contracts(agent_id, status: draft→generated→sent→signed→closed, rent_amount)
      │     └─ signed/active/closed ──▶ closed_deals (count/fees 5%/rent/by-agent)
      ├─ calls(agency_id, status_value, call_time) ◀── campaign run (Vapi) ──▶ ai_agent_stats
      └─ conversations / follow_ups / documents / invoices   (not Overview sources)

GET /dashboard/overview
  = f(agency_id, role, agent_id?, branch→agent_ids?, timeframe|dates?, platform?)
  over: leads + viewings(+leads join) + contracts + calls
```

---

*End of guide. Generated from code inspection only; every endpoint/field above exists in the
current backend. Items marked 🔧/🛡/❌ are exactly as implemented — nothing invented.*
