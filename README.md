# noticom-node

Raspberry Pi node software for [Noticom](https://github.com/SubBass100/noticom),
the notification-chime and push-to-talk intercom for Home Assistant.

**Hardware:** Raspberry Pi 3 or newer, ReSpeaker 2-Mic Pi HAT (or the
Keyestudio clone), a small speaker on the HAT's speaker output, and a button
from a GPIO pin to ground (default GPIO13, normally closed).

> **Status: early scaffold.** Adoption into Home Assistant works and the
> installer sets up the ReSpeaker sound card; the control channel, chime
> playback and PTT recording are next.

## Install

On a fresh Pi with Raspberry Pi OS Lite (tested on Debian 13 "trixie", Pi 3)
with the HAT attached:

```sh
curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo bash
```

For a different button, set it at install time, e.g. the HAT's onboard
normally-open button:

```sh
curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo NOTICOM_BUTTON_PIN=17 NOTICOM_BUTTON_NC=0 bash
```

Reboot when the installer asks (first install adds the sound card overlay).
The node then advertises itself on your LAN. In Home Assistant, open the discovered
Noticom node, click **Adopt**, and press the node's button within 60 seconds.

**Factory reset:** hold the button for 10 seconds while the service starts
(e.g. `sudo systemctl restart noticom-node`, then hold).

## Layout

| Path | What |
|---|---|
| `noticom_node/adoption.py` | mDNS advertisement + `POST /adopt` handshake |
| `noticom_node/state.py` | Identity key, node id and pairing in `/etc/noticom` |
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
