#!/usr/bin/env python3
"""The Musk Oil quest points at the General Store, where the oil is sold.

Part of the Custom Experience, and it has to be: the wording below is only true
because the same switch puts Musk Oil on the General Store Saleswoman's counter
(shop_musk_oil.sql). Rewrite the text without stocking the shop and the quest
sends the player to a shelf that is empty.

new_quest_premium_lv4 asks for one Musk Oil (item 30177) and told the player to
order it "in a very special shop", follow a turning coin, pay in Dragon Marks
and collect it from the Storekeeper. That is the Gameforge item shop flow, and
there is no item shop here. The quest itself only ever checks that the player
holds the item, so nothing but the wording was ever wrong. Three strings say it
the old way and all three are rewritten.

Only translate.lua is edited, never translate_de.lua or translate_tr.lua:
questlua.cpp:563 loads exactly one translation file and locale_service.cpp:477
fixes its base path at "locale/english". The other fourteen ship with the pack
and are never opened.

Idempotent. A second run reports `already patched' for every edit.

    M2SHARE=<context>/serverfiles/share python3 musk_oil_from_the_general_store.py
"""
import io
import os
import sys

# The staged tree that gets built into the game image: <context>/serverfiles/share.
SHARE = os.environ.get("M2SHARE", "")

QUEST = "locale/english/quest"
OBJECT = QUEST + "/object"
TRANSLATE = "locale/english/translate.lua"

# Every file here is read and written as latin-1, which is a byte-for-byte
# round trip for the whole 0-255 range. The pack's text files are cp1252 and
# the object chunks are ASCII; decoding them as anything cleverer would risk
# rewriting bytes this patch never meant to touch.
#
# (relative path, marker that exists ONLY after patching, old text, new text)
EDITS = [
    # --------------------------------------------------------------- (11) ---
    # Musk Oil, item 30177, is stocked by the General Store Saleswoman for
    # 100 Yang. The item shop, the turning coin, the Dragon Marks and the
    # Storekeeper pickup all describe a shop this server does not run.
    (
        TRANSLATE,
        "Oil. The General Store Saleswoman keeps a bottle",
        'gameforge.new_quest_premium_lv4._180_say = "Ahh, Lilac! Now I just need some'
        " unusual Musk[ENTER]Oil. You can only order it in a very special[ENTER]shop. A"
        " turning coin will show you the way. You[ENTER]will need some Dragon Marks, but"
        " I'm sure you've[ENTER]got some stashed away, haven't you? I would be"
        "[ENTER]eternally grateful! Afterwards, you can pick up[ENTER]your order from the"
        ' Storekeeper. Then I would[ENTER]finally have all the ingredients! "',
        'gameforge.new_quest_premium_lv4._180_say = "Ahh, Lilac! Now I just need some'
        " unusual Musk[ENTER]Oil. The General Store Saleswoman keeps a bottle"
        "[ENTER]behind her counter and asks barely a hundred Yang[ENTER]for it. Bring me"
        " one and I would be eternally[ENTER]grateful! Then I would finally have all"
        ' the[ENTER]ingredients! "',
    ),
    (
        TRANSLATE,
        "Oil. Please buy another bottle from the General",
        'gameforge.new_quest_premium_lv4._280_say = "I can\'t produce my fragrance'
        " without the Musk[ENTER]Oil. Please let yourself be led by the turning"
        '[ENTER]coin and get me another specimen. "',
        'gameforge.new_quest_premium_lv4._280_say = "I can\'t produce my fragrance'
        " without the Musk[ENTER]Oil. Please buy another bottle from the General"
        '[ENTER]Store Saleswoman and bring it to me. "',
    ),
    (
        TRANSLATE,
        "Dealer. I can buy it from the General Store",
        'gameforge.new_quest_premium_lv4._300_say = "I need to take some Musk Oil to the'
        " Weapon Shop[ENTER]Dealer. I'm supposed to let myself be led by the[ENTER]turning"
        ' coin, order an unusual fragrance oil and[ENTER]pick it up from the Storekeeper. "',
        'gameforge.new_quest_premium_lv4._300_say = "I need to take some Musk Oil to the'
        " Weapon Shop[ENTER]Dealer. I can buy it from the General Store"
        '[ENTER]Saleswoman for about a hundred Yang. "',
    ),
]


def main():
    if not SHARE or not os.path.isdir(SHARE):
        sys.exit("set M2SHARE to the serverfiles share/ tree (got %r)" % SHARE)

    changed = 0
    skipped = 0

    for rel, marker, old, new in EDITS:
        path = os.path.join(SHARE, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            sys.exit("not found: %s (set M2SHARE to the serverfiles share/ tree)" % path)

        s = io.open(path, encoding="latin-1", newline="").read()
        if marker in s:
            print("already patched: %s" % rel)
            skipped += 1
            continue
        if s.count(old) != 1:
            sys.exit("anchor found %d times (want 1) in %s" % (s.count(old), rel))

        io.open(path, "w", encoding="latin-1", newline="").write(s.replace(old, new, 1))
        print("patched: %s" % rel)
        changed += 1

    print("%d edit(s) applied, %d already in place." % (changed, skipped))


if __name__ == "__main__":
    main()
