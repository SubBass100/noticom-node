"""Entry point: `noticom-node` (run by systemd)."""

from __future__ import annotations

import asyncio
import logging

from . import __version__, mixer
from .adoption import AdoptionServer
from .hardware import FAKE, PushButton, chirp
from .state import NodeState

_LOGGER = logging.getLogger("noticom_node")

FACTORY_RESET_HOLD_S = 10


async def _check_factory_reset(button: PushButton, state: NodeState) -> None:
    """Holding the button for 10 s while the service starts forgets the server."""
    if FAKE or not button.is_pressed:
        return
    _LOGGER.info("Button held at startup; hold %d s to factory reset", FACTORY_RESET_HOLD_S)
    try:
        await asyncio.wait_for(button.released.wait(), FACTORY_RESET_HOLD_S)
    except TimeoutError:
        state.factory_reset()
        await chirp()
        await chirp()
        _LOGGER.warning("Factory reset: pairing and identity key removed")


async def run() -> None:
    """Adopt if unclaimed, then run the control channel."""
    state = NodeState()
    # TODO: use HA's values for this node once the control channel exists.
    await mixer.apply()
    button = PushButton()
    try:
        await _check_factory_reset(button, state)
        pairing = state.pairing
        if pairing is None:
            pairing = await AdoptionServer(state, button.wait_for_press, chirp).run()
            _LOGGER.info("Adopted by %s", pairing.server_url)
        # TODO (docs/DESIGN.md §2-4): connect to f"{pairing.server_url}/api/noticom/ws",
        # answer the challenge, sync chimes, run PlayQueue and PTT recording.
        _LOGGER.info("Paired with %s; control channel not built yet", pairing.server_url)
        await asyncio.Event().wait()
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
