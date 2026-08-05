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
    FastAPI dependency — validates JWT using Supabase and returns user payload.
    Usage: add `current_user: dict = Depends(verify_token)` to any route.
    """
    token = credentials.credentials
    try:
        from database.supabase_client import get_supabase
        sb = get_supabase()
        
        # Validates token securely against Supabase and gets the user
        response = sb.auth.get_user(token)
        
        if not response or not response.user:
            raise Exception("No user found for token")
            
        return {
            "sub": response.user.id,
            "email": response.user.email
        }
    except Exception as e:
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
