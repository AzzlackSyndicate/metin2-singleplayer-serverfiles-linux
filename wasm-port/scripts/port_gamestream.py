#!/usr/bin/env python3
"""Teach the game channel the key agreement as well.

The auth channel speaks it now, but the client opens a second connection for the
game server, and that one runs its own handshake and gets the same 0xfb packet.
It knew neither the header nor what to do with it, so CheckPacket() reported a
desync and asked the application to quit.

Three parts, all required:
  1. the header map -- CheckPacket() resolves sizes from it BEFORE the switch
     runs, so without an entry the new cases would never be reached;
  2. the two cases in HandShakePhase();
  3. the handlers themselves, same shape as the ones in CAccountConnector.

Sizes come from sizeof(), so the 4-byte COMPLETED packet stays correct here by
construction.

Idempotent.
"""
import io

NS = "/opt/m2wasm/src/PyLib/src/bindings/net/PythonNetworkStream.cpp"
HS = "/opt/m2wasm/src/PyLib/src/bindings/net/PythonNetworkStreamPhaseHandShake.cpp"
HD = "/opt/m2wasm/src/PyLib/src/bindings/net/PythonNetworkStream.h"

report = []

# ── 1. header map ────────────────────────────────────────────────────────────
s = io.open(NS, encoding="utf-8", newline="").read()
if "HEADER_GC_KEY_AGREEMENT," not in s:
    anchor = "\t\t\tSet(HEADER_GC_HANDSHAKE_OK,\tCNetworkPacketHeaderMap::TPacketType(sizeof(TPacketGCBlank), STATIC_SIZE_PACKET));\n"
    assert s.count(anchor) == 1, "Handshake-OK-Anker nicht eindeutig"
    add = ("\t\t\t// _IMPROVED_PACKET_ENCRYPTION_: without these two entries CheckPacket()\n"
           "\t\t\t// reports a desync and quits before HandShakePhase() ever sees the header.\n"
           "\t\t\tSet(HEADER_GC_KEY_AGREEMENT,\tCNetworkPacketHeaderMap::TPacketType(sizeof(TPacketKeyAgreement), STATIC_SIZE_PACKET));\n"
           "\t\t\tSet(HEADER_GC_KEY_AGREEMENT_COMPLETED,\tCNetworkPacketHeaderMap::TPacketType(sizeof(TPacketKeyAgreementCompleted), STATIC_SIZE_PACKET));\n")
    s = s.replace(anchor, anchor + add, 1)
    io.open(NS, "w", encoding="utf-8", newline="").write(s)
    report.append("Header-Tabelle: beide Pakete registriert")

# ── 2. + 3. the phase switch and the handlers ────────────────────────────────
h = io.open(HS, encoding="utf-8", newline="").read()

if "case HEADER_GC_KEY_AGREEMENT:" not in h:
    anchor = "\t\tcase HEADER_GC_PING:\n"
    assert h.count(anchor) == 1, "Ping-Zweig nicht eindeutig"
    cases = ("\t\t// _IMPROVED_PACKET_ENCRYPTION_ ------------------------------------------\n"
             "\t\tcase HEADER_GC_KEY_AGREEMENT:\n"
             "\t\t\tif (RecvKeyAgreementPacket())\n"
             "\t\t\t\treturn;\n"
             "\t\t\tbreak;\n\n"
             "\t\tcase HEADER_GC_KEY_AGREEMENT_COMPLETED:\n"
             "\t\t\tif (RecvKeyAgreementCompletedPacket())\n"
             "\t\t\t\treturn;\n"
             "\t\t\tbreak;\n\n")
    h = h.replace(anchor, cases + anchor, 1)
    report.append("HandShakePhase: beide Zweige ergaenzt")

