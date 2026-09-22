"""
Password-reset token enforcement tests (security fix).

Rules enforced by POST /auth/reset-password:
- only JWTs with purpose="password_reset" are accepted as reset tokens
- session JWTs (purpose="session"), purpose-less JWTs, expired tokens and
  Supabase-shaped tokens are rejected, and never reach update_user_by_id
- the inline 6-digit OTP flow still works

Tests invoke reset_password directly (no TestClient / main import) so they
cannot interfere with the app-wide supabase mock binding other suites rely on.
Run: pytest tests/test_auth_password_reset.py -v
"""
import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from jose import jwt

from config import settings
from routers.auth import reset_password, ResetPasswordRequest

USER_ID = "auth-user-abc123"
EMAIL = "owner@agencya.com"
NEW_PASSWORD = "N3wSecret!Pass"


class _FakeRequest:
    """Minimal stand-in for starlette Request (only .headers is accessed)."""

    def __init__(self, authorization: str = None):
        self.headers = {"authorization": authorization} if authorization else {}


def _make_token(purpose, expires_in_s=60, signing_key=None) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": USER_ID,
        "email": EMAIL,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in_s),
    }
    if purpose is not None:
        payload["purpose"] = purpose
    return jwt.encode(payload, signing_key or settings.SECRET_KEY, algorithm="HS256")


# ─── ALLOWED ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_reset_token_via_bearer_header_allows_change():
    sb = MagicMock()
    token = _make_token(purpose="password_reset")
    with patch("routers.auth.get_supabase", return_value=sb):
        result = await reset_password(
            ResetPasswordRequest(new_password=NEW_PASSWORD),
            _FakeRequest(authorization=f"Bearer {token}"),
        )
    assert result["success"] is True
    assert result["data"]["status"] == "password_updated"
    assert result["data"]["user_id"] == USER_ID
    sb.auth.admin.update_user_by_id.assert_called_once_with(USER_ID, {"password": NEW_PASSWORD})


@pytest.mark.asyncio
async def test_valid_reset_token_via_body_field_allows_change():
    sb = MagicMock()
    token = _make_token(purpose="password_reset")
    with patch("routers.auth.get_supabase", return_value=sb):
        result = await reset_password(
            ResetPasswordRequest(password=NEW_PASSWORD, reset_token=token),
            _FakeRequest(),
        )
    assert result["success"] is True
    sb.auth.admin.update_user_by_id.assert_called_once_with(USER_ID, {"password": NEW_PASSWORD})


@pytest.mark.asyncio
async def test_inline_otp_flow_still_works():
    sb = MagicMock()
    sb.auth.verify_otp.return_value = SimpleNamespace(user=SimpleNamespace(id="otp-user-id"))
    with patch("routers.auth.get_supabase", return_value=sb):
        result = await reset_password(
            ResetPasswordRequest(email=EMAIL, token="123456", new_password=NEW_PASSWORD),
            _FakeRequest(),
        )
    assert result["success"] is True
    sb.auth.verify_otp.assert_called_once_with({
        "email": EMAIL, "token": "123456", "type": "recovery",
    })
    sb.auth.admin.update_user_by_id.assert_called_once_with("otp-user-id", {"password": NEW_PASSWORD})


# ─── REJECTED ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_token_rejected():
    sb = MagicMock()
    session_token = _make_token(purpose="session", expires_in_s=86400)
    with patch("routers.auth.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await reset_password(
                ResetPasswordRequest(new_password=NEW_PASSWORD, access_token=session_token),
                _FakeRequest(),
            )
    assert exc_info.value.status_code == 403
    sb.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_session_token_via_authorization_header_rejected():
    sb = MagicMock()
    session_token = _make_token(purpose="session")
    with patch("routers.auth.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await reset_password(
                ResetPasswordRequest(new_password=NEW_PASSWORD),
                _FakeRequest(authorization=f"Bearer {session_token}"),
            )
    assert exc_info.value.status_code == 403
    sb.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_jwt_without_purpose_rejected():
    sb = MagicMock()
    purposeless_token = _make_token(purpose=None)
    with patch("routers.auth.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await reset_password(
                ResetPasswordRequest(new_password=NEW_PASSWORD, reset_token=purposeless_token),
                _FakeRequest(),
            )
    assert exc_info.value.status_code == 403
    sb.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_expired_reset_token_rejected():
    sb = MagicMock()
    expired_token = _make_token(purpose="password_reset", expires_in_s=-10)
    with patch("routers.auth.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await reset_password(
                ResetPasswordRequest(new_password=NEW_PASSWORD, reset_token=expired_token),
                _FakeRequest(),
            )
    assert exc_info.value.status_code == 401
    sb.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_foreign_signed_supabase_style_token_rejected_and_fallback_not_used():
    """A token signed by another authority (e.g. Supabase) must be rejected.

    Regression proof for the removed get_user fallback: the mock would happily
    resolve such a token to a user if the old fallback path were still present.
    """
    sb = MagicMock()
    sb.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(id="victim-user-id"))
    supabase_style_token = _make_token(purpose="session", signing_key="supabase-jwt-secret")
    with patch("routers.auth.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await reset_password(
                ResetPasswordRequest(new_password=NEW_PASSWORD),
                _FakeRequest(authorization=f"Bearer {supabase_style_token}"),
            )
    assert exc_info.value.status_code == 401
    sb.auth.get_user.assert_not_called()
    sb.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_missing_email_confirmation_guard_still_applies():
    """confirm_password mismatch must still fail before any token logic."""
    sb = MagicMock()
    token = _make_token(purpose="password_reset")
    with patch("routers.auth.get_supabase", return_value=sb):
        with pytest.raises(HTTPException) as exc_info:
            await reset_password(
                ResetPasswordRequest(
                    new_password=NEW_PASSWORD,
                    confirm_password="Different!",
                    reset_token=token,
                ),
                _FakeRequest(),
            )
    assert exc_info.value.status_code == 400
    sb.auth.admin.update_user_by_id.assert_not_called()
