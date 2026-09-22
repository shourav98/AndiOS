# Meta Embedded Signup v4 — Frontend Integration Contract

This document specifies the exact contract and implementation for integrating **Meta Embedded Signup (ESU) v4** via Facebook Login for Business in the AndiOS frontend (Next.js / React).

---

## 1. Overview & v4 Architecture

Meta is retiring Embedded Signup v2/v3 in favor of **Embedded Signup v4** built on **Facebook Login for Business**.

Key specifications in v4:
- Configuration is driven by `config_id` defined in Meta Developer Portal (Facebook Login for Business).
- Permissions (`whatsapp_business_management`, `whatsapp_business_messaging`) are bundled inside the configuration ID, so `extras: {}` is passed **empty**.
- **Coexistence** (using the same number on physical phone + Cloud API) is auto-detected by Meta during signup.

### The Asynchronous Race Condition
When the popup completes, Meta emits two separate events in indeterminate order:
1. `FB.login` callback containing `{ authResponse: { code: "..." } }`
2. `window.postMessage` event containing `WA_EMBEDDED_SIGNUP` with `{ event: "FINISH", data: { phone_number_id, waba_id } }`

The frontend **must coordinate this race** by caching whichever arrives first and only firing the backend API callback once **both** are available. If `CANCEL` or `ERROR` arrives, the flow aborts immediately.

---

## 2. API Endpoints Contract

### 2.1 Connect Callback
`POST /connectors/meta-esu/callback`  
(Alias: `POST /connectors/whatsapp/embedded-signup-callback`)

**Headers:**
`Authorization: Bearer <user_jwt_token>`  
`Content-Type: application/json`

**Request Body:**
```json
{
  "code": "AQBx...",
  "waba_id": "105000000000000",
  "phone_number_id": "106000000000000",
  "agent_id": "uuid-of-agent" // Optional: Manager-only override. Ignored/enforced for agents.
}
```

> [!IMPORTANT]
> **Agent Derivation & Security:**
> The backend authoritatively derives the agent and agency from the caller's JWT bearer token.
> - For **agents** (`role: "agent"`), `agent_id` is automatically set to the authenticated user's ID (`current_user.id`). Any caller attempting to provide another agent's ID is rejected with `403 Forbidden`.
> - For **managers/owners** (`role: "owner"`, `role: "manager"`), `agent_id` in the request body is honored if provided, and the backend verifies that the specified agent belongs to the caller's agency (`400 Bad Request` if cross-tenant). If omitted, the connection is configured as the agency-wide fallback WhatsApp sender.

**Success Response (`200 OK`):**
```json
{
  "status": "success",
  "data": {
    "status": "connected",
    "account_id": "8f3b...",
    "phone_number": "+971501234567",
    "phone_number_id": "106000000000000",
    "waba_id": "105000000000000",
    "is_coexistence": true,
    "verified_name": "Elite Real Estate LLC",
    "provider": "meta"
  },
  "message": "WhatsApp account successfully connected via Meta Embedded Signup"
}
```

**Error Responses:**
- `400 Bad Request`: `{"detail": "Phone number ID ... does not belong to WABA ..."}`
- `403 Forbidden`: `{"detail": "Agents can only connect their own personal WhatsApp account."}`
- `409 Conflict`: `{"detail": "Phone number ... is already connected to another account."}`
- `502 Bad Gateway`: `{"detail": "Meta token exchange failed: ..."}`

---

### 2.2 Connection Status
`GET /connectors/meta-esu/status?agent_id=<uuid>`  
(Alias: `GET /connectors/whatsapp/status`)

**Response (`200 OK` - Connected):**
```json
{
  "status": "success",
  "data": {
    "status": "active",
    "connected": true,
    "provider": "meta",
    "account_id": "8f3b...",
    "phone_number": "971501234567",
    "phone_number_id": "106000000000000",
    "waba_id": "105000000000000",
    "is_coexistence": true,
    "connected_at": "2026-09-19T10:00:00+00:00",
    "waba_name": "Elite Real Estate LLC"
  }
}
```

---

### 2.3 Disconnect
`POST /connectors/meta-esu/disconnect`  
(Alias: `DELETE /connectors/whatsapp/disconnect`)

**Request Body:**
```json
{
  "agent_id": "uuid-of-agent" // Optional: disconnects agent's BYON number
}
```

---

## 3. Drop-In React / Next.js Component

