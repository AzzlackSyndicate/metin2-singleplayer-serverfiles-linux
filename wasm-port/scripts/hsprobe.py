#!/usr/bin/env python3
"""Answer the auth server's handshake and report what it sends next.

Counting strings in a binary already misled me twice. The wire is the only place
the answer exists, and this asks it directly instead of waiting for a human to
click through a client.

The exchange, read off the earlier capture:
    server -> fd 01                      HEADER_GC_PHASE, phase 1
    server -> ff <handshake:4><time:4><delta:4>
    client -> ff <the same 13 bytes back>
    ... server may repeat the handshake a few times ...
    then, with encryption ON : fb <~285 bytes>   HEADER_GC_KEY_AGREEMENT
         with encryption OFF : fd <phase>        straight on to PHASE_AUTH
"""
import socket, sys, time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 11000

NAMES = {0xff: "GC_HANDSHAKE", 0xfd: "GC_PHASE", 0xfc: "GC_TIME_SYNC/HANDSHAKE_OK",
         0xfb: "GC_KEY_AGREEMENT", 0xfa: "GC_KEY_AGREEMENT_COMPLETED",
         0xfe: "GC_BINDUDP"}

s = socket.create_connection((HOST, PORT), timeout=8)
s.settimeout(6)
buf = b""
seen = []
deadline = time.time() + 12

while time.time() < deadline:
    try:
        chunk = s.recv(4096)
    except socket.timeout:
        break
    if not chunk:
        print("  Server hat die Verbindung geschlossen")
        break
    buf += chunk

    # Walk what we have. Only the two packets needed to get past the handshake
    # are parsed by length; everything else is reported by its header byte.
    while buf:
        h = buf[0]
        if h == 0xfd and len(buf) >= 2:            # phase: header + 1 byte
            seen.append(("GC_PHASE", buf[1]))
            buf = buf[2:]
        elif h == 0xff and len(buf) >= 13:         # handshake: 13 bytes
            pkt = buf[:13]
            seen.append(("GC_HANDSHAKE", None))
            s.sendall(pkt)                          # echo it back, as the client does
            buf = buf[13:]
        elif h == 0xfb:
            seen.append(("GC_KEY_AGREEMENT", len(buf)))
            buf = b""
        else:
            seen.append((NAMES.get(h, "0x%02x" % h), len(buf)))
            buf = b""
            break

s.close()

print("=== was der Server geschickt hat, in dieser Reihenfolge ===")
for name, extra in seen:
    print("  %-22s %s" % (name, "" if extra is None else extra))

heads = [n for n, _ in seen]
print()
if "GC_KEY_AGREEMENT" in heads:
    print("  ERGEBNIS: der Server verlangt WEITERHIN einen Schluesselaustausch (0xfb)")
    sys.exit(1)
elif heads.count("GC_PHASE") >= 2:
    print("  ERGEBNIS: kein 0xfb mehr -- der Server geht direkt in die naechste Phase")
    sys.exit(0)
else:
    print("  ERGEBNIS: unklar -- zu wenig gesehen, siehe oben")
    sys.exit(2)
