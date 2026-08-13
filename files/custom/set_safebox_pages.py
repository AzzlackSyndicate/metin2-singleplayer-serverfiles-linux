#!/usr/bin/env python3
"""The storeroom at the Storekeeper opens with three pages instead of one.

WHAT WAS ASKED FOR: "extend the storeroom (at storekeeper) from 1 page to 3
pages please" -- for every account, including the ones that already exist, and
without losing a single stored item.

WHERE THE ONE PAGE COMES FROM. Not from the database, which is the first place
anyone looks and the reason this took a while to find. The number is decided in
the game core, in the handler that receives the storeroom contents back from the
db process:

    input_db.cpp:1170  void CInputDB::SafeboxLoad(LPDESC d, const char * c_pData)
    input_db.cpp:1186      BYTE bSize = 1;
    input_db.cpp:1200      if (GetPremiumRemainSeconds(PREMIUM_SAFEBOX) > 0 ||
                               IsEquipUniqueGroup(UNIQUE_GROUP_LARGE_SAFEBOX))
    input_db.cpp:1202          bSize = 3;
    input_db.cpp:1208      //LoadSafebox(p->bSize * SAFEBOX_PAGE_SIZE, ...)
    input_db.cpp:1209      LoadSafebox(bSize * SAFEBOX_PAGE_SIZE, ...)

Line 1208 is upstream's own commented-out line, and it is the whole story:
p->bSize is the `size' column that the db process read out of the safebox table
(ClientManager.cpp:495, :554), and the core stopped using it. What actually
reaches the player is the local bSize -- 1 for everyone, 3 for a premium account
or an equipped UNIQUE_GROUP_LARGE_SAFEBOX item. This server sells neither, so
that branch could never fire and the storeroom was permanently one page.

That also settles the shape of the fix. Raising the database column alone would
have changed nothing at all: the core does not read it. So the change belongs
here, in the default, where it reaches every account at once -- old, new, and
accounts that have no safebox row in the first place, which is most of them
(a row is only ever created by QUERY_SAFEBOX_CHANGE_SIZE, ClientManager.cpp:832,
which nothing on this server calls).

WHAT THE NUMBER MEANS. A row count, not a page count, and this is worth being
exact about because the two are one multiplication apart in the same function.
bSize is multiplied by SAFEBOX_PAGE_SIZE (char.h:53, = 9) on the way into
CHARACTER::LoadSafebox, which hands the product to CSafebox, which builds a grid
five cells wide and that many rows tall:

    safebox.cpp:17     m_pkGrid = M2_NEW CGrid(5, m_iSize);

So bSize 1 -> 9 rows -> 45 cells -> one page, and bSize 3 -> 27 rows -> 135
cells -> three pages. 135 is not a coincidence:

    common/tables.h:752   #define SAFEBOX_MAX_NUM   135
    safebox.h:33          LPITEM m_pkItems[SAFEBOX_MAX_NUM];

Three pages is exactly the fixed array CSafebox stores its items in, so three is
the ceiling this core can carry and a fourth page would write past the end of
m_pkItems on the first item put into it. The guard at the top of patch() checks
that relationship in the tree it is patching and refuses to write if it does not
hold, rather than trusting the numbers quoted here.

NO STORED ITEM CAN BE LOST BY THIS, and there are three separate reasons:

  * The cell numbering does not move. CGrid is row-major and five wide, so cell
    n is at row n/5 whatever the height is. Growing the grid from 9 rows to 27
    only appends rows 9..26; cells 0..44 keep the numbers they already have.
    CGrid's growing constructor (grid.cc:13) copies MIN(new, old) bytes
    linearly, which is the same statement in code.
  * Nothing renumbers on load. LoadSafebox (char.cpp:5643) places each item at
    the pos it came out of the item table with, and item.pos is a smallint
    unsigned, so 0..134 was always representable.
  * The db process sends every row it finds. Its SELECT (ClientManager.cpp:585)
    filters on owner_id and window='SAFEBOX' and nothing else; the grid it
    builds at ClientManager.cpp:620 is used only to find a blank cell for a
    pending item_award and never to decide what to send. So the direction this
    patch moves in -- more cells, never fewer -- cannot drop anything, and even
    the reverse would leave the rows sitting in the item table untouched.

THE CLIENT NEEDS NOTHING. This was the expensive question, because a client
change means rebuilding the WASM engine and possibly re-uploading the 1.75 GB
data archive. It does not: the client already draws as many page tabs as the
server tells it to.

    PythonNetworkStreamPhaseGameItem.cpp:126  RecvSafeBoxSizePacket()
                                              OpenSafeBox(kSafeBoxSize.bSize)
                                              OpenSafeboxWindow(bSize) -> python
    uisafebox.py:353   pageCount = max(1, size // safebox.SAFEBOX_SLOT_Y_COUNT)
    uisafebox.py:354   pageCount = min(3, pageCount)
    uisafebox.py:357   self.__MakePageButton(pageCount)

SAFEBOX_SLOT_Y_COUNT is 9 (PythonSafeBox.h:14), so the 27 the server now sends
becomes three page buttons, and PythonSafeBox.cpp:13 sizes its item vector to
5 * 27 = 135 to match. The client's own clamp is min(3, ...), the same ceiling
the server has. Not one line of client code, and no client asset, has to change.

WHAT IS DELIBERATELY LEFT ALONE. The premium branch at input_db.cpp:1200 still
sets bSize = 3. It is now redundant -- everyone starts at 3 -- but removing it
would be a second hunk in the same function for no behavioural gain, and leaving
it means a future operator who lowers the default still gets the stock premium
behaviour back for free. The practical consequence is that PREMIUM_SAFEBOX and
UNIQUE_GROUP_LARGE_SAFEBOX no longer grant anything, which is the expected
outcome of giving their benefit to everybody.

WHAT WAS RULED OUT. Restoring upstream's commented-out line and letting the
database decide: most accounts have no safebox row, the column defaults to 0,
and bSize 0 makes CSafebox build no grid at all (safebox.cpp:16) -- an unusable
storeroom for everyone who had never been given a row. Also ruled out: touching
CSafebox::ChangeSize. It is only reached when a storeroom is re-opened without
having been closed, and with the default now equal to the maximum its first line
(safebox.cpp:145, `if (m_iSize >= iSize) return;') always returns immediately.

safebox_pages.sql is applied alongside this. It is NOT what makes the third page
appear -- see above -- and the two are independent; it keeps the safebox.size
column agreeing with what the core now hands out, which is what quest scripts
(pc.get_safebox_size, questlua_game.cpp:59) and the db process's item_award
placement read.

Reverting means putting bSize back to 1. The game core has to be recompiled and
its image rebuilt for any of this to reach a player; nothing is built here.

Idempotent. A second run prints `already patched'.

    M2SRC=<context>/game/src python3 set_safebox_pages.py
"""
import io
import os
import re
import sys

