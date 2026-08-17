#!/usr/bin/env python3
"""Linking an item in chat no longer costs a Glass of Insight. Server half.

WHAT WAS ASKED FOR: "Remove the necessity of Glass of Insight to link items in
chat."

WHAT THE GLASS OF INSIGHT ACTUALLY IS HERE. The item the client calls the Glass
of Insight is ITEM_PRISM, vnum 71113 (server/game/src/unique_item.h). It is not
a key and it is not a permission: it is CONSUMED. ProcessTextTag in
input_main.cpp counts the |H...|h hyperlinks in an outgoing line, refuses the
whole message unless the player is carrying at least that many prisms, and then
DELETES one per link -- RemoveSpecifyItem(ITEM_PRISM, hyperlinks). Say three
items in one sentence and three glasses are gone.

So "show someone what I just found" is a purchase, made in a general store,
which is a design for a world with a player economy. This is a single-player
server. There is nobody to buy from, nobody to show off to at a price, and the
only thing the requirement produces is a player who types a link, watches the
message vanish with an error, and concludes chat is broken.

WHERE THE CHECK LIVES, AND WHY THE FIX IS HERE AND NOT AT THE TWO CALLERS.
ProcessTextTag has exactly two callers, CInputMain::Whisper and the chat
handler, and each reacts to a NON-ZERO return by telling the player what is
missing and dropping the line. Editing those two would mean teaching both to
ignore one particular refusal, in two places, forever. Returning 0 from the one
function that decides is a single edit at the point of the decision, and it
leaves both callers correct without knowing anything changed.

WHAT IS DELIBERATELY *NOT* REMOVED. ProcessTextTag answers five ways, and only
two of them are about the prism:

    0  fine, send it
    1  not enough prisms                 <- gone
    2  has prisms but is selling them    <- gone, and it existed only because
                                            of the count in 1
    3  in a trade, exchange or shop      <- KEPT. This is a different rule
                                            entirely: an item link resolves
                                            against the sender's inventory, and
                                            a link sent mid-trade is the classic
                                            scam -- link the good sword, put the
                                            bad one in the window. It has
                                            nothing to do with the glass.
    4  coloured text with no link at all  <- KEPT. Unrelated formatting rule.

So the two prism arms collapse to 0 and the other three answers are untouched.
CountSpecifyItem and RemoveSpecifyItem lose one caller each and keep several
others; nothing is deleted from the core.

THE PRISM ITSELF IS NOT REMOVED FROM THE GAME. It still drops, still stacks,
still sells. It simply is not spent on talking any more -- taking the row out of
item_proto would break every quest and shop entry that names it, for no gain.

ENCODING: this file is read and written as latin-1, byte for byte. input_main.cpp
carries EUC-KR comments from the original Korean source, and decoding it as
UTF-8 fails outright while decoding it as anything lossy would silently rewrite
those comments into replacement characters. latin-1 is the one codec that maps
every byte 0-255 to itself and back, so the bytes this script does not touch are
returned exactly as they were found.

Idempotent. A second run reports `already patched'.

    M2SRC=<context>/game/src python3 link_items_without_a_prism.py
"""
import io
import os
import sys

SRC = os.environ.get("M2SRC", "")
INPUT_MAIN = os.path.join(SRC, "server", "game", "src", "input_main.cpp")

# Must appear CONTIGUOUSLY in NEW below. A marker that spans a line break is a
# marker that never matches, and the patch then re-applies -- fails loudly the
# second time, which is exactly what happened while this was being written.
MARKER = "THE GLASS OF INSIGHT IS NOT SPENT ON TALKING"

