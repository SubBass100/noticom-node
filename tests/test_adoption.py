"""Adoption handshake, checked the same way the HA config flow checks it."""

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from noticom_node import protocol
from noticom_node.adoption import AdoptionServer
from noticom_node.state import NodeState


async def _client(tmp_path, pressed: bool):
    state = NodeState(tmp_path)

    async def wait_for_press(timeout):
        return pressed

    async def chirp():
        pass

    server = AdoptionServer(state, wait_for_press, chirp)
    app = web.Application()
    app.router.add_post("/adopt", server._handle_adopt)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, state


async def test_adopt_success(tmp_path):
    client, state = await _client(tmp_path, pressed=True)
    server_key = protocol.generate_private_key()
    nonce = protocol.new_nonce()
    resp = await client.post(
        "/adopt",
        json={
            "server_pubkey": protocol.public_key_b64(server_key),
            "nonce": nonce,
            "server_url": "http://ha.local:8123",
        },
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["node_id"] == state.node_id
    assert protocol.verify(body["node_pubkey"], protocol.b64d(nonce), body["sig"])
    assert state.pairing.server_url == "http://ha.local:8123"
    assert oct((tmp_path / "node.key").stat().st_mode & 0o777) == "0o600"
    await client.close()


async def test_adopt_button_timeout(tmp_path):
    client, state = await _client(tmp_path, pressed=False)
    resp = await client.post(
        "/adopt",
        json={"server_pubkey": "x", "nonce": protocol.new_nonce(), "server_url": "u"},
    )
    assert resp.status == 408
    assert state.pairing is None
    await client.close()


async def test_adopt_bad_request(tmp_path):
    client, _ = await _client(tmp_path, pressed=True)
    resp = await client.post("/adopt", json={"nonce": "not base64!!"})
    assert resp.status == 400
    await client.close()


def test_factory_reset(tmp_path):
    state = NodeState(tmp_path)
    old = protocol.public_key_b64(state.key)
    from noticom_node.state import Pairing

    state.save_pairing(Pairing("k", "u"))
    state.factory_reset()
    assert state.pairing is None
    assert protocol.public_key_b64(state.key) != old


async def test_hung_chirp_does_not_block_adoption(tmp_path, monkeypatch):
    import asyncio

    from noticom_node import adoption

    monkeypatch.setattr(adoption, "CHIRP_TIMEOUT_S", 0.1)
    state = NodeState(tmp_path)

    async def hang():
        await asyncio.sleep(3600)

    async def pressed(timeout):
        return True

    server = AdoptionServer(state, pressed, hang)
    app = web.Application()
    app.router.add_post("/adopt", server._handle_adopt)
    client = TestClient(TestServer(app))
    await client.start_server()
    nonce = protocol.new_nonce()
    resp = await asyncio.wait_for(client.post("/adopt", json={
        "server_pubkey": protocol.public_key_b64(protocol.generate_private_key()),
        "nonce": nonce, "server_url": "http://ha:8123",
    }), 5)
    assert resp.status == 200
    await client.close()


async def test_say_hello_connects(unused_tcp_port_factory=None):
    import asyncio

    from noticom_node.adoption import say_hello

    seen = asyncio.Event()

    async def handle(reader, writer):
        seen.set()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    assert await say_hello("127.0.0.1", port)
    await asyncio.wait_for(seen.wait(), 2)
    server.close()
    await server.wait_closed()
    assert not await say_hello("127.0.0.1", port, timeout=1)  # nothing listening now
