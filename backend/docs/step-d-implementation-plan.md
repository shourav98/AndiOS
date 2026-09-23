# Step D Implementation Plan — WhatsApp 24h Window Enforcement

> Branch: `byon-meta-esu`
> Rules: No migrations/backfill/deploy. No commits. No pushes. No printing of secrets.
> Status: **PLAN ONLY — no implementation code.**

---

## Gap 1 — Window-Aware Send Router

**Size: Medium**

### Problem

No function in the current send path checks `is_within_24h_window` before composing
or dispatching an outbound WhatsApp message. All callers call `send_whatsapp_for_agency()`
directly with a raw free-form text string. When the 24h customer-service window is closed,
Meta returns HTTP 400 (error 131047). The error is caught at the adapter level and returned
as `SendResult(success=False)` — the message is silently dropped.

### Proposed Function

Add `send_whatsapp_smart()` to `services/whatsapp_service.py`.

```python
async def send_whatsapp_smart(
    agency_id: str,
    lead_id: str,
    body: str,
    agent_id: str | None = None,
    template_name: str = "andios_lead_first_contact",
    template_params: list[str] | None = None,
) -> dict:
    """
    Window-aware WhatsApp send for outbound AI messages to a lead.

    Resolution:
      1. Look up lead.last_inbound_at from `leads` where id = lead_id.
      2. Call is_within_24h_window(last_inbound_at).
         - True  => send free-form text (body) via send_whatsapp_for_agency().
         - False => attempt template send:
             a. Look up lead's WABA via provider_factory for agency/agent.
             b. Query whatsapp_templates for an APPROVED template with
                waba_id = account.external_account_id AND name = template_name.
             c. If APPROVED template found => call provider.send_message()
                with template_name + template_params.
             d. If NO approved template => call _queue_and_notify_agent()
                (see Gap 2). Return {"status": "queued"}.
    """
```

#### DB Query Required

```sql
-- Step 1: fetch last_inbound_at
SELECT last_inbound_at FROM leads WHERE id = :lead_id LIMIT 1;

-- Step 2b: fetch approved template
SELECT id, name, components
FROM whatsapp_templates
WHERE waba_id = :waba_id
  AND name = :template_name
  AND status = 'APPROVED'
LIMIT 1;
```

The `waba_id` comes from `account.external_account_id` on the
`CommunicationAccount` returned by `get_whatsapp_provider_for_agency()`.

#### Current Callers That Must Switch

Only callers sending **TO A LEAD** (not to agents/owners) should switch to
`send_whatsapp_smart()`. Callers that already know the lead context have the
`lead_id` available:

| File | Line | Description | Switch? |
|------|------|-------------|---------|
| `routers/webhooks.py` | 612 | PF webhook — first-contact greeting to new lead | **Yes** |
| `routers/webhooks.py` | 794 | Bayut webhook — first-contact greeting to new lead | **Yes** |
| `routers/webhooks.py` | 938 | Dubizzle webhook — first-contact greeting to new lead | **Yes** |
| `routers/webhooks.py` | 1320 | `whatsapp_inbound` — AI qualification reply to lead | **Yes** |
| `routers/webhooks.py` | 1257 | `whatsapp_inbound` — handover notice to lead | **No** — operational message, should always attempt delivery regardless of window |
| `routers/webhooks.py` | 1287 | `whatsapp_inbound` — handover alert to **agent** | **No** — target is an agent, not a lead |
| `services/scheduler.py` | 89 | Scheduler — 24h viewing reminder to lead | **Yes** — already scheduled near viewing, lead likely in-window; still worth checking |
| `services/scheduler.py` | 121 | Scheduler — 2h viewing reminder to lead | **Yes** — same rationale |
| `services/scheduler.py` | 147 | Scheduler — post-viewing follow-up to lead | **Yes** — occurs 24h after viewing, lead likely outside window |
| `services/scheduler.py` | 176 | Scheduler — feedback follow-up to lead | **Yes** — 48h after viewing, almost certainly outside window |
| `services/scheduler.py` | 306 | Scheduler — weekly re-engagement to lead | **Yes** — 7-day-old leads are always outside window |
| `services/scheduler.py` | 395 | Scheduler — weekly property report to **owner** | **No** — target is an owner/landlord, not a lead |

