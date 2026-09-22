#!/usr/bin/env python3
"""
Backfill Script: Encrypt Plaintext Access Tokens in communication_accounts.

Features:
  - Dry-run by default (run with --execute to write changes).
  - Idempotent: safe to run multiple times without re-encrypting or corrupting data.
  - Verifies by decrypting ciphertext before writing to the database.
  - Handles both legacy `access_token` (VARCHAR/TEXT) and `access_token_enc` (BYTEA).
  - Nulls legacy plaintext `access_token` only after successful verified write.

Usage:
    # Dry-run (default, inspect and report without modifying database):
    python backend/scripts/backfill_encrypt_tokens.py
    python backend/scripts/backfill_encrypt_tokens.py --dry-run

    # Execute encryption backfill:
    python backend/scripts/backfill_encrypt_tokens.py --execute

Requires:
    TOKEN_ENCRYPTION_KEY set in environment or .env
"""
import argparse
import logging
import os
import sys

# Ensure backend root is on sys.path
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from database.supabase_client import get_supabase
from utils.crypto import encrypt_token, is_encrypted, decrypt_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_encrypt_tokens")


def run_backfill(execute: bool = False, sb_client=None) -> dict:
    """
    Run the backfill logic. Separated from CLI entrypoint for direct testability.
    Returns a summary dictionary of results.
    """
    sb = sb_client or get_supabase()

    logger.warning("MAINTENANCE NOTICE: Please run this script during a low-traffic maintenance window to prevent concurrent token updates.")

    # Paginated select with deterministic order("id") to handle large tables safely
    PAGE_SIZE = 100
    offset = 0
    rows = []
    while True:
        try:
            res = (
                sb.table("communication_accounts")
                .select("*")
                .order("id")
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )

            batch = res.data or []
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        except Exception as e:
            logger.error(f"Failed to query communication_accounts (offset={offset}): {e}")
            raise

    logger.info(f"Fetched {len(rows)} accounts to evaluate (execute={execute}).")

    unencrypted_count = 0
    already_encrypted_count = 0
    updated_count = 0
    verified_count = 0
    errors_count = 0

    for row in rows:
        account_id = row.get("id")
        agency_id = row.get("agency_id")
        provider = row.get("provider")

        enc_val = row.get("access_token_enc")
        legacy_val = row.get("access_token")

        # 1. Check if access_token_enc is already populated and encrypted
        if enc_val and is_encrypted(enc_val):
            already_encrypted_count += 1
            # Verify it decrypts cleanly
            try:
                decrypted = decrypt_token(enc_val, account_id=account_id)
                if decrypted:
                    logger.debug(f"Account {account_id}: Token already encrypted and valid.")
                    # If legacy access_token column exists in row and holds plaintext, null it out
                    if "access_token" in row and legacy_val and execute:
                        try:
                            sb.table("communication_accounts").update({"access_token": None}).eq("id", account_id).execute()
                            logger.info(f"Account {account_id}: Nulled redundant plaintext access_token.")
                        except Exception as err:
                            logger.warning(f"Account {account_id}: Could not null redundant plaintext: {err}")
            except Exception as dec_err:
                logger.warning(f"Account {account_id}: Encrypted token could NOT be decrypted (key mismatch?): {dec_err}")
            continue

        # 2. Extract plaintext candidate: unencrypted access_token_enc or legacy access_token
        candidate = legacy_val or enc_val
        if not candidate:
            logger.debug(f"Account {account_id} (agency {agency_id}): No token stored, skipping.")
            continue

        # Verify candidate is indeed unencrypted
        if is_encrypted(candidate):
            already_encrypted_count += 1
            continue

        # If candidate is stored as BYTEA hex (\x...): decode hex of ASCII to string
        if isinstance(candidate, str) and candidate.startswith(("\\x", "\\X")):
            try:
                plain_token = bytes.fromhex(candidate[2:]).decode("utf-8").strip()
            except (ValueError, UnicodeDecodeError):
                plain_token = candidate.strip()
        elif isinstance(candidate, bytes):
            try:
                plain_token = candidate.decode("utf-8").strip()
            except UnicodeDecodeError:
                plain_token = str(candidate).strip()
        else:
            plain_token = str(candidate).strip()

        # Sanity-check plaintext: non-empty, printable, not starting with \x or gAAAAA
        if not plain_token or not plain_token.isprintable() or plain_token.startswith(("\\x", "\\X", "gAAAAA")):
            logger.error(f"Account {account_id}: Plaintext sanity check failed for candidate token. Skipping.")
            errors_count += 1
            continue

        unencrypted_count += 1
        # Never log token prefixes or suffixes: log only length and account id
        logger.info(
            f"Account {account_id} (agency {agency_id}, {provider}): "
            f"Found plaintext token of length {len(plain_token)} characters"
        )

        # 3. Encrypt and pre-verify before writing
        try:
            encrypted = encrypt_token(plain_token)
            verified_plain = decrypt_token(encrypted, account_id=account_id)
            if verified_plain != plain_token:
                raise ValueError("Decrypted token does not match original plaintext pre-write verification!")
            verified_count += 1
        except Exception as crypt_err:
            logger.error(f"Account {account_id}: Pre-write encryption/decryption verification failed: {crypt_err}")
            errors_count += 1
            continue

        # 4. Write verified ciphertext and null legacy plaintext only if column exists in row
        if execute:
            update_payload = {
                "access_token_enc": encrypted,
            }
            if "access_token" in row:
                update_payload["access_token"] = None

            try:
                sb.table("communication_accounts").update(update_payload).eq("id", account_id).execute()
                updated_count += 1
                logger.info(f"Account {account_id}: Successfully encrypted access_token_enc.")
            except Exception as update_err:
                logger.error(f"Account {account_id}: Failed to update database: {update_err}")
                errors_count += 1

    summary = {
        "total": len(rows),
        "already_encrypted": already_encrypted_count,
        "unencrypted_found": unencrypted_count,
        "verified": verified_count,
        "updated": updated_count,
        "errors": errors_count,
        "is_dry_run": not execute,
    }

    logger.info("=" * 60)
    logger.info("Backfill Summary:")
    logger.info(f"  Total Accounts Checked: {summary['total']}")
    logger.info(f"  Already Encrypted:      {summary['already_encrypted']}")
    logger.info(f"  Unencrypted Found:      {summary['unencrypted_found']}")
    logger.info(f"  Pre-write Verified:     {summary['verified']}")
    if execute:
        logger.info(f"  Successfully Updated:   {summary['updated']}")
        logger.info(f"  Errors Encountered:     {summary['errors']}")
    else:
        logger.info("  DRY-RUN completed. No changes written to database. Run with --execute to apply.")
    logger.info("=" * 60)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Encrypt plaintext tokens in communication_accounts")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=False, help="Inspect and report without modifying database (default)")
    group.add_argument("--execute", action="store_true", default=False, help="Perform the actual database update")
    args = parser.parse_args()

    execute_mode = args.execute
    if not execute_mode:
        logger.info("Running in DRY-RUN mode by default. Pass --execute to apply changes.")

    run_backfill(execute=execute_mode)


if __name__ == "__main__":
    main()
