"""Chime cache and player."""

import asyncio
import hashlib

import pytest

from noticom_node.chimecache import ChimeCache
from noticom_node.player import Player
from noticom_node.playqueue import Job

WAV = b"RIFF fake chime"
HASH = hashlib.sha256(WAV).hexdigest()


async def fetch_ok(chime_hash):
    assert chime_hash == HASH
    return WAV


async def test_sync_downloads_verifies_and_prunes(tmp_path):
    cache = ChimeCache(tmp_path)
    (tmp_path / ("0" * 64 + ".wav")).write_bytes(b"stale")
    await cache.sync([{"name": "doorbell", "hash": HASH, "size": len(WAV)}], fetch_ok)
    assert cache.path(HASH).read_bytes() == WAV
    assert not (tmp_path / ("0" * 64 + ".wav")).exists()
    assert cache.names == {HASH: "doorbell"}


async def test_sync_rejects_tampered_download(tmp_path):
    async def fetch_bad(_):
        return b"not what was promised"

    cache = ChimeCache(tmp_path)
    await cache.sync([{"name": "doorbell", "hash": HASH}], fetch_bad)
    assert cache.path(HASH) is None


class Harness:
    def __init__(self, tmp_path, delay=0.0):
        self.cache = ChimeCache(tmp_path)
        self.played, self.reports = [], []
        self.delay = delay

        async def play_file(path):
            self.played.append(path.stem)
            await asyncio.sleep(self.delay)

        self.player = Player(self.cache, play_file)

        async def report(job_id, status):
            self.reports.append((job_id, status))

        self.player.report = report
        self.player.fetch = fetch_ok
        self.task = asyncio.create_task(self.player.run())

    async def settle(self, t=0.1):
        await asyncio.sleep(t)


def chime_job(n):
    return Job(job_id=f"j{n}", kind="chime", chime_hash=HASH)


async def test_plays_chime_fetching_on_demand(tmp_path):
    h = Harness(tmp_path)
    await h.player.add(chime_job(1))
    await h.settle()
    assert h.played == [HASH]
    assert h.reports == [("j1", "started"), ("j1", "done")]
    h.task.cancel()


async def test_overflow_reports_dropped(tmp_path):
    h = Harness(tmp_path, delay=0.3)
    for n in range(7):  # 1 playing + 5 waiting + 1 dropped
        await h.player.add(chime_job(n))
    assert ("j6", "dropped") in h.reports
    h.task.cancel()


async def test_stop_drops_current_and_waiting(tmp_path):
    h = Harness(tmp_path, delay=5)
    await h.player.add(chime_job(1))
    await h.player.add(chime_job(2))
    await h.settle()
    await h.player.stop()
    await h.settle()
    assert ("j1", "dropped") in h.reports and ("j2", "dropped") in h.reports
    assert ("j1", "done") not in h.reports
    assert h.player.queue.depth == 0
    h.task.cancel()


async def test_unsupported_kind_reports_error(tmp_path):
    h = Harness(tmp_path)
    await h.player.add(Job(job_id="c1", kind="clip", clip_url="/x"))
    await h.settle()
    assert h.reports == [("c1", "started"), ("c1", "error")]
    h.task.cancel()


async def test_failed_playback_reports_error_and_continues(tmp_path):
    h = Harness(tmp_path)
    calls = []

    async def flaky(path):
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("aplay failed")

    h.player._play_file = flaky
    await h.player.add(chime_job(1))
    await h.player.add(chime_job(2))
    await h.settle()
    assert ("j1", "error") in h.reports and ("j2", "done") in h.reports
    h.task.cancel()


async def test_clip_plays_chime_then_message(tmp_path):
    h = Harness(tmp_path)
    played_bytes = []

    async def play_file(path):
        played_bytes.append(path.read_bytes())

    async def fetch_clip(url):
        assert url == "/api/noticom/clip/abc"
        return b"RIFF message"

    h.player._play_file = play_file
    h.player.fetch_clip = fetch_clip
    await h.player.add(Job(job_id="m1", kind="clip", clip_url="/api/noticom/clip/abc", pre_chime_hash=HASH))
    await h.settle()
    assert played_bytes == [WAV, b"RIFF message"]  # chime (fetched on demand), then message
    assert h.reports[-1] == ("m1", "done")
    h.task.cancel()


async def test_clip_without_chime_and_with_broken_chime(tmp_path):
    h = Harness(tmp_path)
    played = []

    async def play_file(path):
        played.append(path.read_bytes())

    async def fetch_clip(url):
        return b"RIFF message"

    async def no_chime(_):
        raise FileNotFoundError

    h.player._play_file = play_file
    h.player.fetch_clip = fetch_clip
    h.player.fetch = no_chime
    await h.player.add(Job(job_id="m1", kind="clip", clip_url="/c", pre_chime_hash="f" * 64))
    await h.player.add(Job(job_id="m2", kind="clip", clip_url="/c"))
    await h.settle()
    assert played == [b"RIFF message", b"RIFF message"]  # a missing chime doesn't block the message
    h.task.cancel()


async def test_hold_delays_playback_until_released(tmp_path):
    h = Harness(tmp_path)
    h.player.set_hold(True)
    await h.player.add(chime_job(1))
    await h.settle()
    assert h.played == []  # someone's recording
    h.player.set_hold(False)
    await h.settle()
    assert h.played == [HASH]
    h.task.cancel()
