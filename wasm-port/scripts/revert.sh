#!/bin/sh
# Put the server back. The stock client needs the key agreement the change took
# away, so the change broke the thing that worked -- which is the one outcome I
# said would undo it immediately.
set -e
pkill -x tcpdump 2>/dev/null || true
rm -f /tmp/win.pcap /tmp/td.log

F=/opt/metin2/stack/game/src/server/common/service.h
[ -f "$F.m2orig" ] || { echo "keine Sicherungskopie -- Abbruch"; exit 1; }
cp -p "$F.m2orig" "$F"
echo "=== wiederhergestellt ==="
grep -an "IMPROVED_PACKET_ENCRYPTION" "$F" | cat -v | sed 's/^/  /'

cd /opt/metin2/stack
echo
echo "=== Image neu bauen ==="
docker compose build game 2>&1 | tail -6
echo
echo "=== Cores neu starten ==="
docker compose up -d game 2>&1 | tail -5
echo
echo "=== warten, bis die Ports offen sind ==="
for i in $(seq 1 90); do
    if timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/11000' 2>/dev/null \
    && timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/13000' 2>/dev/null; then
        echo "  oben nach ${i}s"; break
    fi
    sleep 1
done
