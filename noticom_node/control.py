"""Control channel client: the node's half of docs/DESIGN.md §2.

The node connects out to HA, proves its identity by signing HA's
challenge, and requires HA to sign the node's own challenge with the key
saved at adoption. Config and unpair messages are only accepted after that.
It reconnects with exponential back-off for as long as it's paired.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from . import __version__, protocol
from .state import NodeState, Pairing

_LOGGER = logging.getLogger(__name__)

WS_PATH = "/api/noticom/ws"
HEARTBEAT_S = 30
HANDSHAKE_TIMEOUT_S = 10
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0


class Outcome(enum.Enum):
    """Why the control channel stopped for good."""

    UNPAIRED = "unpaired"  # HA told us to forget it (signed)
    RESTART = "restart"  # a config change needs a service restart


@dataclass
class ApplyResult:
    """What applying a config did."""

    ok: bool = True
    reason: str | None = None
    restart: bool = False


ApplyConfig = Callable[[dict[str, Any]], Awaitable[ApplyResult]]


class HandshakeError(Exception):
    """The handshake failed; retry after back-off."""


def ws_url(server_url: str) -> str:
    """http://host:8123 -> ws://host:8123/api/noticom/ws."""
    base = server_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + WS_PATH


class ControlClient:
    """Keeps the node connected to HA until unpaired or a restart is needed."""

    def __init__(
        self,
        state: NodeState,
        pairing: Pairing,
        apply_config: ApplyConfig,
        get_state: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._state = state
        self._pairing = pairing
        self._apply_config = apply_config
        self._get_state = get_state or (lambda: {})

    async def run(self) -> Outcome:
        """Connect, and reconnect after any failure, until an Outcome."""
        backoff = BACKOFF_MIN_S
        url = ws_url(self._pairing.server_url)
        async with aiohttp.ClientSession() as session:
            while True:
                authed = False
                try:
                    async with session.ws_connect(url, heartbeat=HEARTBEAT_S) as ws:
                        node_nonce = await self._handshake(ws)
                        authed = True
                        _LOGGER.info("Connected to Home Assistant at %s", url)
                        outcome = await self._serve(ws, node_nonce)
                        if outcome is not None:
                            return outcome
                    _LOGGER.warning("Home Assistant closed the connection")
                except HandshakeError as err:
                    _LOGGER.warning("Handshake with Home Assistant failed: %s", err)
                except _Unpaired:
                    return Outcome.UNPAIRED
                except (aiohttp.ClientError, OSError, TimeoutError) as err:
                    _LOGGER.warning("Can't reach Home Assistant at %s: %r", url, err)
                if authed:
                    backoff = BACKOFF_MIN_S
                _LOGGER.info("Reconnecting in %.0f s", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _receive(self, ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
        msg = await ws.receive(timeout=HANDSHAKE_TIMEOUT_S)
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise HandshakeError(f"connection closed ({msg.type.name})")
        try:
            return protocol.decode(msg.data)
        except ValueError as err:
            raise HandshakeError("invalid message") from err

    async def _handshake(self, ws: aiohttp.ClientWebSocketResponse) -> str:
        """Mutual auth; returns our nonce (it binds later unpair messages)."""
        challenge = await self._receive(ws)
        if challenge.get("t") != protocol.T_CHALLENGE:
            raise HandshakeError("expected a challenge")
        try:
            server_nonce = protocol.b64d(challenge["nonce"])
        except (KeyError, ValueError) as err:
            raise HandshakeError("bad challenge") from err

        node_nonce = protocol.new_nonce()
        await ws.send_str(
            protocol.encode(
                protocol.T_AUTH,
                node_id=self._state.node_id,
                nonce=node_nonce,
                version=__version__,
                sig=protocol.sign(self._state.key, server_nonce),
            )
        )
        reply = await self._receive(ws)
        if reply.get("t") == protocol.T_UNPAIR:
            self._check_unpair(reply, node_nonce)  # raises _Unpaired if genuine
        if reply.get("t") == protocol.T_ERROR:
            raise HandshakeError(f"server said {reply.get('reason')}")
        if reply.get("t") != protocol.T_WELCOME:
            raise HandshakeError(f"unexpected {reply.get('t')}")
        if not protocol.verify(
            self._pairing.server_pubkey, protocol.b64d(node_nonce), reply.get("sig", "")
        ):
            raise HandshakeError("server failed to prove its identity")
        await self._config(ws, reply.get("config") or {})
        return node_nonce

    async def _serve(
        self, ws: aiohttp.ClientWebSocketResponse, node_nonce: str
    ) -> Outcome | None:
        """Handle messages until the socket closes (None) or an Outcome."""
        if self._restart_pending:
            return Outcome.RESTART
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                data = protocol.decode(msg.data)
            except ValueError:
                continue
            msg_type = data["t"]
            if msg_type == protocol.T_CONFIG:
                await self._config(ws, data.get("config") or {})
                if self._restart_pending:
                    return Outcome.RESTART
            elif msg_type == protocol.T_UNPAIR:
                self._check_unpair(data, node_nonce)
            else:
                _LOGGER.debug("Ignoring %s (not implemented yet)", msg_type)
        return None

    _restart_pending = False

    async def _config(self, ws: aiohttp.ClientWebSocketResponse, config: dict) -> None:
        """Apply a config, report the result and our state."""
        try:
            result = await self._apply_config(config)
        except Exception as err:  # noqa: BLE001 - report, don't drop the link
            _LOGGER.exception("Applying config failed")
            result = ApplyResult(ok=False, reason=repr(err))
        await ws.send_str(
            protocol.encode(protocol.T_CONFIG_RESULT, ok=result.ok, reason=result.reason)
        )
        await ws.send_str(protocol.encode(protocol.T_STATE, **self._get_state()))
        if result.restart:
            self._restart_pending = True

    def _check_unpair(self, data: dict, node_nonce: str) -> None:
        """Raise _Unpaired for a genuinely signed unpair; ignore forgeries."""
        if protocol.verify(
            self._pairing.server_pubkey,
            protocol.unpair_payload(node_nonce),
            data.get("sig", ""),
        ):
            _LOGGER.warning("Home Assistant removed this node")
            raise _Unpaired
        _LOGGER.warning("Ignoring an unpair message with a bad signature")


class _Unpaired(Exception):
    """Internal: a verified unpair arrived."""
