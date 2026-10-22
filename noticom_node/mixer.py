"""Sound card mixer settings: volume and microphone levels.

The ReSpeaker 2-Mic HAT (v1, WM8960 codec at I2C 0x1a) gets a tuned set of
controls, found by testing on a Pi 3 with Raspberry Pi OS (Debian 13,
kernel 6.18) and the stock `wm8960-soundcard` overlay. Any other card (USB,
headphone jack, HDMI) gets a generic treatment: the node looks for the
card's playback and capture volume controls and sets those. A card with no
playback volume control gets software volume instead.

The node applies these at startup and whenever HA changes volume or mic
settings, so the node software owns the mixer state.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from .hardware import CARD, FAKE
from .soundcards import KIND_RESPEAKER, AudioSetup

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


async def _amixer(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "amixer", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def apply_respeaker(
    volume: int = DEFAULT_VOLUME,
    mic_gain: int = DEFAULT_MIC_GAIN,
    mic_boost: int = DEFAULT_MIC_BOOST,
) -> bool:
    """Apply the tuned WM8960 state. Returns False if any control failed."""
    ok = True
    for control, values in build_settings(volume, mic_gain, mic_boost):
        if FAKE:
            _LOGGER.debug("[fake] amixer sset %r %s", control, " ".join(values))
            continue
        code, out = await _amixer("-q", "-c", CARD, "sset", control, *values)
        if code != 0:
            ok = False
            _LOGGER.warning("amixer %r failed: %s", control, out.strip())
    return ok


# ---- generic cards -------------------------------------------------------

PLAYBACK_PREFERENCE = ["Speaker", "PCM", "Headphone", "Master", "Playback", "Front"]
# Other controls in the output path (a PC's built-in audio has several):
# turned fully up and unmuted so only the chosen control sets the volume.
PATH_CONTROLS = ["Master", "Headphone", "Speaker", "Front", "Line Out"]
CAPTURE_PREFERENCE = ["Mic", "Capture", "Mic Capture", "Microphone"]


@dataclass(frozen=True)
class Control:
    """One simple mixer control and what it can do."""

    name: str
    caps: frozenset[str]


_CONTROL = re.compile(r"^Simple mixer control '(.+)',(\d+)$")


def parse_scontents(text: str) -> list[Control]:
    """Parse `amixer scontents` output into controls with their capabilities."""
    controls: list[Control] = []
    name: str | None = None
    for line in text.splitlines():
        if match := _CONTROL.match(line.strip()):
            name = match.group(1) if match.group(2) == "0" else None
        elif name is not None and line.strip().startswith("Capabilities:"):
            controls.append(Control(name, frozenset(line.split(":", 1)[1].split())))
            name = None
    return controls


def pick_control(controls: list[Control], cap: str, preference: list[str], avoid: tuple[str, ...]) -> Control | None:
    """The best control with capability `cap` (e.g. pvolume, cvolume)."""
    usable = [c for c in controls if cap in c.caps]
    for wanted in preference:
        for control in usable:
            if control.name.lower() == wanted.lower():
                return control
    for control in usable:
        if not any(word in control.name.lower() for word in avoid):
            return control
    return None


def path_controls(controls: list[Control], chosen: Control) -> list[Control]:
    """Output-path controls other than the volume control."""
    wanted = {name.lower() for name in PATH_CONTROLS}
    return [
        c for c in controls
        if c.name != chosen.name and c.name.lower() in wanted and "pvolume" in c.caps
    ]


async def _controls(card_id: str) -> list[Control]:
    code, out = await _amixer("-c", card_id, "scontents")
    return parse_scontents(out) if code == 0 else []


async def apply_generic(setup: AudioSetup, volume: int, mic_gain: int) -> tuple[bool, float]:
    """Set volume (and mic gain) on a non-ReSpeaker card.

    Returns (ok, software_gain): software_gain is 1.0 when the card's own
    volume control is used, or volume/100 when it has none.
    """
    ok, software_gain = True, 1.0
    volume, mic_gain = clamp(volume, 0, 100), clamp(mic_gain, 0, 100)
    if setup.output is not None and setup.output.kind != KIND_RESPEAKER:
        controls = await _controls(setup.output.id)
        control = pick_control(controls, "pvolume", PLAYBACK_PREFERENCE, ("mic", "capture"))
        if control is None:
            software_gain = volume / 100
            _LOGGER.info(
                "%s has no volume control; using software volume (%d%%)",
                setup.output.label, volume,
            )
        else:
            args = [f"{volume}%"] + (["unmute"] if "pswitch" in control.caps else [])
            # -M maps percentages to how loud they sound, not raw steps.
            code, out = await _amixer("-q", "-M", "-c", setup.output.id, "sset", control.name, *args)
            if code != 0:
                ok, software_gain = False, volume / 100
                _LOGGER.warning("Couldn't set %s volume: %s", setup.output.label, out.strip())
            for other in path_controls(controls, control):
                path_args = ["100%"] + (["unmute"] if "pswitch" in other.caps else [])
                await _amixer("-q", "-M", "-c", setup.output.id, "sset", other.name, *path_args)
    if setup.input is not None and setup.input.kind != KIND_RESPEAKER:
        controls = await _controls(setup.input.id)
        control = pick_control(controls, "cvolume", CAPTURE_PREFERENCE, ("playback",))
        if control is not None:
            args = [f"{mic_gain}%"] + (["cap"] if "cswitch" in control.caps else [])
            code, out = await _amixer("-q", "-M", "-c", setup.input.id, "sset", control.name, *args)
            if code != 0:
                ok = False
                _LOGGER.warning("Couldn't set %s mic gain: %s", setup.input.label, out.strip())
    return ok, software_gain


async def apply(
    setup: AudioSetup,
    volume: int = DEFAULT_VOLUME,
    mic_gain: int = DEFAULT_MIC_GAIN,
    mic_boost: int = DEFAULT_MIC_BOOST,
) -> tuple[bool, float]:
    """Apply levels to whichever cards are in use. Returns (ok, software_gain)."""
    if FAKE:
        return True, 1.0
    ok, software_gain = True, 1.0
    uses_respeaker = any(
        card is not None and card.kind == KIND_RESPEAKER for card in (setup.output, setup.input)
    )
    if uses_respeaker:
        ok = await apply_respeaker(volume, mic_gain, mic_boost)
    if setup.output is not None and setup.output.kind != KIND_RESPEAKER or (
        setup.input is not None and setup.input.kind != KIND_RESPEAKER
    ):
        generic_ok, software_gain = await apply_generic(setup, volume, mic_gain)
        ok = ok and generic_ok
    return ok, software_gain
