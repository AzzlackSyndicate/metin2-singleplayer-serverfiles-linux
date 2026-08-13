#!/usr/bin/env python3
"""The bonus drops off the metins and bosses become droppable and tradable, and
the four of them that can merge safely also stack.

WHAT WAS ASKED FOR, in three pieces: "Make Exorcism Scroll and Concentrated
Reading Stackable", "make the items, that are dropped from the metins (thieves
gloves, experience ring etc.) all tradable and droppable", and "also, if
possible, make the experience ring's and thief's gloves stackable as well" --
with the condition "make sure, only those experience rings are stackable, that
havent been used yet".

THE SET is exactly the bonus drop list that gen_drops.py hangs on 283 metins and
bosses. Nothing else in the table is touched, and no drop table is touched here:

    25040  Blessing Scroll        ITEM_USE    / USE_TUNING
    39028  bonus switcher         ITEM_USE    / USE_CHANGE_ATTRIBUTE
    39029  bonus adder            ITEM_USE    / USE_ADD_ATTRIBUTE
    70005  Experience Ring        ITEM_UNIQUE / UNIQUE_NONE
    70038  Bravery Cape           ITEM_USE    / USE_SPECIAL
    71001  Exorcism Scroll        ITEM_USE    / USE_AFFECT
    71094  Concentrated Reading   ITEM_USE    / USE_AFFECT
    72005  Thief's Gloves         ITEM_UNIQUE / UNIQUE_NONE

WHAT DROPPABLE AND TRADABLE MEANS HERE. Three separate bits, all in the ANTI_FLAG
column: ANTI_DROP forbids putting the item on the ground, ANTI_GIVE forbids the
player-to-player exchange window, ANTI_MYSHOP forbids putting it in a private
shop. All three come off every row in the set.

ANTI_SELL STAYS ON, deliberately. The request was for tradable and droppable,
which is what a player means by "I want to hand this to my friend" -- not for
NPC-sellable. Selling to a shop is the one irreversible mistake of the four, and
these items have no sensible yang price (the two uniques are priced at 0 in the
table, so a shop would pay nothing for them anyway). If that reading is wrong,
delete the four ANTI_SELL tokens from the `after' values in EDITS below and the
rest of this script is unaffected.

STACKING, AND WHERE IT WAS REFUSED. Two switches decide it, and both are read at
all three merge sites -- char_item.cpp:5628 (dragging one stack onto another),
char_item.cpp:5838 (picking a drop up off the ground) and safebox.cpp:190 (the
storage chest): FLAG must contain ITEM_STACKABLE and ANTI_FLAG must not contain
ANTI_STACK.

    71001, 71094   already carry FLAG = ITEM_STACKABLE | LOG and fail only on
                   ANTI_STACK. Removing that token is the whole change; the FLAG
                   column of this table is not written by this script at all.
    39028, 39029   already carry ITEM_STACKABLE and already have no ANTI_STACK.
                   They stack today; they were only in the set for the drop and
                   trade half.
    25040, 70038   files/fixes/fix_item_stacking.py already made both of these
                   merge, so this script only checks that it ran and then leaves
                   the stacking alone. 70038 still carried ANTI_DROP, ANTI_GIVE
                   and ANTI_MYSHOP after that fix, so it does get the trade half.
                   25040 has ANTI_FLAG = NONE and needs nothing at all; it is
                   listed so the set is complete and so a future edit to that row
                   is noticed here.
    70005, 72005   NOT made stackable. Reason below. They get the trade half and
                   keep ANTI_STACK.

WHY THE CONSUMPTION PATH HAD TO BE CHECKED FIRST. stack_skill_books.py exists
because reading a skill book called ITEM_MANAGER::instance().RemoveItem(item),
which destroys the item OBJECT whatever its count says. That is invisible while
an item cannot stack -- one object is one item -- and deletes the whole pile the
moment it can. So every branch that spends an item in this set was read:

    char_item.cpp:4921   USE_AFFECT (the second of the two branches by that name,
                         the one 71001 and 71094 reach)
                             item->SetCount(item->GetCount() - 1);
    char_item.cpp:2214   USE_AFFECT, the first branch, same line
    char_item.cpp:4485   the body that USE_CHANGE_ATTRIBUTE (4482) and
    ..4866               USE_ADD_ATTRIBUTE (4484) fall into, which runs from the
                         last label of that nine-label run down to USE_BAIT:
                         six SetCount(GetCount() - 1) sites and not a single
                         RemoveItem on `item' anywhere inside it.

All of them decrement. CItem::SetCount removes the item and clears the quickslot
by itself when the count reaches zero (item.cpp:234-278). Nothing here needs the core
patched, so this script does not write to char_item.cpp -- but it does read it
and refuse to touch the item table if any of those spans has changed shape,
because that is the assumption the whole ANTI_STACK removal rests on.

WHY 70005 AND 72005 DO NOT GET THE STACK BIT. They are the two ITEM_UNIQUE rows.
Their ITEM_WEAR column reads WEAR_SHIELD, which is misleading: ProtoReader.cpp:301
turns a wear name into a bit by its position in a list, and "WEAR_SHIELD" sits at
index 7 there, which is 1 << 7 = WEARABLE_UNIQUE in common/item_length.h:319 --
not WEARABLE_SHIELD, which is 1 << 8. So both rows are worn in a unique slot,
CItem::FindEquipCell item.cpp:537-541 sends them to WEAR_UNIQUE1 or WEAR_UNIQUE2,
and they run on a timer.

Equipping moves the whole object into the slot. CHARACTER::EquipItem
(char_item.cpp:6100-6190) and CItem::EquipTo (item.cpp:816-852) never read
GetCount, never split and have no notion of a partial stack -- EquipTo simply
sets m_wCell and hands the object to CHARACTER::SetWear. When the ring runs out,
unique_expire_event (item.cpp:1327-1332) calls RemoveItem on that object. A stack
of five worn rings is therefore one object that grants sixty minutes once and is
then destroyed with all five inside it. Four rings gone. That is not an edge
case: fresh rings are exactly the ones that merge (see the next paragraph), so
the stack-then-equip sequence would be the normal one.

Fixing that means new C++ in CHARACTER::EquipItem -- split one item off the stack
before EquipTo, along the lines of the existing ITEM_SPLIT block at
char_item.cpp:5675-5685 (SetCount, CreateItem, FN_copy_item_socket,
AddToCharacter), guarded to ITEM_UNIQUE so that a quiver of 200 arrows, which is
a stackable item that is MEANT to be worn as a stack, keeps working.

That is what files/custom/split_unique_on_equip.py does, and it is a separate
script for one reason: it changes the core, so it needs the game image REBUILT,
while everything here is a text edit that a restart carries to the players.
Nothing in this script depends on it. If it has been applied, the two rows below
are already stackable and already say `ANTI_SELL', and this script confirms them
and moves on; the core is read for its marker to decide which of the two states
is the expected one. If it has NOT been applied, ITEM_STACKABLE on either row is
a way to destroy four rings out of five, and the guards below stop rather than
let it stand.

WHAT "ONLY UNUSED RINGS STACK" WOULD HAVE COST -- nothing, as it turns out, which
is worth recording for whoever picks the core patch up. All three merge sites
compare every socket and a single differing socket vetoes the merge:

    char_item.cpp:5631   for (int i = 0; i < ITEM_SOCKET_MAX_NUM; ++i)
                             if (item2->GetSocket(i) != item->GetSocket(i))
                                 return false;
    char_item.cpp:5847   the same comparison, written as a break/continue
    safebox.cpp:194      the same comparison again

ITEM_SOCKET_MAX_NUM is 3 (common/item_length.h:11) and the unique timer lives in
the last two of them: ITEM_SOCKET_UNIQUE_REMAIN_TIME is socket 2 and
ITEM_SOCKET_UNIQUE_SAVE_TIME is socket 1 (item_length.h:38-39). A ring is stamped
with its full life at creation -- item_manager.cpp:227 sets socket 2 to VALUE0,
which is 60 for the ring and 30 for the gloves -- and the countdown only runs
while it is worn: CItem::StartUniqueExpireEvent is reached through EquipTo
(item.cpp:898), and unique_expire_event decrements socket 2 once a minute
(item.cpp:1335). Unequipping parks the leftover seconds of the current minute in
socket 1 (item.cpp:1547). So an unused ring reads (0, 0, 60), a ring that has
been worn reads something else in socket 2, in socket 1, or in both, and the two
can never merge. The asked-for behaviour falls out of the core for free; it is
the equip path, not the merge path, that stops this.

WHAT ELSE WAS RULED OUT. A client-side veto: ITEM_ANTIFLAG_STACK and IsStackable()
appear nowhere but the six server sites above, so nothing needs a rebuilt client
archive. ITEM_FLAG_MAKECOUNT, which item_manager.cpp would use to inflate a fresh
stack's count out of VALUE1, is not on any row in this set and no FLAG column is
written here anyway. And SIZE is re-checked on every row before anything is
changed: the column is the inventory footprint, and an item taller than one cell
cannot merge sensibly. All eight are SIZE 1.

ORDER. files/fixes/apply.sh runs before files/custom/apply.sh, so the values
anchored on for 25040 and 70038 are the ones fix_item_stacking.py leaves behind,
not the ones the archive ships. Running this against an unfixed tree stops with a
message saying so rather than guessing.

The game core rebuilds the item_proto MySQL table from this file at boot, so the
core has to be restarted -- not rebuilt -- before any of this reaches a player.
Items already in player inventories are unaffected as objects; the flags are read
from the prototype, so an Exorcism Scroll that is already lying in an inventory
starts merging as soon as the server comes back up.

Idempotent. A second run reports `already patched' and writes nothing.

    M2SRC=<context> M2SHARE=<context>/serverfiles/share \\
        python3 free_metin_drop_items.py
"""
import io
import os
import sys