> **Note on scheduler callers**: The scheduler job functions currently receive
> `viewing_id` and look up the lead. They do not have `lead_id` in scope at the
> call site. The `lead_id` from `viewing.leads.id` must be threaded through when
> switching these callers. This adds a small amount of additional refactoring
> to each scheduler job function.

### Decisions Required from You

1. **First-contact template params for portal webhooks**: The `andios_lead_first_contact`
   template body is:
   > *"Hello {{1}}! Thank you for your inquiry about {{2}} on Property Finder. How can we assist you with details or arranging a viewing?"*

   When switching the portal webhook greeting to use this template:
   - `{{1}}` = `name.split()[0]` (first name)
   - `{{2}}` = `property_ref` or `"a property"` if none

   **Decision needed**: Is this copy acceptable for **all three portals** (PF, Bayut, Dubizzle)
   or should each portal have its own template? Meta template names and copy are **immutable
   after approval submission** — changing them requires creating a new template and waiting
   for re-approval.

2. **Template for AI qualification replies**: The ongoing conversation reply (`whatsapp_inbound`
   line 1320) is a dynamically generated AI response. There is no standard template for
   conversational replies — by design these are free-form. The safe rule is: if the 24h window
   is closed, the AI should NOT attempt to respond with a free-form message. Options:
   - a. Queue the AI reply (same mechanism as Gap 2) until the next time the lead messages in.
   - b. Send an approved "we'll be in touch" template instead of the full AI reply.
   - c. Skip the AI reply silently and wait for the next inbound message to reopen the window.
   
   **Decision needed**: Which behavior should an AI qualification response use when outside
   the 24h window?

3. **Scheduler reminders — template or suppress**: Viewing reminders (24h, 2h) are
   transactional and time-sensitive. They are almost always sent to leads that booked
   via the AI (who messaged in), so `last_inbound_at` is likely recent. However the
   post-viewing follow-up and re-engagement messages are near-certainly outside the window.
   
   **Decision needed**: For scheduler-driven messages that are outside the 24h window,
   should the system use a template or suppress the message entirely?

---

## Gap 2 — No-Template Queue and Agent Notification

**Size: Medium**

### Problem

The docstring in `services/communication/template_service.py` says:
*"If no approved template exists: Queue outbound message and notify agent (do NOT send free-form)."*
There is no implementation of this queue or notification. There is no database table for
outbound queued messages. `get_approved_template_or_none()` exists and returns `None`
when no approved template is found, but nothing acts on that `None`.

### Proposed Schema — `whatsapp_outbound_queue`

```sql
CREATE TABLE whatsapp_outbound_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agency_id       UUID NOT NULL REFERENCES agencies(id),
    agent_id        UUID REFERENCES agents(id),
    lead_id         UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    to_phone        TEXT NOT NULL,
    body            TEXT NOT NULL,           -- original free-form message body
    template_name   TEXT,                    -- chosen template name (if known)
    template_params JSONB DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'pending',
                                             -- 'pending' | 'sent' | 'expired'
    reason          TEXT NOT NULL DEFAULT 'no_template',
                                             -- why it was queued
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    send_after      TIMESTAMPTZ,             -- optional: earliest send time
    sent_at         TIMESTAMPTZ
);

CREATE INDEX idx_wa_queue_lead ON whatsapp_outbound_queue (lead_id);
CREATE INDEX idx_wa_queue_agency_status ON whatsapp_outbound_queue (agency_id, status);
```

