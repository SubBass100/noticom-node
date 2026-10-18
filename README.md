# noticom-node

Raspberry Pi node software for [Noticom](https://github.com/SubBass100/noticom),
the notification-chime and push-to-talk intercom for Home Assistant.

**Hardware:** Raspberry Pi 3 or newer, ReSpeaker 2-Mic Pi HAT (or the
Keyestudio clone), a small speaker on the HAT's speaker output, and a button
from a GPIO pin to ground (default GPIO13, normally open; normally closed
buttons are supported too).

> **Status: early.** The installer sets up the ReSpeaker sound card, and
> nodes adopt into Home Assistant and stay connected, following volume, mic
> and button settings from HA. Chime playback and PTT recording are next.

## Install

On a fresh Pi with Raspberry Pi OS Lite (tested on Debian 13 "trixie", Pi 3)
with the HAT attached:

```sh
curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo bash
```

For a different button, set it at install time, e.g. the HAT's onboard
button:

```sh
curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo NOTICOM_BUTTON_PIN=17 bash
```

Reboot when the installer asks (first install adds the sound card overlay).
The node then advertises itself on your LAN. In Home Assistant, open the discovered
Noticom node, click **Adopt**, and press the node's button within 60 seconds.

**Factory reset** (two steps, so it can't happen by accident):

1. Press the button 5 times quickly (within 4 seconds). The node beeps 3
   times and says "To factory reset, press and hold the button for five
   seconds."
2. Within 10 seconds, press and hold for 5 seconds until it beeps 3 more
   times. The node forgets Home Assistant and advertises itself again.

Not pressing, or letting go early, cancels with a low tone and "Reset
cancelled."

## Layout

| Path | What |
|---|---|
| `noticom_node/adoption.py` | mDNS advertisement + `POST /adopt` handshake |
| `noticom_node/control.py` | Live connection to HA: mutual auth, config, unpair, reconnect |
| `noticom_node/state.py` | Identity key, node id, pairing and cached config in `/etc/noticom` |
| `noticom_node/reset.py` | Two-step factory reset confirmation |
| `noticom_node/playqueue.py` | Playback queue rules (1 playing + 5 waiting, chime priority) |
| `noticom_node/hardware.py` | Button (gpiozero) and audio (aplay) |
| `noticom_node/mixer.py` | WM8960 mixer state: mono output, mic routing, gains |
| `noticom_node/audio.py` | Recording processing: stereo to mono, normalize |
| `noticom_node/protocol.py` | Wire protocol; mirror of the integration's copy |

The protocol spec is
[docs/DESIGN.md](https://github.com/SubBass100/noticom/blob/main/docs/DESIGN.md)
in the integration repo.

## Development

Runs off the Pi with fake hardware:

```sh
pip install -e ".[dev]"
python -m pytest
NOTICOM_FAKE_HW=1 NOTICOM_STATE_DIR=./dev-state noticom-node
```

## License

Apache-2.0
