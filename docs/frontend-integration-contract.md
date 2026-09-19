# Meta Embedded Signup v4 — Frontend Integration Contract

This document provides the exact frontend contract and implementation code for integrating **Meta Embedded Signup (ESU) v4** into the AndiOS frontend (Next.js / React).

---

## 1. Overview

Meta Embedded Signup allows an agency or agent to connect their own WhatsApp Business Account (BYON - Bring Your Own Number) directly inside the AndiOS dashboard without manual API key entry.

The frontend is responsible for:
1. Loading the Meta Facebook JavaScript SDK.
2. Launching the Embedded Signup popup with the required configuration (`config_id`).
3. Listening to the `window.postMessage` events dispatched by Meta's popup to capture the `waba_id` and `phone_number_id`.
4. Receiving the OAuth `code` from the `FB.login` response.
5. Submitting all collected credentials to the backend endpoint `POST /connectors/meta-esu/callback`.
6. Displaying the connection status and handling disconnection.

---

## 2. Prerequisites

- **Meta App ID**: Provided via environment variable (`NEXT_PUBLIC_META_APP_ID`).
- **Embedded Signup Configuration ID (`config_id`)**: Created in Meta App Dashboard under WhatsApp > Quickstart / Embedded Signup configurations.
- **Backend API Base**: Points to `https://andreearizan.softvencealpha.com` (or local `http://localhost:8000`).

---

## 3. Endpoints Contract

### 3.1 `POST /connectors/meta-esu/callback`
Exchanges the short-lived OAuth code for a permanent/extended token, registers the phone number, subscribes webhooks, and persists the encrypted record in Supabase `communication_accounts`.

**Request Body (`application/json`):**
```json
{
  "code": "AQB... (OAuth code from FB.login)",
  "phone_number_id": "106xxxxxxxxxxxx",
  "waba_id": "105xxxxxxxxxxxx",
  "agency_id": "uuid-of-agency",
  "agent_id": "uuid-of-agent (optional, null if agency-wide)",
  "is_coexistence": false
}
```

**Response (`200 OK`):**
```json
{
  "success": true,
  "account_id": "uuid-of-communication-account",
  "phone_number_id": "106xxxxxxxxxxxx",
  "waba_id": "105xxxxxxxxxxxx",
  "is_coexistence": false,
  "message": "WhatsApp number successfully connected via Meta Embedded Signup"
}
```

**Error Responses:**
- `400 Bad Request`: `{ "detail": "Missing required field: ..." }`
- `502 Bad Gateway`: `{ "detail": "Meta token exchange failed: ..." }`

---

### 3.2 `GET /connectors/meta-esu/status`
Checks the current connection status of an agency or agent's WhatsApp integration.

**Query Parameters:**
- `agency_id` (required, UUID)
- `agent_id` (optional, UUID)

**Response (`200 OK` - Connected):**
```json
{
  "connected": true,
  "account_id": "uuid",
  "phone_number_id": "106xxxxxxxxxxxx",
  "waba_id": "105xxxxxxxxxxxx",
  "display_phone_number": "+971501234567",
  "is_coexistence": false,
  "status": "active",
  "provider": "meta",
  "token_expires_at": "2026-11-18T12:00:00Z"
}
```

**Response (`200 OK` - Not Connected):**
```json
{
  "connected": false,
  "status": "not_connected"
}
```

---

### 3.3 `POST /connectors/meta-esu/disconnect`
Disconnects the WhatsApp account, deregisters from AndiOS, and marks the record inactive.

**Request Body (`application/json`):**
```json
{
  "agency_id": "uuid-of-agency",
  "agent_id": "uuid-of-agent (optional)"
}
```

**Response (`200 OK`):**
```json
{
  "success": true,
  "message": "WhatsApp account disconnected successfully"
}
```

---

### 3.4 `POST /connectors/parse-wa-link`
Utility endpoint to parse phone numbers from `wa.me` or `api.whatsapp.com` links pasted by users.

**Request Body (`application/json`):**
```json
{
  "link": "https://wa.me/971501234567?text=Hello"
}
```

