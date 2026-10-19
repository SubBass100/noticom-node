"""Wire protocol helpers (node side).

Kept free of Home Assistant imports so it can be unit-tested on its own.
Mirror of custom_components/noticom/protocol.py in the noticom repo;
https://github.com/SubBass100/noticom/blob/main/docs/DESIGN.md is the spec.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

PROTOCOL_VERSION = 1

# Server -> node message types.
T_CHALLENGE = "challenge"
T_WELCOME = "welcome"
T_CONFIG = "config"
T_CHIMES = "chimes"
T_PLAY = "play"
T_STOP = "stop"
T_UNPAIR = "unpair"
T_UPDATE = "update"  # install a node software version: {"ref": "main"}
T_ERROR = "error"

# Node -> server message types.
T_AUTH = "auth"
T_STATE = "state"
T_PTT = "ptt"
T_JOB = "job"
T_CONFIG_RESULT = "config_result"

# Job statuses reported by nodes.
JOB_STARTED = "started"
JOB_DONE = "done"
JOB_DROPPED = "dropped"
JOB_ERROR = "error"
JOB_FINAL = frozenset({JOB_DONE, JOB_DROPPED, JOB_ERROR})

KIND_CHIME = "chime"
KIND_CLIP = "clip"


def b64e(data: bytes) -> str:
    """Encode bytes as standard base64 text."""
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    """Decode standard base64 text."""
    return base64.b64decode(text.encode("ascii"), validate=True)


def new_nonce() -> str:
    """Return a fresh 32-byte nonce, base64 encoded."""
    return b64e(secrets.token_bytes(32))


def generate_private_key() -> Ed25519PrivateKey:
    """Generate a new Ed25519 private key."""
    return Ed25519PrivateKey.generate()


def private_key_to_b64(key: Ed25519PrivateKey) -> str:
    """Serialize a private key as raw bytes, base64 encoded."""
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    return b64e(raw)


def private_key_from_b64(text: str) -> Ed25519PrivateKey:
    """Load a private key from base64 raw bytes."""
    return Ed25519PrivateKey.from_private_bytes(b64d(text))


def public_key_b64(key: Ed25519PrivateKey) -> str:
    """Return the base64 raw public key for a private key."""
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return b64e(raw)


def sign(key: Ed25519PrivateKey, message: bytes) -> str:
    """Sign a message, returning a base64 signature."""
    return b64e(key.sign(message))


def verify(pubkey_b64: str, message: bytes, sig_b64: str) -> bool:
    """Verify a base64 signature against a base64 raw public key."""
    try:
        Ed25519PublicKey.from_public_bytes(b64d(pubkey_b64)).verify(
            b64d(sig_b64), message
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def request_signing_payload(method: str, path: str, timestamp: int) -> bytes:
    """Build the bytes signed for an authenticated HTTP request."""
    return f"{method.upper()}\n{path}\n{timestamp}".encode()


def verify_request(
    pubkey_b64: str,
    method: str,
    path: str,
    timestamp: str,
    sig_b64: str,
    max_age_s: int = 30,
    now: float | None = None,
) -> bool:
    """Verify a signed HTTP request, rejecting stale or future timestamps."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > max_age_s:
        return False
    return verify(pubkey_b64, request_signing_payload(method, path, ts), sig_b64)


def unpair_payload(node_nonce_b64: str) -> bytes:
    """Bytes the server signs to tell a node to forget it.

    Bound to the node's own handshake nonce, so it can't be replayed on a
    later connection.
    """
    return b"unpair\n" + b64d(node_nonce_b64)


def encode(msg_type: str, **fields: Any) -> str:
    """Encode a protocol message as JSON text."""
    return json.dumps({"t": msg_type, **fields}, separators=(",", ":"))


def decode(text: str) -> dict[str, Any]:
    """Decode a protocol message, requiring an object with a string `t`."""
    msg = json.loads(text)
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
        raise ValueError("protocol message must be an object with a 't' field")
    return msg
