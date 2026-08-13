#!/usr/bin/env python3
"""Experience Rings and Thief's Gloves stack, and wearing one out of the pile
takes one out of the pile.

WHAT WAS ASKED FOR: "also, if possible, make the experience ring's and thief's
gloves stackable as well", with the condition "make sure, only those experience
rings are stackable, that havent been used yet".

WHY THIS IS A SEPARATE SCRIPT FROM free_metin_drop_items.py. That one opens the
whole bonus drop group up for dropping and trading and takes ANTI_STACK off the
two scrolls, and every one of those is a text edit that a restart carries to the
players. This one cannot be: it needs a change to the game core, which means the
core has to be recompiled. Keeping them apart keeps the cheap half cheap, and it
keeps the thing that can be reverted with one line separate from the thing that
cannot.

    ORDER: files/fixes/apply.sh, then free_metin_drop_items.py, then this. The
    ANTI_FLAG values anchored on below are the ones free_metin_drop_items.py
    leaves behind (`ANTI_SELL | ANTI_STACK'), not the ones the archive ships,
    and the anchor enforces that -- run out of order and this stops with a
    sentence saying which script has not run yet.

THE PROBLEM, WHICH IS NOT THE MERGE PATH. Both rows already sit one flag away
from stacking, and the condition about unused rings is already answered by the
core for free. All three merge sites compare every socket and one difference
vetoes the merge (char_item.cpp:5631, char_item.cpp:5847, safebox.cpp:194);
ITEM_SOCKET_MAX_NUM is 3 (common/item_length.h:11) and the unique timer lives in
the last two of them (ITEM_SOCKET_UNIQUE_SAVE_TIME is socket 1,
ITEM_SOCKET_UNIQUE_REMAIN_TIME is socket 2, item_length.h:38-39). A ring is
stamped with its whole life at creation -- item_manager.cpp:227 writes VALUE0,
which is 60 for the ring and 30 for the gloves, into socket 2 -- and the
countdown runs only while it is worn: StartUniqueExpireEvent is reached through
EquipTo (item.cpp:898) and unique_expire_event decrements socket 2 once a minute
(item.cpp:1335), and taking the item off parks the leftover seconds of the
current minute in socket 1 (item.cpp:1547). An unused ring therefore reads
(0, 0, 60) and a ring that has been worn reads something else, so the two never
merge. That is the asked-for behaviour, and no patch here produces it.

WHAT STOPS IT IS THE EQUIP PATH. These two rows are ITEM_UNIQUE. Their ITEM_WEAR
column says WEAR_SHIELD, which is a trap: ProtoReader.cpp:301 turns a wear name
into a bit by its position in a list, and "WEAR_SHIELD" is at index 7 there,
which is 1 << 7 = WEARABLE_UNIQUE in common/item_length.h:319 -- not
WEARABLE_SHIELD, which is 1 << 8. So CItem::FindEquipCell (item.cpp:537-541)
sends both of them to WEAR_UNIQUE1 or WEAR_UNIQUE2, where they run on the timer
above.

CHARACTER::EquipItem (char_item.cpp:6100-6190) and CItem::EquipTo
(item.cpp:816-852) never read GetCount, never split and have no notion of a
partial stack -- EquipTo sets m_wCell and hands the object to SetWear. And when
the timer runs out, unique_expire_event calls RemoveItem on that object
(item.cpp:1327-1332), which destroys it whatever count it is carrying. A stack of
five worn as one object would therefore grant sixty minutes once and be destroyed
with all five inside it. Four rings gone -- and not as an edge case, because
fresh rings are exactly the ones that merge, so stack-then-wear would be the
normal sequence.

So the flag and the split have to change together, and this script refuses to do
one without the other: the core is patched first, and if its anchor does not
match, the item table is not touched at all. That is the same interlock
stack_skill_books.py uses, for the same reason.

WHAT THE CORE PATCH DOES. One block in CHARACTER::EquipItem: if the item is
ITEM_UNIQUE and its count is above one, one is split off and the rest stays in
the bag. It mirrors the split this file already performs when a player drags part
of a stack somewhere (char_item.cpp:5675-5685): SetCount on the original,
ITEM_MANAGER::CreateItem for the remainder, FN_copy_item_socket to carry the
sockets across, AddToCharacter to place it, and an ITEM_SPLIT line in the item
log so the split is visible to whoever reads them.

    ONLY ITEM_UNIQUE, deliberately. Stackable-and-worn is a legitimate
    combination elsewhere and splitting it would break it: a quiver is stackable
    and is MEANT to occupy WEAR_ARROW as a stack of two hundred, and
    char_battle.cpp:2854 refills it by calling EquipItem on one. Splitting every
    stackable item on its way into a slot would leave the archer wearing a single
    arrow. Of the ten types that can be equipped at all -- ITEM_COSTUME,
    ITEM_ARMOR, ITEM_WEAPON, ITEM_ROD, ITEM_PICK, ITEM_UNIQUE, ITEM_DS,
    ITEM_SPECIAL_DS, ITEM_RING and ITEM_BELT, CItem::IsEquipable at
    item.cpp:795-812 -- this block touches exactly one. Arrows are ITEM_WEAPON
    and are not affected; the dragon souls have their own branch further down
    and never reach this code with a count above one.

WHERE THE BLOCK SITS, AND WHY THERE. Directly after the last thing that can
refuse the equip -- the 1.5-second guard after an attack or a skill -- and
directly before the first thing that changes state. Everything above it returns
false without having touched the item: IsExchanging, IsEquipable, CanEquipNow
(which is where the level, sex, anti-flag and "already wearing one of these"
checks live, char_item.cpp:7462-7557), FindEquipCell, the riding, polymorph and
sex guards. So a refused equip can never leave a split stack behind. Below it,
the only failures are EquipTo or SwapItem returning false, and in that case the
player is left with a one-count item and the remainder side by side in the bag --
two fresh rings with identical sockets, which merge back together on the next
drag. Nothing is lost either way.

The order inside the block is the other half of that. The remainder is created
and placed on the character FIRST, and `item' is decremented LAST, so every
failure happens while the original still holds the whole count. The reverse order
-- decrement, then try to place -- has a window in which the count is gone and
the remainder does not exist yet.

WHEN THE BAG IS FULL. The split needs a cell to put the remainder in, and if
there is none the equip is refused rather than performed. The alternative would
be to equip anyway and drop the rest, which is the exact item loss this patch
exists to prevent. The refusal reports itself the way this file already reports a
full inventory everywhere else -- GetEmptyInventory returning -1, a sys_log line,
and ChatPacket(CHAT_TYPE_INFO, LC_TEXT(...)) with the message the pick-up path
uses (char_item.cpp:5893), "You have too many items in your inventory." in
locale/english/locale_string.txt. That literal is not typed out here: it is cp949
and this script would have to carry those bytes. It is lifted out of the file
being patched instead, so it stays byte-identical to the one already there and
keeps whatever translation the server is running.

    This is one cell stricter than it strictly needs to be -- equipping frees the
    stack's own cell a moment later, so in principle the remainder could go
    there. Doing it that way means splitting after a successful EquipTo, and then
    a failure between the two is unrecoverable. One free cell in exchange for no
    failure window is the right trade, and a player carrying a stack of rings in
    a completely full bag is a rare position to be in.

WHAT STILL REACHES EquipTo WITHOUT PASSING THIS BLOCK. Three doors, all checked:

  * CHARACTER::SwapItem (char_item.cpp:6048) calls EquipTo directly, but its only
    caller in the whole source is EquipItem itself (char_item.cpp:6178), so it is
    downstream of this block and the item reaching it is already down to one.
  * input_db.cpp:1568 equips a character's worn items again at login, straight
    through EquipTo. It cannot CREATE a stacked equip -- it only restores what was
    already in a wear cell -- and after this patch nothing can put a stack there
    in the first place. Left alone deliberately: a login is the wrong moment to
    be creating items.
  * cmd_gm.cpp:4132 and the twenty-odd lines under it are the GM outfit
    commands. They build fresh count-1 items and dress a game master in them, so
    there is no stack to split.

Nothing else calls EquipTo, and nothing at all calls CHARACTER::SetWear except
EquipTo and CItem::Unequip (item.cpp:960), so a wear cell has exactly one door.

WHAT THIS MEANS AT THE TABLE. 70005 and 72005 gain ITEM_STACKABLE in FLAG --
first in the list, which is how all 937 stackable rows in this table write it --
and lose ANTI_STACK from ANTI_FLAG. ANTI_SELL stays, as free_metin_drop_items.py
left it. Nothing else on either row changes; VALUE0, which is the life of the
item in minutes, is not touched.

AND AFTERWARDS: two fresh rings merge, because they agree in all three sockets.
A ring that has been worn and taken off does not merge back into the fresh pile,
because socket 2 or socket 1 has moved. Wearing one out of a stack of five leaves
four fresh ones in the bag, because FN_copy_item_socket gives the remainder the
sockets the stack had. And a second ring cannot be taken out of the pile onto the
other unique slot while the first is on: CanEquipNow refuses it at
char_item.cpp:7540 through IsSameSpecialGroup, which returns true for two items
of the same vnum (item.cpp:2129) -- stock behaviour, unchanged here.

REVERTING means taking ITEM_STACKABLE back off the two rows, putting ANTI_STACK
back into their ANTI_FLAG, and deleting the block from EquipItem. Doing only the
first two is safe. Doing only the last one is not, which is why the interlock
runs the core first.

The core has to be RECOMPILED and the image rebuilt for the split to exist; the
item table is re-read from this file at boot, so a restart is enough for the
flags. Do not apply the flags without the core.

Idempotent, in both halves independently. A second run reports `already patched'.

    M2SRC=<context> M2SHARE=<context>/serverfiles/share \\
        python3 split_unique_on_equip.py
"""
import io
import os
import sys

