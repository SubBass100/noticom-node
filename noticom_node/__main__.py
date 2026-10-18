"""Entry point: `noticom-node` (run by systemd)."""

from __future__ import annotations

import asyncio
import logging

from . import __version__, mixer
from .adoption import AdoptionServer
from .chimecache import ChimeCache
from .control import ApplyResult, ControlClient, Outcome
from .player import Player
from .ptt import PttController, Recorder
from .hardware import (
    DEFAULT_BUTTON_NC,
    DEFAULT_BUTTON_PIN,
    PushButton,
    beeps,
    cancel_tone,
    chirp,
    play_file,
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


async def _node(
    state: NodeState,
    button: PushButton,
    player: Player,
    cache: ChimeCache,
    ptt: PttController,
) -> Outcome:
    """Adopt if unclaimed, then stay connected to HA."""
    pairing = state.pairing
    if pairing is None:
        pairing = await AdoptionServer(state, button.wait_for_press, chirp).run()
        _LOGGER.info("Adopted by %s", pairing.server_url)
        await asyncio.sleep(POST_ADOPT_DELAY_S)

    async def apply_config(config: dict) -> ApplyResult:
        state.save_config(config)
        player.queue.chime_priority = bool(config.get("chime_priority", True))
        ptt.enabled = bool(config.get("ptt_enabled", True))
        ok = await mixer.apply(**_mixer_values(config))
        pin = int(config.get("button_pin", button.pin))
        nc = bool(config.get("button_nc", button.normally_closed))
        restart = (pin, nc) != (button.pin, button.normally_closed)
        if restart:
            _LOGGER.info("Button changed to GPIO%d (NC=%s); restarting", pin, nc)
        return ApplyResult(ok=ok, reason=None if ok else "mixer", restart=restart)

    def node_state() -> dict:
        return {"queue_depth": player.queue.depth, "playing": player.queue.current is not None}

    client = ControlClient(
        state, pairing, apply_config, node_state, player=player, cache=cache
    )
    ptt.upload = client.upload_clip
    ptt.on_phase = client.send_ptt
    # Push-to-talk only runs once paired, so the adoption press can't record.
    ptt_task = asyncio.create_task(ptt.run())
    try:
        return await client.run()
    finally:
        ptt_task.cancel()


async def run() -> None:
    """Run the node until it's reset, unpaired or needs a restart."""
    state = NodeState()
    cache = ChimeCache()
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
    player = Player(cache, play_file)
    player.queue.chime_priority = bool(cached.get("chime_priority", True))
    playback = asyncio.create_task(player.run())
    ptt = PttController(button, Recorder(), chirp, cancel_tone, player.set_hold)
    ptt.enabled = bool(cached.get("ptt_enabled", True))
    node = asyncio.create_task(_node(state, button, player, cache, ptt))
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
            ptt.suspended = True  # the confirmation hold mustn't record a message
            try:
                confirmed = await confirm_reset(button, beeps, speak, cancel_tone)
            finally:
                ptt.suspended = False
            button.reset_requested.clear()  # ignore presses made during step 2
            if confirmed:
                node.cancel()
                state.factory_reset()
                _LOGGER.warning("Factory reset: pairing and identity key removed")
                # Exit; systemd restarts the service unclaimed and advertising.
                return
    finally:
        playback.cancel()
        button.close()


def main() -> None:
    """Console script entry point (`noticom-node`, or `noticom-node render-prompts ...`)."""
    import sys  # noqa: PLC0415

    if len(sys.argv) > 1 and sys.argv[1] == "render-prompts":
        from .prompts import main as render_main  # noqa: PLC0415

        sys.exit(render_main(sys.argv[2:]))
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
