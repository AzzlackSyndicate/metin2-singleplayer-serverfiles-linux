#!/bin/sh
# Capture the auth conversation so the two clients can be compared byte for byte.
# Reading source has taken us as far as it can; the wire is the only place the
# difference actually exists.
#
# Filtered to one IP and one port, written to a file, bounded in time. tcpdump is
# a diagnostic tool and gets removed again afterwards.
set -e
IP=88.236.176.201

command -v tcpdump >/dev/null 2>&1 || {
    echo "=== tcpdump nachinstallieren ==="
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tcpdump
}

rm -f /tmp/auth.pcap
echo "=== Mitschnitt startet: Port 11000, nur von $IP, 15 Minuten ==="
nohup timeout 900 tcpdump -i any -n -s 0 \
    "host $IP and tcp port 11000" -w /tmp/auth.pcap >/tmp/tcpdump.log 2>&1 &
sleep 2
if pgrep -x tcpdump >/dev/null; then
    echo "  laeuft (PID $(pgrep -x tcpdump | head -1))"
else
    echo "  START FEHLGESCHLAGEN:"; cat /tmp/tcpdump.log
    exit 1
fi
