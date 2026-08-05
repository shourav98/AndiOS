"""
Auth Router — Full authentication flow via Supabase Auth

POST /auth/register        — Create company + owner account
POST /auth/login           — Email + password login, returns JWT
POST /auth/logout          — Invalidate session
GET  /auth/me              — Current user profile
POST /auth/refresh         — Refresh expired access token
POST /auth/forgot-password — Send password reset email
POST /auth/reset-password  — Set new password (after OTP/link)
POST /auth/verify-otp      — Verify 6-digit email OTP
POST /auth/resend-otp      — Resend OTP verification email
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token, get_current_user_id
from utils.response import api_success
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


def _sync_user_app_metadata(user_id: str, agency_id: str, role: str, agent_id: str) -> None:
    """Store tenant context in Supabase Auth app_metadata for JWT claims."""
    sb = get_supabase()
    sb.auth.admin.update_user_by_id(
        user_id,
        {
            "app_metadata": {
                "agency_id": str(agency_id),
                "role": role,
                "agent_id": str(agent_id),
            }
        },
    )


# ─── Request / Response Models ──────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    agency_name: str
    full_name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    new_password: str
    # access_token is taken from Authorization header (after clicking reset link)


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    token: str          # 6-digit OTP from email
    type: str = "email" # "email" | "recovery"


class ResendOTPRequest(BaseModel):
    email: EmailStr


class RefreshRequest(BaseModel):
    refresh_token: str


# ─── REGISTER (Create Company) ──────────────────────────────────────────────────

@router.post("/register", status_code=201)
async def register(body: RegisterRequest):
    """
    Create a new agency + owner account.
    Steps:
      1. Create Supabase Auth user (triggers email verification OTP)
      2. Insert agency into agencies table
      3. Insert agent (owner role) into agents table
    """
    sb = get_supabase()
    try:
        # Step 1: Create Supabase Auth user
        auth_response = sb.auth.sign_up({
            "email": body.email,
            "password": body.password,
        })

        if not auth_response.user:
            raise HTTPException(status_code=400, detail="Registration failed. Email may already be in use.")

        user_id = auth_response.user.id

        # Step 2: Create agency
        slug = body.agency_name.lower().replace(" ", "-").replace("_", "-")
        # Ensure slug is unique by appending part of user_id
        slug = f"{slug}-{user_id[:6]}"
        agency_result = sb.table("agencies").insert({
            "name": body.agency_name,
            "slug": slug,
            "email": body.email,
            "subscription_plan": "starter",
            "subscription_status": "active",
        }).execute()

        if not agency_result.data:
            raise HTTPException(status_code=500, detail="Failed to create agency.")

        agency = agency_result.data[0]
        agency_id = agency["id"]

        # Step 3: Create agent (owner) profile
        agent_result = sb.table("agents").insert({
            "name": body.full_name,
            "email": body.email,
            "role": "owner",
            "agency_id": agency_id,
            "is_active": True,
        }).execute()

        agent = agent_result.data[0] if agent_result.data else None

        if agent:
            try:
                _sync_user_app_metadata(user_id, agency_id, "owner", agent["id"])
            except Exception as meta_err:
                logger.warning(f"Could not sync app_metadata on register: {meta_err}")

        return api_success(
            message="Account created. Please verify your email with the OTP sent.",
            status_code=201,
            data={
                "user_id": user_id,
                "agency": {
                    "id": agency_id,
                    "name": body.agency_name,
                    "slug": slug,
                },
                "agent": agent,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Register error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ─── VERIFY EMAIL OTP ────────────────────────────────────────────────────────────

@router.post("/verify-otp")
async def verify_otp(body: VerifyOTPRequest):
    """
    Verify the 6-digit OTP sent to the user's email.
    Type = 'email' for registration, 'recovery' for forgot password.
    Returns a session (access_token) on success.
    """
    sb = get_supabase()
    try:
        response = sb.auth.verify_otp({
            "email": body.email,
            "token": body.token,
            "type": body.type,
        })

        if not response.session:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

        # Sync tenant context into JWT app_metadata after email verification
        if response.user and body.type == "email":
            agent_result = (
                sb.table("agents")
                .select("id, agency_id, role")
                .eq("email", body.email)
                .limit(1)
                .execute()
            )
            if agent_result.data:
                agent = agent_result.data[0]
                try:
                    _sync_user_app_metadata(
                        response.user.id,
                        agent["agency_id"],
                        agent["role"],
                        agent["id"],
                    )
                except Exception as meta_err:
                    logger.warning(f"Could not sync app_metadata on verify-otp: {meta_err}")

        return api_success(
            message="Email verified successfully.",
            data={
                "access_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "expires_in": response.session.expires_in,
                "token_type": "Bearer",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OTP verification error: {e}")
        raise HTTPException(status_code=400, detail="OTP verification failed. Please try again.")


# ─── RESEND OTP ──────────────────────────────────────────────────────────────────

@router.post("/resend-otp")
async def resend_otp(body: ResendOTPRequest):
    """Resend email verification OTP."""
    sb = get_supabase()
    try:
        sb.auth.resend({
            "type": "signup",
            "email": body.email,
        })
        return api_success(message="Verification code resent to your email.")
    except Exception as e:
        logger.error(f"Resend OTP error: {e}")
        raise HTTPException(status_code=400, detail="Could not resend OTP.")


# ─── LOGIN ────────────────────────────────────────────────────────────────────────

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

        # Get agent profile + agency info
        agent_result = (
            sb.table("agents")
            .select("id, name, role, email, agency_id, agencies(id, name, slug, subscription_plan)")
            .eq("email", body.email)
            .single()
            .execute()
        )
        agent = agent_result.data if agent_result.data else None

        if agent:
            try:
                _sync_user_app_metadata(user.id, agent["agency_id"], agent["role"], agent["id"])
            except Exception as meta_err:
                logger.warning(f"Could not sync app_metadata on login: {meta_err}")

        return api_success(
            message="User logged in successfully",
            data={
                "access_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "expires_in": response.session.expires_in,
                "token_type": "Bearer",
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "agent": agent,
                },
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")


# ─── LOGOUT ──────────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout(current_user: dict = Depends(verify_token)):
    """Invalidate the current session."""
    try:
        sb = get_supabase()
        sb.auth.sign_out()
        return api_success(message="Logged out successfully")
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return api_success(message="Logged out successfully")  # Always succeed on logout


# ─── ME (Current User) ───────────────────────────────────────────────────────────

@router.get("/me")
async def get_me(current_user: dict = Depends(verify_token)):
    """Get current user profile including agent + agency details."""
    sb = get_supabase()

    user_id = current_user.get("sub")
    email = current_user.get("email")

    agent = None
    if email:
        result = (
            sb.table("agents")
            .select("*, agencies(id, name, slug, subscription_plan, subscription_status)")
            .eq("email", email)
            .single()
            .execute()
        )
        if result.data:
            agent = result.data

    return api_success(
        message="User profile fetched successfully",
        data={
            "user_id": user_id,
            "email": email,
            "agent": agent,
        }
    )


# ─── REFRESH TOKEN ────────────────────────────────────────────────────────────────

@router.post("/refresh")
async def refresh_token(body: RefreshRequest):
    """Refresh an expired access token using refresh_token."""
    sb = get_supabase()
    try:
        response = sb.auth.refresh_session(body.refresh_token)
        if not response.session:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        return api_success(
            message="Token refreshed successfully",
            data={
                "access_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "expires_in": response.session.expires_in,
            }
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail="Token refresh failed")


# ─── FORGOT PASSWORD ──────────────────────────────────────────────────────────────

@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    """
    Send a password reset OTP to the user's email.
    The frontend should then show the OTP input screen.
    """
    sb = get_supabase()
    try:
        # This sends a 6-digit OTP to the email (type=recovery)
        sb.auth.reset_password_email(body.email)
        return api_success(message="Password reset code sent to your email.")
    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        # Always return success to prevent email enumeration
        return api_success(message="If this email exists, a reset code has been sent.")


# ─── RESET PASSWORD ───────────────────────────────────────────────────────────────

@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Set a new password.
    Requires the access_token obtained from /auth/verify-otp (type=recovery).
    Flow: forgot-password → verify-otp (type=recovery) → reset-password
    """
    sb = get_supabase()
    try:
        response = sb.auth.update_user({"password": body.new_password})
        if not response.user:
            raise HTTPException(status_code=400, detail="Password reset failed.")
        return api_success(message="Password updated successfully.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reset password error: {e}")
        raise HTTPException(status_code=400, detail="Password reset failed. Token may be expired.")