SRC = os.environ.get("M2SRC", "")
SHARE = os.environ.get("M2SHARE", "")

CHAR_ITEM = os.path.join(SRC, "server", "game", "src", "char_item.cpp")
PROTO = os.path.join(SHARE, "conf", "item_proto.txt")

# ---------------------------------------------------------------------------
# The core half.

# The last guard in CHARACTER::EquipItem that can refuse the equip. Chosen
# because it is pure ASCII: almost every line around it carries a cp949 Korean
# comment or chat string, and none of those round-trip through an encoding
# cleanly enough to anchor on.
LAST_GUARD = ("&& (dwCurTime - GetLastAttackTime() <= 1500 || "
              "dwCurTime - m_dwLastSkillTime <= 1500))")

# The close of that guard's body. The block goes in directly after it, which is
# after everything that can say no and before anything that changes state.
GUARD_END = "\n\t\treturn false;\n\t}\n"

# How far past LAST_GUARD the close of the body may be. The body is three lines;
# anything much longer means the function is not shaped the way this was written
# against and the insertion point cannot be trusted.
GUARD_SPAN = 400

# The one place in this file that reports a full inventory when a split has
# nowhere to go. The message beside it is lifted out and reused rather than
# retyped, so it stays byte-identical to the one players already get and keeps
# whatever translation locale_string.txt holds for it.
FULL_LOG = ('sys_log(0, "No empty inventory pid %u size %ud itemid %u", '
            'GetPlayerID(), item->GetSize(), item->GetID());')
