#!/usr/bin/env python3
"""Stop inferring what the bytes are and print them.

Everything checks out on paper: identical cipher, identical DH group, opposite
polarity, and the server logged AUTH_PHASE so its own agreement succeeded. Yet
the first byte decrypts to 0x4a instead of 0xfd. One of my inferences is wrong,
and the cheapest way to find out which is to look at the actual bytes:

  before=[..]  the bytes as they arrived. If they already read fd 01 .. then they
               were never encrypted, and decrypting them is what breaks things.
  after=[..]   if the phase packet shows up shifted (xx fd 01) it is a framing
               offset, not a key problem. Pure noise means the secrets differ.

Also records a fingerprint of the shared secret and which algorithms it picked.
If the secrets do turn out to differ, that fingerprint is what a server-side
counterpart would be compared against, and it costs nothing to carry now.

Idempotent.
"""
import io

NS = "/opt/m2wasm/src/NetworkLib/src/NetStream.cpp"
CC = "/opt/m2wasm/src/NetworkLib/src/cipher.cpp"
CH = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/cipher.h"

report = []

# ── 1. a fingerprint of the shared secret + the picked algorithms ────────────
h = io.open(CH, encoding="utf-8", newline="").read()
if "fingerprint" not in h:
    anchor = "  void set_activated(bool value) { activated_ = value; }"
    assert h.count(anchor) == 1, "set_activated-Anker nicht eindeutig"
    h = h.replace(anchor, anchor + """

  // Diagnostic only: the first bytes of the agreed secret and which block
  // ciphers it selected. Two peers that agree must produce the same string.
  const std::string& fingerprint() const { return fingerprint_; }""", 1)
    anchor2 = "  bool activated_;"
    assert h.count(anchor2) == 1, "activated_-Anker nicht eindeutig"
    h = h.replace(anchor2, anchor2 + "\n  std::string fingerprint_;", 1)
    if "#include <string>" not in h:
        h = h.replace("#include <cassert>", "#include <cassert>\n#include <string>", 1)
    io.open(CH, "w", encoding="utf-8", newline="").write(h)
    report.append("cipher.h: Fingerabdruck deklariert")

c = io.open(CC, encoding="utf-8", newline="").read()
if "fingerprint_ =" not in c:
    anchor = "\tif (polarity) {"
    assert c.count(anchor) == 1, "Polaritaets-Anker nicht eindeutig"
    fp = '''	{
		// Diagnostic: identical on both peers when the agreement really matched.
		char fp[128];
		snprintf(fp, sizeof(fp), "shared=%02x%02x%02x%02x%02x%02x%02x%02x len=%u hint=%d/%d klen=%u/%u",
			shared.BytePtr()[0], shared.BytePtr()[1], shared.BytePtr()[2], shared.BytePtr()[3],
			shared.BytePtr()[4], shared.BytePtr()[5], shared.BytePtr()[6], shared.BytePtr()[7],
			(unsigned)shared.size(), hint_0 % kMaxAlgorithms, hint_1 % kMaxAlgorithms,
			(unsigned)key_length_0, (unsigned)key_length_1);
		fingerprint_ = fp;
	}

'''
    c = c.replace(anchor, fp + anchor, 1)
    io.open(CC, "w", encoding="utf-8", newline="").write(c)
    report.append("cipher.cpp: Fingerabdruck erfasst")

# ── 2. hex dumps around the activation boundary ──────────────────────────────
s = io.open(NS, encoding="utf-8", newline="").read()

if "static std::string HexBytes" not in s:
    anchor = "bool CNetworkStream::__RecvInternalBuffer()\n"
    assert s.count(anchor) == 1, "__RecvInternalBuffer-Anker nicht eindeutig"
    helper = '''// Diagnostic helper: the bytes themselves, because every inference about what
// they are has been wrong so far.
static std::string HexBytes(const char* p, int n)
{
	std::string out;
	char b[8];
	for (int i = 0; i < n && i < 24; ++i)
	{
		snprintf(b, sizeof(b), "%02x ", (unsigned char)p[i]);
		out += b;
	}
	return out;
}

'''
    s = s.replace(anchor, helper + anchor, 1)
    report.append("NetStream.cpp: Hex-Helfer eingefuegt")

old_log = '''		m_cipher.Decrypt(&m_recvBuf[m_recvBufOutputPos], restSize);
		SPDLOG_DEBUG("ActivateCipher: {} bytes were already buffered behind COMPLETED, decrypted now (first header now 0x{:02x})",
			restSize, (unsigned char)m_recvBuf[m_recvBufOutputPos]);'''
new_log = '''		const std::string before = HexBytes(&m_recvBuf[m_recvBufOutputPos], restSize);
		m_cipher.Decrypt(&m_recvBuf[m_recvBufOutputPos], restSize);
		SPDLOG_DEBUG("ActivateCipher: {} bytes behind COMPLETED  before=[{}] after=[{}]  {}",
			restSize, before, HexBytes(&m_recvBuf[m_recvBufOutputPos], restSize),
			m_cipher.fingerprint());'''
if "before=[{}] after=[{}]" not in s:
    assert s.count(old_log) == 1, "Logzeile in ActivateCipher nicht eindeutig"
    s = s.replace(old_log, new_log, 1)
    report.append("ActivateCipher: Bytes davor und danach")

# also: the empty-buffer branch should still report the fingerprint
old_empty = '''		SPDLOG_DEBUG("ActivateCipher: buffer empty behind COMPLETED");'''
new_empty = '''		SPDLOG_DEBUG("ActivateCipher: buffer empty behind COMPLETED  {}", m_cipher.fingerprint());'''
if "buffer empty behind COMPLETED  {}" not in s:
    assert s.count(old_empty) == 1, "leerer Zweig nicht eindeutig"
    s = s.replace(old_empty, new_empty, 1)
    report.append("ActivateCipher: Fingerabdruck auch im leeren Zweig")

# ── 3. every packet that arrives after the switch ────────────────────────────
old_recv = '''		if (recvSize > 0 && IsSecurityMode())
			m_cipher.Decrypt(&m_recvBuf[m_recvBufInputPos], recvSize);'''
new_recv = '''		if (recvSize > 0 && IsSecurityMode())
		{
			m_cipher.Decrypt(&m_recvBuf[m_recvBufInputPos], recvSize);
			SPDLOG_DEBUG("recv {} bytes, decrypted: [{}]",
				recvSize, HexBytes(&m_recvBuf[m_recvBufInputPos], recvSize));
		}'''
if "recv {} bytes, decrypted" not in s:
    assert s.count(old_recv) == 1, "Empfangs-Decrypt nicht eindeutig"
    s = s.replace(old_recv, new_recv, 1)
    report.append("Empfangspfad: jedes Paket nach der Umschaltung")

if "#include <string>" not in s:
    s = "#include <string>\n" + s
    report.append("NetStream.cpp: <string> eingebunden")

io.open(NS, "w", encoding="utf-8", newline="").write(s)

print("=== Aenderungen ===")
for r in report:
    print("  " + r)
if not report:
    print("  keine -- schon vorhanden")
