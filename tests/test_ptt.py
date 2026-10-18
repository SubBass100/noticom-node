"""Push-to-talk: taps, holds, limits and failure feedback."""

import asyncio
import io
import wave

from noticom_node.ptt import PttController

from .test_reset import FakeButton


class FakeRecorder:
    def __init__(self):
        self.recording = False
        self.started_at = None

    async def start(self):
        self.recording = True
        self.started_at = asyncio.get_running_loop().time()

    async def stop(self):
        self.recording = False
        secs = asyncio.get_running_loop().time() - self.started_at
        return bytes(int(secs * 16000) * 4)  # stereo 16-bit silence


class Harness:
    def __init__(self, upload_ok=True, connected=True):
        self.button = FakeButton()
        self.recorder = FakeRecorder()
        self.events, self.uploads, self.holds = [], [], []

        async def chirp():
            assert not self.recorder.recording, "chirp must finish before recording"
            self.events.append("chirp")

        async def fail():
            self.events.append("fail")

        async def upload(wav):
            self.uploads.append(wav)
            return upload_ok

        self.ptt = PttController(
            self.button, self.recorder, chirp, fail, self.holds.append,
            hold_s=0.05, min_s=0.1, max_s=0.5,
        )
        if connected:
            self.ptt.upload = upload
        self.task = asyncio.create_task(self.ptt.run())

    async def hold(self, seconds):
        await asyncio.sleep(0.01)
        self.button.press()
        await asyncio.sleep(seconds)
        self.button.release()
        await asyncio.sleep(0.1)

    def close(self):
        self.task.cancel()


async def test_tap_is_silent():
    h = Harness()
    await h.hold(0.02)  # shorter than hold_s
    assert h.events == [] and h.uploads == [] and h.holds == []
    h.close()


async def test_hold_records_and_uploads_mono_wav():
    h = Harness()
    await h.hold(0.3)
    assert h.events == ["chirp"]
    assert h.holds == [True, False]  # playback paused only while recording
    assert len(h.uploads) == 1
    with wave.open(io.BytesIO(h.uploads[0])) as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 16000
    h.close()


async def test_too_short_message_discarded():
    h = Harness()
    await h.hold(0.1)  # passes hold_s (0.05) but records < min_s (0.1)
    assert h.events == ["chirp"] and h.uploads == []
    h.close()


async def test_max_length_stops_recording():
    h = Harness()
    h_task = asyncio.create_task(h.hold(1.2))  # max_s is 0.5
    await asyncio.sleep(0.8)
    assert len(h.uploads) == 1  # sent at the limit, while still held
    await h_task
    h.close()


async def test_offline_or_refused_plays_fail_tone():
    for h in (Harness(connected=False), Harness(upload_ok=False)):
        await h.hold(0.3)
        assert h.events == ["chirp", "fail"]
        h.close()


async def test_suspended_or_disabled_does_nothing():
    h = Harness()
    h.ptt.suspended = True
    await h.hold(0.3)
    h.ptt.suspended = False
    h.ptt.enabled = False
    await h.hold(0.3)
    assert h.events == [] and h.uploads == []
    h.close()
