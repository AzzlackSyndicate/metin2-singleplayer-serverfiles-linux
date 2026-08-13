#!/usr/bin/env python3
"""`STACKABLE' in the FLAG column is not the stack bit. `ITEM_STACKABLE' is.

The server maps flag NAMES to BITS by their position in a list (ProtoReader.cpp).
`ITEM_STACKABLE' is index 2 and sets ITEM_FLAG_STACKABLE. The bare word
`STACKABLE' is index 14, which is ITEM_FLAG_APPLICABLE -- a bit nothing in the
drop, stack or pick-up paths ever reads.

So eight rows have been claiming to stack for as long as this table has existed,
in a spelling that looks right and does nothing. It was found while fixing the
two items that were actually reported (fix_item_stacking.py, which repaired
Blessing Scroll 72301 by the same route); these eight are the rest of the same
mistake, left over because they were outside that brief:

    39033  71109  72304  72306  72307  72309  72314  76015

among them Blessing Marble and the Scroll of Correction.

Only the token is changed. Every other flag on the row, the ANTI_FLAG column and
every other column are left exactly as they were -- which matters, because a row
that also carries ANTI_STACK still will not stack, and deciding that is a
separate judgement about that item rather than a spelling correction.

The table is TAB-separated and holds cp949 Korean names, so it is read and
written as raw bytes with surrogateescape: a decode-and-re-encode round trip
would quietly rewrite every name in the file.

Idempotent. A second run reports `already patched'.
"""
import io
import os
import sys

SHARE = os.environ.get("M2SHARE", "")
TABLE = os.path.join(SHARE, "conf/item_proto.txt")

VNUMS = {"39033", "71109", "72304", "72306", "72307", "72309", "72314", "76015"}


def main():
    if not os.path.isfile(TABLE):
        sys.exit("not found: %s (set M2SHARE)" % TABLE)

    text = io.open(TABLE, "rb").read().decode("utf-8", "surrogateescape")
    lines = text.split("\n")

    header = [h.strip().upper() for h in lines[0].split("\t")]
    try:
        flag_col = header.index("FLAG")
    except ValueError:
        sys.exit("the FLAG column is not where this table says it is")

    changed = 0
    seen = set()
    for i, line in enumerate(lines):
        if i == 0 or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) <= flag_col:
            continue
        vnum = fields[0].strip()
        if vnum not in VNUMS:
            continue
        seen.add(vnum)

        tokens = [t.strip() for t in fields[flag_col].split("|")]
        if "STACKABLE" not in tokens:
            continue
        tokens = ["ITEM_STACKABLE" if t == "STACKABLE" else t for t in tokens]
        fields[flag_col] = " | ".join(tokens)
        lines[i] = "\t".join(fields)
        changed += 1

    missing = VNUMS - seen
    if missing:
        sys.exit("these vnums are not in the table: %s" % ", ".join(sorted(missing)))
    if not changed:
        print("already patched")
        return

    io.open(TABLE, "wb").write("\n".join(lines).encode("utf-8", "surrogateescape"))
    print("patched: %d row(s) now spell the stack bit in the way the server reads" % changed)


if __name__ == "__main__":
    main()
