"""Entry point: `noticom-node` (run by systemd)."""

from __future__ import annotations

import asyncio
import logging

from . import __version__, mixer
from .adoption import AdoptionServer
from .hardware import PushButton, beeps, cancel_tone, chirp, speak
from .reset import confirm_reset
from .state import NodeState

_LOGGER = logging.getLogger("noticom_node")


async def _node(state: NodeState, button: PushButton) -> None:
    """Adopt if unclaimed, then run the control channel."""
    pairing = state.pairing
    if pairing is None:
        pairing = await AdoptionServer(state, button.wait_for_press, chirp).run()
        _LOGGER.info("Adopted by %s", pairing.server_url)
    # TODO (docs/DESIGN.md §2-4): connect to f"{pairing.server_url}/api/noticom/ws",
    # answer the challenge, sync chimes, run PlayQueue and PTT recording.
    _LOGGER.info("Paired with %s; control channel not built yet", pairing.server_url)
    await asyncio.Event().wait()


async def run() -> None:
    """Run the node until a factory reset is requested."""
    state = NodeState()
    # TODO: use HA's values for this node once the control channel exists.
    await mixer.apply()
    button = PushButton()
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
                node.result()  # re-raise a crash so systemd restarts us
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
