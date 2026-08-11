"""The COMPLETED packet is 4 bytes on the wire, not 1.

The server declares three dummy bytes after the header. Transcribing the struct
without them made the client consume 1 byte of a 4-byte packet and leave three
zeros in the buffer -- which the dispatcher then read as header 0x00, did not
recognise, and silently waited on forever, with the phase packet stuck behind it.

Idempotent.
"""
import io
P = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/Packet.h"
s = io.open(P, encoding="utf-8", newline="").read()

old = """struct TPacketKeyAgreementCompleted
{
	BYTE bHeader;
};"""
new = """struct TPacketKeyAgreementCompleted
{
	BYTE bHeader;
	BYTE data[3]; // dummy (not used), but it IS on the wire: the packet is 4 bytes
};"""

if "dummy (not used)" in s:
    print("  schon vorhanden")
else:
    assert s.count(old) == 1, "Struktur nicht eindeutig gefunden"
    io.open(P, "w", encoding="utf-8", newline="").write(s.replace(old, new, 1))
    print("  TPacketKeyAgreementCompleted ist jetzt 4 Bytes")
