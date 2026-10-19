<p align="center">
  <img src="docs/noticom-icon.png" alt="Noticom icon" width="140">
</p>

<h1 align="center">noticom-node</h1>

<p align="center"><b>Turns a Raspberry Pi into a Noticom speaker: it plays chimes from Home Assistant and works as a push-to-talk intercom.</b></p>

---

This is the software for the speakers (we call them **nodes**) used by
[Noticom](https://github.com/SubBass100/noticom), a chime and intercom
system for Home Assistant. Install Noticom in Home Assistant first, then
follow these steps for each node.

## What you need for each node

- **Raspberry Pi 3 or newer**, with a power supply and a microSD card
- **Something to play sound on.** Pick one:

| Sound hardware | Chimes | Intercom (talk) | Notes |
|---|---|---|---|
| **ReSpeaker 2-Mic Pi HAT** (or the Keyestudio version) + a small speaker | ✅ | ✅ | The original setup, fully tuned |
| **USB sound card or USB speakerphone** with a microphone | ✅ | ✅ | Any standard USB audio device |
| **The Pi's headphone jack** + powered speakers | ✅ | ❌ | Chimes only. Pi 3 and 4 only (the Pi 5 has no jack) |

- **A push button** (optional). You need one to talk on the intercom. Without
  one, you add and reset the node with a command instead (see below).

## Wiring

- **ReSpeaker:** push the HAT onto the Pi's pin header and plug the speaker
  into the HAT's **speaker** connector.
- **USB sound card:** plug it in, along with its speaker and microphone.
- **Headphone jack:** plug in powered speakers (ones with their own power
  or a USB cable). The jack is too weak for a bare speaker.
- **Button (optional):** connect it between **GPIO13** and **ground (GND)**.
  On the ReSpeaker, GPIO13 is on its Grove connector. Using a different pin
  or no button is fine: see [Using a different button](#using-a-different-button).

The node finds your sound hardware by itself. If there's more than one (say,
a USB card and the headphone jack), it prefers the ReSpeaker, then USB,
then the jack, and you can pick a different one in Home Assistant with the
node's **Sound output** setting.

## Setting up a node

### 1. Prepare the Pi

1. Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to put
   **Raspberry Pi OS Lite (64-bit)** on the microSD card.
2. In Imager's settings, set a **hostname** (for example
   `noticom-kitchen`), a **username and password**, your **Wi-Fi**, and
   turn on **SSH**.
3. Put the card in the Pi and power it up.

### 2. Install the software

Connect to the Pi with SSH (for example `ssh pi@noticom-kitchen.local`)
and run this one line:

```sh
curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo bash
```

The installer sets up the sound card (if you have a ReSpeaker), the button,
a natural-sounding voice for spoken prompts, and the Noticom service. It takes a few minutes, and
longer on a Pi 3.

**If it asks you to reboot, run `sudo reboot`.** That only happens the
first time on a ReSpeaker, to switch on its sound card.

No button? Install with `NOTICOM_BUTTON_PIN=none`:

```sh
curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo NOTICOM_BUTTON_PIN=none bash
```

### 3. Add it to Home Assistant

1. In Home Assistant, go to **Settings → Devices & services**. The node
   appears under **Discovered** within a minute or two.
