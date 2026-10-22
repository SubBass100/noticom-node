"""Button and audio, with stand-ins for development off the Pi.

Set NOTICOM_FAKE_HW=1 to run without GPIO or ALSA: the button reports a
press immediately and audio calls are logged instead of played.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import struct
import time
import wave
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

FAKE = os.environ.get("NOTICOM_FAKE_HW") == "1"
# Local button defaults, used before HA has sent this node a config (e.g.
# for the adoption press). Set per node in /etc/noticom/node.env.
# GPIO17 is the HAT's onboard button; 13 is on its Grove connector.
def parse_pin(value) -> int | None:
    """A GPIO number, or None for "no button" (none/0/empty)."""
    if value is None or str(value).strip().lower() in ("", "none", "0", "off"):
        return None
    return int(value)


DEFAULT_BUTTON_PIN = parse_pin(os.environ.get("NOTICOM_BUTTON_PIN", "13"))
DEFAULT_BUTTON_NC = os.environ.get("NOTICOM_BUTTON_NC", "0") == "1"
# Factory reset: this many presses within RESET_WINDOW_S seconds. A gesture,
# not a long hold, because a broken wire on an NC button reads as held forever.
RESET_PRESSES = 5
RESET_WINDOW_S = 4.0
# ALSA card name set by `dtoverlay=wm8960-soundcard,alsaname=seeed2micvoicec`.
CARD = "seeed2micvoicec"
SHORT_SOUND_TIMEOUT_S = 8  # beeps and prompts; chimes/messages use play_file


class _Output:
    """Where sound goes right now (set from the chosen sound card)."""

    device: str | None = f"plughw:CARD={CARD},DEV=0"
    gain: float = 1.0  # software volume, for cards without a volume control
    warned = False


def set_output(device: str | None, gain: float = 1.0) -> None:
    """Point all playback at an ALSA device, with an optional software volume."""
    _Output.device = device
    _Output.gain = max(0.0, min(1.0, gain))
    _Output.warned = False


def _scale(data: bytes) -> bytes:
    """Apply the software volume to 16-bit WAV bytes."""
    if _Output.gain >= 0.999:
        return data
    import numpy as np  # noqa: PLC0415

    with wave.open(io.BytesIO(data)) as wav:
        params = wav.getparams()
        frames = wav.readframes(params.nframes)
    if params.sampwidth != 2:
        return data
    samples = (np.frombuffer(frames, "<i2").astype(np.float32) * _Output.gain).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setparams(params)
        wav.writeframes(samples.tobytes())
    return out.getvalue()


async def _aplay(data: bytes, device: str | None, timeout: float | None) -> tuple[int, str]:
    """Run aplay on WAV bytes. Returns (exit code, stderr). Cancellable."""
    device = device or _Output.device
    if device is None:
        if not _Output.warned:
            _LOGGER.error("No speaker or sound card found; nothing can be played")
            _Output.warned = True
        return 1, "no sound card"
    proc = await asyncio.create_subprocess_exec(
        "aplay", "-q", "-D", device, "-",
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(_scale(data)), timeout)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, err.decode(errors="replace").strip()


class PushButton:
    """A GPIO button to ground, exposed as asyncio events.

    The pin uses the internal pull-up. A normally open button reads high at
    rest and low when pressed; a normally closed one is the reverse, so for
    NC we swap gpiozero's (active-low) press and release.
    """

    def __init__(
        self, pin: int | None = DEFAULT_BUTTON_PIN, normally_closed: bool = DEFAULT_BUTTON_NC
    ) -> None:
        self.pin = pin
        self.requested_pin = pin  # stays set if GPIO turns out to be unusable
        self.normally_closed = normally_closed
        self._loop = asyncio.get_running_loop()
        self.pressed = asyncio.Event()
        self.released = asyncio.Event()
        self.reset_requested = asyncio.Event()
        self._press_times: list[float] = []
        self._button = None
        if FAKE or pin is None:  # no button: pair and reset with `noticom-node` commands
            return
        try:
            from gpiozero import Button  # imported lazily: needs GPIO

            self._button = Button(pin, pull_up=True, bounce_time=0.03)
        except Exception as err:  # noqa: BLE001 - e.g. a PC with no GPIO pins
            _LOGGER.warning(
                "Can't use GPIO%s for the button (%s). Carrying on without a button: "
                "pair and reset with `sudo noticom-node pair` / `reset`.", pin, err,
            )
            self.pin = None
            return
        on_low = lambda: self._loop.call_soon_threadsafe(  # noqa: E731
            self._on_release if normally_closed else self._on_press
        )
        on_high = lambda: self._loop.call_soon_threadsafe(  # noqa: E731
            self._on_press if normally_closed else self._on_release
        )
        self._button.when_pressed = on_low
        self._button.when_released = on_high

    @property
    def is_pressed(self) -> bool:
        """Whether the button is physically held down right now."""
        if self._button is None:
            return False
        low = self._button.is_pressed  # gpiozero: active = pin pulled low
        return not low if self.normally_closed else low

    @property
    def pin_level(self) -> str:
        """Raw pin level, for diagnostics."""
        if self._button is None:
            return "n/a"
        return "LOW" if self._button.is_pressed else "HIGH"

    def _on_press(self) -> None:
        self.released.clear()
        self.pressed.set()
        now = time.monotonic()
        self._press_times = [
            t for t in self._press_times if now - t <= RESET_WINDOW_S
        ] + [now]
        if len(self._press_times) >= RESET_PRESSES:
            self._press_times.clear()
            self.reset_requested.set()

    def _on_release(self) -> None:
        self.pressed.clear()
        self.released.set()

    async def wait_for_press(self, timeout: float) -> bool:
        """Wait for a press; True if pressed within `timeout` seconds."""
        if FAKE:
            _LOGGER.info("[fake] button pressed")
            return True
        if self.pin is None:
            await asyncio.sleep(timeout)
            return False
        self.pressed.clear()
        try:
            await asyncio.wait_for(self.pressed.wait(), timeout)
        except TimeoutError:
            return False
        return True

    def close(self) -> None:
        """Release the GPIO line."""
        if self._button:
            self._button.close()


def beep_wav(freq: float = 1000, ms: int = 120, rate: int = 16000) -> bytes:
    """Generate a short sine beep as WAV bytes (for the PTT/adoption chirp)."""
    frames = int(rate * ms / 1000)
    fade = max(1, frames // 10)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        samples = bytearray()
        for i in range(frames):
            env = min(1.0, i / fade, (frames - i) / fade)
            value = int(12000 * env * math.sin(2 * math.pi * freq * i / rate))
            samples += struct.pack("<h", value)
        wav.writeframes(bytes(samples))
    return buf.getvalue()


async def play_wav_bytes(data: bytes, device: str | None = None) -> None:
    """Play a short sound (beep, prompt). Never raises; problems are logged."""
    if FAKE:
        _LOGGER.info("[fake] play %d bytes", len(data))
        return
    try:
        code, err = await _aplay(data, device, SHORT_SOUND_TIMEOUT_S)
    except TimeoutError:
        _LOGGER.warning(
            "Playing a sound on %s hung for %d s; the sound card isn't working. "
            "Check: speaker-test -D %s -c 2 -t sine -l 1",
            device or _Output.device, SHORT_SOUND_TIMEOUT_S, device or _Output.device,
        )
        return
    if code != 0 and err != "no sound card":
        _LOGGER.warning("Couldn't play a sound on %s: %s", device or _Output.device, err)


async def chirp(device: str | None = None) -> None:
    """Quick beep: 'I'm listening' / 'press to confirm'."""
    await play_wav_bytes(beep_wav(), device)


