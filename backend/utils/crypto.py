"""
Token Encryption/Decryption Utility.

Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256) via cryptography.
Stores Fernet ciphertext in the `communication_accounts.access_token_enc` BYTEA column
as a PostgreSQL hex string literal (r"\\x" + hex(base64_fernet_token)).

Key Management:
  - Uses a dedicated `TOKEN_ENCRYPTION_KEY` environment variable.
  - Supports comma-separated keys for zero-downtime rotation via MultiFernet:
      TOKEN_ENCRYPTION_KEY="primary_new_key,secondary_old_key"
    The first key is used for all new encryptions; any key in the list can decrypt.
  - Hard failure on missing or invalid keys (no hardcoded fallback keys).
  - NEVER derives from SECRET_KEY.

Backward Compatibility & Transition:
  - decrypt_token() detects unencrypted legacy plaintext (e.g. "EAA..."),
    including legacy plaintext stored as BYTEA hex, logs a transition warning
    once per account, and returns the plaintext safely so live accounts
    continue functioning before and during migration (guarded by ALLOW_LEGACY_PLAINTEXT_TOKENS).
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Union

from cryptography.fernet import Fernet, MultiFernet, InvalidToken

logger = logging.getLogger(__name__)

class TokenDecryptionError(Exception):
    """Raised when token decryption fails (corrupted ciphertext, key mismatch, etc.)."""
    pass


# Process-level registry to track legacy plaintext logging once per account
_LOGGED_LEGACY_ACCOUNTS: set[str] = set()


def validate_token_encryption_key() -> None:
    """
    Validate TOKEN_ENCRYPTION_KEY.
    Fails fast with RuntimeError/ValueError if missing or invalid.
    """
    _get_multi_fernet()


@lru_cache(maxsize=1)
def _get_multi_fernet() -> MultiFernet:
    """
    Build and cache a MultiFernet instance from TOKEN_ENCRYPTION_KEY.
    Supports comma-separated keys for key rotation.
    Fails hard if TOKEN_ENCRYPTION_KEY is not set or contains invalid keys.
    """
    raw_keys = os.getenv("TOKEN_ENCRYPTION_KEY", "").strip()
    if not raw_keys:
        try:
            from config import settings
            raw_keys = getattr(settings, "TOKEN_ENCRYPTION_KEY", "").strip()
        except Exception:
            raw_keys = ""

    if not raw_keys:
        logger.critical("[Crypto] TOKEN_ENCRYPTION_KEY is not set.")
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be configured with a valid 32-byte Fernet key.")

    key_list = [
        k.strip().encode("utf-8") if isinstance(k.strip(), str) else k.strip()
        for k in raw_keys.split(",")
        if k.strip()
    ]
    if not key_list:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is empty or contains no valid keys.")

    fernets = []
    for k in key_list:
        try:
            fernets.append(Fernet(k))
        except Exception as e:
            logger.critical("[Crypto] Invalid key in TOKEN_ENCRYPTION_KEY: %s", e)
            raise ValueError(f"Invalid key in TOKEN_ENCRYPTION_KEY: {e}") from e

    return MultiFernet(fernets)


def is_encrypted(value: Union[str, bytes, None]) -> bool:
    """
    Check if a stored token appears to be encrypted with Fernet.
    Stored values in Postgres BYTEA are hex of the base64 Fernet token,
    so after hex-decoding the value starts with b"gAAAAA".
    """
    if not value:
        return False

    if isinstance(value, str):
        if value.startswith(("\\x", "\\X")):
            try:
                raw_bytes = bytes.fromhex(value[2:])
                return raw_bytes.startswith(b"gAAAAA")
            except ValueError:
                return False
        return value.startswith("gAAAAA")

    if isinstance(value, bytes):
        if value.startswith(b"gAAAAA"):
            return True
        if value.startswith((b"\\x", b"\\X")):
            try:
                raw_bytes = bytes.fromhex(value[2:].decode("ascii"))
                return raw_bytes.startswith(b"gAAAAA")
            except (ValueError, UnicodeDecodeError):
                return False
        return False

    return False


def encrypt_token(plaintext: str) -> str:
    """
    Encrypt a plaintext access token using MultiFernet.

    Returns:
        PostgreSQL BYTEA hex literal string (r"\\x..." + hex) ready for
        insertion into `access_token_enc BYTEA` column via Supabase/PostgREST.

    Raises:
        ValueError: If plaintext is empty or already encrypted (double-encryption guard).
        RuntimeError / Exception: If key is missing/invalid or encryption fails.
    """
    if not plaintext:
        raise ValueError("Cannot encrypt empty token.")

    if is_encrypted(plaintext):
        raise ValueError("Attempted to double-encrypt an already-encrypted token.")

    mf = _get_multi_fernet()
    try:
        ciphertext_bytes = mf.encrypt(plaintext.encode("utf-8"))
        # PostgreSQL BYTEA accepts standard \x hex literal
        return "\\x" + ciphertext_bytes.hex()
    except Exception as exc:
        logger.error(f"[Crypto] encrypt_token failed: {exc}")
        raise


def decrypt_token(stored_val: Union[str, bytes, None], account_id: str | None = None) -> str:
    """
    Decrypt a stored access token from `communication_accounts.access_token_enc` or `access_token`.

    Handles:
      1. Encrypted PostgreSQL BYTEA hex string (starts with "\\x" and hex-decodes to "gAAAAA")
      2. Encrypted standard Fernet string or bytes (starts with "gAAAAA")
      3. Legacy unencrypted plaintext stored directly (e.g. starts with "EAA" or "AC")
      4. Legacy unencrypted plaintext stored in BYTEA (starts with "\\x" and hex-decodes to ASCII plaintext)

    Guards:
      - ALLOW_LEGACY_PLAINTEXT_TOKENS controls whether unencrypted plaintext is accepted.
      - Logs a transition warning once per account ID.

    Returns:
        Plaintext token, or "" if input is empty.
    """
    if not stored_val:
        return ""

    allow_legacy_str = os.getenv("ALLOW_LEGACY_PLAINTEXT_TOKENS", "true").lower()
    if not os.getenv("ALLOW_LEGACY_PLAINTEXT_TOKENS"):
        try:
            from config import settings
            allow_legacy_str = str(getattr(settings, "ALLOW_LEGACY_PLAINTEXT_TOKENS", True)).lower()
        except Exception:
            allow_legacy_str = "true"
    allow_legacy = allow_legacy_str in ("1", "true", "yes")

    # If it is encrypted with Fernet
    if is_encrypted(stored_val):
        ciphertext_bytes: bytes | None = None
        if isinstance(stored_val, str):
            if stored_val.startswith(("\\x", "\\X")):
                try:
                    ciphertext_bytes = bytes.fromhex(stored_val[2:])
                except ValueError:
                    ciphertext_bytes = None
            elif stored_val.startswith("gAAAAA"):
                ciphertext_bytes = stored_val.encode("utf-8")
        elif isinstance(stored_val, bytes):
            if stored_val.startswith((b"\\x", b"\\X")):
                try:
                    ciphertext_bytes = bytes.fromhex(stored_val[2:].decode("ascii"))
                except (ValueError, UnicodeDecodeError):
                    ciphertext_bytes = None
            else:
                ciphertext_bytes = stored_val

        if ciphertext_bytes:
            try:
                mf = _get_multi_fernet()
                plaintext_bytes = mf.decrypt(ciphertext_bytes)
                return plaintext_bytes.decode("utf-8")
            except InvalidToken as err:
                logger.error("[Crypto] decrypt_token: InvalidToken — key mismatch or corrupted ciphertext.")
                raise TokenDecryptionError(f"Token decryption failed: invalid token or key mismatch: {err}") from err
            except Exception as exc:
                logger.error(f"[Crypto] decrypt_token unexpected failure: {exc}")
                raise TokenDecryptionError(f"Unexpected token decryption error: {exc}") from exc

    # Value is NOT encrypted: check for legacy plaintext
    if not allow_legacy:
        raise RuntimeError("Legacy unencrypted plaintext tokens are not allowed (ALLOW_LEGACY_PLAINTEXT_TOKENS=False).")

    # Could be plaintext stored in BYTEA (e.g. \\x454141... for "EAA...")
    plaintext_result: str = ""
    if isinstance(stored_val, str):
        if stored_val.startswith(("\\x", "\\X")):
            try:
                decoded_bytes = bytes.fromhex(stored_val[2:])
                plaintext_result = decoded_bytes.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as dec_err:
                raise TokenDecryptionError(f"Cannot decode stored token bytes as UTF-8 plaintext: {dec_err}") from dec_err
        else:
            plaintext_result = stored_val
    elif isinstance(stored_val, bytes):
        try:
            plaintext_result = stored_val.decode("utf-8")
        except UnicodeDecodeError as dec_err:
            raise TokenDecryptionError(f"Cannot decode stored token bytes as UTF-8 plaintext: {dec_err}") from dec_err
    else:
        plaintext_result = str(stored_val)


    if plaintext_result:
        log_key = account_id or plaintext_result[:12]
        if log_key not in _LOGGED_LEGACY_ACCOUNTS:
            _LOGGED_LEGACY_ACCOUNTS.add(log_key)
            logger.warning(
                "[Crypto] Unencrypted legacy plaintext token read for account '%s'. "
                "Returning plaintext as-is. Run scripts/backfill_encrypt_tokens.py to encrypt.",
                account_id or "unknown",
            )
        return plaintext_result

    return ""
