"""Updating the node software when Home Assistant asks.

HA only says *which* version to install (a git ref like "main" or "v0.3.0").
The download location is fixed here, so the update command can't be used to
run anything other than this project's own installer.

The installer stops and restarts this service, so it can't run inside it:
it's started as a separate transient systemd unit with `systemd-run`. When
it finishes, the service restarts on the new version and reconnects to HA,
which then sees the new version.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from .hardware import FAKE

_LOGGER = logging.getLogger(__name__)

INSTALL_URL = "https://raw.githubusercontent.com/SubBass100/noticom-node/{ref}/install.sh"
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


class UpdateError(Exception):
    """The update couldn't be started."""


def valid_ref(ref: str) -> bool:
    """A git branch, tag or commit name, with nothing that could escape a URL or shell."""
    return bool(_REF.match(ref)) and ".." not in ref


def install_command(ref: str) -> list[str]:
    """The command that runs the installer for `ref`, detached from this service."""
    url = INSTALL_URL.format(ref=ref)
    script = f"curl -fsSL '{url}' | NOTICOM_REF='{ref}' bash"
    return [
        "systemd-run",
        f"--unit=noticom-update-{int(time.time())}",
        "--collect",
        "--quiet",
        "--description=Noticom node update",
        "/bin/bash", "-c", script,
    ]


async def start_update(ref: str) -> None:
    """Start installing `ref` in the background. Raises UpdateError."""
    if not valid_ref(ref):
        raise UpdateError(f"refusing odd version name {ref!r}")
    command = install_command(ref)
    _LOGGER.warning("Updating node software to %s (the service will restart)", ref)
    if FAKE:
        _LOGGER.info("[fake] would run: %s", " ".join(command))
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as err:
        raise UpdateError("systemd-run is not available") from err
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise UpdateError(err.decode(errors="replace").strip() or "systemd-run failed")
