#!/usr/bin/env python3
"""Send a LOGIN3 packet the way the client does, with and without the trailing
sequence byte, and report which shape the server accepts.

Deliberately uses a made-up account: the question is whether the packet is
STRUCTURALLY accepted, and "no such account" is as good an answer as "welcome"
for that. No real credentials go over the wire, and nothing is written to disk.

Layout, read off the capture of the real client:
    6f                header, HEADER_CG_LOGIN3 = 111
    31 bytes          account name, NUL padded
    17 bytes          password, NUL padded
    16 bytes          adwClientKey[4]
    [1 byte]          sequence -- the server registers Login3 with sequence=true
"""
import socket, struct, sys, time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 11000

PHASE_AUTH = 0x0a


def handshake_and_phase(s):
    """Answer handshakes until the server announces a phase other than 1."""
    buf = b""
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            return None, buf
        if not chunk:
            return None, buf
        buf += chunk
        while buf:
            h = buf[0]
            if h == 0xFD and len(buf) >= 2:
                phase = buf[1]
                buf = buf[2:]
                if phase != 1:
                    return phase, buf
            elif h == 0xFF and len(buf) >= 13:
                s.sendall(buf[:13])          # echo the handshake back
                buf = buf[13:]
            else:
                return ("unerwartet 0x%02x" % h), buf
    return None, buf


def build_login(seq_byte):
    p = bytes([0x6F])
    p += b"probe_account".ljust(31, b"\x00")
    p += b"not_a_real_pw".ljust(17, b"\x00")
    p += bytes(16)                            # adwClientKey, zeros will do
    if seq_byte is not None:
        p += bytes([seq_byte])
    return p


def attempt(label, seq_byte):
    print("=== %s ===" % label)
    s = socket.create_connection((HOST, PORT), timeout=8)
    s.settimeout(6)
    phase, rest = handshake_and_phase(s)
    print("  Phase nach dem Handshake: %r" % (phase,))
    if phase != PHASE_AUTH:
        print("  -> nicht in der Auth-Phase, Abbruch")
        s.close(); return

    pkt = build_login(seq_byte)
    print("  sende %d Byte Login3%s" % (len(pkt),
          "" if seq_byte is None else " + Sequenzbyte 0x%02x" % seq_byte))
    s.sendall(pkt)

    s.settimeout(6)
    try:
        reply = s.recv(4096)
    except socket.timeout:
        print("  ANTWORT: keine (Zeitueberschreitung) -- der Server wartet auf mehr")
        s.close(); return
    if not reply:
        print("  ANTWORT: Verbindung geschlossen, ohne ein Byte zu senden")
    else:
        print("  ANTWORT: %d Byte, erstes Kopfbyte 0x%02x (%d)" % (len(reply), reply[0], reply[0]))
        print("           %s" % reply[:24].hex(" "))
    s.close()


attempt("ohne Sequenzbyte (65 Byte)", None)
print()
attempt("mit Sequenzbyte (66 Byte)", 0xAF)
