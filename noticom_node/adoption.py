"""Adoption server for an unclaimed node (docs/DESIGN.md §1)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable

import ifaddr
from aiohttp import web
from zeroconf import IPVersion
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from . import __version__, protocol
from .state import NodeState, Pairing

_LOGGER = logging.getLogger(__name__)

ADOPT_PORT = 8742
SERVICE_TYPE = "_noticom._tcp.local."
BUTTON_WINDOW_S = 60
CHIRP_TIMEOUT_S = 3
HA_SERVICE_TYPE = "_home-assistant._tcp.local."
HELLO_INTERVAL_S = 20


async def say_hello(host: str, port: int, timeout: float = 3.0) -> bool:
    """Open (and close) a TCP connection to HA.

    The point is the side effect: our ARP request teaches HA's host this
    node's hardware address. Some Wi-Fi access points drop HA's broadcast
    ARP lookups to sleeping 2.4 GHz clients, which makes adoption fail with
    "No route to host" until the node has talked to HA first.
    """
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


def local_ipv4_addresses() -> list[str]:
    """This node's LAN IPv4 addresses (no loopback or link-local)."""
    found: list[str] = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            addr = ipaddress.IPv4Address(ip.ip)
            if addr.is_loopback or addr.is_link_local:
                continue
            found.append(str(addr))
    return found


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

        # Home Assistant ignores mDNS services that carry no address, so the
        # A records must be included explicitly.
        addresses = local_ipv4_addresses()
        while not addresses:
            _LOGGER.warning("No LAN IPv4 address yet; waiting for network")
            await asyncio.sleep(5)
            addresses = local_ipv4_addresses()

        azc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"noticom-{self._state.node_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(a) for a in addresses],
            port=ADOPT_PORT,
            properties={"node_id": self._state.node_id, "version": __version__},
            server=f"{socket.gethostname()}.local.",
        )
        await azc.async_register_service(info)
        _LOGGER.info(
            "Unclaimed; advertising as %s on %s:%d",
            self._state.node_id, ", ".join(addresses), ADOPT_PORT,
        )
        hello = asyncio.create_task(self._greet_home_assistant(azc))
        try:
            await self._adopted.wait()
        finally:
            hello.cancel()
            await azc.async_unregister_service(info)
            await azc.async_close()
            await runner.cleanup()
        pairing = self._state.pairing
        assert pairing is not None
        return pairing

    async def _greet_home_assistant(self, azc: AsyncZeroconf) -> None:
        """Find Home Assistant over mDNS and say hello to it regularly."""
        found: dict[str, tuple[str, int]] = {}  # service name -> (ip, port)

        def on_change(zeroconf, service_type, name, state_change) -> None:
            if state_change is ServiceStateChange.Removed:
                found.pop(name, None)
            else:
                asyncio.ensure_future(resolve(service_type, name))

        async def resolve(service_type: str, name: str) -> None:
            info = AsyncServiceInfo(service_type, name)
            if await info.async_request(azc.zeroconf, 3000):
                ipv4 = [a for a in info.parsed_addresses() if ":" not in a]
                if ipv4 and info.port:
                    is_new = name not in found
                    found[name] = (ipv4[0], info.port)
                    if is_new:
                        _LOGGER.info("Found Home Assistant at %s:%d", ipv4[0], info.port)
                        await say_hello(ipv4[0], info.port)

        browser = AsyncServiceBrowser(azc.zeroconf, HA_SERVICE_TYPE, handlers=[on_change])
        try:
            while True:
                for host, port in list(found.values()):
                    await say_hello(host, port)
                await asyncio.sleep(HELLO_INTERVAL_S)
        finally:
            await browser.async_cancel()

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

            # The chirp is a courtesy: never let a sound problem block pairing.
            try:
                await asyncio.wait_for(self._chirp(), CHIRP_TIMEOUT_S)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Adoption chirp failed (%r); waiting for the button anyway", err)
            _LOGGER.info("Adoption requested; press the button within %d s", BUTTON_WINDOW_S)
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
