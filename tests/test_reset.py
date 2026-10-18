"""Two-step reset confirmation, with a fake button and short timings."""

import asyncio

from noticom_node.reset import CANCELLED, PROMPT, confirm_reset

WINDOW, HOLD = 0.3, 0.2


class FakeButton:
    def __init__(self):
        self.pressed = asyncio.Event()
        self.released = asyncio.Event()
        self._down = False

    @property
    def is_pressed(self):
        return self._down

    def press(self):
        self._down = True
        self.released.clear()
        self.pressed.set()

    def release(self):
        self._down = False
        self.pressed.clear()
        self.released.set()


class Recorder:
    def __init__(self):
        self.events = []

    async def beeps(self, n):
        self.events.append(f"beep{n}")

    async def say(self, text):
        self.events.append(text)

    async def cancel_tone(self):
        self.events.append("cancel")


async def run(button, rec):
    return await confirm_reset(
        button, rec.beeps, rec.say, rec.cancel_tone, window_s=WINDOW, hold_s=HOLD
    )


async def test_hold_long_enough_confirms():
    button, rec = FakeButton(), Recorder()
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, button.press)
    assert await run(button, rec)
    assert rec.events == ["beep3", PROMPT, "beep3"]


async def test_no_press_cancels():
    button, rec = FakeButton(), Recorder()
    assert not await run(button, rec)
    assert rec.events == ["beep3", PROMPT, "cancel", CANCELLED]


async def test_released_early_cancels():
    button, rec = FakeButton(), Recorder()
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, button.press)
    loop.call_later(0.1, button.release)  # held 0.05 s < HOLD
    assert not await run(button, rec)
    assert rec.events[-2:] == ["cancel", CANCELLED]


async def test_already_holding_after_prompt_counts():
    button, rec = FakeButton(), Recorder()
    button.press()
    assert await run(button, rec)


async def test_press_before_prompt_ends_then_released_does_not_count():
    """A stale press event from before the prompt mustn't start the hold."""
    button, rec = FakeButton(), Recorder()
    button.press()
    button.release()  # 5th tap of the gesture
    assert not await run(button, rec)
