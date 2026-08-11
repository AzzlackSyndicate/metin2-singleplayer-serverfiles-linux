#!/bin/bash
# Install Google Chrome stable in WSL (needed to load the wasm client page).
mkdir -p /root/kscripts/out
LOG=/root/kscripts/out/k03-install-chrome.txt
{
echo "=== /dev/dri ==="
ls -la /dev/dri 2>&1
echo "=== download ==="
cd /root/kscripts
curl -fsSL -o chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
ls -la chrome.deb
echo "=== apt install ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq /root/kscripts/chrome.deb
echo "=== verify ==="
command -v google-chrome && google-chrome --version
} > "$LOG" 2>&1
echo "rc=$? log=$LOG"
