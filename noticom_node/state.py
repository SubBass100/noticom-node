"""Persistent node state: identity key and pairing.

Lives in /etc/noticom by default (override with NOTICOM_STATE_DIR for
development). The private key is written with mode 0600.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import protocol

DEFAULT_STATE_DIR = Path("/etc/noticom")


def state_dir() -> Path:
    """Return the state directory."""
    return Path(os.environ.get("NOTICOM_STATE_DIR", DEFAULT_STATE_DIR))


@dataclass
class Pairing:
    """What the node knows about the server it is bonded to."""

    server_pubkey: str
    server_url: str


class NodeState:
    """Load and save the node's identity and pairing."""

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = directory or state_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._key_path = self.dir / "node.key"
        self._id_path = self.dir / "node_id"
        self._pairing_path = self.dir / "pairing.json"
        self._config_path = self.dir / "config.json"

    @property
    def node_id(self) -> str:
        """Stable node id, created on first use."""
        if not self._id_path.exists():
            self._id_path.write_text(uuid.uuid4().hex[:12])
        return self._id_path.read_text().strip()

    @property
    def key(self) -> Ed25519PrivateKey:
        """Node private key, created on first use."""
        if not self._key_path.exists():
            key = protocol.generate_private_key()
            fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(protocol.private_key_to_b64(key))
        return protocol.private_key_from_b64(self._key_path.read_text().strip())

    @property
    def pairing(self) -> Pairing | None:
        """Current pairing, or None if unclaimed."""
        if not self._pairing_path.exists():
            return None
        data = json.loads(self._pairing_path.read_text())
        return Pairing(data["server_pubkey"], data["server_url"])

    def save_pairing(self, pairing: Pairing) -> None:
        """Bond to a server."""
        tmp = self._pairing_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"server_pubkey": pairing.server_pubkey, "server_url": pairing.server_url}
            )
        )
        tmp.replace(self._pairing_path)

    @property
    def config(self) -> dict:
        """Last config received from HA ({} if none yet)."""
        try:
            return json.loads(self._config_path.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def save_config(self, config: dict) -> None:
        """Cache HA's config so the node starts with it before reconnecting."""
        tmp = self._config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config))
        tmp.replace(self._config_path)

    def factory_reset(self) -> None:
        """Forget the server and its config, and rotate the identity key."""
        for path in (self._pairing_path, self._key_path, self._config_path):
            path.unlink(missing_ok=True)