SHARE = os.environ.get("M2SHARE", "")
SRC = os.environ.get("M2SRC", "")

PROTO = "conf/item_proto.txt"
CHAR_ITEM = os.path.join("server", "game", "src", "char_item.cpp")

# Looked up by name below rather than trusted blindly: a column inserted upstream
# would otherwise silently move every edit onto the wrong field.
COL_ANTI_FLAG = "ANTI_FLAG"
COL_FLAG = "FLAG"
COL_SIZE = "SIZE"

# ---------------------------------------------------------------------------
# The read-only interlock on the core.
#
# Nothing is patched in char_item.cpp. These spans are read to confirm that every
# branch which spends one of the items being made mergeable decrements the count
# instead of destroying the object -- the assumption the ANTI_STACK removals rest
# on. If a span has moved or changed shape, the item table is left alone.

# Both USE_AFFECT branches sit at the same indentation inside their switch, and
# the case that follows each one is found at that same indentation. Bounding the
# span this way rather than by line number survives edits elsewhere in the file.
CASE_INDENT = "\n" + "\t" * 5 + "case "

CONSUME = "item->SetCount(item->GetCount() - 1);"

# `item' specifically. RemoveItem(item2, ...) is legitimate in these spans -- it
# is the target of the scroll, not the scroll -- so the two forms that name the
# consumed item itself are matched instead of the bare function name.
DESTROY = ("RemoveItem(item)", "RemoveItem(item,")