FULL_HEAD = 'LC_TEXT("'
FULL_TAIL = '"));'

# The marker that says this file has already been through here. A phrase from
# the comment rather than a line of code: every statement in the block below
# appears elsewhere in this file for other purposes.
MARKER = "One out of the pile, not the pile into the slot"


def block(message):
    """The C++ that goes into EquipItem, with the full-inventory message spliced
    into it."""
    return (
        "\n"
        "\t// One out of the pile, not the pile into the slot.\n"
        "\t//\n"
        "\t// A unique item is worn in WEAR_UNIQUE1 or WEAR_UNIQUE2 and runs on\n"
        "\t// a timer, and unique_expire_event (item.cpp) destroys the item\n"
        "\t// OBJECT when that timer reaches zero -- whatever count the object\n"
        "\t// happens to be carrying. EquipTo() below moves the whole object\n"
        "\t// into the slot and has no notion of a partial stack, so a stack of\n"
        "\t// five Experience Rings worn as one object would grant sixty\n"
        "\t// minutes once and then be destroyed with all five inside it.\n"
        "\t//\n"
        "\t// ITEM_UNIQUE only, deliberately. A quiver is stackable and is MEANT\n"
        "\t// to be worn as a stack of two hundred -- char_battle.cpp refills\n"
        "\t// WEAR_ARROW by calling EquipItem() on one -- so splitting every\n"
        "\t// stackable item on its way into a slot would leave the archer\n"
        "\t// wearing a single arrow.\n"
        "\t//\n"
        "\t// Everything that can refuse this equip has already refused it\n"
        "\t// above, so a refusal cannot leave a split stack behind. The\n"
        "\t// remainder is created and handed to the character FIRST and `item'\n"
        "\t// is decremented LAST, so every failure inside here happens while\n"
        "\t// the original still holds the whole count and nothing is lost.\n"
        "\t// FN_copy_item_socket carries the sockets across, which is what\n"
        "\t// keeps the remainder merging with other unused ones: the unique\n"
        "\t// timer lives in sockets 1 and 2, and the merge paths compare them.\n"
        "\tif (ITEM_UNIQUE == item->GetType() && item->GetCount() > 1)\n"
        "\t{\n"
        "\t\tint iRestCell = GetEmptyInventory(item->GetSize());\n"
        "\n"
        "\t\tif (iRestCell < 0)\n"
        "\t\t{\n"
        "\t\t\t// No cell for the rest of the pile. Refuse the equip rather\n"
        "\t\t\t// than wear the pile, which is the loss this block prevents.\n"
        "\t\t\tsys_log(0, \"No empty inventory to split a unique stack pid %u "
        "vnum %u count %u\", GetPlayerID(), item->GetVnum(), item->GetCount());\n"
        "\t\t\tChatPacket(CHAT_TYPE_INFO, LC_TEXT(\"" + message + "\"));\n"
        "\t\t\treturn false;\n"
        "\t\t}\n"
        "\n"
        "\t\tLPITEM pkRest = ITEM_MANAGER::instance().CreateItem(item->GetVnum(), "
        "item->GetCount() - 1);\n"
        "\n"
        "\t\tif (!pkRest)\n"
        "\t\t{\n"
        "\t\t\tsys_err(\"EquipItem: cannot create the remainder of a stack of "
        "%s\", item->GetName());\n"
        "\t\t\treturn false;\n"
        "\t\t}\n"
        "\n"
        "\t\tFN_copy_item_socket(pkRest, item);\n"
        "\n"
        "\t\tif (!pkRest->AddToCharacter(this, TItemPos(INVENTORY, iRestCell)))\n"
        "\t\t{\n"
        "\t\t\t// `item' has not been touched yet, so destroying the remainder\n"
        "\t\t\t// puts everything back exactly as it was.\n"
        "\t\t\tM2_DESTROY_ITEM(pkRest);\n"
        "\t\t\treturn false;\n"
        "\t\t}\n"
        "\n"
        "\t\titem->SetCount(1);\n"
        "\n"
        "\t\tchar szSplitBuf[51 + 1];\n"
        "\t\tsnprintf(szSplitBuf, sizeof(szSplitBuf), \"%u %u %u %u \", "
        "pkRest->GetID(), pkRest->GetCount(), item->GetCount(), "
        "item->GetCount() + pkRest->GetCount());\n"
        "\t\tLogManager::instance().ItemLog(this, item, \"ITEM_SPLIT\", "
        "szSplitBuf);\n"
        "\t}\n"
    )


