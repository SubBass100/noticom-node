"""Plays jobs from HA through the PlayQueue, one at a time."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from . import protocol
from .chimecache import ChimeCache, Fetch
from .playqueue import Job, PlayQueue

_LOGGER = logging.getLogger(__name__)

Report = Callable[[str, str], Awaitable[None]]  # (job_id, status)
PlayFile = Callable[[Path], Awaitable[None]]


class Player:
    """Owns the queue and the playback loop; survives reconnects."""

    def __init__(self, cache: ChimeCache, play_file: PlayFile) -> None:
        self.queue = PlayQueue()
        self._cache = cache
        self._play_file = play_file
        self._wake = asyncio.Event()
        self._playing: asyncio.Task | None = None
        self.report: Report | None = None  # set while connected to HA
        self.fetch: Fetch | None = None  # set while connected to HA

    async def _report(self, job_id: str, status: str) -> None:
        if self.report is not None:
            try:
                await self.report(job_id, status)
            except ConnectionError:
                pass

    async def add(self, job: Job) -> None:
        """Queue a job from a `play` message."""
        if self.queue.add(job):
            self._wake.set()
        else:
            _LOGGER.info("Queue full; dropped job %s", job.job_id)
            await self._report(job.job_id, protocol.JOB_DROPPED)

    async def stop(self) -> None:
        """Stop the current job and clear the queue."""
        dropped = self.queue.clear()
        if self._playing is not None:
            self._playing.cancel()
        for job in dropped:
            await self._report(job.job_id, protocol.JOB_DROPPED)

    async def run(self) -> None:
        """The playback loop."""
        while True:
            await self._wake.wait()
            self._wake.clear()
            while (job := self.queue.start_next()) is not None:
                await self._report(job.job_id, protocol.JOB_STARTED)
                status = protocol.JOB_DONE
                self._playing = asyncio.create_task(self._play(job))
                try:
                    await self._playing
                except asyncio.CancelledError:
                    if self.queue.current is not job:  # stopped via stop()
                        status = protocol.JOB_DROPPED
                    else:
                        raise
                except Exception as err:  # noqa: BLE001 - report and carry on
                    _LOGGER.warning("Job %s failed: %r", job.job_id, err)
                    status = protocol.JOB_ERROR
                finally:
                    self._playing = None
                if self.queue.current is job:
                    self.queue.finish_current()
                if status != protocol.JOB_DROPPED:
                    await self._report(job.job_id, status)

    async def _play(self, job: Job) -> None:
        if job.kind != protocol.KIND_CHIME or not job.chime_hash:
            raise ValueError(f"unsupported job kind {job.kind!r}")
        path = self._cache.path(job.chime_hash)
        if path is None:
            # Not synced yet (e.g. just added in HA): fetch it now.
            if self.fetch is None:
                raise FileNotFoundError(f"chime {job.chime_hash[:12]} not cached")
            path = await self._cache.fetch(job.chime_hash, self.fetch)
        name = self._cache.names.get(job.chime_hash, job.chime_hash[:12])
        _LOGGER.info("Playing chime %s", name)
        await self._play_file(path)
