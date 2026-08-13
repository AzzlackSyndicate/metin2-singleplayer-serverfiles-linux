#!/usr/bin/env python3
"""Blessing Scrolls and Bravery Capes that refuse to merge into one stack.

THE REPORT: "blessing scrolls are not stackable, different counts of bravery
capes also not stackable".

WHAT WAS MEASURED. Stacking in this core is decided in exactly two places, and
both ask the same two questions about the item prototype:

    char_item.cpp:5629  (dragging one stack onto another)
        item2->IsStackable() && !IS_SET(item2->GetAntiFlag(), ITEM_ANTIFLAG_STACK)

    char_item.cpp:5838  (picking a drop up off the ground)
        item->IsStackable() && !IS_SET(item->GetAntiFlag(), ITEM_ANTIFLAG_STACK)

So an item merges only when the FLAG column contains ITEM_STACKABLE *and* the
ANTI_FLAG column does not contain ANTI_STACK. Two independent switches, and each
of the reported items was tripped by a different one.

    25040  Blessing Scroll  FLAG = NONE
    27001  Red Potion(S)    FLAG = ITEM_STACKABLE | ITEM_SLOW_QUERY

That is the whole Blessing Scroll bug: the row carries no flags at all, so
IsStackable() is false and every scroll picked up takes a fresh inventory slot.
The proof that this is an oversight rather than a design choice is in the same
table -- vnum 76016 is the very same Blessing Scroll (same name in every one of
the fifteen item_names files, same ITEM_USE/USE_TUNING type) and it reads
ITEM_STACKABLE | LOG. Row 25040 is the one that gets dropped: it appears in
common_drop_item.txt, in thirty-one mob_drop_item.txt entries and in thirty-nine
special_item_group.txt entries, which is why this is the row players notice.

The Bravery Capes were tripped by the other switch. All four cape rows already
carry ITEM_STACKABLE, but 39006 and 70038 also carry ANTI_STACK, which vetoes
both merge paths above. Two capes of the same vnum therefore sit side by side
forever, no matter what counts they hold -- which is precisely the "different
counts of bravery capes" symptom. The two remaining cape vnums, 70057 and 76007,
are the same item with the same USE_SPECIAL effect and carry no ANTI_STACK, so
there is no property of the cape itself that requires the veto.

Vnum 72301, the Blessing Scroll handed out by the level 66, 93, 95 and flame 102
main quests, was failing on both switches at once. Its FLAG column reads
"LOG | STACKABLE", and bare "STACKABLE" is not the stack bit. ProtoReader.cpp
turns a flag name into a bit by its position in a hard-coded list, and in that
list "ITEM_STACKABLE" sits at index 2 (ITEM_FLAG_STACKABLE) while "STACKABLE"
sits at index 14, which is ITEM_FLAG_APPLICABLE -- a bit the server never reads.
The word looks right and does nothing. On top of that the row carried
ANTI_STACK, so the quests that call pc.give_item2(72301, 1) five times in a row
handed the player five separate one-count slots.

WHAT WAS RULED OUT. The suspicion that the capes are dropped in bundles of 5,
10, 25 and 50 does not hold: every cape entry in every server-side drop table
gives a count of exactly 1 (62 entries in mob_drop_item.txt, 16 in the .edited
copy, 8 in the hector copy, and all but one special_item_group entry). Bundled
counts can only be reaching players through the admin panel's give-item form,
which takes a free-form quantity. That makes the four cape vnums irrelevant to
the complaint: the counts differ because whoever created them chose the counts,
and they refuse to combine because of ANTI_STACK, not because they are four
different items. No drop table is touched here.

Also ruled out: a missing maximum-stack setting. There is no per-item stack
limit in this schema. ITEM_MAX_COUNT is a compile-time 200 in
common/item_length.h, enforced in CItem::SetCount, and the SIZE column is the
inventory footprint rather than a stack size. Every row patched here already has
SIZE 1, which the guards below re-check, because an item taller than one cell
cannot merge sensibly.

WHAT THIS DEVIATES FROM. The reference [40250] serverfiles carry all five field
values unchanged, so none of this is local corruption -- it is how the item table
shipped. 272 rows in the table pair ITEM_STACKABLE with ANTI_STACK, so that
combination is an upstream convention and not a typo; the two cape rows are
changed here because they were reported, and nothing else with that shape is
touched. Reverting means putting ANTI_STACK back into the ANTI_FLAG column of
39006, 70038 and 72301.

The game core rebuilds the item_proto MySQL table from this file at boot, so the
core has to be restarted before any of this reaches a player.

Idempotent. Point it at the game image's build context with M2SHARE or a single
argument; a second run reports `already patched'.
"""
import os
import sys

