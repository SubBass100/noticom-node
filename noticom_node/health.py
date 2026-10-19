"""Keep the node reachable and report hardware problems clearly.

- Wi-Fi power saving makes the Pi miss incoming ARP lookups, so Home
  Assistant gets "No route to host" while the node can still reach HA. OS
  settings to disable it don't reliably survive a reboot (for example when
  Raspberry Pi Imager sets Wi-Fi up via cloud-init/netplan), so the node
  turns it off itself at startup and re-checks every minute.
- The sound hardware in use is logged at startup, and a node with no
  speaker at all (usually: not rebooted after installing) is logged loudly
  instead of failing silently on every chime.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .hardware import FAKE

_LOGGER = logging.getLogger(__name__)

POWERSAVE_CHECK_S = 60
NET = Path("/sys/class/net")


def wireless_interfaces(net: Path = NET) -> list[str]:
    """Names of wireless network interfaces (e.g. wlan0)."""
    try:
        return sorted(p.parent.name for p in net.glob("*/wireless"))
    except OSError:
        return []


async def _iw(*args: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "iw", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 127, "iw is not installed"
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def disable_wifi_powersave(interfaces: list[str] | None = None) -> list[str]:
    """Turn Wi-Fi power saving off where it's on. Returns interfaces changed."""
    changed = []
    for iface in wireless_interfaces() if interfaces is None else interfaces:
        code, out = await _iw("dev", iface, "get", "power_save")
        if code != 0 or "on" not in out.lower().split(":")[-1]:
            continue
        code, out = await _iw("dev", iface, "set", "power_save", "off")
        if code == 0:
            changed.append(iface)
        else:
            _LOGGER.warning("Couldn't turn off Wi-Fi power saving on %s: %s", iface, out)
    return changed


async def keep_wifi_awake() -> None:
    """Run forever, keeping Wi-Fi power saving off."""
    if FAKE:
        return
    first = True
    while True:
        changed = await disable_wifi_powersave()
        if changed:
            log = _LOGGER.info if first else _LOGGER.warning
            log(
                "Turned off Wi-Fi power saving on %s so Home Assistant can reach this node",
                ", ".join(changed),
            )
        first = False
        await asyncio.sleep(POWERSAVE_CHECK_S)


def check_audio(setup) -> bool:
    """Log what the node will play on and record from. False if no speaker."""
    if FAKE:
        return True
    if setup.output is None:
        _LOGGER.error(
            "No speaker or sound card found, so nothing will play. If you just "
            "installed with a ReSpeaker HAT, reboot the Pi (sudo reboot). Otherwise "
            "plug in a USB sound card or a speaker on the headphone jack."
        )
        return False
    mic = (
        f"microphone: {setup.input.label}"
        if setup.input is not None
        else "no microphone, so this node plays chimes only"
    )
    _LOGGER.info("Sound: playing on %s; %s", setup.output.label, mic)
    return True
