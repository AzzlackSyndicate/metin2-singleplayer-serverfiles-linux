#!/usr/bin/env python3
"""Give CNetworkStream the cipher, ported from the stock 2014 client.

Five edits, all of them the stock client's own (EterLib/NetStream.{h,cpp}):

    header  : include cipher.h, three public methods, one member
    cpp     : the three bodies, IsSecurityMode(), decrypt after recv,
              encrypt before send, CleanUp on Clear

The buffers are std::vector<char> in this tree where the stock client had raw
pointers, so `m_recvBuf + pos` becomes `&m_recvBuf[pos]`. Nothing else differs.

Idempotent.
"""
import io, re, sys

H = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/NetStream.h"
C = "/opt/m2wasm/src/NetworkLib/src/NetStream.cpp"

# ── what is already there? ───────────────────────────────────────────────────
h = io.open(H, encoding="utf-8", newline="").read()
c = io.open(C, encoding="utf-8", newline="").read()

print("=== Ausgangslage ===")
print("  IsSecurityMode deklariert:", "IsSecurityMode" in h)
m = re.search(r".*IsSecurityMode.*", c)
print("  im cpp:", m.group(0).strip()[:70] if m else "nicht vorhanden")

report = []

# ── header ───────────────────────────────────────────────────────────────────
if "cipher.h" not in h:
    anchor = '#include "EterBase/tea.h"\n'
    assert h.count(anchor) == 1, "tea.h-Anker nicht eindeutig"
    h = h.replace(anchor, '#include "NetworkLib/cipher.h"\n' + anchor, 1)
    report.append("Header: cipher.h eingebunden")

if "ActivateCipher" not in h:
    anchor = "\t\tvoid Clear();\n"
    assert h.count(anchor) == 1, "Clear()-Anker nicht eindeutig"
    decl = (
        "\n"
        "\t\t// _IMPROVED_PACKET_ENCRYPTION_ -- the Diffie-Hellman exchange the\n"
        "\t\t// server opens the auth phase with. Ported from the stock client;\n"
        "\t\t// without it a stock r40250 sends 0xfb and waits forever.\n"
        "\t\tbool IsSecurityMode();\n"
        "\t\tsize_t Prepare(void* buffer, size_t* length);\n"
        "\t\tbool Activate(size_t agreed_length, const void* buffer, size_t length);\n"
        "\t\tvoid ActivateCipher();\n"
        "\n"
    )
    h = h.replace(anchor, anchor + decl, 1)
    report.append("Header: die vier Methoden deklariert")

if "m_cipher" not in h:
    anchor = "\t\tbool\tm_isOnline = false;\n"
    assert h.count(anchor) == 1, "m_isOnline-Anker nicht eindeutig"
    h = h.replace(anchor, anchor + "\n\t\tCipher\tm_cipher;\n", 1)
    report.append("Header: Cipher-Mitglied eingefuegt")

io.open(H, "w", encoding="utf-8", newline="").write(h)

# ── implementation ───────────────────────────────────────────────────────────
if "CNetworkStream::ActivateCipher" not in c:
    anchor = "void CNetworkStream::Disconnect()\n"
    assert c.count(anchor) == 1, "Disconnect-Anker nicht eindeutig"
    bodies = (
        "// _IMPROVED_PACKET_ENCRYPTION_ ---------------------------------------------\n"
        "// Verbatim from the stock client (EterLib/NetStream.cpp). Activate() passes\n"
        "// polarity=true: the client is the second party to the agreement, and the two\n"
        "// sides must not pick the same one or the derived keys do not match.\n"
        "bool CNetworkStream::IsSecurityMode()\n"
        "{\n"
        "\treturn m_cipher.activated();\n"
        "}\n"
        "\n"
        "size_t CNetworkStream::Prepare(void* buffer, size_t* length)\n"
        "{\n"
        "\treturn m_cipher.Prepare(buffer, length);\n"
        "}\n"
        "\n"
        "bool CNetworkStream::Activate(size_t agreed_length, const void* buffer, size_t length)\n"
        "{\n"
        "\treturn m_cipher.Activate(true, agreed_length, buffer, length);\n"
        "}\n"
        "\n"
        "void CNetworkStream::ActivateCipher()\n"
        "{\n"
        "\treturn m_cipher.set_activated(true);\n"
        "}\n"
        "\n"
    )
    c = c.replace(anchor, bodies + anchor, 1)
    report.append("cpp: die vier Methodenruempfe eingefuegt")

# decrypt what just arrived
if "m_cipher.Decrypt" not in c:
    anchor = ("\t\tm_recvBufInputPos += recvSize;\n")
    assert c.count(anchor) == 1, "recv-Anker nicht eindeutig"
    dec = ("\n\t\tif (IsSecurityMode())\n"
           "\t\t\tm_cipher.Decrypt(&m_recvBuf[m_recvBufInputPos], recvSize);\n\n")
    c = c.replace(anchor, dec + anchor, 1)
    report.append("cpp: Entschluesselung nach recv")

# encrypt what is about to leave
if "m_cipher.Encrypt" not in c:
    anchor = "\tint sendSize = send(m_sock, &m_sendBuf[m_sendBufOutputPos], dataSize, 0);\n"
    assert c.count(anchor) == 1, "send-Anker nicht eindeutig"
    enc = ("\tif (IsSecurityMode())\n"
           "\t\tm_cipher.Encrypt(&m_sendBuf[m_sendBufOutputPos], dataSize);\n\n")
    c = c.replace(anchor, enc + anchor, 1)
    report.append("cpp: Verschluesselung vor send")

# forget the keys when the stream is reset
if "m_cipher.CleanUp" not in c:
    anchor = "void CNetworkStream::Clear()\n{\n"
    assert c.count(anchor) == 1, "Clear-Anker nicht eindeutig"
    c = c.replace(anchor, anchor + "\tm_cipher.CleanUp();\n", 1)
    report.append("cpp: CleanUp in Clear()")

io.open(C, "w", encoding="utf-8", newline="").write(c)

print("\n=== Aenderungen ===")
for r in report:
    print("  " + r)
if not report:
    print("  keine -- alles schon vorhanden")

print("\n=== Gegenprobe ===")
h = io.open(H, encoding="utf-8").read()
c = io.open(C, encoding="utf-8").read()
for what, txt, blob in (("cipher.h eingebunden", "cipher.h", h),
                        ("Cipher-Mitglied", "m_cipher;", h),
                        ("Prepare deklariert", "size_t Prepare", h),
                        ("Rumpf ActivateCipher", "CNetworkStream::ActivateCipher", c),
                        ("Decrypt eingehaengt", "m_cipher.Decrypt", c),
                        ("Encrypt eingehaengt", "m_cipher.Encrypt", c),
                        ("CleanUp", "m_cipher.CleanUp", c)):
    print("  %-24s %s" % (what, "ok" if txt in blob else "FEHLT"))