**Response (`200 OK`):**
```json
{
  "valid": true,
  "e164": "+971501234567",
  "raw_phone": "971501234567"
}
```

---

## 4. Frontend Implementation (React / Next.js Component)

Here is the complete, drop-in React hook and button component:

```tsx
import React, { useEffect, useState } from 'react';

declare global {
  interface Window {
    fbAsyncInit: () => void;
    FB: any;
  }
}

interface MetaSignupProps {
  agencyId: string;
  agentId?: string;
  onSuccess: (data: any) => void;
  onError: (err: any) => void;
}

export const WhatsAppConnectButton: React.FC<MetaSignupProps> = ({
  agencyId,
  agentId,
  onSuccess,
  onError,
}) => {
  const [loading, setLoading] = useState(false);
  const [sessionInfo, setSessionInfo] = useState<{
    phone_number_id?: string;
    waba_id?: string;
  }>({});

  useEffect(() => {
    // 1. Initialize Meta JS SDK
    window.fbAsyncInit = function () {
      window.FB.init({
        appId: process.env.NEXT_PUBLIC_META_APP_ID,
        cookie: true,
        xfbml: true,
        version: 'v22.0',
      });
    };

    // Load SDK script if not already loaded
    if (!document.getElementById('facebook-jssdk')) {
      const js = document.createElement('script');
      js.id = 'facebook-jssdk';
      js.src = 'https://connect.facebook.net/en_US/sdk.js';
      document.body.appendChild(js);
    }

    // 2. Listen to Embedded Signup session events (captures phone_number_id & waba_id)
    const handleMessage = (event: MessageEvent) => {
      // Validate origin from Meta
      if (
        event.origin !== 'https://www.facebook.com' &&
        event.origin !== 'https://web.facebook.com'
      ) {
        return;
      }

      try {
        const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        if (data.type === 'WA_EMBEDDED_SIGNUP') {
          if (data.event === 'FINISH') {
            const { phone_number_id, waba_id } = data.data;
            setSessionInfo({ phone_number_id, waba_id });
          } else if (data.event === 'CANCEL') {
            setLoading(false);
            onError(new Error('User cancelled WhatsApp signup'));
          } else if (data.event === 'ERROR') {
            setLoading(false);
            onError(new Error(data.data?.error_message || 'WhatsApp signup error'));
          }
        }
      } catch (err) {
        // Non-JSON message, ignore
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [onError]);

  const launchWhatsAppSignup = () => {
    if (!window.FB) {
      onError(new Error('Facebook SDK not loaded yet. Please refresh.'));
      return;
    }

    setLoading(true);

    window.FB.login(
      (response: any) => {
        if (response.authResponse?.code) {
          const code = response.authResponse.code;
          
          // Send code + sessionInfo to backend
          sendToBackend(code, sessionInfo.phone_number_id, sessionInfo.waba_id);
        } else {
          setLoading(false);
          onError(new Error('Failed to obtain authorization code from Meta'));
        }
      },
      {
        config_id: process.env.NEXT_PUBLIC_META_ESU_CONFIG_ID,
        response_type: 'code',
        override_default_response_type: true,
        extras: {
          setup: {},
          featureType: '',
          sessionInfoVersion: '3',
        },
      }
    );
  };

  const sendToBackend = async (code: string, phone_number_id?: string, waba_id?: string) => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/connectors/meta-esu/callback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code,
          phone_number_id,
          waba_id,
          agency_id: agencyId,
          agent_id: agentId || null,
          is_coexistence: false,
        }),
      });

      const result = await res.json();
      if (!res.ok) {
        throw new Error(result.detail || 'Failed to complete signup with backend');
      }

      setLoading(false);
      onSuccess(result);
    } catch (err: any) {
      setLoading(false);
      onError(err);
    }
  };

  return (
    <button
      onClick={launchWhatsAppSignup}
      disabled={loading}
      className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-green-600 hover:bg-green-700 disabled:opacity-50"
    >
      {loading ? 'Connecting...' : 'Connect WhatsApp (Meta)'}
    </button>
  );
};
```
