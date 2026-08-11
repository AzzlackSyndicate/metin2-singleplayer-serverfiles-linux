#!/bin/sh
# What happened on the server since the rebuild? Specifically: did the client
# connect, did the handshake complete, and did anything follow it.
docker exec metin2-game bash -c '
echo "=== auth: Verbindungen von aussen (nicht 127.0.0.1) ==="
grep -a "new connection" /opt/metin2/var/auth/syslog 2>/dev/null | grep -v "127.0.0.1" | tail -8

echo
echo "=== auth: Handshake-Ergebnisse ==="
grep -a "Handshake" /opt/metin2/var/auth/syslog 2>/dev/null | tail -8

echo
echo "=== auth: die letzten 20 Zeilen ==="
tail -20 /opt/metin2/var/auth/syslog 2>/dev/null

echo
echo "=== auth: syserr seit dem Neustart ==="
tail -12 /opt/metin2/var/auth/syserr 2>/dev/null
'