SRC = os.environ.get("M2SRC", "")

INPUT_DB = os.path.join(SRC, "server", "game", "src", "input_db.cpp")
CHAR_H = os.path.join(SRC, "server", "game", "src", "char.h")
SAFEBOX_CPP = os.path.join(SRC, "server", "game", "src", "safebox.cpp")
TABLES_H = os.path.join(SRC, "server", "common", "tables.h")

# How many pages the storeroom opens with. Three is not a preference that can be
# raised: it is SAFEBOX_MAX_NUM / (5 * SAFEBOX_PAGE_SIZE), and the check below
# computes exactly that from the tree rather than trusting this comment.
PAGES = 3

# The one function the default lives in. Bounded on both sides, because
# `BYTE bSize' also appears in CInputDB::SafeboxChangeSize twelve lines further
# down, where it is the size the db process just wrote and must not be touched.
FUNC_HEAD = "void CInputDB::SafeboxLoad(LPDESC d, const char * c_pData)"
FUNC_TAIL = ("d->GetCharacter()->LoadSafebox(bSize * SAFEBOX_PAGE_SIZE, "
             "p->dwGold, p->wItemCount, "
             "(TPlayerItem *) (c_pData + sizeof(TSafeboxTable)));")

DEFAULT = "\tBYTE bSize = 1;"

# The marker that says this file has already been through here. A phrase from
# the comment rather than the assignment itself, because `BYTE bSize = 3;' is
# one character away from the premium line that is already in this function.
MARKER = "Three pages of storeroom for everybody"

REPLACEMENT = """\t// Three pages of storeroom for everybody, not one.
\t//
\t// Upstream opens the storeroom at one page and only widens it to three
\t// for a premium account or an equipped UNIQUE_GROUP_LARGE_SAFEBOX item
\t// (the branch a few lines below). This server sells neither, so that
\t// branch could never fire and the storeroom was permanently 45 cells.
\t//
\t// This is a ROW count, not a page count. It is multiplied by
\t// SAFEBOX_PAGE_SIZE (9) on the way into LoadSafebox at the end of this
\t// function, and CSafebox builds a grid five cells wide and that many rows
\t// tall -- so 3 becomes 27 rows, which is 135 cells, which is exactly
\t// SAFEBOX_MAX_NUM and therefore exactly the size of CSafebox::m_pkItems.
\t// Three is the ceiling this core can carry: a fourth page would write
\t// past the end of that array on the first item put into it.
\t//
\t// The size stored in the database (p->bSize) is NOT what decides this --
\t// upstream's own line that used it is commented out at the bottom of this
\t// function -- so no account needs a safebox row for the third page to
\t// appear, and most accounts do not have one.
\tBYTE bSize = %d;""" % PAGES


