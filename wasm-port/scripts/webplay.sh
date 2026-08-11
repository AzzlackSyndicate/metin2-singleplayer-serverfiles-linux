#!/bin/bash
# Serve the browser client so it can be played from Windows.
#
# WSL2 forwards Windows localhost into the VM, so both services bind inside WSL
# and Windows reaches them at the same port numbers. Testing from Windows rather
# than from Chrome-under-Xvfb is the point: that host has no GPU, so the 3D layer
# there says nothing about whether this is playable.
#
# Two services, because a browser cannot open a TCP socket:
#   m2-ws2tcp      WebSocket in, TCP out. Pinned to our server with -allow.
#   serve-webfs.py the page, the wasm and the 408 content-addressed blobs.
set -u
D=/opt/m2wasm/dist/browser
OUT=/opt/m2wasm-webplay
mkdir -p "$OUT"

pkill -f m2-ws2tcp 2>/dev/null
pkill -f serve-webfs 2>/dev/null
sleep 1

PROXY=$(ls "$D"/m2-ws2tcp*linux*64 2>/dev/null | head -1)
[ -z "$PROXY" ] && PROXY=$(find /opt/m2wasm -name "m2-ws2tcp*" -type f -perm -u+x 2>/dev/null | grep -iE "linux" | head -1)
echo "  Proxy:  $PROXY"
chmod +x "$PROXY" 2>/dev/null

# -allow pins the proxy to our server. Without it any page could dial anywhere.
# -listen is the ADDRESS only; the port is its own flag. Passing "host:port" to
# -listen makes it try to resolve that as a hostname and fail.
setsid nohup "$PROXY" -listen 127.0.0.1 -port 11496 -allow 95.179.165.121 \
      > "$OUT/proxy.log" 2>&1 < /dev/null &
setsid nohup python3 "$D/serve-webfs.py" --port 8730 \
      > "$OUT/serve.log" 2>&1 < /dev/null &
sleep 3

echo "  Ping:   $(curl -s --max-time 3 http://127.0.0.1:11496/ping || echo 'KEINE ANTWORT')"
echo "  Seite:  $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8730/index.html)"

SERVERS=$(python3 - <<'PY'
import base64, json
j = {"version": 1, "region": 0, "servers": [
    {"name": "Singleplayer Official Metin2", "host": "95.179.165.121",
     "auth_port": 11000, "channels": [{"name": "CH1", "port": 13000}]}]}
print(base64.b64encode(json.dumps(j).encode()).decode())
PY
)
echo
echo "=== im Windows-Browser oeffnen ==="
echo "http://127.0.0.1:8730/index.html?serverHost=127.0.0.1&serverPort=11496&servers=$SERVERS"
