"""Entry point: `noticom-node` (run by systemd)."""

from __future__ import annotations

import asyncio
import logging

from . import __version__, mixer
from .adoption import AdoptionServer
from .control import ApplyResult, ControlClient, Outcome
from .hardware import (
    DEFAULT_BUTTON_NC,
    DEFAULT_BUTTON_PIN,
    PushButton,
    beeps,
    cancel_tone,
    chirp,
    speak,
)
from .reset import confirm_reset
from .state import NodeState

_LOGGER = logging.getLogger("noticom_node")


POST_ADOPT_DELAY_S = 3  # give HA time to create the node's entry


def _mixer_values(config: dict) -> dict:
    return {
        k: config[k] for k in ("volume", "mic_gain", "mic_boost") if k in config
    }


async def _node(state: NodeState, button: PushButton) -> Outcome:
    """Adopt if unclaimed, then stay connected to HA."""
    pairing = state.pairing
    if pairing is None:
        pairing = await AdoptionServer(state, button.wait_for_press, chirp).run()
        _LOGGER.info("Adopted by %s", pairing.server_url)
        await asyncio.sleep(POST_ADOPT_DELAY_S)

    async def apply_config(config: dict) -> ApplyResult:
        state.save_config(config)
        ok = await mixer.apply(**_mixer_values(config))
        pin = int(config.get("button_pin", button.pin))
        nc = bool(config.get("button_nc", button.normally_closed))
        restart = (pin, nc) != (button.pin, button.normally_closed)
        if restart:
            _LOGGER.info("Button changed to GPIO%d (NC=%s); restarting", pin, nc)
        return ApplyResult(ok=ok, reason=None if ok else "mixer", restart=restart)

    return await ControlClient(state, pairing, apply_config).run()


async def run() -> None:
    """Run the node until it's reset, unpaired or needs a restart."""
    state = NodeState()
    cached = state.config  # HA's last config, so settings apply before connecting
    await mixer.apply(**_mixer_values(cached))
    button = PushButton(
        int(cached.get("button_pin", DEFAULT_BUTTON_PIN)),
        bool(cached.get("button_nc", DEFAULT_BUTTON_NC)),
    )
    _LOGGER.info(
        "Button GPIO%d (normally %s): pin %s, reads as %s",
        button.pin,
        "closed" if button.normally_closed else "open",
        button.pin_level,
        "PRESSED" if button.is_pressed else "released",
    )
    node = asyncio.create_task(_node(state, button))
    try:
        while True:
            reset = asyncio.create_task(button.reset_requested.wait())
            done, _ = await asyncio.wait(
                {node, reset}, return_when=asyncio.FIRST_COMPLETED
            )
            if node in done:
                reset.cancel()
                outcome = node.result()  # re-raises a crash so systemd restarts us
                if outcome is Outcome.UNPAIRED:
                    state.factory_reset()
                    await beeps(3)
                    _LOGGER.warning("Unpaired by Home Assistant; back to unclaimed")
                # Exit either way; systemd restarts the service.
                return
            _LOGGER.info("Reset requested (5 presses); waiting for confirmation hold")
            confirmed = await confirm_reset(button, beeps, speak, cancel_tone)
            button.reset_requested.clear()  # ignore presses made during step 2
            if confirmed:
                node.cancel()
                state.factory_reset()
                _LOGGER.warning("Factory reset: pairing and identity key removed")
                # Exit; systemd restarts the service unclaimed and advertising.
                return
    finally:
        button.close()


def main() -> None:
    """Console script entry point."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _LOGGER.info("noticom-node %s starting", __version__)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
