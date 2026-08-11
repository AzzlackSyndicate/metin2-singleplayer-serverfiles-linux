#!/usr/bin/env python3
"""The point-change packet is 17 bytes on the wire, not 14.

The stock client declares its header field as `int`, and so does the server; this
tree narrowed it to BYTE. Every such packet therefore left 3 bytes behind, and
since the server sends a burst of them when a character enters the world, the
stream desynchronised right at the character screen -- the client then discarded
its receive buffer and lost whatever came with it, including the phase change it
was waiting for. Hence a loading screen that never ends.

Confirmed against the captured stream, byte for byte:
  11 00 00 00 | 2e 45 00 00 | 11 | 00 00 00 00 | 64 00 00 00
  header=17     vid=17710     type  amount=0     value=100

A survey of all 210 packet structs shared with the stock client found this as the
only size divergence, so this is a one-off, not the first of many.

Idempotent.
"""
import io

P = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/Packet.h"
T = "/opt/m2wasm/src/EngineLib/test/packet_layout_table.h"

s = io.open(P, encoding="utf-8", newline="").read()
old = """typedef struct packet_point_change
{
	BYTE        header;"""
new = """typedef struct packet_point_change
{
	// int, not BYTE: the wire format is 4 bytes here. Both the stock client and
	// the server declare it this way, and the packet is 17 bytes, not 14.
	int         header;"""

if "int, not BYTE" in s:
    print("  Packet.h: schon angepasst")
else:
    assert s.count(old) == 1, "packet_point_change nicht eindeutig gefunden"
    io.open(P, "w", encoding="utf-8", newline="").write(s.replace(old, new, 1))
    print("  Packet.h: TPacketGCPointChange ist jetzt 17 Bytes")

# the layout test asserts the old size and would now fail
t = io.open(T, encoding="utf-8", newline="").read()
oldt = "M(TPacketGCPointChange          ,  14) \\"
newt = "M(TPacketGCPointChange          ,  17) \\"
if oldt in t:
    io.open(T, "w", encoding="utf-8", newline="").write(t.replace(oldt, newt, 1))
    print("  packet_layout_table.h: erwartete Groesse auf 17 gesetzt")
elif newt in t:
    print("  packet_layout_table.h: schon 17")
else:
    print("  packet_layout_table.h: Eintrag nicht gefunden -- bitte pruefen")
