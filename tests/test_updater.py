"""Self-update: only this project's installer, only sane version names."""

import pytest

from noticom_node import updater


@pytest.mark.parametrize("ref", ["main", "v0.3.0", "7396f4f9b7e69ccfeace98f0a9d5ecfa3d4a5a3d", "release/1.0"])
def test_valid_refs(ref):
    assert updater.valid_ref(ref)


@pytest.mark.parametrize("ref", ["", "main; rm -rf /", "x'y", "$(id)", "../evil", "-rf", "a b", "a" * 200])
def test_invalid_refs(ref):
    assert not updater.valid_ref(ref)


def test_install_command_is_detached_and_fixed_url():
    cmd = updater.install_command("v0.3.0")
    assert cmd[0] == "systemd-run" and "--collect" in cmd
    script = cmd[-1]
    assert script == (
        "curl -fsSL 'https://raw.githubusercontent.com/SubBass100/noticom-node/v0.3.0/install.sh'"
        " | NOTICOM_REF='v0.3.0' bash"
    )


async def test_start_update_rejects_bad_ref():
    with pytest.raises(updater.UpdateError):
        await updater.start_update("main && reboot")


async def test_start_update_runs_systemd_run(monkeypatch):
    ran = []

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        ran.append(args)
        return Proc()

    monkeypatch.setattr(updater, "FAKE", False)
    monkeypatch.setattr(updater.asyncio, "create_subprocess_exec", fake_exec)
    await updater.start_update("main")
    assert ran[0][0] == "systemd-run"