async def beeps(count: int, freq: float = 1400, device: str | None = None) -> None:
    """A run of short beeps, e.g. 3 for 'reset step reached'."""
    for _ in range(count):
        await play_wav_bytes(beep_wav(freq=freq, ms=110), device)
        await asyncio.sleep(0.08)


async def cancel_tone(device: str | None = None) -> None:
    """A low, longer tone meaning 'cancelled'."""
    await play_wav_bytes(beep_wav(freq=400, ms=450), device)


async def speak(text: str, device: str | None = None) -> None:
    """Say text: the Piper-rendered prompt if there is one, else espeak-ng."""
    if FAKE:
        _LOGGER.info("[fake] say: %s", text)
        return
    from .prompts import cached  # noqa: PLC0415 - keeps hardware import-light

    if (rendered := cached(text)) is not None:
        await play_file(rendered, device)
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "espeak-ng", "-v", "en-us", "-s", "150", "-a", "200", "--stdout", text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        _LOGGER.warning("espeak-ng not installed; can't say: %s", text)
        return
    wav, _ = await proc.communicate()
    if wav:
        await play_wav_bytes(wav, device)


async def play_file(path, device: str | None = None) -> None:
    """Play a WAV file (chime, message). Raises if it can't be played;
    cancelling stops playback immediately."""
    if FAKE:
        _LOGGER.info("[fake] play %s", path)
        await asyncio.sleep(0.05)
        return
    data = await asyncio.get_running_loop().run_in_executor(None, Path(path).read_bytes)
    code, err = await _aplay(data, device, None)
    if code != 0:
        raise RuntimeError(f"aplay failed: {err}")
