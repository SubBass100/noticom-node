"""Button polarity, using gpiozero's mock pins."""

import asyncio

import pytest
from gpiozero import Device
from gpiozero.pins.mock import MockFactory

from noticom_node import hardware


@pytest.fixture(autouse=True)
def mock_pins(monkeypatch):
    monkeypatch.setattr(hardware, "FAKE", False)
    Device.pin_factory = MockFactory()
    yield
    Device.pin_factory.reset()


async def settle():
    await asyncio.sleep(0.05)


@pytest.mark.parametrize("nc", [True, False])
async def test_press_and_release(nc):
    button = hardware.PushButton(13, normally_closed=nc)
    pin = Device.pin_factory.pin(13)
    # At rest an NC button holds the pin low; an NO one lets the pull-up win.
    (pin.drive_low if nc else pin.drive_high)()
    await settle()
    assert not button.is_pressed

    (pin.drive_high if nc else pin.drive_low)()  # press
    await settle()
    assert button.is_pressed
    assert button.pressed.is_set() and not button.released.is_set()

    (pin.drive_low if nc else pin.drive_high)()  # release
    await settle()
    assert not button.is_pressed
    assert button.released.is_set() and not button.pressed.is_set()
    button.close()


async def test_wait_for_press_nc():
    button = hardware.PushButton(13, normally_closed=True)
    pin = Device.pin_factory.pin(13)
    pin.drive_low()
    await settle()
    asyncio.get_running_loop().call_later(0.05, pin.drive_high)
    assert await button.wait_for_press(1)
    button.close()


async def test_wait_for_press_times_out():
    button = hardware.PushButton(13, normally_closed=True)
    Device.pin_factory.pin(13).drive_low()
    await settle()
    assert not await button.wait_for_press(0.1)
    button.close()


async def test_five_quick_presses_request_reset(monkeypatch):
    button = hardware.PushButton(13, normally_closed=True)
    pin = Device.pin_factory.pin(13)
    pin.drive_low()
    await settle()
    for _ in range(hardware.RESET_PRESSES - 1):
        pin.drive_high(); await settle(); pin.drive_low(); await settle()
    assert not button.reset_requested.is_set()
    pin.drive_high(); await settle()
    assert button.reset_requested.is_set()
    button.close()


async def test_slow_presses_do_not_reset(monkeypatch):
    button = hardware.PushButton(13, normally_closed=False)
    clock = [100.0]
    monkeypatch.setattr(hardware.time, "monotonic", lambda: clock[0])
    for _ in range(hardware.RESET_PRESSES + 2):
        button._on_press()
        clock[0] += 1.5  # 5 presses span 6 s > 4 s window
    assert not button.reset_requested.is_set()
    button.close()