# (case label, how many of them there must be, what reaches it)
CORE_SPANS = [
    ("case USE_AFFECT :", 2,
     "71001 Exorcism Scroll and 71094 Concentrated Reading"),
    # USE_CHANGE_ATTRIBUTE and USE_ADD_ATTRIBUTE are two of the nine case labels
    # that fall through into one body, so the span is taken from the LAST label
    # of that run -- anchoring on either of the two names would bound a span
    # that is empty, because the label directly below it is the next case.
    ("case USE_ADD_ATTRIBUTE2:", 1,
     "39028 and 39029, whose USE_CHANGE_ATTRIBUTE and USE_ADD_ATTRIBUTE "
     "branches share the body this label falls into"),
]

# ---------------------------------------------------------------------------
# The item table.
#
# (vnum, label, ANTI_FLAG before, ANTI_FLAG after, FLAG it must have, stacks?)
#
# FLAG is never written -- it is stated so that a row whose flags changed
# upstream stops this script instead of being quietly re-flagged. `stacks' is
# what the row is expected to do AFTER this run, and it is cross-checked against
# both switches: a True row must end up with ITEM_STACKABLE and without
# ANTI_STACK, a False row must end up with neither of those two agreeing.
EDITS = [
    (
        "25040", "Blessing Scroll",
        "NONE",
        "NONE",
        "ITEM_STACKABLE",
        True,
        "already free of every ANTI flag and already merging since "
        "files/fixes/fix_item_stacking.py -- checked, not changed",
    ),
    (
        "39028", "bonus switcher",
        "ANTI_DROP | ANTI_SELL | ANTI_MYSHOP",
        "ANTI_SELL",
        "ITEM_STACKABLE | LOG",
        True,
        "already stacks; only the ground, the exchange window and the private "
        "shop were closed to it",
    ),
    (
        "39029", "bonus adder",
        "ANTI_DROP | ANTI_SELL | ANTI_MYSHOP",
        "ANTI_SELL",
        "ITEM_STACKABLE | LOG",
        True,
        "same row shape as 39028",
    ),
    (
        "70005", "Experience Ring",
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_STACK | ANTI_MYSHOP",
        "ANTI_SELL | ANTI_STACK",
        "CONFIRM_WHEN_USE",
        False,
        "ANTI_STACK stays: the equip path moves the whole object into the "
        "unique slot and unique_expire_event destroys it there, so a worn "
        "stack of five would expire as one",
    ),
    (
        "70038", "Bravery Cape",
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_MYSHOP",
        "ANTI_SELL",
        "ITEM_STACKABLE | ITEM_IRREMOVABLE",
        True,
        "fix_item_stacking.py took ANTI_STACK off this row and left the other "
        "three ANTI flags standing, so it merged but could still not be "
        "dropped or handed over",
    ),
    (
        "71001", "Exorcism Scroll",
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_STACK | ANTI_MYSHOP",
        "ANTI_SELL",
        "ITEM_STACKABLE | LOG",
        True,
        "the row already claimed ITEM_STACKABLE and failed only on ANTI_STACK; "
        "USE_AFFECT spends it with SetCount, not RemoveItem",
    ),
    (
        "71094", "Concentrated Reading",
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_STACK | ANTI_MYSHOP",
        "ANTI_SELL",
        "ITEM_STACKABLE | LOG",
        True,
        "same row shape and same USE_AFFECT branch as 71001",
    ),
    (
        "72005", "Thief's Gloves",
        "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_STACK | ANTI_MYSHOP",
        "ANTI_SELL | ANTI_STACK",
        "LOG",
        False,
        "same unique slot and same expiry as 70005",
    ),
]

