"""
Auth Router — Login / session management via Supabase Auth
POST /auth/login    — email+password login, returns JWT
POST /auth/logout   — invalidate session
GET  /auth/me       — current user profile
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token, get_current_user_id
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/login")
async def login(body: LoginRequest):
    """
    Login via Supabase Auth.
    Returns access_token (JWT) to use in Authorization: Bearer header.
    """
    sb = get_supabase()
    try:
        response = sb.auth.sign_in_with_password({
            "email": body.email,
            "password": body.password,
        })
        if not response.session:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user = response.user
        # Look up agent profile
        agent = (
            sb.table("agents")
            .select("id, name, role, email")
            .eq("email", body.email)
            .single()
            .execute()
        )

        return {
            "access_token": response.session.access_token,
            "refresh_token": response.session.refresh_token,
            "expires_in": response.session.expires_in,
            "token_type": "Bearer",
            "user": {
                "id": user.id,
                "email": user.email,
                "agent": agent.data if agent.data else None,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")


@router.post("/logout")
async def logout(current_user: dict = Depends(verify_token)):
    """Invalidate the current session."""
    try:
        sb = get_supabase()
        sb.auth.sign_out()
        return {"status": "logged_out"}
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return {"status": "logged_out"}  # Always succeed on logout


@router.get("/me")
async def get_me(user_id: str = Depends(get_current_user_id)):
    """Get current user profile including agent details."""
    sb = get_supabase()

    # Get Supabase auth user
    auth_user = sb.auth.get_user()
    email = auth_user.user.email if auth_user and auth_user.user else None

    # Get agent profile
    agent = None
    if email:
        result = sb.table("agents").select("*").eq("email", email).single().execute()
        agent = result.data if result.data else None

    return {
        "user_id": user_id,
        "email": email,
        "agent": agent,
    }


@router.post("/refresh")
async def refresh_token(refresh_token: str):
    """Refresh an expired access token."""
    sb = get_supabase()
    try:
        response = sb.auth.refresh_session(refresh_token)
        if not response.session:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        return {
            "access_token": response.session.access_token,
            "refresh_token": response.session.refresh_token,
            "expires_in": response.session.expires_in,
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Token refresh failed")