# ---------------------------------------------------------------------------
# The item table half.

COL_ANTI_FLAG = "ANTI_FLAG"
COL_FLAG = "FLAG"
COL_SIZE = "SIZE"
COL_TYPE = "ITEM_TYPE"

UNIQUE = b"ITEM_UNIQUE"

# (vnum, label, column, before, after)
#
# The ANTI_FLAG values are what free_metin_drop_items.py leaves behind. The FLAG
# values put ITEM_STACKABLE first, which is how every one of the 937 stackable
# rows in the shipped table writes it -- the reader splits the column on `|' and
# looks each name up, so the order is cosmetic, but matching the table matters
# to anyone reading it.
EDITS = [
    ("70005", "Experience Ring", COL_FLAG,
     "CONFIRM_WHEN_USE", "ITEM_STACKABLE | CONFIRM_WHEN_USE"),
    ("70005", "Experience Ring", COL_ANTI_FLAG,
     "ANTI_SELL | ANTI_STACK", "ANTI_SELL"),
    ("72005", "Thief's Gloves", COL_FLAG,
     "LOG", "ITEM_STACKABLE | LOG"),
    ("72005", "Thief's Gloves", COL_ANTI_FLAG,
     "ANTI_SELL | ANTI_STACK", "ANTI_SELL"),
]

