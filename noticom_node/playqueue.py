"""The node's playback queue (docs/DESIGN.md §4).

One job plays at a time; up to MAX_WAITING jobs wait behind it. A job that
arrives when the waiting list is full is dropped. With chime priority on, a
chime is placed after any waiting chimes but ahead of waiting clips. The job
that is already playing is never interrupted.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .protocol import KIND_CHIME

MAX_WAITING = 5


@dataclass
class Job:
    """One thing to play, as received in a `play` message."""

    job_id: str
    kind: str
    chime_hash: str | None = None
    clip_url: str | None = None
    pre_chime_hash: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_message(cls, msg: dict[str, Any]) -> Job:
        """Build a job from a decoded `play` message."""
        known = {"t", "job_id", "kind", "chime_hash", "clip_url", "pre_chime_hash"}
        return cls(
            job_id=msg["job_id"],
            kind=msg["kind"],
            chime_hash=msg.get("chime_hash"),
            clip_url=msg.get("clip_url"),
            pre_chime_hash=msg.get("pre_chime_hash"),
            extra={k: v for k, v in msg.items() if k not in known},
        )


class PlayQueue:
    """Ordering and capacity rules only; playback lives elsewhere."""

    def __init__(self, chime_priority: bool = True) -> None:
        self.chime_priority = chime_priority
        self.current: Job | None = None
        self._waiting: deque[Job] = deque()

    @property
    def waiting(self) -> list[Job]:
        """Jobs waiting behind the current one, in play order."""
        return list(self._waiting)

    @property
    def depth(self) -> int:
        """Waiting jobs plus the playing one."""
        return len(self._waiting) + (1 if self.current else 0)

    def add(self, job: Job) -> bool:
        """Queue a job. Returns False if it was dropped (queue full)."""
        if self.current is None and not self._waiting:
            self.current = job
            return True
        if len(self._waiting) >= MAX_WAITING:
            return False
        if self.chime_priority and job.kind == KIND_CHIME:
            index = 0
            for queued in self._waiting:
                if queued.kind != KIND_CHIME:
                    break
                index += 1
            self._waiting.insert(index, job)
        else:
            self._waiting.append(job)
        return True

    def start_next(self) -> Job | None:
        """Move the next waiting job to current, if nothing is playing."""
        if self.current is None and self._waiting:
            self.current = self._waiting.popleft()
        return self.current

    def finish_current(self) -> Job | None:
        """Mark the current job finished and return it."""
        done, self.current = self.current, None
        return done

    def clear(self) -> list[Job]:
        """Drop everything, including the current job; return what was dropped."""
        dropped = ([self.current] if self.current else []) + list(self._waiting)
        self.current = None
        self._waiting.clear()
        return dropped
