"""Entry point: `noticom-node` (run by systemd).

Also: `noticom-node pair`, `noticom-node reset` (see localctl.py) and
`noticom-node render-prompts` (see prompts.py).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from . import __version__, hardware, mixer, soundcards
from .adoption import AdoptionServer
from .chimecache import ChimeCache
from .control import ApplyResult, ControlClient, Outcome
from .hardware import (
    DEFAULT_BUTTON_NC,
    DEFAULT_BUTTON_PIN,
    PushButton,
    beeps,
    cancel_tone,
    chirp,
    parse_pin,
    play_file,
    speak,
)
from .health import check_audio, keep_wifi_awake
from .localctl import LocalControl
from .player import Player
from .ptt import PttController, Recorder
from .reset import confirm_reset
from .state import NodeState

_LOGGER = logging.getLogger("noticom_node")

POST_ADOPT_DELAY_S = 3  # give HA time to create the node's entry


class Node:
    """Everything the node runs, wired together."""

    def __init__(self) -> None:
        self.state = NodeState()
        self.cache = ChimeCache()
        self.local = LocalControl()
        cached = self.state.config  # HA's last config, applied before reconnecting
        self.config: dict = cached
        self.button = PushButton(
            parse_pin(cached.get("button_pin", DEFAULT_BUTTON_PIN)),
            bool(cached.get("button_nc", DEFAULT_BUTTON_NC)),
        )
        self.player = Player(self.cache, play_file)
        self.recorder = Recorder()
        self.ptt = PttController(self.button, self.recorder, chirp, cancel_tone, self.player.set_hold)
        self.audio = soundcards.choose()
        self._welcomed = False

    # ---- audio --------------------------------------------------------

    async def apply_audio(self, config: dict) -> bool:
        """Choose sound cards and set levels from a config."""
        self.audio = soundcards.choose(
            config.get("output_device", soundcards.AUTO),
            config.get("input_device", soundcards.AUTO),
        )
        ok, software_gain = await mixer.apply(
            self.audio,
            **{k: config[k] for k in ("volume", "mic_gain", "mic_boost") if k in config},
        )
        out = self.audio.output
        hardware.set_output(out.playback_device if out else None, software_gain)
        if self.audio.input is not None:
            self.recorder.device = self.audio.input.capture_device
        self.player.queue.chime_priority = bool(config.get("chime_priority", True))
        self.ptt.enabled = (
            bool(config.get("ptt_enabled", True))
            and self.audio.has_mic
            and self.button.pin is not None
        )
        return ok

    def capabilities(self) -> dict:
        """What this node can do, for Home Assistant."""
        return {**self.audio.capabilities(), "button": self.button.pin is not None}

    # ---- confirmation (button or `noticom-node pair`) -------------------

    async def wait_for_confirmation(self, timeout: float) -> bool:
        """True when the button is pressed or `noticom-node pair` is run."""
        press = asyncio.create_task(self.button.wait_for_press(timeout))
        remote = asyncio.create_task(self.local.confirm.wait())
        done, pending = await asyncio.wait(
            {press, remote}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if remote in done:
            self.local.take_confirmation()
            _LOGGER.info("Adoption confirmed by `noticom-node pair`")
            return True
        return press in done and press.result()

    # ---- running ------------------------------------------------------

    async def connect(self) -> Outcome:
        """Adopt if unclaimed, then stay connected to HA."""
        pairing = self.state.pairing
        if pairing is None:
            if self.button.pin is None:
                _LOGGER.info("No button: run `sudo noticom-node pair` to confirm adoption")
            pairing = await AdoptionServer(self.state, self.wait_for_confirmation, chirp).run()
            _LOGGER.info("Adopted by %s", pairing.server_url)
            await self.local.adopted()
            await asyncio.sleep(POST_ADOPT_DELAY_S)
        else:
            self.local.paired_name = self.config.get("name", "this node")

        async def apply_config(config: dict) -> ApplyResult:
            self.state.save_config(config)
            self.config = config
            ok = await self.apply_audio(config)
            if "name" in config:
                self.local.paired_name = config["name"]
                if not self._welcomed:
                    self._welcomed = True
                    await self.local.connected(config["name"])
            pin = parse_pin(config.get("button_pin", self.button.pin))
            nc = bool(config.get("button_nc", self.button.normally_closed))
            restart = (pin, nc) != (self.button.pin, self.button.normally_closed)
            if restart:
                _LOGGER.info("Button changed (GPIO%s, NC=%s); restarting", pin, nc)
            return ApplyResult(ok=ok, reason=None if ok else "mixer", restart=restart)

        def node_state() -> dict:
            return {
                "queue_depth": self.player.queue.depth,
                "playing": self.player.queue.current is not None,
                "caps": self.capabilities(),
            }

        client = ControlClient(
            self.state, pairing, apply_config, node_state,
            player=self.player, cache=self.cache, get_caps=self.capabilities,
        )
        self.ptt.upload = client.upload_clip
        self.ptt.on_phase = client.send_ptt
        # Push-to-talk only runs once paired, so the adoption press can't record.
        ptt_task = asyncio.create_task(self.ptt.run())
        try:
            return await client.run()
        finally:
            ptt_task.cancel()

    async def run(self) -> None:
        """Run until reset, unpaired or a restart is needed."""
        await self.local.start()
        wifi = asyncio.create_task(keep_wifi_awake())  # HA must always be able to reach us
        await self.apply_audio(self.config)
        check_audio(self.audio)
        if self.button.pin is None:
            _LOGGER.info("No button configured (pair and reset with `sudo noticom-node pair|reset`)")
        else:
            _LOGGER.info(
                "Button GPIO%d (normally %s): pin %s, reads as %s",
                self.button.pin,
                "closed" if self.button.normally_closed else "open",
                self.button.pin_level,
                "PRESSED" if self.button.is_pressed else "released",
            )
        playback = asyncio.create_task(self.player.run())
        node = asyncio.create_task(self.connect())
        try:
            while True:
                gesture = asyncio.create_task(self.button.reset_requested.wait())
                command = asyncio.create_task(self.local.reset_requested.wait())
                done, _ = await asyncio.wait(
                    {node, gesture, command}, return_when=asyncio.FIRST_COMPLETED
                )
                gesture.cancel()
                command.cancel()
                if node in done:
                    outcome = node.result()  # re-raises a crash so systemd restarts us
                    if outcome is Outcome.UNPAIRED:
                        self.state.factory_reset()
                        await beeps(3)
                        _LOGGER.warning("Unpaired by Home Assistant; back to unclaimed")
                    return  # systemd restarts the service
                if command in done:
                    node.cancel()
                    self.state.factory_reset()
                    _LOGGER.warning("Factory reset (command line): pairing and identity key removed")
                    return
                _LOGGER.info("Reset requested (5 presses); waiting for confirmation hold")
                self.ptt.suspended = True  # the confirmation hold mustn't record a message
                try:
                    confirmed = await confirm_reset(self.button, beeps, speak, cancel_tone)
                finally:
                    self.ptt.suspended = False
                self.button.reset_requested.clear()  # ignore presses made during step 2
                if confirmed:
                    node.cancel()
                    self.state.factory_reset()
                    _LOGGER.warning("Factory reset: pairing and identity key removed")
                    return
        finally:
            playback.cancel()
            wifi.cancel()
            self.button.close()
            await self.local.stop()


def main() -> None:
    """Console script entry point."""
    args = sys.argv[1:]
    if args and args[0] == "render-prompts":
        from .prompts import main as render_main  # noqa: PLC0415

        sys.exit(render_main(args[1:]))
    if args and args[0] in ("pair", "reset"):
        from .localctl import main as local_main  # noqa: PLC0415

        sys.exit(local_main(args))
    if args:
        print("Usage: noticom-node [pair | reset [--yes] | render-prompts ...]")
        sys.exit(2)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _LOGGER.info("noticom-node %s starting", __version__)

    async def run() -> None:
        await Node().run()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
