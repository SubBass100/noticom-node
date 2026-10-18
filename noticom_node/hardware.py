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
import wave

_LOGGER = logging.getLogger(__name__)

FAKE = os.environ.get("NOTICOM_FAKE_HW") == "1"
DEFAULT_BUTTON_PIN = 17  # ReSpeaker 2-Mic HAT onboard button


class PushButton:
    """A GPIO button exposed as asyncio events."""

    def __init__(self, pin: int = DEFAULT_BUTTON_PIN) -> None:
        self._loop = asyncio.get_running_loop()
        self.pressed = asyncio.Event()
        self.released = asyncio.Event()
        self._button = None
        if FAKE:
            return
        from gpiozero import Button  # imported lazily: needs GPIO

        self._button = Button(pin, pull_up=True, bounce_time=0.03)
        self._button.when_pressed = lambda: self._loop.call_soon_threadsafe(
            self._on_press
        )
        self._button.when_released = lambda: self._loop.call_soon_threadsafe(
            self._on_release
        )

    @property
    def is_pressed(self) -> bool:
        """Current button state."""
        return bool(self._button and self._button.is_pressed)

    def _on_press(self) -> None:
        self.released.clear()
        self.pressed.set()

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


async def play_wav_bytes(data: bytes, device: str = "default") -> None:
    """Play WAV bytes through ALSA via aplay."""
    if FAKE:
        _LOGGER.info("[fake] play %d bytes on %s", len(data), device)
        return
    proc = await asyncio.create_subprocess_exec(
        "aplay", "-q", "-D", device, "-",
        stdin=asyncio.subprocess.PIPE,
    )
    await proc.communicate(data)


async def chirp(device: str = "default") -> None:
    """Quick beep: 'I'm listening' / 'press to confirm'."""
    await play_wav_bytes(beep_wav(), device)
