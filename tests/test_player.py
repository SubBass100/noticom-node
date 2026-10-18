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
