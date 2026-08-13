#!/usr/bin/env python3
"""Skill books of the same skill merge into one stack.

WHAT WAS ASKED FOR: "wir hatten einen fix eingebaut und bravery capes stackable
zu machen. Koennen wir gleichnamige fertigkeitsbuecher auch stackable machen?" --
followed by the condition that matters: "buecher der gleichen faehigkeiten
sollten nur verschmelzen natuerlich, unterschiedliche nicht".

That condition is the whole difficulty, because a skill book does not always
carry its skill in its item number. char_item.cpp:2118 reads it two different
ways:

    if (item->GetVnum() == 50300)
        dwVnum = item->GetSocket(0);   // the generic book: skill is in socket 0
    else
        dwVnum = item->GetValue(0);    // 50401..50511: skill is the row itself

So every generic book is vnum 50300 and shows the same name whatever it teaches.
Stacking by vnum alone would pile a Sword Aura book onto a Dark Orb book, they
would show as "x2" of one thing, and the other skill would be gone.

WHY THAT CANNOT HAPPEN. All three merge paths compare the sockets, not only the
vnum, and a single differing socket vetoes the merge:

    char_item.cpp:5628  dragging one stack onto another
        for (int i = 0; i < ITEM_SOCKET_MAX_NUM; ++i)
            if (item2->GetSocket(i) != item->GetSocket(i))
                return false;

    char_item.cpp:5838  picking a drop up off the ground
        for (j = 0; j < ITEM_SOCKET_MAX_NUM; ++j)
            if (item2->GetSocket(j) != item->GetSocket(j))
                break;
        if (j != ITEM_SOCKET_MAX_NUM)
            continue;

    safebox.cpp:190     merging inside the storage chest, same comparison

Two 50300 books for different skills differ in socket 0 and therefore never
merge, in any of the three. Two for the same skill agree in every socket and do.
The per-skill books 50401..50511 carry their skill in the row, so equal vnum
already means equal skill. That is exactly the asked-for behaviour, and it is
enforced by the core rather than by this patch -- which is why this patch is
allowed to set the flag on all 45 rows including the generic one.

THE SECOND HALF, WITHOUT WHICH THIS WOULD DESTROY ITEMS. Reading a book runs

    ITEM_MANAGER::instance().RemoveItem(item);

and RemoveItem destroys the item OBJECT, whatever its count says -- it is not a
consumption path, it is a deletion path. Unstackable books made that identical:
one object was one book. Set the stack flag and leave this line alone and the
first book read out of a stack of twenty deletes all twenty. So the flag and
this line have to change together, and this script refuses to do one without the
other: the core is patched first, and if its anchor does not match, the item
table is not touched at all.

The replacement is the same one every other consumable in this file already
uses, e.g. ITEM_USE / USE_ABILITY_UP twelve lines further down:

    item->SetCount(item->GetCount() - 1);

CItem::SetCount handles the last one correctly on its own (item.cpp:245): at
zero it removes the item from the character, clears the quickslot and destroys
the object. Nothing after the call touches `item' again -- SetSkillNextReadTime
takes dwVnum, which was read before -- which is required, because at zero the
object is already gone.

WHY THIS IS A CUSTOM-EXPERIENCE CHANGE AND NOT A BUG FIX. Unlike the Blessing
Scroll, there is no contradiction in the shipped data to point at: all 45
ITEM_SKILLBOOK rows agree on FLAG = NONE and ANTI_FLAG = NONE, and no sibling
book anywhere in the table is stackable. That is a decision upstream made, not
an oversight, and undoing it also changes how the core consumes the item. So it
belongs behind the switch with the other deliberate changes.

WHAT WAS RULED OUT. A client-side veto: IsStackable() and ITEM_ANTIFLAG_STACK
appear nowhere but the six server sites above, so the browser client neither
gates the merge nor needs a rebuilt data archive to show the count. And
ITEM_FLAG_MAKECOUNT, which item_manager.cpp:269 would use to inflate a count
from VALUE1 on a freshly created stackable item, is not among the flags written
here -- FLAG becomes exactly ITEM_STACKABLE.

Reverting means putting FLAG back to NONE on the 45 rows and RemoveItem back
into the two branches. The game core rebuilds the item_proto table from this
file at boot, so the core has to be restarted before any of it reaches a player.

Idempotent, in both halves independently. A second run reports `already
patched'.

    M2SRC=<context>/game/src M2SHARE=<context>/game/src/serverfiles/share \\
        python3 stack_skill_books.py
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

# The case body is bounded by these two, and both edits are made strictly
# between them. char_item.cpp handles thirty-odd item types and the deletion
# call below appears in several of them, so replacing it file-wide would consume
# one of something else entirely.
CASE_HEAD = "case ITEM_SKILLBOOK:"
CASE_TAIL = "SetSkillNextReadTime(dwVnum, get_global_time() + iReadDelay);"

# Not anchored on the surrounding lines, because they carry cp949 Korean
# comments that no encoding round-trips cleanly. Position inside the bounded
# span identifies them instead, and there must be exactly two.
DELETE_CALL = "ITEM_MANAGER::instance().RemoveItem(item);"

TAB = "\t" * 5

# The marker that says this file has already been through here. Deliberately a
# phrase from the comment rather than the SetCount call, which appears dozens of
# times in this file for other item types.
MARKER = "One book off the stack, not the whole stack"

# The anchor above does not include the tabs already in front of it, so the
# first line of each block below starts where the deleted call started and only
# the continuation lines carry indentation of their own.
BROKEN_BOOK = (
    "// One book, not the pile -- see the note on the next branch. This\n"
    + TAB + "// one is the broken-book case: a generic 50300 that never got a\n"
    + TAB + "// skill number written into its socket. It consumes the book to\n"
    + TAB + "// get it out of the inventory, and it must consume exactly one.\n"
    + TAB + "item->SetCount(item->GetCount() - 1);"
)

BOOK_READ = (
    "// One book off the stack, not the whole stack. RemoveItem()\n"
    + TAB + "// destroys the item object outright, whatever its count says --\n"
    + TAB + "// it is a deletion path, not a consumption path. That was the\n"
    + TAB + "// same thing while books could not stack, because one object was\n"
    + TAB + "// one book; now that they can, it would delete every book in the\n"
    + TAB + "// pile to read one of them.\n"
    + TAB + "// SetCount() decrements instead, and when the last book is spent\n"
    + TAB + "// it removes the item and clears the quickslot itself. Nothing\n"
    + TAB + "// below may touch `item' again -- at zero the object is gone --\n"
    + TAB + "// and nothing does: SetSkillNextReadTime takes dwVnum, which was\n"
    + TAB + "// read further up.\n"
    + TAB + "item->SetCount(item->GetCount() - 1);"
)

# ---------------------------------------------------------------------------
# The item table half.

COL_TYPE = "ITEM_TYPE"
COL_SIZE = "SIZE"
COL_ANTI_FLAG = "ANTI_FLAG"
COL_FLAG = "FLAG"

SKILLBOOK = b"ITEM_SKILLBOOK"
FLAG_BEFORE = b"NONE"
FLAG_AFTER = b"ITEM_STACKABLE"

# The shipped table holds 45 of them: the generic 50300 and 44 per-skill books.
# A count far below that means the file is not the one this was written against.
EXPECTED_MIN = 40


def patch_core():
    """Returns True if it wrote, False if it was already patched. Exits on doubt."""
    if not SRC or not os.path.isfile(CHAR_ITEM):
        sys.exit("not found: %s (set M2SRC to the staged game/src tree)" % CHAR_ITEM)

    s = io.open(CHAR_ITEM, encoding="utf-8", errors="surrogateescape").read()

    if MARKER in s:
        print("   already patched: server/game/src/char_item.cpp")
        return False

    if s.count(CASE_HEAD) != 1:
        sys.exit("%s appears %d times in char_item.cpp (expected exactly 1) -- "
                 "refusing to guess" % (CASE_HEAD, s.count(CASE_HEAD)))

    head = s.index(CASE_HEAD)
    if s.count(CASE_TAIL) != 1:
        sys.exit("the end of the skill book case (%s) appears %d times in "
                 "char_item.cpp (expected exactly 1)"
                 % (CASE_TAIL, s.count(CASE_TAIL)))
    tail = s.index(CASE_TAIL, head)
    if tail < head:
        sys.exit("the end of the skill book case sits before its beginning in "
                 "char_item.cpp -- the case is not shaped as expected")

    body = s[head:tail]
    found = body.count(DELETE_CALL)
    if found != 2:
        sys.exit("the skill book case deletes the item %d times (expected "
                 "exactly 2: the broken book and the book that was read) -- "
                 "refusing to guess" % found)

    # First occurrence is the dwVnum == 0 branch, second is the successful read.
    body = body.replace(DELETE_CALL, BROKEN_BOOK, 1)
    at = body.index(DELETE_CALL)
    body = body[:at] + BOOK_READ + body[at + len(DELETE_CALL):]

    io.open(CHAR_ITEM, "w", encoding="utf-8", errors="surrogateescape",
            newline="").write(s[:head] + body + s[tail:])
    print("   patched: server/game/src/char_item.cpp "
          "(reading a book now spends one, not the stack)")
    return True


def patch_proto():
    """Returns the number of rows changed."""
    if not SHARE or not os.path.isfile(PROTO):
        sys.exit("not found: %s (set M2SHARE to the server share tree)" % PROTO)

    # Read as bytes: the Korean item names are cp949 and a few are not valid in
    # any other encoding, so decoding the whole table only to encode it again
    # risks rewriting rows this patch never touches.
    raw = open(PROTO, "rb").read()
    lines = raw.split(b"\n")

    header = lines[0].split(b"\t")
    columns = {}
    for index, name in enumerate(header):
        columns[name.decode("ascii", "replace")] = index
    for name in (COL_TYPE, COL_SIZE, COL_ANTI_FLAG, COL_FLAG):
        if name not in columns:
            sys.exit("column %s is missing from the header of item_proto.txt" % name)
    width = len(header)

    changed = 0
    already = 0
    seen = 0

    for number, line in enumerate(lines):
        if number == 0 or not line.strip():
            continue
        fields = line.split(b"\t")
        if len(fields) != width:
            sys.exit("row %d of item_proto.txt has %d columns, expected %d"
                     % (number + 1, len(fields), width))
        if fields[columns[COL_TYPE]] != SKILLBOOK:
            continue

        seen += 1
        vnum = fields[0].decode("ascii", "replace")

        if fields[columns[COL_SIZE]] != b"1":
            sys.exit("skill book %s occupies %s inventory cells and cannot be "
                     "merged" % (vnum, fields[columns[COL_SIZE]]
                                 .decode("ascii", "replace")))

        # A row carrying ANTI_STACK would take the flag and still refuse to
        # merge, which is the worst outcome: the change looks applied and does
        # nothing. None of the 45 shipped rows has it; say so loudly if one does.
        anti = fields[columns[COL_ANTI_FLAG]]
        if b"ANTI_STACK" in anti:
            sys.exit("skill book %s carries ANTI_STACK in its ANTI_FLAG column, "
                     "which vetoes both merge paths -- the stack flag alone "
                     "would have no effect. Remove it there first." % vnum)

        current = fields[columns[COL_FLAG]]
        if current == FLAG_AFTER:
            already += 1
            continue
        if current != FLAG_BEFORE:
            sys.exit("skill book %s has FLAG = %r, expected %r -- refusing to "
                     "overwrite flags this patch did not put there"
                     % (vnum, current.decode("ascii", "replace"),
                        FLAG_BEFORE.decode("ascii")))

        fields[columns[COL_FLAG]] = FLAG_AFTER
        lines[number] = b"\t".join(fields)
        changed += 1

    if seen < EXPECTED_MIN:
        sys.exit("only %d ITEM_SKILLBOOK rows in item_proto.txt (expected at "
                 "least %d) -- this is not the table this was written against"
                 % (seen, EXPECTED_MIN))

    if not changed:
        print("   already patched: conf/item_proto.txt (%d skill books)" % already)
        return 0

    open(PROTO, "wb").write(b"\n".join(lines))
    print("   patched: conf/item_proto.txt (%d skill books made stackable)"
          % changed)
    return changed


def main():
    # The core first, always. If its anchor has moved, the item table must stay
    # exactly as it is: stackable books against an unpatched core delete a whole
    # stack per reading, which is far worse than books that do not stack.
    wrote_core = patch_core()
    wrote_rows = patch_proto()

    if wrote_core or wrote_rows:
        print("   Books of the same skill now merge; books of different skills "
              "still cannot,")
        print("   because every merge path compares the sockets as well as the "
              "item number.")


if __name__ == "__main__":
    main()
