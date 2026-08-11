#!/bin/sh
# The server is provably ready and waiting. What does the client think it is
# doing? Its own log is the other half of this and we have not looked at it yet.
D=/opt/m2wasm/dist/desktop
echo "=== Dateien, die der Lauf hinterlassen hat ==="
find "$D" -maxdepth 2 -newermt '-40 minutes' -type f 2>/dev/null | head -20 | sed 's/^/  /'

echo
echo "=== Logdateien ==="
for f in "$D"/syserr.txt "$D"/log.txt "$D"/syslog.txt "$D"/*.log; do
    [ -f "$f" ] || continue
    printf '  --- %s (%s) ---\n' "$f" "$(du -h "$f" | cut -f1)"
    tail -30 "$f" | sed 's/^/    /'
done

echo
echo "=== und ganz allgemein alles Log-artige im Baum ==="
find "$D" -maxdepth 2 \( -name '*.txt' -o -name '*.log' \) 2>/dev/null | head -10 | sed 's/^/  /'