# What the ANTI_FLAG column holds before free_metin_drop_items.py has run, and
# before files/fixes has run. Recognised only so that running the three scripts
# out of order produces a sentence instead of a puzzle.
OUT_OF_ORDER = {
    "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_STACK | ANTI_MYSHOP":
        "free_metin_drop_items.py has not run against this tree yet. It has to "
        "run first -- it is what opens these rows up for dropping and trading, "
        "and its own anchors are the values the archive ships.",
}


def patch_core():
    """Returns True if it wrote, False if it was already patched. Exits on doubt."""
    if not SRC or not os.path.isfile(CHAR_ITEM):
        sys.exit("not found: %s (set M2SRC to the staged game/src tree)"
                 % CHAR_ITEM)

    s = io.open(CHAR_ITEM, encoding="utf-8", errors="surrogateescape").read()

    if MARKER in s:
        print("   already patched: server/game/src/char_item.cpp")
        return False

    # The insertion point.
    if s.count(LAST_GUARD) != 1:
        sys.exit("the 1.5-second guard at the end of CHARACTER::EquipItem "
                 "appears %d times in char_item.cpp (expected exactly 1) -- "
                 "refusing to guess where the split belongs"
                 % s.count(LAST_GUARD))

    head = s.index(LAST_GUARD)
    end = s.find(GUARD_END, head)
    if end < 0 or end - head > GUARD_SPAN:
        sys.exit("the body of the last guard in CHARACTER::EquipItem is not "
                 "shaped as expected (its closing brace is %s) -- the split "
                 "would land somewhere unknown"
                 % ("missing" if end < 0 else "%d characters away" % (end - head)))
    at = end + len(GUARD_END)

    # The message, lifted out of the file rather than retyped: it is cp949 and
    # carrying those bytes in this script would mean choosing an encoding for
    # them. Reusing the exact literal also keeps whatever translation
    # locale_string.txt holds for it.
    if s.count(FULL_LOG) != 1:
        sys.exit("the full-inventory report in char_item.cpp appears %d times "
                 "(expected exactly 1), so the message this patch reuses "
                 "cannot be identified" % s.count(FULL_LOG))
    mark = s.index(FULL_LOG)
    start = s.find(FULL_HEAD, mark)
    stop = s.find(FULL_TAIL, start)
    if start < 0 or stop < 0 or start - mark > 200:
        sys.exit("the full-inventory message no longer follows its sys_log line "
                 "in char_item.cpp, so it cannot be reused here")
    message = s[start + len(FULL_HEAD):stop]
    if not message or '"' in message or "\n" in message:
        sys.exit("the full-inventory message extracted from char_item.cpp is "
                 "not a single plain string literal -- refusing to splice it")

    io.open(CHAR_ITEM, "w", encoding="utf-8", errors="surrogateescape",
            newline="").write(s[:at] + block(message) + s[at:])
    print("   patched: server/game/src/char_item.cpp "
          "(wearing one out of a stack now takes one out of the stack)")
    return True


