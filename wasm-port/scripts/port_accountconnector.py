#!/usr/bin/env python3
"""Teach the auth state machine to answer the key agreement.

The two handlers are the stock client's (UserInterface/AccountConnector.cpp),
rewritten for this tree's span-based Recv/Send instead of the old (size, ptr)
pair. The dispatch entries go in both places the stock client has them: the
handshake state and the auth state, because the server can send the agreement
in either.

Idempotent.
"""
import io, sys

H = "/opt/m2wasm/src/PyLib/src/bindings/net/AccountConnector.h"
C = "/opt/m2wasm/src/PyLib/src/bindings/net/AccountConnector.cpp"

h = io.open(H, encoding="utf-8", newline="").read()
c = io.open(C, encoding="utf-8", newline="").read()

print("=== Voraussetzung: erbt CAccountConnector von CNetworkStream? ===")
import re
m = re.search(r"class\s+CAccountConnector\s*:([^\{]*)", h)
print("  " + (m.group(1).strip() if m else "Klassenkopf nicht gefunden"))

report = []

# ── declarations ─────────────────────────────────────────────────────────────
if "__AuthState_RecvKeyAgreement" not in h:
    anchor = "\t\tbool __AuthState_RecvPing();\n"
    assert h.count(anchor) == 1, "Ping-Anker nicht eindeutig"
    h = h.replace(anchor, anchor +
                  "\n\t\t// _IMPROVED_PACKET_ENCRYPTION_ (ported from the stock client)\n"
                  "\t\tbool __AuthState_RecvKeyAgreement();\n"
                  "\t\tbool __AuthState_RecvKeyAgreementCompleted();\n", 1)
    report.append("Header: beide Handler deklariert")
    io.open(H, "w", encoding="utf-8", newline="").write(h)

# ── bodies ───────────────────────────────────────────────────────────────────
if "CAccountConnector::__AuthState_RecvKeyAgreement" not in c:
    anchor = "bool CAccountConnector::__AuthState_RecvPhase()\n"
    assert c.count(anchor) == 1, "RecvPhase-Anker nicht eindeutig"
    bodies = '''// _IMPROVED_PACKET_ENCRYPTION_ -------------------------------------------------
// A stock r40250 answers the handshake with 0xfb and waits for the client's half
// of a Diffie-Hellman exchange. This client had the whole feature removed, so it
// discarded that packet and waited for a phase change that had already passed --
// which looks, from the login screen, like a server that never answers.
//
// Ported from the stock 2014 client, whose exchange this server was built
// against. Recv/Send are this tree's span form; everything else is unchanged.
bool CAccountConnector::__AuthState_RecvKeyAgreement()
{
\tTPacketKeyAgreement packet;
\tif (!Recv(std::as_writable_bytes(std::span(&packet, 1))))
\t\treturn false;

\tSPDLOG_DEBUG("KEY_AGREEMENT RECV agreed={} data={}", packet.wAgreedLength, packet.wDataLength);

\tTPacketKeyAgreement packetToSend;
\tsize_t dataLength = TPacketKeyAgreement::MAX_DATA_LEN;
\tsize_t agreedLength = Prepare(packetToSend.data, &dataLength);
\tif (agreedLength == 0)
\t{
\t\tSPDLOG_ERROR("KEY_AGREEMENT: Prepare failed");
\t\tDisconnect();
\t\treturn false;
\t}

\tif (!Activate(packet.wAgreedLength, packet.data, packet.wDataLength))
\t{
\t\tSPDLOG_ERROR("KEY_AGREEMENT: Activate failed");
\t\tDisconnect();
\t\treturn false;
\t}

\tpacketToSend.bHeader = HEADER_CG_KEY_AGREEMENT;
\tpacketToSend.wAgreedLength = (WORD)agreedLength;
\tpacketToSend.wDataLength = (WORD)dataLength;

\tif (!Send(std::as_bytes(std::span(&packetToSend, 1))))
\t{
\t\tSPDLOG_ERROR("KEY_AGREEMENT: send failed");
\t\treturn false;
\t}

\tSPDLOG_DEBUG("KEY_AGREEMENT SEND agreed={} data={}", agreedLength, dataLength);
\treturn true;
}

// The server says "from here on, everything is encrypted". Nothing is decrypted
// before this point and nothing after it is not, so the switch has to be flipped
// exactly here.
bool CAccountConnector::__AuthState_RecvKeyAgreementCompleted()
{
\tTPacketKeyAgreementCompleted packet;
\tif (!Recv(std::as_writable_bytes(std::span(&packet, 1))))
\t\treturn false;

\tSPDLOG_DEBUG("KEY_AGREEMENT_COMPLETED");
\tActivateCipher();
\treturn true;
}

'''
    c = c.replace(anchor, bodies + anchor, 1)
    report.append("cpp: beide Handler eingefuegt")

# ── dispatch, in both states the stock client wires them into ────────────────
dispatch = (
    "\tif (!__AnalyzePacket(HEADER_GC_KEY_AGREEMENT, sizeof(TPacketKeyAgreement), "
    "&CAccountConnector::__AuthState_RecvKeyAgreement))\n\t\treturn false;\n\n"
    "\tif (!__AnalyzePacket(HEADER_GC_KEY_AGREEMENT_COMPLETED, sizeof(TPacketKeyAgreementCompleted), "
    "&CAccountConnector::__AuthState_RecvKeyAgreementCompleted))\n\t\treturn false;\n\n"
)

if c.count("HEADER_GC_KEY_AGREEMENT,") == 0:
    for state, anchor in (
        ("Handshake-Zustand",
         "\tif (!__AnalyzePacket(HEADER_GC_HANDSHAKE, sizeof(TPacketGCHandshake), "
         "&CAccountConnector::__AuthState_RecvHandshake))\n\t\treturn false;\n"),
        ("Auth-Zustand",
         "\tif (!__AnalyzePacket(HEADER_GC_PHASE, sizeof(TPacketGCPhase), "
         "&CAccountConnector::__AuthState_RecvPhase))\n\t\treturn false;\n\n"
         "\tif (!__AnalyzePacket(HEADER_GC_PING, sizeof(TPacketGCPing), "
         "&CAccountConnector::__AuthState_RecvPing))\n\t\treturn false;\n"),
    ):
        if anchor in c:
            c = c.replace(anchor, anchor + "\n" + dispatch, 1)
            report.append("cpp: Verteilung im %s" % state)
        else:
            report.append("cpp: Anker fuer %s NICHT gefunden" % state)
else:
    report.append("cpp: Verteilung schon vorhanden")

io.open(C, "w", encoding="utf-8", newline="").write(c)

print("\n=== Aenderungen ===")
for r in report:
    print("  " + r)

print("\n=== Gegenprobe ===")
c = io.open(C, encoding="utf-8").read()
h = io.open(H, encoding="utf-8").read()
print("  Deklarationen        : %s" % ("ok" if "__AuthState_RecvKeyAgreementCompleted();" in h else "FEHLT"))
print("  Ruempfe              : %s" % ("ok" if "CAccountConnector::__AuthState_RecvKeyAgreement()" in c else "FEHLT"))
print("  Verteilungseintraege : %d (erwartet 4)" % c.count("HEADER_GC_KEY_AGREEMENT"))
