"""Sound hardware detection and generic mixer handling."""

from noticom_node import mixer, soundcards

CARDS = """\
 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 1 [seeed2micvoicec]: simple-card - seeed2micvoicec
                      seeed2micvoicec
 2 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi
                      vc4-hdmi
 3 [Device         ]: USB-Audio - USB Audio Device
                      C-Media Electronics Inc. USB Audio Device at usb-3f980000.usb-1.4, full speed
"""


def fake_asound(tmp_path, cards=CARDS, pcms=None):
    pcms = pcms or {0: ["pcm0p"], 1: ["pcm0p", "pcm0c"], 2: ["pcm0p"], 3: ["pcm0p", "pcm0c"]}
    (tmp_path / "cards").write_text(cards)
    for index, names in pcms.items():
        d = tmp_path / f"card{index}"
        d.mkdir()
        for name in names:
            (d / name).mkdir()
    return tmp_path


def test_list_cards_kinds(tmp_path):
    cards = soundcards.list_cards(fake_asound(tmp_path))
    assert [(c.id, c.kind, c.playback, c.capture) for c in cards] == [
        ("Headphones", "jack", True, False),
        ("seeed2micvoicec", "respeaker", True, True),
        ("vc4hdmi", "hdmi", True, False),
        ("Device", "usb", True, True),
    ]
    assert cards[3].label == "USB: USB Audio Device"
    assert cards[2].playback_device == "hdmi:CARD=vc4hdmi,DEV=0"
    assert cards[0].playback_device == "plughw:CARD=Headphones,DEV=0"


def test_auto_prefers_respeaker_then_usb_then_jack(tmp_path):
    cards = soundcards.list_cards(fake_asound(tmp_path))
    setup = soundcards.choose(cards=cards)
    assert (setup.output.id, setup.input.id) == ("seeed2micvoicec", "seeed2micvoicec")
    no_hat = [c for c in cards if c.kind != "respeaker"]
    setup = soundcards.choose(cards=no_hat)
    assert (setup.output.id, setup.input.id) == ("Device", "Device")
    jack_only = [c for c in cards if c.kind in ("jack", "hdmi")]
    setup = soundcards.choose(cards=jack_only)
    assert setup.output.id == "Headphones" and setup.input is None
    caps = setup.capabilities()
    assert caps["mic"] is False and caps["mic_boost"] is False
    assert [o["id"] for o in caps["outputs"]] == ["Headphones", "vc4hdmi"]


def test_explicit_choice_and_fallback(tmp_path):
    cards = soundcards.list_cards(fake_asound(tmp_path))
    setup = soundcards.choose("Headphones", "none", cards)
    assert setup.output.id == "Headphones" and setup.input is None
    # A chosen card that's been unplugged falls back to automatic.
    setup = soundcards.choose("GoneUSB", "auto", cards)
    assert setup.output.id == "seeed2micvoicec"


def test_no_cards(tmp_path):
    setup = soundcards.choose(cards=soundcards.list_cards(tmp_path))
    assert setup.output is None and setup.input is None


SCONTENTS_USB = """\
Simple mixer control 'Speaker',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
Simple mixer control 'Mic',0
  Capabilities: pvolume pvolume-joined cvolume cvolume-joined pswitch pswitch-joined cswitch cswitch-joined
  Playback channels: Mono
Simple mixer control 'Auto Gain Control',0
  Capabilities: pswitch pswitch-joined
"""


def test_parse_and_pick_controls():
    controls = mixer.parse_scontents(SCONTENTS_USB)
    assert [c.name for c in controls] == ["Speaker", "Mic", "Auto Gain Control"]
    play = mixer.pick_control(controls, "pvolume", mixer.PLAYBACK_PREFERENCE, ("mic", "capture"))
    cap = mixer.pick_control(controls, "cvolume", mixer.CAPTURE_PREFERENCE, ("playback",))
    assert play.name == "Speaker" and cap.name == "Mic"
    # A card with only an oddly named control still gets used...
    odd = mixer.parse_scontents("Simple mixer control 'Weird Volume',0\n  Capabilities: pvolume\n")
    assert mixer.pick_control(odd, "pvolume", mixer.PLAYBACK_PREFERENCE, ("mic",)).name == "Weird Volume"
    # ...but never a mic's playback (monitor) control.
    mic_only = mixer.parse_scontents("Simple mixer control 'Mic',0\n  Capabilities: pvolume cvolume\n")
    assert mixer.pick_control(mic_only, "pvolume", mixer.PLAYBACK_PREFERENCE, ("mic", "capture")) is None


async def test_generic_uses_card_controls_or_software_volume(tmp_path, monkeypatch):
    cards = soundcards.list_cards(fake_asound(tmp_path))
    calls = []

    async def fake_amixer(*args):
        calls.append(args)
        if args[-1] == "scontents":
            return 0, SCONTENTS_USB if args[1] == "Device" else ""
        return 0, ""

    monkeypatch.setattr(mixer, "_amixer", fake_amixer)
    monkeypatch.setattr(mixer, "FAKE", False)

    usb = soundcards.choose("Device", "Device", cards)
    ok, gain = await mixer.apply(usb, volume=40, mic_gain=70, mic_boost=3)
    assert ok and gain == 1.0
    assert ("-q", "-M", "-c", "Device", "sset", "Speaker", "40%", "unmute") in calls
    assert ("-q", "-M", "-c", "Device", "sset", "Mic", "70%", "cap") in calls

    calls.clear()
    jack = soundcards.choose("Headphones", "none", cards)  # fake jack has no controls
    ok, gain = await mixer.apply(jack, volume=40)
    assert ok and gain == 0.4  # software volume
    assert not any("sset" in c for c in calls)


PC_CARDS = """\
 0 [HDMI           ]: HDA-Intel - HDA Intel HDMI
                      HDA Intel HDMI at 0xf7e34000 irq 33
 1 [PCH            ]: HDA-Intel - HDA Intel PCH
                      HDA Intel PCH at 0xf7e30000 irq 32
"""

SCONTENTS_PC = """\
Simple mixer control 'Master',0
  Capabilities: pvolume pvolume-joined pswitch pswitch-joined
Simple mixer control 'Headphone',0
  Capabilities: pvolume pswitch
Simple mixer control 'PCM',0
  Capabilities: pvolume
Simple mixer control 'Capture',0
  Capabilities: cvolume cswitch
"""


def test_pc_prefers_built_in_audio_over_hdmi(tmp_path):
    cards = soundcards.list_cards(
        fake_asound(tmp_path, PC_CARDS, {0: ["pcm3p", "pcm7p"], 1: ["pcm0p", "pcm0c"]})
    )
    assert [(c.id, c.kind) for c in cards] == [("HDMI", "hdmi"), ("PCH", "other")]
    setup = soundcards.choose(cards=cards)
    assert setup.output.id == "PCH"
    assert setup.output.label == "HDA Intel PCH"


def test_pc_path_controls_turned_up():
    controls = mixer.parse_scontents(SCONTENTS_PC)
    chosen = mixer.pick_control(controls, "pvolume", mixer.PLAYBACK_PREFERENCE, ("mic", "capture"))
    assert chosen.name == "PCM"
    assert [c.name for c in mixer.path_controls(controls, chosen)] == ["Master", "Headphone"]