2. Click **Add**, give it a name (like *Kitchen*), and click **Submit**.
3. The node beeps. **Press its button within 60 seconds**, or, if it has
   no button (or it's hard to reach), run this on the Pi:

   ```sh
   sudo noticom-node pair
   ```

   It confirms the request and, once the node is connected, prints
   `Welcome! This node is now connected to Home Assistant as "Kitchen".`
   You can also run it first and click Submit afterwards: it waits up to
   2 minutes.

Done. You can now play chimes on it from automations, and, if it has a
microphone and a button, use it as an intercom.

> **It didn't show up?** In Home Assistant click **Add integration**,
> search **Noticom**, and enter the Pi's IP address. Run `hostname -I` on
> the Pi to find it.

## Using the button

| Do this | What happens |
|---|---|
| **Hold** the button, wait for the beep, talk, let go | Sends your message to the other nodes |
| **Tap** it | Nothing, so a bump won't send anything |
| **Press 5 times quickly**, then **hold for 5 seconds** when asked | Factory reset (see below) |

A **low tone** after you let go means the message couldn't be sent,
usually because the node or Home Assistant is offline.

## Factory reset

A reset makes the node forget its Home Assistant, so you can add it again
(or to a different Home Assistant).

**From the command line** (works on any node):

```sh
sudo noticom-node reset
```

**With the button:**

1. Press the button **5 times quickly**. The node beeps 3 times and says
   *"To factory reset, press and hold the button for five seconds."*
2. **Press and hold** the button for 5 seconds, until it beeps 3 more
   times.

If you don't press, or you let go too soon, it says *"Reset cancelled"* and
nothing changes. Deleting the node in Home Assistant also resets it.

## Using a different button

The button pin and type can be changed any time in Home Assistant: open
the node under **Settings → Devices & services → Noticom** and click
**Configure**.

Use pin **0** for "no button".

You can also set them when installing. For example, to use the HAT's
built-in button:

```sh
curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo NOTICOM_BUTTON_PIN=17 bash
```

Add `NOTICOM_BUTTON_NC=1` if your button is **normally closed** (connected
until pressed). Most buttons are normally open.

## Updating

Easiest: in Home Assistant, install the node's **Node software** update
(under **Settings → Updates**, or **Noticom → Settings → Update all**). The
node updates itself and restarts.

Or, over SSH, run the install line again. Your node stays connected to Home
Assistant and keeps its settings. Nodes on version 0.2.0 or older need this
once before they can update from Home Assistant.

```sh
curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo bash
```

## Troubleshooting

See what the node is doing:

```sh
journalctl -u noticom-node -f
```

| Problem | What to try |
|---|---|
| No sound at all | The log's `Sound: playing on …` line shows which output the node chose. Check the speaker is connected there, or pick another under the node's **Sound output** in Home Assistant. With a ReSpeaker, `aplay -l` should list `seeed2micvoicec`; if not, re-run the installer and reboot |
| No sound from the headphone jack | Use powered speakers, and check the node's **Volume** in Home Assistant |
| The installer says no ReSpeaker was found | Make sure the HAT is pushed firmly onto all the pins, then run the installer again |
| Home Assistant says "Couldn't reach the node" | Update the node (run the install line again): it now keeps Wi-Fi power saving off and introduces itself to Home Assistant so Home Assistant can find it. As a quick workaround, run `ping -c 3 <your Home Assistant IP>` on the Pi, then try again |
| The button does nothing | Check the log line `Button GPIO13 …`: it should say *released* when you're not touching it. If it says *PRESSED*, change the button type to normally closed in Home Assistant |
| Messages sound too quiet or distorted | Adjust the node's **Mic gain** and **Mic boost** in Home Assistant |

## For developers

The protocol and design are in Noticom's
[docs/DESIGN.md](https://github.com/SubBass100/noticom/blob/main/docs/DESIGN.md).

Run it on any computer without the hardware:

```sh
pip install -e ".[dev]"
python -m pytest
NOTICOM_FAKE_HW=1 NOTICOM_STATE_DIR=./dev-state NOTICOM_CACHE_DIR=./dev-cache noticom-node
```

| File | What it does |
|---|---|
| `noticom_node/adoption.py` | Announces the node on the network and handles being added |
| `noticom_node/control.py` | Stays connected to Home Assistant and follows its settings |
| `noticom_node/player.py`, `playqueue.py` | Plays chimes and messages, one at a time, up to 5 waiting |
| `noticom_node/chimecache.py` | Keeps a copy of every chime on the node |
| `noticom_node/ptt.py` | Push-to-talk recording |
| `noticom_node/soundcards.py` | Finds the sound hardware (ReSpeaker, USB, headphone jack, HDMI) |
| `noticom_node/audio.py`, `mixer.py` | Sound processing and volume/mic settings for any card |
| `noticom_node/localctl.py` | `sudo noticom-node pair` / `reset` |
| `noticom_node/updater.py` | Installs a new version when Home Assistant asks |
| `noticom_node/reset.py`, `prompts.py` | Factory reset and the spoken prompts |
| `noticom_node/state.py` | The node's identity and pairing (in `/etc/noticom`) |
| `install.sh` | The one-line installer |

## License

[Apache-2.0](LICENSE)
