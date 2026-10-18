#!/usr/bin/env bash
# Noticom node installer.
#   curl -sSL https://raw.githubusercontent.com/SubBass100/noticom-node/main/install.sh | sudo bash
set -euo pipefail

REPO="https://github.com/SubBass100/noticom-node"
REF="${NOTICOM_REF:-main}"
PREFIX=/opt/noticom-node
STATE=/etc/noticom

log() { printf '\033[1;34m[noticom]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[noticom]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root (pipe to 'sudo bash')."
[[ -r /etc/os-release ]] && . /etc/os-release
log "OS: ${PRETTY_NAME:-unknown}, kernel $(uname -r)"

log "Installing system packages"
apt-get update -qq
# GPIO (lgpio) and NumPy come from apt: lgpio isn't usable from a plain pip
# install, and apt's NumPy avoids slow source builds on 32-bit Pi OS.
apt-get install -y -qq python3 python3-venv python3-dev python3-lgpio \
  python3-numpy alsa-utils git iw

# --- ReSpeaker 2-Mic HAT -----------------------------------------------
# The v1 board (and the Keyestudio clone) has a WM8960 codec at I2C 0x1a.
# Seeed's respeaker-2mic-v1_0 overlay doesn't supply the codec's master
# clock on current kernels (playback fails: "no PCM clock"), so we use the
# kernel's stock wm8960-soundcard overlay, keeping Seeed's card name.
CONFIG_TXT=/boot/firmware/config.txt
OVERLAY_LINE="dtoverlay=wm8960-soundcard,alsaname=seeed2micvoicec"
REBOOT_NEEDED=0

apt-get install -y -qq i2c-tools
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_i2c 0
fi

probe() {  # probe <hex addr>: true if a device (or a bound driver) answers
  i2cdetect -y 1 "0x$1" "0x$1" 2>/dev/null | grep -qE "^[0-9a-f]0:.*($1|UU)"
}

if probe 1a; then
  log "Found ReSpeaker 2-Mic HAT v1 (WM8960 codec)"
  [[ -f $CONFIG_TXT ]] || die "$CONFIG_TXT not found"
  # Drop Seeed's broken overlay if someone installed it by hand.
  if grep -qE '^dtoverlay=respeaker-2mic-v1_0' "$CONFIG_TXT"; then
    sed -i 's/^dtoverlay=respeaker-2mic-v1_0/#&  # replaced by noticom/' "$CONFIG_TXT"
    REBOOT_NEEDED=1
  fi
  if ! grep -qxF "$OVERLAY_LINE" "$CONFIG_TXT"; then
    printf '\n# noticom: ReSpeaker 2-Mic HAT\n%s\n' "$OVERLAY_LINE" >> "$CONFIG_TXT"
    REBOOT_NEEDED=1
  fi
elif probe 18; then
  log "WARNING: found a ReSpeaker 2-Mic HAT v2 (TLV320AIC3104 codec)."
  log "         v2 boards aren't supported by the installer yet."
else
  log "WARNING: no ReSpeaker codec found on I2C bus 1. Is the HAT seated?"
fi
# Mixer levels (mono output, mic routing, gains) are applied by the
# noticom-node service itself at startup, so nothing to store here.

# --- Wi-Fi power saving ---------------------------------------------------
# With power saving on, the Pi's Wi-Fi misses incoming ARP lookups: the node
# can reach HA but HA gets "No route to host" connecting to the node. A node
# must stay reachable, so turn it off for every Wi-Fi connection, now and
# for good (NetworkManager global default; 2 = disable).
if [[ -d /etc/NetworkManager/conf.d ]]; then
  cat > /etc/NetworkManager/conf.d/noticom-wifi-powersave.conf <<'NM'
# Written by the noticom-node installer: keep Wi-Fi awake so Home
# Assistant can always reach this node.
[connection]
wifi.powersave = 2
NM
  if command -v nmcli >/dev/null; then
    while IFS=: read -r name type; do
      [[ $type == 802-11-wireless ]] || continue
      nmcli connection modify "$name" 802-11-wireless.powersave 2 || true
    done < <(nmcli -t -f NAME,TYPE connection show)
  fi
fi
for dev in /sys/class/net/*/wireless; do
  [[ -e $dev ]] || continue
  iface=$(basename "$(dirname "$dev")")
  iw "$iface" set power_save off 2>/dev/null && log "Wi-Fi power saving off on $iface"
done

log "Installing noticom-node ($REF) into $PREFIX"
# --system-site-packages so the venv sees apt's lgpio and NumPy. Recreated
# each run so re-installs and upgrades start clean.
systemctl stop noticom-node.service 2>/dev/null || true
rm -rf "$PREFIX/venv"
python3 -m venv --system-site-packages "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "git+$REPO@$REF"

install -d -m 700 "$STATE"
# Button used before HA sends this node a config (e.g. the adoption press).
# Override at install time, e.g. NOTICOM_BUTTON_PIN=17 or NOTICOM_BUTTON_NC=1.
if [[ ! -f $STATE/node.env || -n ${NOTICOM_BUTTON_PIN:-}${NOTICOM_BUTTON_NC:-} ]]; then
  cat > "$STATE/node.env" <<ENV
NOTICOM_BUTTON_PIN=${NOTICOM_BUTTON_PIN:-13}
NOTICOM_BUTTON_NC=${NOTICOM_BUTTON_NC:-0}
ENV
fi
log "Button: GPIO$(sed -n 's/^NOTICOM_BUTTON_PIN=//p' "$STATE/node.env"), normally $( [[ $(sed -n 's/^NOTICOM_BUTTON_NC=//p' "$STATE/node.env") == 1 ]] && echo closed || echo open)"

log "Installing systemd service"
curl -sSL "https://raw.githubusercontent.com/SubBass100/noticom-node/$REF/systemd/noticom-node.service" \
  -o /etc/systemd/system/noticom-node.service
systemctl daemon-reload
systemctl enable --now noticom-node.service

if [[ $REBOOT_NEEDED -eq 1 ]]; then
  log "Sound card overlay added. REBOOT to finish: sudo reboot"
  log "After the reboot the node appears in Home Assistant under"
else
  log "Done. The node should now appear in Home Assistant under"
fi
log "Settings -> Devices & services as a discovered Noticom node."
log "Logs: journalctl -u noticom-node -f"
