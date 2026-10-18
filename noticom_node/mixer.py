"""WM8960 mixer setup for the ReSpeaker 2-Mic HAT (v1, codec at I2C 0x1a).

Values found by testing on a Pi 3 with Raspberry Pi OS (Debian 13, kernel
6.18) using the stock `wm8960-soundcard` overlay. The node applies these at
startup and whenever HA changes volume or mic settings, so the node software
owns the mixer state.
"""

from __future__ import annotations

import asyncio
import logging

from .hardware import CARD, FAKE

_LOGGER = logging.getLogger(__name__)

DEFAULT_VOLUME = 100  # 'Speaker' %, the HA volume setting
DEFAULT_MIC_GAIN = 90  # 'Capture' % (input PGA), the HA mic gain setting
DEFAULT_MIC_BOOST = 3  # 'Input Boost Mixer' 0-3 (0/13/20/29 dB), the HA mic boost

# Fixed routing and gain stages. The WM8960 powers up with its output
# mixers and mic inputs disconnected, so all of these are required.
BASE_SETTINGS: list[tuple[str, str]] = [
    # Output: DAC -> output mixers -> speaker amp, mono to both channels
    # (the speaker is wired to one side).
    ("Left Output Mixer PCM", "on"),
    ("Right Output Mixer PCM", "on"),
    ("DAC Mono Mix", "Mono"),
    ("PCM Playback -6dB", "off"),
    ("Playback", "100%"),  # DAC digital volume, 100% = 0 dB
    ("Speaker AC", "5"),  # class-D boost, 5 = 1.8x
    ("Speaker DC", "5"),
    # Input: both mics (LINPUT1/RINPUT1) -> boost -> ADC.
    ("Left Boost Mixer LINPUT1", "on"),
    ("Right Boost Mixer RINPUT1", "on"),
    ("Left Input Mixer Boost", "on"),
    ("Right Input Mixer Boost", "on"),
]


def clamp(value: int, low: int, high: int) -> int:
    """Clamp an int into [low, high]."""
    return max(low, min(high, int(value)))


def build_settings(
    volume: int = DEFAULT_VOLUME,
    mic_gain: int = DEFAULT_MIC_GAIN,
    mic_boost: int = DEFAULT_MIC_BOOST,
) -> list[tuple[str, list[str]]]:
    """Return (control, amixer values) pairs for the full mixer state."""
    settings = [(name, [value]) for name, value in BASE_SETTINGS]
    boost = str(clamp(mic_boost, 0, 3))
    settings += [
        ("Speaker", [f"{clamp(volume, 0, 100)}%"]),
        ("Capture", [f"{clamp(mic_gain, 0, 100)}%", "cap"]),
        ("Left Input Boost Mixer LINPUT1", [boost]),
        ("Right Input Boost Mixer RINPUT1", [boost]),
    ]
    return settings


async def apply(
    volume: int = DEFAULT_VOLUME,
    mic_gain: int = DEFAULT_MIC_GAIN,
    mic_boost: int = DEFAULT_MIC_BOOST,
) -> bool:
    """Apply the mixer state with amixer. Returns False if any control failed."""
    ok = True
    for control, values in build_settings(volume, mic_gain, mic_boost):
        if FAKE:
            _LOGGER.debug("[fake] amixer sset %r %s", control, " ".join(values))
            continue
        proc = await asyncio.create_subprocess_exec(
            "amixer", "-q", "-c", CARD, "sset", control, *values,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            ok = False
            _LOGGER.warning("amixer %r failed: %s", control, err.decode().strip())
    return ok
