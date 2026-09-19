# AndiOS BYON — Human Setup Checklist & Operational Guide

This document outlines all non-automated setup tasks required by humans (developers, agency admins, and clients) to operate Meta WhatsApp Cloud API and Embedded Signup in production.

---

## 1. Supabase Database Migration (One-time)

Before using the updated code on production, run the migration script in your **Supabase SQL Editor**:

1. Open your Supabase Dashboard: `https://app.supabase.com`
2. Select your project -> **SQL Editor** -> **New Query**
3. Paste and run the contents of [`backend/database/schema_v11_security.sql`](file:///c:/Users/Shourav/Desktop/AndiOS/backend/database/schema_v11_security.sql):
   - Safely migrates `access_token` to `TEXT` (ready for app-level Fernet AES encryption).
   - Adds `token_expires_at` and `is_coexistence` columns to `communication_accounts`.
   - Creates the `whatsapp_processed_messages` idempotency table with index.
4. Verify execution returns `Success. No rows returned`.

---

## 2. Meta Developer Portal Setup (One-time Tech Provider Setup)

To allow external agencies to connect their numbers via Embedded Signup:

### 2.1 Meta Business Verification
- Navigate to **Meta Business Settings** -> **Security Center**.
- Complete **Business Verification** (upload trade license / legal documents).
- *Note:* WhatsApp Tech Provider / Advanced Access features require verified business status.

### 2.2 App Setup & Permissions
- Go to [developers.facebook.com](https://developers.facebook.com) -> Select your App.
- Under **App Roles**, ensure developers and testers have access.
- Under **App Review** -> **Permissions and Features**, request **Advanced Access** for:
  - `whatsapp_business_management`
  - `whatsapp_business_messaging`
- Set App Mode to **Live** when testing external phone numbers.

### 2.3 Embedded Signup Configuration
- Go to **WhatsApp** -> **Quickstart** or **Configuration**.
- Create an Embedded Signup Configuration (`config_id`):
  - Add your frontend domain: `https://andi-os.vercel.app` to Allowed Domains.
  - Set Webhook URL to: `https://andreearizan.softvencealpha.com/webhooks/whatsapp`
  - Verify Token: Match `WHATSAPP_VERIFY_TOKEN` (e.g. `andios_verify_token`).
- Subscribe to Webhook fields:
  - `messages` (inbound messages & replies)
  - `message_template_status_update` (template approvals)
  - `smb_message_echoes` (for WhatsApp Coexistence mode)
  - `smb_app_state_sync` (for WhatsApp Coexistence mode)

---

## 3. Production Server Environment Variables (`.env`)

On your production server (`root@159.198.77.123` via CloudPanel or SSH):

Path: `/home/softvencealpha-andreearizan/htdocs/andreearizan.softvencealpha.com/.env`

Ensure the following variables are set:

```ini
APP_ENV=production
SECRET_KEY=your_32_char_secret_key
API_BASE_URL=https://andreearizan.softvencealpha.com
FRONTEND_URL=https://andi-os.vercel.app

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...

# WhatsApp Provider Configuration
WHATSAPP_PROVIDER=meta
META_GRAPH_API_VERSION=v22.0
WHATSAPP_VERIFY_TOKEN=andios_verify_token
META_APP_SECRET=your_meta_app_secret_here
META_APP_ID=your_meta_app_id_here

# System User Token (Fallback for default agency)
WHATSAPP_API_KEY=EAA...
WHATSAPP_PHONE_NUMBER_ID=106...

# Optional dedicated encryption key (or leave empty to derive from SECRET_KEY)
TOKEN_ENCRYPTION_KEY=

# Legacy Twilio feature flag (false for new agencies, true if legacy numbers exist)
ENABLE_TWILIO_PROVISIONING=false
```

After updating `.env`, restart the backend service:
```bash
systemctl restart andios-backend
# Or restart via CloudPanel Python app manager
```

---

## 4. WhatsApp Coexistence Mode (UAE & GCC Mobile Numbers)

### What is Coexistence?
Allows real estate agents to keep using their **WhatsApp Business App** on their physical mobile phone (e.g., UAE `+971 50...`) while AndiOS AI assistant simultaneously listens, records leads, and sends automated replies via the Meta Cloud API.

### Eligibility & Rules:
1. The phone number must already be registered and active in the **WhatsApp Business App** on Android or iOS for at least 7 days.
2. During Embedded Signup v4, Meta's popup detects the number is already registered in the WA Business App and offers **"Keep using WhatsApp Business App"** (Coexistence).
3. The user confirms via an SMS/Voice OTP or 6-digit PIN in their WhatsApp Business app.
4. Both Cloud API and WhatsApp Business App remain active simultaneously.

---

## 5. Verification & Test Checklist

- [ ] Run `schema_v11_security.sql` in Supabase SQL Editor.
- [ ] Verify `GET https://andreearizan.softvencealpha.com/health` returns status healthy.
- [ ] Test Webhook verification:
  `curl "https://andreearizan.softvencealpha.com/webhooks/whatsapp?hub.mode=subscribe&hub.challenge=test1234&hub.verify_token=andios_verify_token"`
  Expect response: `test1234`
- [ ] Test status endpoint:
  `GET https://andreearizan.softvencealpha.com/connectors/meta-esu/status?agency_id=<AGENCY_UUID>`
- [ ] Run full backend test suite:
  `pytest tests/` (223 passed, 0 failures).
