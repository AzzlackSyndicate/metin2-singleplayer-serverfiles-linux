#!/usr/bin/env python3
"""Close the gap between COMPLETED and the first encrypted packet.

The server sends COMPLETED in clear, flushes, activates its cipher, and then
immediately sends the phase packet -- encrypted. Both can land in one recv().
Everything that recv() delivers is decrypted at the moment it is read, and at
that moment our cipher is still off, so the phase bytes stay encrypted forever.
The client then meets a header it does not know, and __AnalyzePacket answers
"true" for that -- no error, no log, it just waits. Exactly the hang we see.

Fix: when the switch is flipped, decrypt whatever is still sitting behind
COMPLETED in the buffer. Those bytes arrived under the old regime and are the
only ones the recv path could not handle. Costs nothing when the buffer is empty,
which is the case whenever the two packets do arrive separately.

The log line is deliberate: it is the measurement that proves the diagnosis.

Also guards the recv-side Decrypt with recvSize > 0. select() makes a negative
return unlikely, but Decrypt takes a size_t, so -1 would ask it to work through
16 exabytes.

Idempotent.
"""
import io

N = "/opt/m2wasm/src/NetworkLib/src/NetStream.cpp"
s = io.open(N, encoding="utf-8", newline="").read()
report = []

# ── 1. the boundary ──────────────────────────────────────────────────────────
old_activate = """void CNetworkStream::ActivateCipher()
{
\treturn m_cipher.set_activated(true);
}"""

new_activate = """void CNetworkStream::ActivateCipher()
{
\tm_cipher.set_activated(true);

\t// The server sends COMPLETED in clear, flushes, and only then turns its own
\t// cipher on -- so the very next packet it writes is already encrypted and can
\t// share a segment with COMPLETED. Anything still unread behind COMPLETED was
\t// therefore received while our cipher was off and skipped decryption in
\t// __RecvInternalBuffer. It is decrypted here, once, in order: the CTR
\t// keystream must see every byte exactly once or the rest of the session is
\t// noise. Normally this is zero bytes and the whole thing is a no-op.
\tconst int restSize = m_recvBufInputPos - m_recvBufOutputPos;
\tif (restSize > 0)
\t{
\t\tm_cipher.Decrypt(&m_recvBuf[m_recvBufOutputPos], restSize);
\t\tSPDLOG_DEBUG("ActivateCipher: {} bytes were already buffered behind COMPLETED, decrypted now (first header now 0x{:02x})",
\t\t\trestSize, (unsigned char)m_recvBuf[m_recvBufOutputPos]);
\t}
\telse
\t{
\t\tSPDLOG_DEBUG("ActivateCipher: buffer empty behind COMPLETED");
\t}
}"""

# NOT a check for "restSize": __RecvInternalBuffer already has a local by that
# name, so the guard matched code it had nothing to do with and skipped the edit.
if "already buffered behind COMPLETED" not in s:
    assert s.count(old_activate) == 1, "ActivateCipher nicht eindeutig gefunden"
    s = s.replace(old_activate, new_activate, 1)
    report.append("ActivateCipher entschluesselt jetzt den Rest im Puffer")

# ── 2. never hand Decrypt a negative length ──────────────────────────────────
old_recv = """		if (IsSecurityMode())
			m_cipher.Decrypt(&m_recvBuf[m_recvBufInputPos], recvSize);"""
new_recv = """		// recvSize > 0: a spurious readable socket returns -1, and Decrypt takes
		// a size_t.
		if (recvSize > 0 && IsSecurityMode())
			m_cipher.Decrypt(&m_recvBuf[m_recvBufInputPos], recvSize);"""

if "recvSize > 0 && IsSecurityMode()" not in s:
    assert s.count(old_recv) == 1, "Decrypt-Aufruf im Empfangspfad nicht eindeutig"
    s = s.replace(old_recv, new_recv, 1)
    report.append("Decrypt gegen negative Laenge abgesichert")

io.open(N, "w", encoding="utf-8", newline="").write(s)

print("=== Aenderungen ===")
for r in report:
    print("  " + r)
if not report:
    print("  keine -- schon vorhanden")

print("\n=== Gegenprobe am geschriebenen Text ===")
t = io.open(N, encoding="utf-8").read()
print("  Rest-Entschluesselung : %s" % ("ok" if "already buffered behind COMPLETED" in t else "FEHLT"))
print("  Laengenschutz         : %s" % ("ok" if "recvSize > 0 && IsSecurityMode()" in t else "FEHLT"))
