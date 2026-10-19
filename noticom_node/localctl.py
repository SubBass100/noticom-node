"""Local control: `sudo noticom-node pair` and `sudo noticom-node reset`.

The running service listens on a Unix socket that only root can open, so
being able to use it proves the same thing as pressing the node's button:
you're in control of this Pi. Nothing is exposed on the network.

- pair: confirms an adoption request from Home Assistant (one that's
  waiting now, or one that arrives within CONFIRM_WINDOW_S), then reports
  "welcome" with the name HA gave the node.
- reset: factory reset (the command asks for confirmation first).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

CONFIRM_WINDOW_S = 120
WELCOME_WAIT_S = 30


def socket_path() -> Path:
    """Where the control socket lives (override with NOTICOM_RUN_DIR)."""
    return Path(os.environ.get("NOTICOM_RUN_DIR", "/run/noticom")) / "control.sock"


class LocalControl:
    """The service side of the control socket."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or socket_path()
        self.confirm = asyncio.Event()
        self.reset_requested = asyncio.Event()
        self.paired_name: str | None = None
        self._expiry: asyncio.TimerHandle | None = None
        self._watchers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Open the socket (root-only). Problems are logged, not fatal."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.unlink(missing_ok=True)
            self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))
            os.chmod(self.path, 0o600)
        except OSError as err:
            _LOGGER.warning("Local control socket unavailable (%s); `noticom-node pair` won't work", err)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        self.path.unlink(missing_ok=True)

    # ---- adoption hooks -------------------------------------------------

    def take_confirmation(self) -> None:
        """An adoption request used the confirmation."""
        self.confirm.clear()
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None

    async def adopted(self) -> None:
        """Adoption finished; HA will send our name when we connect."""
        await self._notify({"status": "adopted"})

    async def connected(self, name: str) -> None:
        """Connected to HA and given a name: say welcome and hang up."""
        self.paired_name = name
        await self._notify({"status": "welcome", "name": name}, close=True)

    # ---- socket ---------------------------------------------------------

    async def _send(self, writer: asyncio.StreamWriter, msg: dict) -> None:
        try:
            writer.write((json.dumps(msg) + "\n").encode())
            await writer.drain()
        except (ConnectionError, RuntimeError):
            self._watchers.discard(writer)

    async def _notify(self, msg: dict, close: bool = False) -> None:
        for writer in list(self._watchers):
            await self._send(writer, msg)
            if close:
                writer.close()
        if close:
            self._watchers.clear()

    def _arm(self) -> None:
        self.confirm.set()
        if self._expiry is not None:
            self._expiry.cancel()
        self._expiry = asyncio.get_running_loop().call_later(CONFIRM_WINDOW_S, self._expire)

    def _expire(self) -> None:
        self._expiry = None
        if self.confirm.is_set():
            self.confirm.clear()
            asyncio.ensure_future(self._notify({"status": "timeout"}, close=True))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            cmd = json.loads(line or b"{}").get("cmd")
        except (TimeoutError, ValueError):
            writer.close()
            return
        if cmd == "pair":
            if self.paired_name is not None:
                await self._send(writer, {"status": "already", "name": self.paired_name})
                writer.close()
                return
            self._arm()
            self._watchers.add(writer)
            _LOGGER.info("Pairing confirmed from the command line; waiting for Home Assistant")
            await self._send(writer, {"status": "waiting", "window": CONFIRM_WINDOW_S})
        elif cmd == "reset":
            _LOGGER.warning("Factory reset requested from the command line")
            await self._send(writer, {"status": "resetting"})
            writer.close()
            self.reset_requested.set()
        else:
            await self._send(writer, {"status": "error", "message": f"unknown command {cmd!r}"})
            writer.close()


# ---- command-line side ------------------------------------------------------


def _print(text: str) -> None:
    print(text, flush=True)


async def _client(cmd: str) -> int:
    path = socket_path()
    try:
        reader, writer = await asyncio.open_unix_connection(str(path))
    except PermissionError:
        _print("Run this with sudo:  sudo noticom-node " + cmd)
        return 1
    except (FileNotFoundError, ConnectionRefusedError):
        _print("The Noticom service isn't running. Start it with:  sudo systemctl start noticom-node")
        return 1
    writer.write((json.dumps({"cmd": cmd}) + "\n").encode())
    await writer.drain()
    deadline = CONFIRM_WINDOW_S + WELCOME_WAIT_S + 10
    try:
        while True:
            line = await asyncio.wait_for(reader.readline(), deadline)
            if not line:
                return 0
            msg = json.loads(line)
            status = msg.get("status")
            if status == "waiting":
                _print("Waiting for Home Assistant...")
                _print("In Home Assistant, open the Noticom card for this node and click Submit")
                _print(f"(or, if you already did, it will go through now). You have {msg['window'] // 60} minutes.")
            elif status == "adopted":
                _print("Home Assistant accepted this node. Connecting...")
            elif status == "welcome":
                _print(f'Welcome! This node is now connected to Home Assistant as "{msg["name"]}".')
                return 0
            elif status == "already":
                _print(f'This node is already connected to Home Assistant as "{msg["name"]}".')
                _print("To add it again, first run:  sudo noticom-node reset")
                return 0
            elif status == "timeout":
                _print("Home Assistant didn't ask to add this node in time. Click Submit on its card, then run this again.")
                return 1
            elif status == "resetting":
                _print("Done. The node has forgotten Home Assistant and can be added again.")
                return 0
            else:
                _print(msg.get("message", "Something went wrong."))
                return 1
    except TimeoutError:
        _print("No answer from the Noticom service. Check its log:  journalctl -u noticom-node -n 30")
        return 1
    finally:
        writer.close()


def main(argv: list[str]) -> int:
    """`noticom-node pair` or `noticom-node reset [--yes]`."""
    cmd = argv[0]
    if cmd == "reset" and "--yes" not in argv[1:] and "-y" not in argv[1:]:
        answer = input("This makes the node forget Home Assistant. Type yes to continue: ")
        if answer.strip().lower() not in ("yes", "y"):
            _print("Cancelled.")
            return 1
    try:
        return asyncio.run(_client(cmd))
    except KeyboardInterrupt:
        _print("\nCancelled.")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["pair"]))