# The values these two rows carry in the pristine archive, i.e. before
# files/fixes/apply.sh has run. Recognised only to turn "unexpected value" into
# a sentence an operator can act on.
BEFORE_THE_FIXES = {
    ("25040", COL_FLAG): "NONE",
    ("70038", COL_ANTI_FLAG): "ANTI_DROP | ANTI_SELL | ANTI_GIVE | ANTI_STACK | ANTI_MYSHOP",
}

STACK_FLAG = b"ITEM_STACKABLE"
ANTI_STACK = b"ANTI_STACK"

# The companion script, split_unique_on_equip.py, teaches CHARACTER::EquipItem to
# split one item off a stack of ITEM_UNIQUE before wearing it, and then makes
# 70005 and 72005 stackable. That is the one condition under which ITEM_STACKABLE
# on those two rows is safe rather than a way to destroy four rings out of five,
# so this script does not simply forbid it -- it asks the core whether the split
# is there. The phrase is the marker from that script's own comment block.
#
# Without this, running files/custom/apply.sh a second time against a tree that
# already has both scripts applied would stop here: apply.sh runs this script
# before that one, and on the second pass it would find an ITEM_STACKABLE it had
# been told to refuse.
SPLIT_MARKER = "One out of the pile, not the pile into the slot"

# What the two unique rows look like once split_unique_on_equip.py has run.
# (FLAG, ANTI_FLAG before, ANTI_FLAG after, stacks?) -- before and after are the
# same value, because by then there is nothing left for this script to do to
# them. Substituted for the EDITS entries above when the marker is in the core.
AFTER_THE_SPLIT = {
    "70005": ("ITEM_STACKABLE | CONFIRM_WHEN_USE", "ANTI_SELL", "ANTI_SELL", True),
    "72005": ("ITEM_STACKABLE | LOG", "ANTI_SELL", "ANTI_SELL", True),
}


