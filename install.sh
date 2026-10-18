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
apt-get install -y -qq python3 python3-venv python3-dev alsa-utils git

# TODO: ReSpeaker 2-Mic HAT driver/overlay. The right steps depend on the
# board revision (WM8960 vs TLV320AIC3104 codec) and the Pi OS release, so
# this is filled in once tested on real hardware. Until then, set the HAT up
# by hand and confirm it shows up in `aplay -l`.
if ! aplay -l 2>/dev/null | grep -qiE 'seeed|wm8960|respeaker|tlv320'; then
  log "WARNING: no ReSpeaker sound card detected in 'aplay -l' yet."
fi

log "Installing noticom-node ($REF) into $PREFIX"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "git+$REPO@$REF"

install -d -m 700 "$STATE"

log "Installing systemd service"
curl -sSL "https://raw.githubusercontent.com/SubBass100/noticom-node/$REF/systemd/noticom-node.service" \
  -o /etc/systemd/system/noticom-node.service
systemctl daemon-reload
systemctl enable --now noticom-node.service

log "Done. The node should now appear in Home Assistant under"
log "Settings -> Devices & services as a discovered Noticom node."
log "Logs: journalctl -u noticom-node -f"
