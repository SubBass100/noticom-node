"""Local chime cache, keyed by SHA-256 hash (docs/DESIGN.md §2-3)."""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("/var/cache/noticom/chimes")

Fetch = Callable[[str], Awaitable[bytes]]


def cache_dir() -> Path:
    """The cache directory (override with NOTICOM_CACHE_DIR)."""
    return Path(os.environ.get("NOTICOM_CACHE_DIR", DEFAULT_CACHE_DIR))


class ChimeCache:
    """Holds the chimes HA lists, downloading missing ones by hash."""

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = directory or cache_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.names: dict[str, str] = {}  # hash -> name, from the last manifest

    def path(self, chime_hash: str) -> Path | None:
        """The cached file for a hash, if present."""
        path = self.dir / f"{chime_hash}.wav"
        return path if path.exists() else None

    async def fetch(self, chime_hash: str, fetch: Fetch) -> Path:
        """Download one chime, verify its hash, and store it."""
        data = await fetch(chime_hash)
        if hashlib.sha256(data).hexdigest() != chime_hash:
            raise ValueError(f"chime {chime_hash[:12]} failed its hash check")
        path = self.dir / f"{chime_hash}.wav"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    async def sync(self, manifest: list[dict], fetch: Fetch) -> None:
        """Make the cache match HA's chime list."""
        self.names = {c["hash"]: c["name"] for c in manifest if "hash" in c}
        for chime_hash, name in self.names.items():
            if self.path(chime_hash):
                continue
            try:
                await self.fetch(chime_hash, fetch)
                _LOGGER.info("Downloaded chime %s", name)
            except Exception as err:  # noqa: BLE001 - keep syncing the rest
                _LOGGER.warning("Couldn't download chime %s: %r", name, err)
        for path in self.dir.glob("*.wav"):
            if path.stem not in self.names:
                path.unlink(missing_ok=True)
