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

_LOGGER = logging.getLogger(__name__)

FAKE = os.environ.get("NOTICOM_FAKE_HW") == "1"
# Local button defaults, used before HA has sent this node a config (e.g.
# for the adoption press). Set per node in /etc/noticom/node.env.
# GPIO17 is the HAT's onboard button; 13 is on its Grove connector.
DEFAULT_BUTTON_PIN = int(os.environ.get("NOTICOM_BUTTON_PIN", "13"))
DEFAULT_BUTTON_NC = os.environ.get("NOTICOM_BUTTON_NC", "1") == "1"
# Factory reset: this many presses within RESET_WINDOW_S seconds. A gesture,
# not a long hold, because a broken wire on an NC button reads as held forever.
RESET_PRESSES = 5
RESET_WINDOW_S = 4.0
# ALSA card name set by `dtoverlay=wm8960-soundcard,alsaname=seeed2micvoicec`.
CARD = "seeed2micvoicec"
# The Pi's default card is its headphone jack, so always name the HAT.
OUTPUT_DEVICE = f"plughw:CARD={CARD},DEV=0"


class PushButton:
    """A GPIO button to ground, exposed as asyncio events.

    The pin uses the internal pull-up. A normally open button reads high at
    rest and low when pressed; a normally closed one is the reverse, so for
    NC we swap gpiozero's (active-low) press and release.
    """

    def __init__(
        self, pin: int = DEFAULT_BUTTON_PIN, normally_closed: bool = DEFAULT_BUTTON_NC
    ) -> None:
        self.pin = pin
        self.normally_closed = normally_closed
        self._loop = asyncio.get_running_loop()
        self.pressed = asyncio.Event()
        self.released = asyncio.Event()
        self.reset_requested = asyncio.Event()
        self._press_times: list[float] = []
        self._button = None
        if FAKE:
            return
        from gpiozero import Button  # imported lazily: needs GPIO

        self._button = Button(pin, pull_up=True, bounce_time=0.03)
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


async def play_wav_bytes(data: bytes, device: str = OUTPUT_DEVICE) -> None:
    """Play WAV bytes through ALSA via aplay."""
    if FAKE:
        _LOGGER.info("[fake] play %d bytes on %s", len(data), device)
        return
    proc = await asyncio.create_subprocess_exec(
        "aplay", "-q", "-D", device, "-",
        stdin=asyncio.subprocess.PIPE,
    )
    await proc.communicate(data)


async def chirp(device: str = OUTPUT_DEVICE) -> None:
    """Quick beep: 'I'm listening' / 'press to confirm'."""
    await play_wav_bytes(beep_wav(), device)
