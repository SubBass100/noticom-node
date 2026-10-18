"""Entry point: `noticom-node` (run by systemd)."""

from __future__ import annotations

import asyncio
import logging

from . import __version__, mixer
from .adoption import AdoptionServer
from .hardware import RESET_PRESSES, PushButton, chirp
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
    reset = asyncio.create_task(button.reset_requested.wait())
    try:
        done, _ = await asyncio.wait({node, reset}, return_when=asyncio.FIRST_COMPLETED)
        if node in done:
            node.result()  # re-raise a crash so systemd restarts us
        else:
            node.cancel()
            state.factory_reset()
            _LOGGER.warning("Factory reset (%d presses): pairing and key removed", RESET_PRESSES)
            for _ in range(3):
                await chirp()
            # Exit; systemd restarts the service unclaimed and advertising.
    finally:
        reset.cancel()
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
