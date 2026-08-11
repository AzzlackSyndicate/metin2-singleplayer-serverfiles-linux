#!/usr/bin/env python3
"""Add the key agreement packets to the Old-Metin2 client's Packet.h.

Three header bytes and two structs, taken verbatim from the stock 2014 client,
which is the counterpart our server was built and tested against:

    0xfb  HEADER_CG_KEY_AGREEMENT             client -> server, our half
    0xfb  HEADER_GC_KEY_AGREEMENT             server -> client, its half
    0xfa  HEADER_GC_KEY_AGREEMENT_COMPLETED   server -> client, switch it on

The structs go inside the file's `#pragma pack(1)` region, which is what makes
TPacketKeyAgreement 1+2+2+256 = 261 bytes -- the exact length the server was
measured sending.

Idempotent: run it twice and the second run reports that everything is present.
"""
import io, sys

P = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/Packet.h"
s = io.open(P, encoding="utf-8", newline="").read()

changed = []

# ── 1. client -> server ──────────────────────────────────────────────────────
cg_anchor = "    HEADER_CG_HANDSHAKE                         = 0xff,\n"
cg_add = ("\tHEADER_CG_KEY_AGREEMENT\t\t\t\t\t\t= 0xfb,"
          "\t// _IMPROVED_PACKET_ENCRYPTION_\n")
if "HEADER_CG_KEY_AGREEMENT" in s:
    changed.append("CG-Kopfbyte: schon vorhanden")
else:
    assert s.count(cg_anchor) == 1, "CG-Anker nicht eindeutig"
    s = s.replace(cg_anchor, cg_anchor + cg_add, 1)
    changed.append("CG-Kopfbyte 0xfb eingefuegt")

# ── 2. server -> client ──────────────────────────────────────────────────────
gc_anchor = "\tHEADER_GC_HANDSHAKE_OK\t\t\t\t\t\t= 0xfc, // 252\n"
gc_add = ("\tHEADER_GC_KEY_AGREEMENT_COMPLETED\t\t\t= 0xfa,"
          "\t// _IMPROVED_PACKET_ENCRYPTION_\n"
          "\tHEADER_GC_KEY_AGREEMENT\t\t\t\t\t\t= 0xfb,"
          "\t// _IMPROVED_PACKET_ENCRYPTION_\n")
if "HEADER_GC_KEY_AGREEMENT" in s:
    changed.append("GC-Kopfbytes: schon vorhanden")
else:
    assert s.count(gc_anchor) == 1, "GC-Anker nicht eindeutig"
    s = s.replace(gc_anchor, gc_add + gc_anchor, 1)
    changed.append("GC-Kopfbytes 0xfa/0xfb eingefuegt")

# ── 3. the two structs, inside the packed region ─────────────────────────────
structs = """
// _IMPROVED_PACKET_ENCRYPTION_ -- the Diffie-Hellman exchange this client had
// removed. Ported from the stock 2014 client (UserInterface/Packet.h); the
// server sends 1+2+2+256 = 261 bytes and expects the same shape back.
struct TPacketKeyAgreement
{
\tstatic const int MAX_DATA_LEN = 256;
\tBYTE bHeader;
\tWORD wAgreedLength;
\tWORD wDataLength;
\tBYTE data[MAX_DATA_LEN];
};

struct TPacketKeyAgreementCompleted
{
\tBYTE bHeader;
};

"""
tail = "#pragma pack(pop)"
if "TPacketKeyAgreement" in s:
    changed.append("Strukturen: schon vorhanden")
else:
    i = s.rfind(tail)
    assert i > 0, "abschliessendes #pragma pack(pop) nicht gefunden"
    s = s[:i] + structs + s[i:]
    changed.append("Strukturen eingefuegt")

io.open(P, "w", encoding="utf-8", newline="").write(s)

for c in changed:
    print("  " + c)

print("\n=== Gegenprobe ===")
t = io.open(P, encoding="utf-8").read()
for name in ("HEADER_CG_KEY_AGREEMENT", "HEADER_GC_KEY_AGREEMENT",
             "HEADER_GC_KEY_AGREEMENT_COMPLETED",
             "TPacketKeyAgreement", "TPacketKeyAgreementCompleted"):
    print("  %-36s %s" % (name, "ok" if name in t else "FEHLT"))