SHARE = os.environ.get("M2SHARE", "")

PROTO = "conf/item_proto.txt"

# The table is tab separated and the first row names the columns. These two are
# looked up by name below rather than trusted blindly, because a column inserted
# upstream would otherwise silently move the edit onto the wrong field.
COL_ANTI_FLAG = "ANTI_FLAG"
COL_FLAG = "FLAG"
COL_SIZE = "SIZE"

# (vnum, column name, value before, value after, why)
EDITS = [
    (
        "25040", COL_FLAG,
        "NONE",
        "ITEM_STACKABLE",
        "the dropped Blessing Scroll carried no flags at all, while the "
        "identical scroll at vnum 76016 is ITEM_STACKABLE | LOG",
    ),
    (
        "72301", COL_FLAG,
        "LOG | STACKABLE",
        "ITEM_STACKABLE | LOG",
        "bare STACKABLE is index 14 in the reader's list, which is the "
        "APPLICABLE bit the server never reads, not the stack bit",
    ),
    (
        "72301", COL_ANTI_FLAG,
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_STACK | ANTI_MYSHOP",
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_MYSHOP",
        "the quest rewards call give_item2 once per scroll, so ANTI_STACK "
        "turned a five-scroll reward into five one-count slots",
    ),
    (
        "39006", COL_ANTI_FLAG,
        "ANTI_DROP | ANTI_SELL | ANTI_STACK | ANTI_MYSHOP",
        "ANTI_DROP | ANTI_SELL | ANTI_MYSHOP",
        "the row already claims ITEM_STACKABLE, and the sibling capes 70057 "
        "and 76007 carry no ANTI_STACK",
    ),
    (
        "70038", COL_ANTI_FLAG,
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_STACK | ANTI_MYSHOP",
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_MYSHOP",
        "same contradiction as 39006 on the cape the quests and chests hand "
        "out",
    ),
]


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else SHARE
    path = os.path.join(root, PROTO)
    if not os.path.isfile(path):
        sys.exit("not found: %s (set M2SHARE to the server share tree)" % path)

    # The table is read as bytes throughout. The Korean item names are cp949 and
    # a few of them are not valid in any other encoding, so decoding the file
    # only to encode it again risks rewriting rows this patch never touches.
    raw = open(path, "rb").read()
    lines = raw.split(b"\n")

    header = lines[0].split(b"\t")
    columns = {}
    for index, name in enumerate(header):
        columns[name.decode("ascii", "replace")] = index
    for name in (COL_ANTI_FLAG, COL_FLAG, COL_SIZE):
        if name not in columns:
            sys.exit("column %s is missing from the header of %s" % (name, PROTO))
    width = len(header)

    # A row is found by its vnum in column 0 rather than by matching text
    # anywhere in the line: a tab or a stray flag name inside an item name would
    # otherwise let a regexp corrupt a neighbouring field.
    rows = {}
    for number, line in enumerate(lines):
        if number == 0 or not line.strip():
            continue
        fields = line.split(b"\t")
        if len(fields) != width:
            sys.exit("row %d of %s has %d columns, expected %d"
                     % (number + 1, PROTO, len(fields), width))
        rows.setdefault(fields[0], []).append(number)

    changed = 0
    for vnum, column, old, new, why in EDITS:
        key = vnum.encode("ascii")
        found = rows.get(key, [])
        if len(found) != 1:
            sys.exit("vnum %s appears %d times in %s, expected exactly once"
                     % (vnum, len(found), PROTO))
        number = found[0]
        fields = lines[number].split(b"\t")

        if fields[columns[COL_SIZE]] != b"1":
            sys.exit("vnum %s occupies %s inventory cells and cannot be merged"
                     % (vnum, fields[columns[COL_SIZE]].decode("ascii", "replace")))

        current = fields[columns[column]]
        if current == new.encode("ascii"):
            print("already patched: %s %s" % (vnum, column))
            continue
        if current != old.encode("ascii"):
            sys.exit("vnum %s has %s = %r, expected %r"
                     % (vnum, column, current.decode("ascii", "replace"), old))

        fields[columns[column]] = new.encode("ascii")
        lines[number] = b"\t".join(fields)
        print("patched: %s %s -> %s" % (vnum, column, new))
        print("         %s" % why)
        changed += 1

    if not changed:
        return

    open(path, "wb").write(b"\n".join(lines))
    print("\n%d field(s) changed in %s." % (changed, PROTO))
    print("The game core rebuilds the item_proto table from this file at boot, "
          "so it has to be restarted before players see the change.")


if __name__ == "__main__":
    main()
