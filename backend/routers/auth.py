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
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from typing import Optional
from database.supabase_client import get_supabase
from middleware.auth_middleware import verify_token, get_current_user_id
from utils.response import api_success
from config import settings
from jose import jwt
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

# Token Expiration in Seconds
JWT_EXPIRY_SECONDS = 86400         # 24 hours for normal login
RESET_TOKEN_EXPIRY_SECONDS = 150   # 2.5 minutes strictly for password reset (Industry Standard)


def _generate_24h_jwt(
    user_id: str,
    email: str,
    agency_id: Optional[str] = None,
    role: Optional[str] = None,
    agent_id: Optional[str] = None
) -> str:
    """Generate a custom 24-hour JWT access token for API requests."""
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "email": email,
        "agency_id": str(agency_id) if agency_id else None,
        "role": role or "owner",
        "agent_id": str(agent_id) if agent_id else None,
        "purpose": "session",
        "iat": now,
        "exp": now + timedelta(seconds=JWT_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _generate_reset_token(user_id: str, email: str) -> str:
    """Generate a short-lived (10 min) single-purpose JWT strictly for password reset."""
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "email": email,
        "purpose": "password_reset",
        "iat": now,
        "exp": now + timedelta(seconds=RESET_TOKEN_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


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
    new_password: Optional[str] = None
    password: Optional[str] = None
    confirm_password: Optional[str] = None
    email: Optional[EmailStr] = None
    token: Optional[str] = None          # 6-digit OTP code OR JWT token
    reset_token: Optional[str] = None    # JWT reset token from verify-otp
    access_token: Optional[str] = None   # JWT access token from verify-otp

    @property
    def target_password(self) -> str:
        return self.new_password or self.password or ""

    @property
    def candidate_jwt(self) -> Optional[str]:
        for t in (self.reset_token, self.access_token, self.token):
            if t and t.strip().startswith("eyJ"):
                return t.strip()
        return None



class VerifyOTPRequest(BaseModel):
    email: EmailStr
    token: str  # 6-digit OTP from email
    # Frontend sends:
    #   "signup"   → registration verification (mapped to Supabase "email" internally)
    #   "recovery" → forgot password verification
    # "email" is also accepted for backward compatibility
    type: str = "signup"


class ResendOTPRequest(BaseModel):
    email: EmailStr
    type: str  # Required: "signup" (registration) | "recovery" (forgot-password)

    def validate_type(self) -> None:
        if self.type not in ("signup", "recovery"):
            raise ValueError(f'type must be "signup" or "recovery", got "{self.type}"')


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
        # Step 1: Create Supabase Auth user (try sign_up, fallback to admin create if SMTP fails)
        user = None
        try:
            auth_response = sb.auth.sign_up({
                "email": body.email,
                "password": body.password,
            })
            if auth_response and auth_response.user:
                user = auth_response.user
        except Exception as signup_err:
            logger.warning(f"Supabase sign_up note ({signup_err}), creating user directly via admin API...")

        if not user:
            try:
                admin_res = sb.auth.admin.create_user({
                    "email": body.email,
                    "password": body.password,
                    "email_confirm": True,
                })
                if admin_res and admin_res.user:
                    user = admin_res.user
            except Exception as admin_err:
                logger.error(f"Admin create_user error: {admin_err}")
                raise HTTPException(status_code=400, detail="Registration failed. Email may already be registered.")

        if not user:
            raise HTTPException(status_code=400, detail="Registration failed. Email may already be in use.")

        user_id = user.id


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

    Frontend sends:
      type = "signup"   → registration email confirmation (Supabase: "email")
      type = "recovery" → forgot password OTP

    Note: "email" is also accepted as an alias for "signup" (backward compat).
    Returns access_token on success.
    """
    # Validate type
    if body.type not in ("signup", "email", "recovery"):
        raise HTTPException(
            status_code=400,
            detail=f'Invalid type "{body.type}". Must be "signup" or "recovery".'
        )

    # Map frontend-friendly type → Supabase internal type
    # Frontend always sends "signup" or "recovery".
    # Supabase verify_otp uses "email" for registration, "recovery" for password reset.
    supabase_type = "email" if body.type in ("signup", "email") else "recovery"
    is_registration = supabase_type == "email"

    sb = get_supabase()
    try:
        response = sb.auth.verify_otp({
            "email": body.email,
            "token": body.token,
            "type": supabase_type,
        })

        if not response.session:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

        # ── Recovery flow (Forgot Password) ──────────────────────────────────
        if not is_registration:
            reset_token = _generate_reset_token(
                user_id=response.user.id if response.user else "",
                email=body.email,
            )
            return api_success(
                message="OTP verified. Please set your new password before the token expires.",
                data={
                    "access_token": reset_token,        # for Authorization: Bearer header
                    "reset_token": reset_token,         # explicit reset token field
                    "expires_in": RESET_TOKEN_EXPIRY_SECONDS, # 150 seconds (2.5 minutes)
                    "token_type": "Bearer",
                    "purpose": "password_reset",
                }
            )

        # ── Registration flow (Signup Confirmation) ───────────────────────────
        agent = None
        if response.user and is_registration:
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

        token_24h = _generate_24h_jwt(
            user_id=response.user.id if response.user else "",
            email=body.email,
            agency_id=agent.get("agency_id") if agent else None,
            role=agent.get("role") if agent else "owner",
            agent_id=agent.get("id") if agent else None,
        )

        return api_success(
            message="Email verified successfully.",
            data={
                "access_token": token_24h,
                "supabase_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "expires_in": JWT_EXPIRY_SECONDS,
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
    """
    Resend OTP to user's email.
    type = "signup"   → registration email confirmation OTP
    type = "recovery" → forgot password OTP

    The 'type' field is REQUIRED. Returns 400 if missing or invalid.
    """
    # Validate type
    if body.type not in ("signup", "recovery"):
        raise HTTPException(
            status_code=400,
            detail=f'Invalid type "{body.type}". Must be "signup" (registration) or "recovery" (forgot-password).'
        )

    sb = get_supabase()
    try:
        if body.type == "recovery":
            # For forgot-password flow: send a fresh 6-digit recovery OTP
            sb.auth.reset_password_email(body.email)
            message = "Password reset code resent to your email."
        else:
            # For registration flow: resend signup confirmation OTP
            sb.auth.resend({
                "type": "signup",
                "email": body.email,
            })
            message = "Verification code resent to your email."

        return api_success(
            message=message,
            data={
                "email": body.email,
                "type": body.type,
                "note": "Check your inbox for the new OTP code."
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Resend OTP error: {err_msg}")
        if "security purposes" in err_msg.lower() or "rate" in err_msg.lower() or "seconds" in err_msg.lower():
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait 60 seconds before requesting another code."
            )
        raise HTTPException(
            status_code=400,
            detail="Failed to resend OTP. Please ensure your email is correct and try again."
        )


# ─── LOGIN ────────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest):
    """
    Login via Supabase Auth.
    Returns 24-hour access_token (JWT) to use in Authorization: Bearer header.
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

        token_24h = _generate_24h_jwt(
            user_id=user.id,
            email=user.email,
            agency_id=agent.get("agency_id") if agent else None,
            role=agent.get("role") if agent else "owner",
            agent_id=agent.get("id") if agent else None,
        )

        return api_success(
            message="User logged in successfully",
            data={
                "access_token": token_24h,
                "supabase_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "expires_in": JWT_EXPIRY_SECONDS,
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
    user_id = current_user.get("sub")
    email = current_user.get("email")
    try:
        sb = get_supabase()
        sb.auth.sign_out()
    except Exception as e:
        logger.error(f"Logout error: {e}")

    return api_success(
        message="Logged out successfully",
        data={
            "user_id": user_id,
            "email": email,
            "status": "logged_out"
        }
    )


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
        if not response.session or not response.user:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        user = response.user
        agent_result = (
            sb.table("agents")
            .select("id, agency_id, role")
            .eq("email", user.email)
            .maybe_single()
            .execute()
        )
        agent = agent_result.data if agent_result and agent_result.data else None

        token_24h = _generate_24h_jwt(
            user_id=user.id,
            email=user.email,
            agency_id=agent.get("agency_id") if agent else None,
            role=agent.get("role") if agent else "owner",
            agent_id=agent.get("id") if agent else None,
        )

        return api_success(
            message="Token refreshed successfully",
            data={
                "access_token": token_24h,
                "supabase_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "expires_in": JWT_EXPIRY_SECONDS,
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
        return api_success(
            message="Password reset code sent to your email.",
            data={
                "email": body.email,
                "type": "recovery",
                "status": "otp_sent",
                "note": "A 6-digit password recovery OTP has been sent to your email."
            }
        )
    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        # Always return success to prevent email enumeration
        return api_success(
            message="If this email exists, a reset code has been sent.",
            data={
                "email": body.email,
                "type": "recovery",
                "status": "otp_sent",
                "note": "If an account exists with this email, a verification code was dispatched."
            }
        )


# ─── RESET PASSWORD ───────────────────────────────────────────────────────────────

@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
):
    """
    Set a new password after OTP verification.

    Industry-standard flow:
      1. POST /auth/forgot-password  → email with 6-digit OTP sent (type=recovery)
      2. POST /auth/verify-otp       → {email, token, type="recovery"} → returns access_token
      3. POST /auth/reset-password   → Authorization: Bearer <access_token> + new password

    Alternative (one-step): provide {email, token, password, confirm_password} in body.
    The OTP is verified inline and password is updated in one call.
    """
    password_to_set = body.target_password
    if not password_to_set:
        raise HTTPException(status_code=400, detail="Password is required.")

    if body.confirm_password and body.confirm_password != password_to_set:
        raise HTTPException(status_code=400, detail="Passwords do not match.")

    sb = get_supabase()
    user_id = None
    user_email = body.email

    # ── Method 1: JWT Token from Authorization Header OR Request Body ───────────
    # Accepts:
    #   - Header: Authorization: Bearer <reset_token>
    #   - Body: { "reset_token": "..." } or { "access_token": "..." } or { "token": "eyJ..." }
    token = None
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif body.candidate_jwt:
        token = body.candidate_jwt

    if token:
        # Single-purpose enforcement: only password-reset tokens may reset passwords.
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            if payload and payload.get("sub"):
                if payload.get("purpose") != "password_reset":
                    raise HTTPException(
                        status_code=403,
                        detail="Invalid token for password reset. Request a fresh reset code.",
                    )
                user_id = payload["sub"]
                if not user_email and payload.get("email"):
                    user_email = payload["email"]
        except HTTPException:
            raise
        except Exception as e:
            logger.debug(f"JWT decode error on reset-password: {e}")

        # NOTE: deliberately no Supabase get_user(token) fallback here — an ordinary
        # Supabase session/access token must never be able to change a password.

    # ── Method 2: Inline 6-digit OTP verification + password reset ────────────────
    # Requires both email AND 6-digit OTP code in body.
    if not user_id and body.email and body.token:
        otp_candidate = body.token.strip()
        if not otp_candidate.startswith("eyJ"):
            try:
                v_res = sb.auth.verify_otp({
                    "email": body.email,
                    "token": otp_candidate,
                    "type": "recovery",
                })
                if v_res and v_res.user:
                    user_id = v_res.user.id
                    if not user_email:
                        user_email = v_res.user.email
            except Exception as e:
                logger.warning(f"Inline OTP verification failed: {e}")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid or expired OTP code. Please request a new code."
                )

    # ── No valid authentication provided ──────────────────────────────────────────
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=(
                "Authentication required. Provide Authorization: Bearer <reset_token> header, "
                "or pass 'reset_token' / 'token' in the request body, or pass 'email' + 'token' (OTP)."
            )
        )

    # ── Update the password ───────────────────────────────────────────────────────
    try:
        sb.auth.admin.update_user_by_id(user_id, {"password": password_to_set})

        # Resolve user email for rich response data
        if not user_email:
            try:
                user_record = sb.auth.admin.get_user_by_id(user_id)
                if user_record and user_record.user:
                    user_email = user_record.user.email
            except Exception:
                pass

        return api_success(
            message="Password updated successfully.",
            data={
                "user_id": str(user_id),
                "email": user_email,
                "status": "password_updated",
                "next_step": "login_with_new_password"
            }
        )
    except Exception as e:
        logger.error(f"Reset password error: {e}")
        raise HTTPException(status_code=400, detail="Password reset failed. Please try again.")


