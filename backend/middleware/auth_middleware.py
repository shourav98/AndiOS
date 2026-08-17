from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database.supabase_client import get_supabase
from config import settings
from jose import jwt, JWTError
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    FastAPI dependency — validates JWT (local 24h JWT or Supabase token)
    and returns enriched user payload with agency_id, role, and agent_id.
    """
    token = credentials.credentials

    # 1. Fast local 24-hour JWT decoding
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload and payload.get("sub"):
            return payload
    except JWTError:
        pass
    except Exception as e:
        logger.debug(f"Local JWT decode note: {e}")

    # 2. Fallback to Supabase Auth get_user
    try:
        sb = get_supabase()
        response = sb.auth.get_user(token)

        if not response or not response.user:
            raise Exception("No user found for token")

        user = response.user
        email = user.email

        payload = {
            "sub": user.id,
            "email": email,
            "agency_id": None,
            "role": None,
            "agent_id": None,
        }

        # Prefer JWT app_metadata (set on register/login sync)
        app_meta = user.app_metadata or {}
        if app_meta.get("agency_id"):
            payload["agency_id"] = app_meta["agency_id"]
        if app_meta.get("role"):
            payload["role"] = app_meta["role"]
        if app_meta.get("agent_id"):
            payload["agent_id"] = app_meta["agent_id"]

        # Fallback: load from agents table by email
        if email and not payload["agency_id"]:
            agent_result = (
                sb.table("agents")
                .select("id, agency_id, role")
                .eq("email", email)
                .eq("is_active", True)
                .limit(1)
                .execute()
            )
            if agent_result.data:
                agent = agent_result.data[0]
                payload["agency_id"] = agent["agency_id"]
                payload["role"] = agent["role"]
                payload["agent_id"] = agent["id"]

        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Invalid JWT: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )



def get_current_user_id(current_user: dict = Depends(verify_token)) -> str:
    """Extract Supabase Auth user ID from JWT sub claim."""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    return user_id
