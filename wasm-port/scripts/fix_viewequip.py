#!/usr/bin/env python3
"""HEADER_GC_VIEW_EQUIP (99) carries 32 equipment slots, not 11.

Found by headermatch.py once it learned to read `Packet(&p, sizeof(p))`, which
is how this send site is written (char.cpp:6321, CHARACTER::SendEquipment):

    server  sizeof(TPacketViewEquip)   = 1221 = 5 + 32 * 38
    client  sizeof(TPacketGCViewEquip) =  423 = 5 + 11 * 38

Both sides call the array WEAR_MAX_NUM, and both sides are right about their own
side: this serverfile raised WEAR_MAX_NUM to 32 (common/length.h:65) while the
client's wearable-slot enum still ends at 11 (GameLib/ItemData.h). The name
matched, so a name-based diff saw nothing; only the header number tells the
truth. The stream is STATIC-length for header 99, so the client would read 423
bytes and leave 798 bytes of equipment in the buffer -- every later packet then
frames against garbage. The original Windows client has the same 11 and would
desync just as badly; it simply never met a 32-slot server.

The fix keeps the two meanings apart instead of conflating them:

  * VIEW_EQUIP_SLOT_MAX_NUM = 32 -- how many slots are ON THE WIRE, decided by
    the server, used by the packet struct and by nothing else.
  * WEAR_MAX_NUM = 11 -- how many slots this client's equipment window HAS.
    The receive loop keeps using it, so the UI is unchanged; the trailing 21
    slots are read off the wire and ignored, which is what a client that does
    not display them should do.

Idempotent.
"""
import io, re

P = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/Packet.h"
T = "/opt/m2wasm/src/EngineLib/test/packet_layout_table.h"
R = "/opt/m2wasm/src/PyLib/src/bindings/net/PythonNetworkStreamPhaseGame.cpp"


def patch(path, pairs):
    s = io.open(path, encoding="utf-8", newline="").read()
    done = []
    for old, new in pairs:
        if new in s:
            continue
        if s.count(old) != 1:
            print("  !! nicht eindeutig (%dx), uebersprungen: %s"
                  % (s.count(old), old.strip()[:60]))
            continue
        s = s.replace(old, new, 1)
        done.append(old.strip()[:66])
    io.open(path, "w", encoding="utf-8", newline="").write(s)
    print("=== %s ===" % path.rsplit("/", 1)[-1])
    for d in done:
        print("  geaendert: " + d)
    if not done:
        print("  keine Aenderung -- schon angepasst")


patch(P, [
    ("\tWEAR_MAX_NUM = 11,",
     "\tWEAR_MAX_NUM = 11,\t\t\t// slots this client's equipment window has\n"
     "\t// Slots the SERVER puts on the wire in HEADER_GC_VIEW_EQUIP. This\n"
     "\t// serverfile's WEAR_MAX_NUM is 32 (common/length.h), so the packet is\n"
     "\t// 1221 bytes, not 423 -- see fix_viewequip.py.\n"
     "\tVIEW_EQUIP_SLOT_MAX_NUM = 32,"),
    ("\tTEquipmentItemSet equips[WEAR_MAX_NUM];",
     "\tTEquipmentItemSet equips[VIEW_EQUIP_SLOT_MAX_NUM];   // wire width, not window width"),
])

patch(R, [
    ("\tfor (int i = 0; i < WEAR_MAX_NUM; ++i)\n"
     "\t{\n"
     "\t\tTEquipmentItemSet & rItemSet = kViewEquipPacket.equips[i];",
     "\t// The wire carries VIEW_EQUIP_SLOT_MAX_NUM slots; this window shows the\n"
     "\t// first WEAR_MAX_NUM of them. The rest are read and dropped on purpose.\n"
     "\tfor (int i = 0; i < WEAR_MAX_NUM; ++i)\n"
     "\t{\n"
     "\t\tTEquipmentItemSet & rItemSet = kViewEquipPacket.equips[i];"),
])

# the layout table asserts the old width
t = io.open(T, encoding="utf-8", newline="").read()
t2, n = re.subn(r'(M\(\s*TPacketGCViewEquip\s*,\s*)423(\s*\))', r'\g<1>1221\2', t)
io.open(T, "w", encoding="utf-8", newline="").write(t2)
print("=== packet_layout_table.h ===")
print("  Tabelle: TPacketGCViewEquip 423 -> 1221" if n
      else "  keine Aenderung -- schon angepasst")
