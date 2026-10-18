"""Wi-Fi power-save guard and sound card check."""

from noticom_node import health


def test_wireless_interfaces(tmp_path):
    (tmp_path / "wlan0" / "wireless").mkdir(parents=True)
    (tmp_path / "eth0").mkdir()
    assert health.wireless_interfaces(tmp_path) == ["wlan0"]


async def test_disable_powersave_only_when_on(monkeypatch):
    calls = []
    state = {"wlan0": "on", "wlan1": "off"}

    async def fake_iw(*args):
        calls.append(args)
        iface = args[1]
        if args[2] == "get":
            return 0, f"Power save: {state[iface]}"
        state[iface] = "off"
        return 0, ""

    monkeypatch.setattr(health, "_iw", fake_iw)
    assert await health.disable_wifi_powersave(["wlan0", "wlan1"]) == ["wlan0"]
    assert ("dev", "wlan1", "set", "power_save", "off") not in calls
    assert await health.disable_wifi_powersave(["wlan0"]) == []  # already off now


async def test_missing_iw_is_harmless(monkeypatch):
    async def no_iw(*args):
        return 127, "iw is not installed"

    monkeypatch.setattr(health, "_iw", no_iw)
    assert await health.disable_wifi_powersave(["wlan0"]) == []


def test_sound_card_present(tmp_path):
    cards = tmp_path / "cards"
    cards.write_text(
        " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
        " 1 [seeed2micvoicec]: simple-card - seeed2micvoicec\n"
    )
    assert health.sound_card_present(cards)
    cards.write_text(" 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n")
    assert not health.sound_card_present(cards)
    assert not health.sound_card_present(tmp_path / "missing")