if "CPythonNetworkStream::RecvKeyAgreementPacket" not in h:
    anchor = "bool CPythonNetworkStream::RecvHandshakePacket()\n"
    assert h.count(anchor) == 1, "RecvHandshakePacket-Anker nicht eindeutig"
    bodies = '''// _IMPROVED_PACKET_ENCRYPTION_ -------------------------------------------------
// The same exchange the auth channel performs, on the game connection. The
// server opens with its half and will not proceed without ours.
bool CPythonNetworkStream::RecvKeyAgreementPacket()
{
\tTPacketKeyAgreement packet;
\tif (!Recv(std::as_writable_bytes(std::span(&packet, 1))))
\t\treturn false;

\tSPDLOG_DEBUG("GAME KEY_AGREEMENT RECV agreed={} data={}", packet.wAgreedLength, packet.wDataLength);

\tTPacketKeyAgreement packetToSend;
\tsize_t dataLength = TPacketKeyAgreement::MAX_DATA_LEN;
\tsize_t agreedLength = Prepare(packetToSend.data, &dataLength);
\tif (agreedLength == 0)
\t{
\t\tSPDLOG_ERROR("GAME KEY_AGREEMENT: Prepare failed");
\t\tDisconnect();
\t\treturn false;
\t}

\tif (!Activate(packet.wAgreedLength, packet.data, packet.wDataLength))
\t{
\t\tSPDLOG_ERROR("GAME KEY_AGREEMENT: Activate failed");
\t\tDisconnect();
\t\treturn false;
\t}

\tpacketToSend.bHeader = HEADER_CG_KEY_AGREEMENT;
\tpacketToSend.wAgreedLength = (WORD)agreedLength;
\tpacketToSend.wDataLength = (WORD)dataLength;

\tif (!Send(std::as_bytes(std::span(&packetToSend, 1))))
\t{
\t\tSPDLOG_ERROR("GAME KEY_AGREEMENT: send failed");
\t\treturn false;
\t}

\tSPDLOG_DEBUG("GAME KEY_AGREEMENT SEND agreed={} data={}", agreedLength, dataLength);
\treturn true;
}

// Everything after this packet is encrypted, so the switch is flipped exactly
// here -- and ActivateCipher() also takes care of anything the server already
// sent behind it in the same segment.
bool CPythonNetworkStream::RecvKeyAgreementCompletedPacket()
{
\tTPacketKeyAgreementCompleted packet;
\tif (!Recv(std::as_writable_bytes(std::span(&packet, 1))))
\t\treturn false;

\tSPDLOG_DEBUG("GAME KEY_AGREEMENT_COMPLETED");
\tActivateCipher();
\treturn true;
}

'''
    h = h.replace(anchor, bodies + anchor, 1)
    report.append("Spielkanal: beide Handler eingefuegt")

io.open(HS, "w", encoding="utf-8", newline="").write(h)

# ── declarations ─────────────────────────────────────────────────────────────
d = io.open(HD, encoding="utf-8", newline="").read()
if "RecvKeyAgreementPacket" not in d:
    anchor = "\t\tbool RecvHandshakeOKPacket();\n"
    assert d.count(anchor) == 1, "Deklarations-Anker nicht eindeutig"
    d = d.replace(anchor, anchor +
                  "\n\t\t// _IMPROVED_PACKET_ENCRYPTION_\n"
                  "\t\tbool RecvKeyAgreementPacket();\n"
                  "\t\tbool RecvKeyAgreementCompletedPacket();\n", 1)
    io.open(HD, "w", encoding="utf-8", newline="").write(d)
    report.append("Header: beide Methoden deklariert")

print("=== Aenderungen ===")
for r in report:
    print("  " + r)
if not report:
    print("  keine -- schon vorhanden")

print("\n=== Gegenprobe ===")
print("  Tabelleneintraege : %d (erwartet 2)" %
      io.open(NS, encoding="utf-8").read().count("HEADER_GC_KEY_AGREEMENT"))
hh = io.open(HS, encoding="utf-8").read()
print("  Zweige            : %d (erwartet 2)" % hh.count("case HEADER_GC_KEY_AGREEMENT"))
print("  Ruempfe           : %d (erwartet 2)" % hh.count("CPythonNetworkStream::RecvKeyAgreement"))
