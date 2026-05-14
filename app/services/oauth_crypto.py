"""AES-256-GCM encryption for OAuth tokens at rest.

Key is loaded from the OAUTH_ENCRYPTION_KEY environment variable as base64.
Validation is called from startup_validator at app boot.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_KEY_BYTES = 32
_NONCE_BYTES = 12


class OAuthCryptoError(RuntimeError):
    """Raised when encryption/decryption or key validation fails."""


def _load_key() -> bytes:
    """Read OAUTH_ENCRYPTION_KEY from env, validate, return raw 32-byte key."""
    encoded = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip()
    if not encoded:
        raise OAuthCryptoError(
            "OAUTH_ENCRYPTION_KEY environment variable is not set. "
            "Generate one with: "
            "python -c \"import secrets, base64; "
            "print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise OAuthCryptoError(
            f"OAUTH_ENCRYPTION_KEY is not valid base64: {exc}"
        ) from exc
    if len(raw) != _KEY_BYTES:
        raise OAuthCryptoError(
            f"OAUTH_ENCRYPTION_KEY must decode to exactly {_KEY_BYTES} bytes; "
            f"got {len(raw)}."
        )
    return raw


def encrypt_token_payload(payload: dict) -> Tuple[bytes, bytes, bytes]:
    """Encrypt a JSON-serializable dict; return (ciphertext, nonce, tag).

    The GCM authentication tag is split off the ciphertext (last 16 bytes) so
    callers can persist the fields separately in MongoDB.
    """
    key = _load_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    ciphertext, tag = ct_with_tag[:-16], ct_with_tag[-16:]
    return ciphertext, nonce, tag


def decrypt_token_payload(ciphertext: bytes, nonce: bytes, tag: bytes) -> dict:
    """Decrypt a (ciphertext, nonce, tag) triple back to the original dict."""
    key = _load_key()
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext + tag, associated_data=None)
    except InvalidTag as exc:
        raise OAuthCryptoError(
            "OAuth token decryption failed authentication "
            "(tampered ciphertext, wrong key, or corrupted nonce/tag)."
        ) from exc
    return json.loads(plaintext.decode("utf-8"))


def validate_encryption_key_at_startup(*, has_existing_credentials: bool) -> None:
    """Verify the env var. Called from startup_validator.

    - Missing key + no existing data: warn only (feature unused)
    - Missing key + existing data: fail fast (can't decrypt existing tokens)
    - Wrong format/length: always fail fast
    """
    try:
        _load_key()
    except OAuthCryptoError as exc:
        if "not set" in str(exc) and not has_existing_credentials:
            logger.warning(
                "OAUTH_ENCRYPTION_KEY not set; OAuth subscription auth is "
                "disabled. Set the env var to enable Claude Code / Codex "
                "subscription support."
            )
            return
        raise