> **Note**: This migration should be in a new `schema_vXX_whatsapp_queue.sql` file
> and applied by you manually. No backfill needed — no existing data qualifies.

### Proposed "Notify Agent" Mechanism

The codebase already has one agent-notification mechanism: the **handover alert**
in `routers/webhooks.py` (lines 1270-1290):

1. Looks up `lead.assigned_agent_id`.
2. Queries `agents` for `name, phone, whatsapp_number, email`.
3. Composes a structured text message and sends it via `send_whatsapp_for_agency()`
   to the agent's personal WhatsApp number.

**Proposal**: Reuse this exact pattern for the no-template notification.
Extract the core look-up-and-notify logic into a shared helper function:

```python
# New shared helper — location TBD (whatsapp_service.py or notification_service.py)
async def _notify_agent_of_blocked_message(
    agency_id: str,
    lead: dict,
    blocked_body: str,
    reason: str,
) -> None:
    """
    Alert the lead's assigned agent that an outbound message was blocked
    and queued (e.g., outside 24h window with no approved template).
    Uses same channel as handover alert.
    """
```

Proposed alert body:

```
📬 *Queued Message Alert*

A message to lead *{lead_name}* could not be sent.
📋 Reason: {reason}
📱 Lead phone: {lead_phone}
💬 Queued message: _{body[:200]}_

No approved WhatsApp template exists for your WABA.
Please send the message manually or connect approved templates
in your account settings.
```

**No new notification mechanism is needed.** This deliberately reuses the existing
WhatsApp-to-agent pattern rather than email or push.

### Decisions Required from You

1. **Who to notify if the lead has no `assigned_agent_id`?** Fallback options:
   - Notify the agency's default admin agent (query `agents` where `agency_id = X AND is_admin = true`).
   - Log a warning and do not notify (message stays in queue but nobody is alerted).
   - **Decision needed**.

2. **Queue TTL / expiry**: How long should a queued message stay `pending` before
   it is marked `expired`? Once the lead messages back in (reopening the 24h window),
   should the system automatically drain the queue and send queued messages?
   **Decision needed**.

3. **Dashboard visibility**: Should queued messages be visible to the agent in the
   dashboard? This would require a new API endpoint reading `whatsapp_outbound_queue`.
   **Decision needed** (scope).

---

## Gap 3 — Inbound wa.me Lead Creation and Deduplication

**Size: Large**

### Problem

`_find_lead_by_sender_phone()` is called in `whatsapp_inbound` for every inbound message.
If no matching lead exists, the message is silently dropped (`continue`). There is no path
for creating a new lead from an inbound WhatsApp message.

A buyer who clicks a Property Finder wa.me link (format:
`https://api.whatsapp.com/send?phone=971XXXXXXXXX&text=I+am+interested+in+REF-12345`)
sends their first inbound WhatsApp message to the agency number. That number may or may
not match an existing lead in the DB. If not, the message is dropped — no lead is created,
no AI greeting is triggered, no deduplication against a pre-existing PF webhook lead is run.

### PF wa.me Property Ref Format

Based on the PF webhook payload parsing in `routers/webhooks.py` line 519:

```python
property_ref = str(lead_data.get("property_ref") or lead_data.get("listing_id") or lead_data.get("reference_no", ""))
```

PF property references follow the format `REF-NNNNNN` (e.g. `REF-789012`) or
a numeric string. The wa.me pre-fill text is typically:

```
I am interested in REF-789012
```

or

```
Hi, I saw your listing REF-789012 on Property Finder
```

**Proposed regex to extract property_ref from wa.me inbound body:**

```python
import re
_PF_REF_PATTERN = re.compile(r'\bREF[-\s]?(\d+)\b', re.IGNORECASE)

def _extract_property_ref_from_wame_body(body: str) -> str | None:
    m = _PF_REF_PATTERN.search(body)
    if m:
        return f"REF-{m.group(1)}"
    return None
```

### Proposed Logic (within `whatsapp_inbound`, after the current lead lookup)

