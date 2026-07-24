"""
JWT Auth Middleware — verifies Supabase-issued JWTs on protected routes.
"""
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from config import settings
import logging

logger = logging.getLogger(__name__)

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    FastAPI dependency — validates JWT and returns decoded payload.
    Usage: add `current_user: dict = Depends(verify_token)` to any route.
    """
    token = credentials.credentials
    try:
        # Supabase JWTs are signed with the JWT secret
        payload = jwt.decode(
            token,
            settings.SUPABASE_SERVICE_ROLE_KEY,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        return payload
    except JWTError as e:
        logger.warning(f"Invalid JWT: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user_id(current_user: dict = Depends(verify_token)) -> str:
    """Extract user/agent ID from JWT sub claim."""
    user_id = current_user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    return user_id