OLD = (
    "\tint nPrismCount = ch->CountSpecifyItem(ITEM_PRISM);\n"
    "\n"
    "\tif (nPrismCount < hyperlinks)\n"
    "\t\treturn 1;\n"
    "\n"
    "\n"
    "\tif (!ch->GetMyShop())\n"
    "\t{\n"
    "\t\tch->RemoveSpecifyItem(ITEM_PRISM, hyperlinks);\n"
    "\t\treturn 0;\n"
    "\t} else\n"
    "\t{\n"
    "\t\tint sellingNumber = ch->GetMyShop()->GetNumberByVnum(ITEM_PRISM);\n"
    "\t\tif(nPrismCount - sellingNumber < hyperlinks)\n"
    "\t\t{\n"
    "\t\t\treturn 2;\n"
    "\t\t} else\n"
    "\t\t{\n"
    "\t\t\tch->RemoveSpecifyItem(ITEM_PRISM, hyperlinks);\n"
    "\t\t\treturn 0;\n"
    "\t\t}\n"
    "\t}\n"
    "\t\n"
    "\treturn 4;"
)

NEW = (
    "\t// THE GLASS OF INSIGHT IS NOT SPENT ON TALKING -- no prism is required\n"
    "\t// to link an item in chat, and none is consumed.\n"
    "\t//\n"
    "\t// What stood here counted ITEM_PRISM (vnum 71113) in the sender's\n"
    "\t// inventory, refused the whole line unless there was one per hyperlink,\n"
    "\t// and then deleted one per hyperlink. On a server with a player economy\n"
    "\t// that is a price on showing off. On a single-player server there is\n"
    "\t// nobody to buy the glasses from and nobody to show off to, so the only\n"
    "\t// thing it produced was a player whose message disappeared with an error\n"
    "\t// they had no way to act on.\n"
    "\t//\n"
    "\t// The other three answers this function gives are deliberately intact,\n"
    "\t// and none of them was about the glass:\n"
    "\t//   3  in a trade or a shop  -- kept. A link resolves against the\n"
    "\t//      SENDER's inventory, so a link sent mid-trade is the classic\n"
    "\t//      swap: show the good sword, put the bad one in the window.\n"
    "\t//   4  coloured text with no link -- kept. A formatting rule.\n"
    "\t//   0  send it.\n"
    "\t// Both branches that used to answer 1 and 2 now answer 0, which is why\n"
    "\t// this returns unconditionally rather than falling through to the 4\n"
    "\t// below -- reaching that 4 would refuse every linked message instead.\n"
    "\t//\n"
    "\t// The item is untouched everywhere else: it still drops, stacks and\n"
    "\t// sells. Only the toll is gone. CountSpecifyItem and RemoveSpecifyItem\n"
    "\t// each lose one caller here and keep the rest.\n"
    "\treturn 0;"
)


def main():
    if not SRC:
        sys.stderr.write("M2SRC is not set. Nothing changed.\n")
        return 1
    if not os.path.isfile(INPUT_MAIN):
        sys.stderr.write("not found: %s\nNothing changed.\n" % INPUT_MAIN)
        return 1

    # latin-1 both ways: see the header. Every byte round-trips.
    src = io.open(INPUT_MAIN, encoding="latin-1").read()

    if MARKER in src:
        print("   already patched: server/game/src/input_main.cpp")
        return 0

    n = src.count(OLD)
    if n != 1:
        sys.stderr.write(
            "the prism block in ProcessTextTag was found %d times, expected 1.\n"
            "input_main.cpp is not the file this patch was written against.\n"
            "Nothing changed.\n" % n)
        return 1

    out = src.replace(OLD, NEW, 1)

    # A cheap proof that the two callers still read a non-zero answer as a
    # refusal -- if either had been rewritten to test for a specific code, this
    # patch would silently mean something else.
    if out.count("int processReturn = ProcessTextTag(ch,") != 2:
        sys.stderr.write("ProcessTextTag no longer has its two known callers. "
                         "Nothing changed.\n")
        return 1

    io.open(INPUT_MAIN, "w", encoding="latin-1", newline="").write(out)
    print("   patched: server/game/src/input_main.cpp "
          "(item links cost no Glass of Insight)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
