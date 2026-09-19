"""
Token Encryption/Decryption Utility.

Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256).
Tokens are stored as base64-encoded TEXT in the `access_token` column.

Key derivation:
  The encryption key is derived from settings.SECRET_KEY using PBKDF2-HMAC-SHA256,
  so you do NOT need a separate TOKEN_ENCRYPTION_KEY env var — the existing
  SECRET_KEY is used. If you want a separate key, set TOKEN_ENCRYPTION_KEY in .env.

Security properties:
  - Each encryption call produces a unique ciphertext (random IV via Fernet)
  - Constant-time decryption (Fernet)
  - decrypt_token() returns "" on any error (fail safe — never crashes the app)

Usage:
    from utils.crypto import encrypt_token, decrypt_token

    stored = encrypt_token("EAAxxxxx...")   # store this in DB
    plain  = decrypt_token(stored)          # retrieve plaintext
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """
    Build (and cache) a Fernet instance from the configured secret key.

    Priority:
      1. TOKEN_ENCRYPTION_KEY env var (must be a valid URL-safe base64 32-byte key)
      2. Derived from SECRET_KEY via PBKDF2 (32-byte SHA256 digest → Fernet key)
    """
    raw_key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    if raw_key:
        try:
            # Validate it's a proper Fernet key
            fernet = Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)
            return fernet
        except Exception:
            logger.warning("[Crypto] TOKEN_ENCRYPTION_KEY is set but invalid — falling back to SECRET_KEY derivation")

    # Derive from SECRET_KEY
    from config import settings
    secret = (settings.SECRET_KEY or "").encode("utf-8")
    if not secret or secret == b"change-me-in-production":
        logger.critical(
            "[Crypto] SECRET_KEY is not set or is the default insecure value. "
            "Token encryption will use an INSECURE derived key. "
            "Set SECRET_KEY (or TOKEN_ENCRYPTION_KEY) in production .env immediately."
        )

    # PBKDF2 → 32 bytes → URL-safe base64 → Fernet key
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        secret,
        b"andios-token-salt-v1",  # static salt (OK — key material comes from SECRET_KEY)
        iterations=100_000,
        dklen=32,
    )
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt_token(plaintext: str) -> str:
    """
    Encrypt a provider access token for DB storage.

    Args:
        plaintext: The raw access token (e.g. "EAAxxxxx...").

    Returns:
        A URL-safe base64 string (Fernet token) safe to store in TEXT column.
        Returns "" if plaintext is empty.
    """
    if not plaintext:
        return ""
    try:
        fernet = _get_fernet()
        ciphertext = fernet.encrypt(plaintext.encode("utf-8"))
        return ciphertext.decode("utf-8")
    except Exception as exc:
        logger.error(f"[Crypto] encrypt_token failed: {exc}")
        return ""


def decrypt_token(ciphertext: str) -> str:
    """
    Decrypt a stored provider access token.

    Args:
        ciphertext: The stored Fernet token from DB.

    Returns:
        Plaintext token, or "" on any decryption failure (fail safe).
    """
    if not ciphertext:
        return ""
    try:
        fernet = _get_fernet()
        plaintext = fernet.decrypt(ciphertext.encode("utf-8"))
        return plaintext.decode("utf-8")
    except InvalidToken:
        logger.error(
            "[Crypto] decrypt_token: InvalidToken — key mismatch or corrupted ciphertext. "
            "If you rotated SECRET_KEY, re-encrypt all tokens."
        )
        return ""
    except Exception as exc:
        logger.error(f"[Crypto] decrypt_token failed unexpectedly: {exc}")
        return ""


def is_encrypted(value: str) -> bool:
    """
    Heuristic check: does the string look like a Fernet token?
    Fernet tokens start with 'gAAAAA' (version byte 0x80 + timestamp).
    """
    return bool(value and value.startswith("gAAAAA"))
