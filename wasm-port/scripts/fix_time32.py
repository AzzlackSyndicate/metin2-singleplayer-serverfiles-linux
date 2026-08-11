#!/usr/bin/env python3
"""The two former time_t fields are 4 bytes on this server, not 8.

This tree replaced time_t with int64_t on purpose, and the reasoning in the
source is sound as far as it goes: a wire field's width must not depend on the
build. It picked 8 because MSVC's time_t is 8 by default. But the peer decides
the wire format, and our peer is a 32-bit Linux build whose time_t is 4. Read
straight out of the server binary's debug info, no inference:

    sizeof(TPacketGCTime) = 5      sizeof(TPlayerSkill) = 6

So the width stays fixed -- the argument in the comment is kept -- it is just
fixed at the value this server actually writes. Consequences:

    TPacketGCTime         9 -> 5
    TPlayerSkill         10 -> 6
    TPacketGCSkillLevelNew 2551 -> 1531, which is what the server sends for
                          header 76, so the skill packet stops desyncing the
                          stream a few seconds into the world.

If this client is ever pointed at a server built 64-bit, this is the line to
revisit -- hence the note rather than a silent change.

Idempotent.
"""
import io

P = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/Packet.h"
T = "/opt/m2wasm/src/EngineLib/test/packet_layout_table.h"

s = io.open(P, encoding="utf-8", newline="").read()
report = []

pairs = [
    ("\tstd::int64_t tNextRead;",
     "\tstd::int32_t tNextRead;   // 4 bytes: our server is a 32-bit build (time_t == 4)"),
    ('    std::int64_t time;   // NOT time_t — see TPlayerSkill\'s note',
     '    std::int32_t time;   // 4 bytes: see TPlayerSkill\'s note, and the server binary'),
    ('static_assert(sizeof(TPlayerSkill) == 10, "wire layout — see the int64_t note above");',
     'static_assert(sizeof(TPlayerSkill) == 6, "wire layout — 32-bit server, see the note above");'),
    ('static_assert(sizeof(TPacketGCTime) == 9, "wire layout — see TPlayerSkill\'s int64_t note");',
     'static_assert(sizeof(TPacketGCTime) == 5, "wire layout — 32-bit server, see TPlayerSkill\'s note");'),
]

for old, new in pairs:
    if new in s:
        continue
    if s.count(old) != 1:
        print("  !! nicht eindeutig, uebersprungen: %s" % old.strip()[:60])
        continue
    s = s.replace(old, new, 1)
    report.append(old.strip()[:60])

io.open(P, "w", encoding="utf-8", newline="").write(s)

print("=== Packet.h ===")
for r in report:
    print("  geaendert: " + r)
if not report:
    print("  keine Aenderung -- schon angepasst")

# the layout table asserts the old widths
t = io.open(T, encoding="utf-8", newline="").read()
for name, old_v, new_v in (("TPlayerSkill", 10, 6),
                           ("TPacketGCTime", 9, 5),
                           ("TPacketGCSkillLevelNew", 2551, 1531)):
    import re
    pat = re.compile(r'(M\(\s*' + name + r'\s*,\s*)' + str(old_v) + r'(\s*\))')
    t, n = pat.subn(lambda m: m.group(1) + str(new_v) + m.group(2), t)
    if n:
        print("  Tabelle: %s %d -> %d" % (name, old_v, new_v))
io.open(T, "w", encoding="utf-8", newline="").write(t)