```tsx
import React, { useEffect, useRef, useState } from 'react';

declare global {
  interface Window {
    fbAsyncInit: () => void;
    FB: any;
  }
}

interface MetaSignupProps {
  agentId?: string;
  onSuccess: (data: any) => void;
  onError: (err: Error) => void;
}

export const WhatsAppConnectButton: React.FC<MetaSignupProps> = ({
  agentId,
  onSuccess,
  onError,
}) => {
  const [connecting, setConnecting] = useState(false);

  // Synchronized refs to eliminate stale closure bugs across async callbacks
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const agentIdRef = useRef(agentId);
  agentIdRef.current = agentId;

  // Refs to coordinate the asynchronous race between FB.login code and postMessage sessionInfo
  const authCodeRef = useRef<string | null>(null);
  const sessionInfoRef = useRef<{ phone_number_id: string; waba_id: string } | null>(null);
  const callbackFiredRef = useRef<boolean>(false);

  // Coordinated callback: fires ONLY when both auth code and sessionInfo are received
  const checkAndSendToBackend = async () => {
    if (callbackFiredRef.current) return;
    if (!authCodeRef.current || !sessionInfoRef.current) return;

    callbackFiredRef.current = true;
    try {
      const response = await fetch('/api/connectors/meta-esu/callback', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
        },
        body: JSON.stringify({
          code: authCodeRef.current,
          phone_number_id: sessionInfoRef.current.phone_number_id,
          waba_id: sessionInfoRef.current.waba_id,
          agent_id: agentIdRef.current || null,
        }),
      });

      const resData = await response.json();
      if (!response.ok) {
        throw new Error(resData.detail || 'Backend failed to connect WhatsApp number.');
      }

      setConnecting(false);
      onSuccessRef.current(resData.data);
    } catch (err: any) {
      setConnecting(false);
      onErrorRef.current(err);
    }
  };

  const checkAndSendToBackendRef = useRef(checkAndSendToBackend);
  checkAndSendToBackendRef.current = checkAndSendToBackend;

  useEffect(() => {
    // 1. Initialize Meta JS SDK
    window.fbAsyncInit = function () {
      window.FB.init({
        appId: process.env.NEXT_PUBLIC_META_APP_ID,
        cookie: true,
        xfbml: true,
        version: 'v26.0',
      });
    };

    if (!document.getElementById('facebook-jssdk')) {
      const js = document.createElement('script');
      js.id = 'facebook-jssdk';
      js.src = 'https://connect.facebook.net/en_US/sdk.js';
      document.body.appendChild(js);
    }

    // 2. Listen to WA_EMBEDDED_SIGNUP window postMessage events
    const handlePostMessage = (event: MessageEvent) => {
      if (
        event.origin !== 'https://www.facebook.com' &&
        event.origin !== 'https://web.facebook.com'
      ) {
        return;
      }

      try {
        const payload = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        if (payload.type === 'WA_EMBEDDED_SIGNUP') {
          if (payload.event === 'FINISH') {
            const { phone_number_id, waba_id } = payload.data || {};
            sessionInfoRef.current = { phone_number_id, waba_id };
            checkAndSendToBackendRef.current();
          } else if (payload.event === 'CANCEL') {
            setConnecting(false);
            onErrorRef.current(new Error('User cancelled WhatsApp signup.'));
          } else if (payload.event === 'ERROR') {
            setConnecting(false);
            onErrorRef.current(new Error(payload.data?.error_message || 'Meta signup failed.'));
          }
        }
      } catch {
        // Non-JSON message from other origins/extensions
      }
    };

    window.addEventListener('message', handlePostMessage);
    return () => window.removeEventListener('message', handlePostMessage);
  }, []);

  const launchPopup = () => {
    if (!window.FB) {
      onErrorRef.current(new Error('Facebook SDK is still loading. Please try again.'));
      return;
    }

    setConnecting(true);
    authCodeRef.current = null;
    sessionInfoRef.current = null;
    callbackFiredRef.current = false;

    // Facebook Login for Business v4
    window.FB.login(
      (response: any) => {
        if (response.authResponse?.code) {
          authCodeRef.current = response.authResponse.code;
          checkAndSendToBackend();
        } else {
          setConnecting(false);
          onError(new Error('Meta authorization was not completed.'));
        }
      },
      {
        config_id: process.env.NEXT_PUBLIC_META_ESU_CONFIG_ID,
        response_type: 'code',
        override_default_response_type: true,
        extras: {}, // Must be empty in v4
      }
    );
  };

  return (
    <button
      onClick={launchPopup}
      disabled={connecting}
      className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-lg shadow-sm transition disabled:opacity-50"
    >
      {connecting ? 'Connecting...' : 'Connect WhatsApp (Meta ESU v4)'}
    </button>
  );
};
```