def _spans(s, label, expected, reached_by):
    """Yields the body of each `label' case, bounded by the next case at the
    same indentation."""
    anchor = CASE_INDENT[:-len("case ")] + label
    found = s.count(anchor)
    if found != expected:
        sys.exit("char_item.cpp holds %d occurrences of `%s' at the expected "
                 "indentation, not %d -- the branch that spends %s is not "
                 "where it was measured, so the item table is left alone"
                 % (found, label, expected, reached_by))

    at = 0
    for _ in range(expected):
        head = s.index(anchor, at)
        tail = s.find(CASE_INDENT, head + len(anchor))
        if tail < 0:
            sys.exit("the case following `%s' in char_item.cpp could not be "
                     "found, so the branch that spends %s cannot be bounded"
                     % (label, reached_by))
        yield s[head:tail]
        at = tail


def read_core():
    """Reads char_item.cpp and checks every consumption span. Returns True if the
    core also carries split_unique_on_equip.py's split. Exits on doubt."""
    path = os.path.join(SRC, CHAR_ITEM)
    if not SRC or not os.path.isfile(path):
        sys.exit("not found: %s (set M2SRC to the staged game/src tree). This "
                 "script does not patch the core, but it refuses to make an "
                 "item mergeable without first reading how the core spends it."
                 % path)

    s = io.open(path, encoding="utf-8", errors="surrogateescape").read()

    for label, expected, reached_by in CORE_SPANS:
        for body in _spans(s, label, expected, reached_by):
            for form in DESTROY:
                if form in body:
                    sys.exit(
                        "the `%s' branch in char_item.cpp destroys the item "
                        "object (%s) instead of decrementing its count. "
                        "Stackable items on that branch would lose the whole "
                        "pile per use, so nothing is changed. See "
                        "stack_skill_books.py for how that is repaired."
                        % (label, form))
            if CONSUME not in body:
                sys.exit("the `%s' branch in char_item.cpp no longer contains "
                         "`%s', so it is not spending %s the way this patch "
                         "was measured against"
                         % (label, CONSUME, reached_by))

    print("   core read: every branch that spends these items decrements the "
          "count")

    split = SPLIT_MARKER in s
    if split:
        print("   core read: EquipItem splits a stack of unique items, so "
              "70005 and 72005 may stack")
    return split


