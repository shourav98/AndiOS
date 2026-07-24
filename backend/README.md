# AndiOS Backend API — Phase 1

> AI-powered real estate lead management for Dubai property agencies.
> Built with **FastAPI** + **Supabase** + **OpenAI GPT-4o** + **WhatsApp** + **Google Calendar**

---

## Quick Start

### 1. Clone & Setup Environment
```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your actual API keys
```

### 3. Set Up Supabase Database
1. Go to your [Supabase](https://supabase.com) project
2. Open **SQL Editor**
3. Paste and run the contents of `database/schema.sql`

### 4. Run the Server
```bash
uvicorn main:app --reload --port 8000
```

API docs available at: **http://localhost:8000/docs**

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | ✅ | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | Service role key (full DB access) |
| `OPENAI_API_KEY` | ✅ | GPT-4o API key |
| `WHATSAPP_PROVIDER` | ✅ | `360dialog` or `twilio` |
| `WHATSAPP_API_KEY` | ✅ | 360dialog API key |
| `WHATSAPP_VERIFY_TOKEN` | ✅ | For webhook verification |
| `GOOGLE_CLIENT_ID` | ⚡ | For Google Calendar OAuth |
| `GOOGLE_CLIENT_SECRET` | ⚡ | For Google Calendar OAuth |
| `PROPERTY_FINDER_WEBHOOK_SECRET` | ⚡ | PF webhook signing secret |

---

## API Endpoints

### 🏠 Leads
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/leads` | List leads with filters |
| `GET` | `/leads/stats` | Overview dashboard stats |
| `GET` | `/leads/{id}` | Lead + full conversation + viewings |
| `PATCH` | `/leads/{id}` | Update lead status/agent |
| `POST` | `/leads/{id}/handover` | AI → human handover |
| `POST` | `/leads/{id}/restore-ai` | Re-enable AI handling |

### 💬 Conversations
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/conversations/{lead_id}` | Full WhatsApp thread |
| `POST` | `/conversations/{lead_id}/send` | Agent manual reply |
| `POST` | `/conversations/{lead_id}/read` | Mark messages as read |

### 📅 Viewings
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/viewings` | All viewings (filterable) |
| `GET` | `/viewings/available-slots` | Free calendar slots |
| `POST` | `/viewings` | Book viewing + Calendar event |
| `GET` | `/viewings/{id}` | Single viewing detail |
| `PATCH` | `/viewings/{id}` | Update / cancel viewing |

### 👥 Team
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/agents` | List all agents |
| `POST` | `/agents` | Add new agent |
| `GET` | `/agents/{id}` | Agent profile + stats |
| `PATCH` | `/agents/{id}` | Update agent |
| `DELETE` | `/agents/{id}` | Deactivate agent |

### 📊 Reports
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/reports/owner` | List all reports |
| `POST` | `/reports/owner/generate` | Generate AI owner report |
| `GET` | `/reports/owner/{id}` | Get specific report |

### 🔌 Connectors
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/connectors` | All integrations status |
| `GET` | `/connectors/google-calendar/auth` | Start OAuth flow |
| `GET` | `/connectors/google-calendar/callback` | OAuth callback |
| `POST` | `/connectors/google-calendar/test` | Test connection |
| `POST` | `/connectors/whatsapp/test` | Send test message |

### 🔗 Webhooks
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/webhooks/property-finder` | New lead from PF portal |
| `GET` | `/webhooks/whatsapp` | WhatsApp verification |
| `POST` | `/webhooks/whatsapp` | Inbound WhatsApp message |

### 🔐 Auth
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/login` | Login → returns JWT |
| `POST` | `/auth/logout` | Logout |
| `GET` | `/auth/me` | Current user profile |
| `POST` | `/auth/refresh` | Refresh access token |

---

## Key Automation Flows

### Lead Capture → WhatsApp < 3 min
```
PF Webhook → Dedup check → Store in Supabase → AI greeting via WhatsApp
```

### AI Qualification Loop
```
Inbound WhatsApp → Match lead → Handover check → AI response
                                     ↓ (if complex)
                              Flag handover → Notify agent
```

### Viewing Booking
```
AI offers slots → Lead picks time → Create in Supabase + Google Calendar
→ WhatsApp confirmation → Schedule: 24h reminder, 2h reminder, 
  post-viewing follow-up, 48h feedback
```

### Owner Report
```
POST /reports/owner/generate → Pull Supabase metrics → GPT-4o narrative → Store
```

---

## Running Tests
```bash
pytest tests/ -v
```

---

## Project Structure
```
backend/
├── main.py                    # FastAPI entry point
├── config.py                  # Settings from .env
├── requirements.txt
├── pytest.ini
├── .env.example
├── database/
│   ├── schema.sql             # Run in Supabase SQL editor
│   └── supabase_client.py
├── routers/
│   ├── auth.py
│   ├── webhooks.py            # PF + WhatsApp webhooks
│   ├── leads.py
│   ├── conversations.py
│   ├── viewings.py
│   ├── agents.py
│   ├── reports.py
│   └── connectors.py
├── services/
│   ├── ai_service.py          # GPT-4o qualify/respond/report
│   ├── whatsapp_service.py    # 360dialog / Twilio
│   ├── calendar_service.py    # Google Calendar OAuth + events
│   ├── dedup_service.py       # Lead deduplication
│   └── scheduler.py           # APScheduler reminders
├── models/
│   ├── lead.py
│   ├── conversation.py
│   ├── viewing.py
│   └── agent.py
├── middleware/
│   └── auth_middleware.py     # JWT verification
└── tests/
    └── test_api.py
```

---

## Phase 2 & 3 Ready
This architecture is designed to extend cleanly:
- **Phase 2**: Add `routers/contracts.py` + document generation service
- **Phase 3**: Add `routers/voice.py` + outbound call service

All data is in Supabase — no rebuild needed.
