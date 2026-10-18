"""Two-step factory reset confirmation.

Step 1 (in PushButton): 5 quick presses request a reset.
Step 2 (here): 3 beeps, a spoken prompt, then the user must start holding
the button within WINDOW_S seconds and keep holding for HOLD_S seconds.
Anything else cancels. A stuck or broken button can't produce step 1, and
a stray tap or two can't produce step 2.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

_LOGGER = logging.getLogger(__name__)

WINDOW_S = 10.0  # time allowed to start the hold
HOLD_S = 5.0  # how long the hold must last

PROMPT = "To factory reset, press and hold the button for five seconds."
CANCELLED = "Reset cancelled."


class Button(Protocol):
    """What confirm_reset needs from a button."""

    pressed: asyncio.Event
    released: asyncio.Event

    @property
    def is_pressed(self) -> bool: ...


async def confirm_reset(
    button: Button,
    beeps: Callable[[int], Awaitable[None]],
    say: Callable[[str], Awaitable[None]],
    cancel_tone: Callable[[], Awaitable[None]],
    window_s: float = WINDOW_S,
    hold_s: float = HOLD_S,
) -> bool:
    """Run step 2. Returns True only if the user confirmed the reset."""
    await beeps(3)
    await say(PROMPT)

    # The window starts after the prompt. If they're already holding, the
    # hold starts now; otherwise wait for a fresh press.
    if not button.is_pressed:
        button.pressed.clear()
        try:
            await asyncio.wait_for(button.pressed.wait(), window_s)
        except TimeoutError:
            _LOGGER.info("Reset cancelled: no press within %.0f s", window_s)
            await _cancel(cancel_tone, say)
            return False

    button.released.clear()
    if not button.is_pressed:  # tapped and let go already
        _LOGGER.info("Reset cancelled: released before %.0f s", hold_s)
        await _cancel(cancel_tone, say)
        return False
    try:
        await asyncio.wait_for(button.released.wait(), hold_s)
    except TimeoutError:
        await beeps(3)
        return True
    _LOGGER.info("Reset cancelled: released before %.0f s", hold_s)
    await _cancel(cancel_tone, say)
    return False


async def _cancel(
    cancel_tone: Callable[[], Awaitable[None]], say: Callable[[str], Awaitable[None]]
) -> None:
    await cancel_tone()
    await say(CANCELLED)