def patch_proto():
    """Returns the number of fields changed."""
    if not SHARE or not os.path.isfile(PROTO):
        sys.exit("not found: %s (set M2SHARE to the server share tree)" % PROTO)

    # Raw bytes throughout: the item names are cp949 and a few of them are not
    # valid in any other encoding, so decoding the table only to encode it again
    # would quietly rewrite rows this patch never touches.
    raw = open(PROTO, "rb").read()
    lines = raw.split(b"\n")

    header = lines[0].split(b"\t")
    columns = {}
    for index, name in enumerate(header):
        columns[name.decode("ascii", "replace")] = index
    for name in (COL_ANTI_FLAG, COL_FLAG, COL_SIZE, COL_TYPE):
        if name not in columns:
            sys.exit("column %s is missing from the header of item_proto.txt"
                     % name)
    width = len(header)

    rows = {}
    for number, line in enumerate(lines):
        if number == 0 or not line.strip():
            continue
        fields = line.split(b"\t")
        if len(fields) != width:
            sys.exit("row %d of item_proto.txt has %d columns, expected %d"
                     % (number + 1, len(fields), width))
        rows.setdefault(fields[0], []).append(number)

    changed = 0
    already = 0

    for vnum, label, column, old, new in EDITS:
        key = vnum.encode("ascii")
        found = rows.get(key, [])
        if len(found) != 1:
            sys.exit("vnum %s (%s) appears %d times in item_proto.txt, expected "
                     "exactly once" % (vnum, label, len(found)))
        number = found[0]
        fields = lines[number].split(b"\t")

        # The split above is written for ITEM_UNIQUE and nothing else. A row
        # that has become some other type must not be made stackable by it.
        if fields[columns[COL_TYPE]] != UNIQUE:
            sys.exit("vnum %s (%s) is %s, not ITEM_UNIQUE. The split this patch "
                     "puts into CHARACTER::EquipItem only covers ITEM_UNIQUE, "
                     "so this row must not be made stackable"
                     % (vnum, label,
                        fields[columns[COL_TYPE]].decode("ascii", "replace")))

        # SIZE is the inventory footprint, not a stack limit -- there is no
        # per-item stack limit in this schema, only the compile-time 200 in
        # common/item_length.h. An item taller than one cell cannot merge.
        if fields[columns[COL_SIZE]] != b"1":
            sys.exit("vnum %s (%s) occupies %s inventory cells and cannot be "
                     "merged" % (vnum, label, fields[columns[COL_SIZE]]
                                 .decode("ascii", "replace")))

        current = fields[columns[column]]
        if current == new.encode("ascii"):
            already += 1
            continue
        if current != old.encode("ascii"):
            hint = OUT_OF_ORDER.get(current.decode("ascii", "replace"))
            if hint:
                sys.exit("vnum %s (%s) has %s = %r. %s"
                         % (vnum, label, column,
                            current.decode("ascii", "replace"), hint))
            sys.exit("vnum %s (%s) has %s = %r, expected %r"
                     % (vnum, label, column,
                        current.decode("ascii", "replace"), old))

        fields[columns[column]] = new.encode("ascii")
        lines[number] = b"\t".join(fields)
        print("   patched: %s %-18s %s -> %s" % (vnum, label, old, new))
        changed += 1

    if not changed:
        print("   already patched: conf/item_proto.txt (%d fields)" % already)
        return 0

    open(PROTO, "wb").write(b"\n".join(lines))
    print("   patched: conf/item_proto.txt (%d fields)" % changed)
    return changed


def main():
    # The core first, always. If its anchor has moved, the item table must stay
    # exactly as it is: a stackable ring against a core that still wears the
    # whole pile destroys four rings out of five, which is far worse than a ring
    # that does not stack.
    wrote_core = patch_core()
    wrote_rows = patch_proto()

    if wrote_core or wrote_rows:
        print("   Unused Experience Rings and Thief's Gloves now merge, and "
              "wearing one takes")
        print("   one. A ring that has been worn does not merge back into the "
              "fresh pile --")
        print("   its timer sits in a socket, and every merge path compares "
              "the sockets.")
        print("   THE CORE HAS TO BE RECOMPILED for the split to exist. A "
              "restart is not enough.")


if __name__ == "__main__":
    main()
