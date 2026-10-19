"""`noticom-node pair` / `reset` over the local control socket."""

import asyncio
import json
import stat

from noticom_node import localctl


async def ask(path, cmd):
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write((json.dumps({"cmd": cmd}) + "\n").encode())
    await writer.drain()
    return reader, writer


async def read(reader):
    return json.loads(await asyncio.wait_for(reader.readline(), 2))


async def test_pair_confirms_and_welcomes(tmp_path):
    ctl = localctl.LocalControl(tmp_path / "control.sock")
    await ctl.start()
    assert stat.S_IMODE((tmp_path / "control.sock").stat().st_mode) == 0o600  # root-only
    reader, writer = await ask(ctl.path, "pair")
    assert (await read(reader))["status"] == "waiting"
    assert ctl.confirm.is_set()
    ctl.take_confirmation()  # an adoption request used it
    await ctl.adopted()
    assert (await read(reader))["status"] == "adopted"
    await ctl.connected("Kitchen")
    assert await read(reader) == {"status": "welcome", "name": "Kitchen"}
    writer.close()
    # Once paired, pair just says so.
    reader, writer = await ask(ctl.path, "pair")
    assert await read(reader) == {"status": "already", "name": "Kitchen"}
    writer.close()
    await ctl.stop()


async def test_pair_window_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(localctl, "CONFIRM_WINDOW_S", 0.1)
    ctl = localctl.LocalControl(tmp_path / "control.sock")
    await ctl.start()
    reader, writer = await ask(ctl.path, "pair")
    await read(reader)
    assert (await read(reader))["status"] == "timeout"
    assert not ctl.confirm.is_set()
    writer.close()
    await ctl.stop()


async def test_reset_command(tmp_path):
    ctl = localctl.LocalControl(tmp_path / "control.sock")
    await ctl.start()
    reader, writer = await ask(ctl.path, "reset")
    assert (await read(reader))["status"] == "resetting"
    assert ctl.reset_requested.is_set()
    writer.close()
    await ctl.stop()


async def test_cli_talks_to_service(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NOTICOM_RUN_DIR", str(tmp_path))
    ctl = localctl.LocalControl()
    await ctl.start()

    async def adopt_soon():
        await asyncio.sleep(0.1)
        ctl.take_confirmation()
        await ctl.adopted()
        await ctl.connected("Garage")

    asyncio.get_running_loop().create_task(adopt_soon())
    assert await localctl._client("pair") == 0
    out = capsys.readouterr().out
    assert "Waiting for Home Assistant" in out
    assert 'Welcome! This node is now connected to Home Assistant as "Garage".' in out
    await ctl.stop()


async def test_cli_when_service_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NOTICOM_RUN_DIR", str(tmp_path / "nope"))
    assert await localctl._client("pair") == 1
    assert "isn't running" in capsys.readouterr().out
