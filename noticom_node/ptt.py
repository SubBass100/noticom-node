"""Push-to-talk: hold the button to record, release to send."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .audio import SAMPLE_RATE, process_recording
from .hardware import CARD, FAKE

_LOGGER = logging.getLogger(__name__)

HOLD_TO_TALK_S = 0.25  # a press must last this long to start recording (taps stay silent)
MIN_MESSAGE_S = 0.5  # shorter recordings are discarded
MAX_MESSAGE_S = 45.0
CAPTURE_DEVICE = f"plughw:CARD={CARD},DEV=0"
BYTES_PER_SECOND = SAMPLE_RATE * 2 * 2  # stereo 16-bit


class Recorder:
    """Records raw 16 kHz stereo 16-bit PCM from the HAT's two mics."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._chunks: list[bytes] = []
        self._reader: asyncio.Task | None = None
        self._started = 0.0

    async def start(self) -> None:
        self._chunks = []
        self._started = time.monotonic()
        if FAKE:
            return
        self._proc = await asyncio.create_subprocess_exec(
            "arecord", "-q", "-D", CAPTURE_DEVICE, "-f", "S16_LE",
            "-r", str(SAMPLE_RATE), "-c", "2", "-t", "raw",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self._proc and self._proc.stdout
        while chunk := await self._proc.stdout.read(8192):
            self._chunks.append(chunk)

    async def stop(self) -> bytes:
        """Stop and return everything recorded."""
        if FAKE:
            seconds = time.monotonic() - self._started
            return bytes(int(seconds * BYTES_PER_SECOND) // 4 * 4)
        if self._proc is not None:
            if self._proc.returncode is None:
                self._proc.terminate()
            await self._proc.wait()
            if self._reader is not None:
                await self._reader
            self._proc = None
        return b"".join(self._chunks)


Feedback = Callable[[], Awaitable[None]]
Upload = Callable[[bytes], Awaitable[bool]]


class PttController:
    """Turns button holds into uploaded messages."""

    def __init__(
        self,
        button,
        recorder,
        chirp: Feedback,
        fail_tone: Feedback,
        set_hold: Callable[[bool], None],
        hold_s: float = HOLD_TO_TALK_S,
        min_s: float = MIN_MESSAGE_S,
        max_s: float = MAX_MESSAGE_S,
    ) -> None:
        self.button = button
        self.recorder = recorder
        self.chirp = chirp
        self.fail_tone = fail_tone
        self.set_hold = set_hold  # pauses the player while recording
        self.hold_s, self.min_s, self.max_s = hold_s, min_s, max_s
        self.enabled = True
        self.suspended = False  # e.g. during the factory-reset confirmation
        self.upload: Upload | None = None  # set while connected to HA
        self.on_phase: Callable[[str], Awaitable[None]] | None = None

    async def run(self) -> None:
        """Watch the button forever."""
        while True:
            self.button.pressed.clear()
            await self.button.pressed.wait()
            if not self.enabled or self.suspended:
                continue
            try:
                await self._handle_press()
            except Exception:  # noqa: BLE001 - never let PTT kill the node
                _LOGGER.exception("Push-to-talk failed")
                self.set_hold(False)

    async def _held_for(self, seconds: float) -> bool:
        """True if the button is still down after `seconds`."""
        self.button.released.clear()
        if not self.button.is_pressed:
            return False
        try:
            await asyncio.wait_for(self.button.released.wait(), seconds)
        except TimeoutError:
            return True
        return False

    async def _handle_press(self) -> None:
        if not await self._held_for(self.hold_s):
            return  # a tap (part of the reset gesture, or accidental)
        if self.suspended:
            return
        self.set_hold(True)
        try:
            await self.chirp()  # finish the chirp before recording so it isn't captured
            await self.recorder.start()
            if self.on_phase:
                await self.on_phase("start")
            started = time.monotonic()
            if self.button.is_pressed:
                self.button.released.clear()
                try:
                    await asyncio.wait_for(self.button.released.wait(), self.max_s)
                except TimeoutError:
                    _LOGGER.info("Message hit the %.0f s limit", self.max_s)
            raw = await self.recorder.stop()
            duration = time.monotonic() - started
        finally:
            self.set_hold(False)

        if duration < self.min_s or not raw:
            _LOGGER.info("Message too short (%.1f s); discarded", duration)
            return
        wav = await asyncio.get_running_loop().run_in_executor(None, process_recording, raw)
        if self.upload is None:
            _LOGGER.warning("Not connected to Home Assistant; message not sent")
            await self.fail_tone()
            return
        if not await self.upload(wav):
            await self.fail_tone()
            return
        _LOGGER.info("Sent a %.1f s message", duration)
