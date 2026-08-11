#!/bin/sh
# WSLg builds its Wayland/X session for a login user. This distro was made for
# building the server and has only root, which is why glxinfo reported
# "screen 0 does not appear to be DRI3 capable" and fell back to llvmpipe.
#
# Adding an ordinary user costs nothing and does not disturb the server build,
# which keeps running as root.
set -e
U=m2
if id "$U" >/dev/null 2>&1; then
    echo "Benutzer '$U' existiert bereits"
else
    useradd -m -s /bin/bash "$U"
    usermod -aG sudo,video,render "$U" 2>/dev/null || true
    echo "Benutzer '$U' angelegt"
fi
# Read access to the tree we build in, so the client can be started as this user.
chmod -R a+rX /opt/m2wasm 2>/dev/null || true
id "$U" | sed 's/^/  /'
