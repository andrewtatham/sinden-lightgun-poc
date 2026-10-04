#!/bin/sh
# One-off: let your normal user read the Sinden guns' buttons and talk to their recoil serial port.
# Run with:  sudo ./setup-permissions.sh      (no reboot needed; unplug/replug not needed either)
set -e
rm -f /etc/udev/rules.d/99-sinden-lightgun.rules
cat > /etc/udev/rules.d/70-sinden-lightgun.rules <<'RULES'
# Sinden lightgun (USB 16c0:0f01-0f04, one per player): give the logged-in user access to its input + serial devices
SUBSYSTEM=="input", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="0f0[1-4]", TAG+="uaccess"
SUBSYSTEM=="tty",   ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="0f0[1-4]", TAG+="uaccess"
RULES
udevadm control --reload
udevadm trigger --action=add --subsystem-match=input --subsystem-match=tty
echo "Done. Check with:  ls -l /dev/ttyACM*  (you should see a '+' in the permissions)"