```
# Current code — message is dropped here:
lead, match_reason = _find_lead_by_sender_phone(sb, from_phone, agency_id=resolved_agency_id, ...)
if lead is None:
    continue  # <= proposed insertion point BEFORE this continue

# Proposed addition:
if lead is None and resolved_agency_id:
    property_ref = _extract_property_ref_from_wame_body(message_body)
    if property_ref:
        # 1. Dedup check: same phone + same property already in leads?
        already_exists = await is_duplicate_lead_for_property(from_phone, property_ref)
        if already_exists:
            existing = await get_existing_lead_by_phone(from_phone)
            if existing:
                lead = existing  # fall through to normal inbound processing

        if lead is None:
            # 2. Create a new lead from inbound wa.me
            new_lead_row = sb.table("leads").insert({
                "name": "WhatsApp Enquiry",   # placeholder — AI will ask for name
                "phone": from_phone,
                "source": "whatsapp_wame",
                "property_ref": property_ref,
                "status": "new",
                "ai_stage": "greeting",
                "is_ai_handling": True,
                "agency_id": resolved_agency_id,
                "assigned_agent_id": resolved_agent_id,
            }).execute()
            lead = new_lead_row.data[0]

if lead is None:
    continue  # genuinely unknown sender with no property_ref
```

### Deduplication Wiring Status After This Change

| Handler | Wired? | Status |
|---------|--------|--------|
| `property_finder_webhook` | Yes | Already wired at line 551 |
| `bayut_webhook` | Unknown | Needs verification |
| `dubizzle_webhook` | Unknown | Needs verification |
| `whatsapp_inbound` (new) | No | **New wiring required** |

### Decisions Required from You

1. **Lead placeholder name for wa.me leads**: Inbound WhatsApp senders have no name.
   Options: `"WhatsApp Enquiry"` vs leaving `name = NULL` and having the AI ask.
   **Decision needed**.

2. **property_ref required or optional**: Should lead creation be gated on identifying
   a property_ref from the message, or should any inbound from an unknown number
   create a lead (phone-only)? Current proposal: **require property_ref** to avoid
   junk leads from spam/wrong-number messages. **Decision needed**.

3. **AI greeting behavior**: After creating the lead, the current inbound flow proceeds
   to `qualify_and_respond()` which generates a contextual AI reply. This reply will be
   free-form. Since this is the very first message from this lead (and thus `last_inbound_at`
   is NOW — within the 24h window), the 24h window IS open. A free-form AI reply is
   therefore allowed. **No decision needed on this point unless you want a dedicated
   first-contact template instead.**

4. **property_ref format confirmation**: Confirm the exact format (e.g. `REF-NNNNNN`)
   used in your live listing inventory on Property Finder. This determines the regex.
   **Decision needed**.

---

## Summary Table

| Gap | Size | New Files | Schema Change | Decisions Needed |
|-----|------|-----------|---------------|------------------|
| 1. Window-aware send router | Medium | None — 1 new function in `whatsapp_service.py` | None | 3 (template copy, AI reply behavior, scheduler behavior) |
| 2. No-template queue + notify | Medium | Optional helper function | Yes — `whatsapp_outbound_queue` table | 3 (no-agent fallback, TTL/drain, dashboard) |
| 3. Inbound wa.me lead creation | Large | None — adds logic to `webhooks.py` | None | 3 (placeholder name, property_ref-required, ref format) |

---

## Recommended Implementation Order

1. **Gap 1** first — no schema change, immediately fixes the silent-drop bug on all
   three portal webhooks.
2. **Gap 3** next — enables inbound wa.me conversion, independent of template queue.
3. **Gap 2** last — the queue depends on Gap 1 existing (the "no approved template"
   branch), and requires schema migration approval.

---

> All items marked **"Decision needed"** must be confirmed before implementation begins.
> No code will be written until you respond to these questions.