def read(path):
    """Reads a source file preserving bytes that are cp949 Korean comments."""
    if not os.path.isfile(path):
        sys.exit("not found: %s (set M2SRC to the staged game/src tree)" % path)
    return io.open(path, encoding="utf-8", errors="surrogateescape").read()


def check_ceiling():
    """Refuses to write unless three pages really is what this core can hold."""
    page_size = re.search(r"\bSAFEBOX_PAGE_SIZE\s*=\s*(\d+)\s*,", read(CHAR_H))
    if not page_size:
        sys.exit("could not find SAFEBOX_PAGE_SIZE in char.h -- refusing to "
                 "guess how many rows a storeroom page is")

    max_num = re.search(r"#define\s+SAFEBOX_MAX_NUM\s+(\d+)", read(TABLES_H))
    if not max_num:
        sys.exit("could not find SAFEBOX_MAX_NUM in common/tables.h -- "
                 "refusing to guess how many cells CSafebox can hold")

    # The grid width. Hard-coded in both places CSafebox builds a grid, so it is
    # read from there rather than assumed; if upstream ever widens the storeroom
    # the arithmetic below has to follow it.
    widths = set(re.findall(r"CGrid\((\d+),\s*m_iSize\)", read(SAFEBOX_CPP)))
    if len(widths) != 1:
        sys.exit("safebox.cpp builds its grid with %d different widths (%s) -- "
                 "expected exactly one" % (len(widths), ", ".join(sorted(widths))
                                           or "none"))

    width = int(widths.pop())
    rows = PAGES * int(page_size.group(1))
    cells = width * rows
    ceiling = int(max_num.group(1))

    if cells > ceiling:
        sys.exit("%d pages is %d cells (%d wide x %d rows) but "
                 "SAFEBOX_MAX_NUM is %d -- CSafebox::m_pkItems is not big "
                 "enough and the %dth item would corrupt memory"
                 % (PAGES, cells, width, rows, ceiling, ceiling + 1))

    return width, rows, cells, ceiling


def patch():
    """Returns True if it wrote, False if it was already patched."""
    if not SRC:
        sys.exit("M2SRC is not set (it wants the staged game/src directory)")

    s = read(INPUT_DB)

    if MARKER in s:
        print("   already patched: server/game/src/input_db.cpp")
        return False

    width, rows, cells, ceiling = check_ceiling()

    if s.count(FUNC_HEAD) != 1:
        sys.exit("%s appears %d times in input_db.cpp (expected exactly 1) -- "
                 "refusing to guess" % (FUNC_HEAD, s.count(FUNC_HEAD)))
    head = s.index(FUNC_HEAD)

    if s.count(FUNC_TAIL) != 1:
        sys.exit("the LoadSafebox call that ends CInputDB::SafeboxLoad appears "
                 "%d times in input_db.cpp (expected exactly 1) -- the function "
                 "is not shaped as expected" % s.count(FUNC_TAIL))
    tail = s.index(FUNC_TAIL)
    if tail < head:
        sys.exit("the LoadSafebox call sits before CInputDB::SafeboxLoad in "
                 "input_db.cpp -- the function is not shaped as expected")

    body = s[head:tail]
    found = body.count(DEFAULT)
    if found != 1:
        sys.exit("the storeroom default (%s) appears %d times inside "
                 "CInputDB::SafeboxLoad (expected exactly 1) -- refusing to "
                 "guess" % (DEFAULT.strip(), found))

    body = body.replace(DEFAULT, REPLACEMENT, 1)

    io.open(INPUT_DB, "w", encoding="utf-8", errors="surrogateescape",
            newline="").write(s[:head] + body + s[tail:])

    print("   patched: server/game/src/input_db.cpp (the storeroom opens with "
          "%d pages" % PAGES)
    print("            = %d rows = %d cells, of the %d SAFEBOX_MAX_NUM allows)"
          % (rows, cells, ceiling))

    print("   Every account gets this, old and new: the size is decided in the "
          "core, not in")
    print("   the database. No stored item moves -- the grid is %d wide and "
          "row-major, so" % width)
    print("   cells 0..%d keep their numbers and cells %d..%d are appended "
          "after them." % (cells // PAGES - 1, cells // PAGES, cells - 1))
    return True


if __name__ == "__main__":
    patch()