def main():
    split = read_core()

    root = sys.argv[1] if len(sys.argv) > 1 else SHARE
    path = os.path.join(root, PROTO)
    if not os.path.isfile(path):
        sys.exit("not found: %s (set M2SHARE to the server share tree)" % path)

    # Read and written as raw bytes throughout. The item names are cp949 and a
    # few of them are not valid in any other encoding, so decoding the whole
    # table only to encode it again would quietly rewrite rows this patch never
    # touches.
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
    # anywhere in the line: a stray flag name inside an item name would
    # otherwise let a match corrupt a neighbouring field.
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
    already = 0

    for vnum, label, old, new, flag, stacks, why in EDITS:
        # Once the core can split a worn stack, the two unique rows are the
        # companion script's business and this one only confirms them.
        if split and vnum in AFTER_THE_SPLIT:
            flag, old, new, stacks = AFTER_THE_SPLIT[vnum]

        key = vnum.encode("ascii")
        found = rows.get(key, [])
        if len(found) != 1:
            sys.exit("vnum %s (%s) appears %d times in %s, expected exactly once"
                     % (vnum, label, len(found), PROTO))
        number = found[0]
        fields = lines[number].split(b"\t")

        # An item taller than one inventory cell cannot merge, and SIZE is the
        # footprint rather than a stack limit -- there is no per-item stack limit
        # in this schema, only the compile-time 200 in common/item_length.h.
        if fields[columns[COL_SIZE]] != b"1":
            sys.exit("vnum %s (%s) occupies %s inventory cells and cannot be "
                     "merged" % (vnum, label,
                                 fields[columns[COL_SIZE]]
                                 .decode("ascii", "replace")))

        current_flag = fields[columns[COL_FLAG]]

        # Checked against the live value and before anything else about the
        # FLAG column, because this is the one wrong value that costs a player
        # items rather than an operator a confusing message.
        if not stacks and STACK_FLAG in current_flag:
            sys.exit("vnum %s (%s) carries ITEM_STACKABLE in the table, but "
                     "CHARACTER::EquipItem in this tree does not split a stack "
                     "before wearing it. It moves the whole stacked object into "
                     "the unique slot, where unique_expire_event destroys it -- "
                     "a worn stack of five expires as one and four are lost. "
                     "Apply files/custom/split_unique_on_equip.py, which patches "
                     "the core and sets this flag together, or take the flag "
                     "back off. Refusing to continue." % (vnum, label))

        if current_flag != flag.encode("ascii"):
            hint = BEFORE_THE_FIXES.get((vnum, COL_FLAG))
            if hint is not None and current_flag == hint.encode("ascii"):
                sys.exit("vnum %s (%s) still has the FLAG value it carries in "
                         "the pristine archive. files/fixes/apply.sh has not "
                         "run against this tree, and it has to run first."
                         % (vnum, label))
            sys.exit("vnum %s (%s) has FLAG = %r, expected %r -- this script "
                     "does not write the FLAG column, and it will not act on a "
                     "row whose flags someone else has changed"
                     % (vnum, label,
                        current_flag.decode("ascii", "replace"), flag))

        # The two switches have to agree with each other, in both directions --
        # a row that claims ITEM_STACKABLE and keeps ANTI_STACK looks patched
        # and does nothing, and a row that loses ANTI_STACK without claiming
        # ITEM_STACKABLE is the same mistake the other way round. Both values
        # come out of EDITS by this point, so this is the table above checked
        # against itself: it is what stops someone flipping a `stacks' column
        # or editing an `after' value without thinking the pair through.
        keeps_veto = ANTI_STACK in new.encode("ascii")
        if stacks and STACK_FLAG not in current_flag:
            sys.exit("vnum %s (%s) is expected to merge but its FLAG column "
                     "does not contain ITEM_STACKABLE" % (vnum, label))
        if stacks and keeps_veto:
            sys.exit("vnum %s (%s) is expected to merge but this patch would "
                     "leave ANTI_STACK on it, which vetoes all three merge "
                     "sites" % (vnum, label))
        if not stacks and not keeps_veto:
            sys.exit("vnum %s (%s) must keep ANTI_STACK -- see the note in the "
                     "head of this script" % (vnum, label))

        current = fields[columns[COL_ANTI_FLAG]]
        if current == new.encode("ascii"):
            already += 1
            if old != new:
                print("   already patched: %s %s" % (vnum, COL_ANTI_FLAG))
            continue
        if current != old.encode("ascii"):
            hint = BEFORE_THE_FIXES.get((vnum, COL_ANTI_FLAG))
            if hint is not None and current == hint.encode("ascii"):
                sys.exit("vnum %s (%s) still has the ANTI_FLAG value it carries "
                         "in the pristine archive. files/fixes/apply.sh has not "
                         "run against this tree, and it has to run first -- it "
                         "is what takes ANTI_STACK off this row."
                         % (vnum, label))
            sys.exit("vnum %s (%s) has %s = %r, expected %r"
                     % (vnum, label, COL_ANTI_FLAG,
                        current.decode("ascii", "replace"), old))

        fields[columns[COL_ANTI_FLAG]] = new.encode("ascii")
        lines[number] = b"\t".join(fields)
        print("   patched: %s %-22s %s -> %s" % (vnum, label, old, new))
        print("            %s" % why)
        changed += 1

    if not changed:
        print("   already patched: %s (%d rows)" % (PROTO, already))
        return

    open(path, "wb").write(b"\n".join(lines))
    print("\n   %d row(s) changed in %s." % (changed, PROTO))
    print("   The bonus drops can now be dropped, handed over and put in a "
          "private shop.")
    print("   Selling them to an NPC is still refused, and the Experience Ring "
          "and Thief's")
    print("   Gloves still do not stack -- see the head of this script for "
          "both reasons.")


if __name__ == "__main__":
    main()
