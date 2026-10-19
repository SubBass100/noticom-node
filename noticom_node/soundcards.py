"""Find the node's sound hardware: ReSpeaker HAT, USB sound card, the Pi's
headphone jack or HDMI.

Cards are read from /proc/asound. In "auto" mode the best output is, in
order: ReSpeaker, USB, headphone jack, HDMI; the best input is the ReSpeaker,
then a USB card with a microphone. A node with no input is chime-only.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

ASOUND = Path("/proc/asound")
RESPEAKER_ID = "seeed2micvoicec"

KIND_RESPEAKER = "respeaker"
KIND_USB = "usb"
KIND_JACK = "jack"
KIND_HDMI = "hdmi"
KIND_OTHER = "other"

OUTPUT_PREFERENCE = [KIND_RESPEAKER, KIND_USB, KIND_JACK, KIND_HDMI, KIND_OTHER]
INPUT_PREFERENCE = [KIND_RESPEAKER, KIND_USB, KIND_OTHER]

AUTO = "auto"
NONE = "none"

_CARD_LINE = re.compile(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(\S+)\s+-\s+(.*)$")


@dataclass(frozen=True)
class SoundCard:
    """One ALSA card."""

    index: int
    id: str  # stable ALSA id, e.g. "Headphones", "Device", "seeed2micvoicec"
    driver: str
    name: str
    kind: str
    playback: bool
    capture: bool

    @property
    def label(self) -> str:
        """Human-friendly name for Home Assistant."""
        return {
            KIND_RESPEAKER: "ReSpeaker HAT",
            KIND_JACK: "Headphone jack",
            KIND_HDMI: f"HDMI ({self.name})",
            KIND_USB: f"USB: {self.name}",
        }.get(self.kind, self.name)

    @property
    def playback_device(self) -> str:
        """ALSA device string for playing on this card."""
        if self.kind == KIND_HDMI:
            return f"hdmi:CARD={self.id},DEV=0"  # handles HDMI's IEC958 framing
        return f"plughw:CARD={self.id},DEV=0"

    @property
    def capture_device(self) -> str:
        """ALSA device string for recording from this card."""
        return f"plughw:CARD={self.id},DEV=0"

    def as_dict(self) -> dict:
        """For reporting to Home Assistant."""
        return {**asdict(self), "label": self.label}


def _kind(card_id: str, driver: str) -> str:
    if card_id == RESPEAKER_ID:
        return KIND_RESPEAKER
    if driver == "USB-Audio":
        return KIND_USB
    if card_id == "Headphones" or "headpho" in driver:
        return KIND_JACK
    if card_id.startswith("vc4hdmi") or "hdmi" in driver.lower():
        return KIND_HDMI
    return KIND_OTHER


def list_cards(asound: Path = ASOUND) -> list[SoundCard]:
    """All sound cards, as the kernel sees them right now."""
    try:
        text = (asound / "cards").read_text()
    except OSError:
        return []
    cards = []
    for line in text.splitlines():
        match = _CARD_LINE.match(line)
        if not match:
            continue
        index, card_id, driver, name = match.groups()
        card_dir = asound / f"card{index}"
        pcms = [p.name for p in card_dir.glob("pcm*")] if card_dir.exists() else []
        cards.append(
            SoundCard(
                index=int(index),
                id=card_id,
                driver=driver,
                name=name.strip(),
                kind=_kind(card_id, driver),
                playback=any(p.endswith("p") for p in pcms),
                capture=any(p.endswith("c") for p in pcms),
            )
        )
    return cards


def _pick(cards: list[SoundCard], choice: str, preference: list[str]) -> SoundCard | None:
    if choice == NONE:
        return None
    if choice not in ("", AUTO):
        for card in cards:
            if card.id == choice:
                return card
        # The chosen card is unplugged: fall back to automatic.
    ranked = sorted(cards, key=lambda c: (preference.index(c.kind) if c.kind in preference else 99, c.index))
    for card in ranked:
        if card.kind in preference:
            return card
    return None


@dataclass(frozen=True)
class AudioSetup:
    """Which cards the node plays on and records from."""

    output: SoundCard | None
    input: SoundCard | None
    cards: tuple[SoundCard, ...]

    @property
    def has_mic(self) -> bool:
        return self.input is not None

    def capabilities(self) -> dict:
        """What the node reports to Home Assistant."""
        return {
            "mic": self.has_mic,
            "mic_boost": self.input is not None and self.input.kind == KIND_RESPEAKER,
            "output": self.output.id if self.output else None,
            "input": self.input.id if self.input else None,
            "outputs": [
                {"id": c.id, "label": c.label, "kind": c.kind} for c in self.cards if c.playback
            ],
        }


def choose(
    output_choice: str = AUTO,
    input_choice: str = AUTO,
    cards: list[SoundCard] | None = None,
) -> AudioSetup:
    """Pick output and input cards from what's present."""
    cards = list_cards() if cards is None else cards
    outputs = [c for c in cards if c.playback]
    inputs = [c for c in cards if c.capture]
    return AudioSetup(
        output=_pick(outputs, output_choice, OUTPUT_PREFERENCE),
        input=_pick(inputs, input_choice, INPUT_PREFERENCE),
        cards=tuple(cards),
    )
