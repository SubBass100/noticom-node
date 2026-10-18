"""Adoption server for an unclaimed node (docs/DESIGN.md §1)."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable

from aiohttp import web
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from . import __version__, protocol
from .state import NodeState, Pairing

_LOGGER = logging.getLogger(__name__)

ADOPT_PORT = 8742
SERVICE_TYPE = "_noticom._tcp.local."
BUTTON_WINDOW_S = 60


class AdoptionServer:
    """Advertise over mDNS and accept one adoption request."""

    def __init__(
        self,
        state: NodeState,
        wait_for_press: Callable[[float], Awaitable[bool]],
        chirp: Callable[[], Awaitable[None]],
    ) -> None:
        self._state = state
        self._wait_for_press = wait_for_press
        self._chirp = chirp
        self._adopted = asyncio.Event()
        self._busy = asyncio.Lock()

    async def run(self) -> Pairing:
        """Serve until adopted; return the new pairing."""
        app = web.Application()
        app.router.add_post("/adopt", self._handle_adopt)
        app.router.add_get("/info", self._handle_info)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, port=ADOPT_PORT)
        await site.start()

        azc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"noticom-{self._state.node_id}.{SERVICE_TYPE}",
            port=ADOPT_PORT,
            properties={"node_id": self._state.node_id, "version": __version__},
            server=f"{socket.gethostname()}.local.",
        )
        await azc.async_register_service(info)
        _LOGGER.info("Unclaimed; advertising as %s", self._state.node_id)
        try:
            await self._adopted.wait()
        finally:
            await azc.async_unregister_service(info)
            await azc.async_close()
            await runner.cleanup()
        pairing = self._state.pairing
        assert pairing is not None
        return pairing

    async def _handle_info(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"node_id": self._state.node_id, "version": __version__}
        )

    async def _handle_adopt(self, request: web.Request) -> web.Response:
        if self._busy.locked():
            return web.json_response({"error": "busy"}, status=409)
        async with self._busy:
            try:
                body = await request.json()
                server_pubkey = body["server_pubkey"]
                nonce = protocol.b64d(body["nonce"])
                server_url = body["server_url"]
            except (ValueError, KeyError, TypeError):
                return web.json_response({"error": "bad_request"}, status=400)

            await self._chirp()
            if not await self._wait_for_press(BUTTON_WINDOW_S):
                return web.json_response({"error": "button_timeout"}, status=408)

            self._state.save_pairing(Pairing(server_pubkey, server_url))
            key = self._state.key
            reply = web.json_response(
                {
                    "node_id": self._state.node_id,
                    "node_pubkey": protocol.public_key_b64(key),
                    "sig": protocol.sign(key, nonce),
                }
            )
            # Let the reply go out before the server shuts down.
            asyncio.get_running_loop().call_later(1, self._adopted.set)
            return reply
