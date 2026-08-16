# -*- coding: utf-8 -*-
"""Add a custom "Anti-XP Ring" (vnum 71111) to conf/item_proto.txt.

WHAT IT IS. A worn accessory that STOPS experience gain while it is on -- for a
player who wants to farm a spot, or hold a level for a duel bracket, without
levelling. Bought from the General Store for 100 Yang (shop_anti_xp_ring.sql
shelves it); taken off, experience resumes.

HOW IT STOPS EXPERIENCE, AND WHY NO CORE CHANGE. The reward path multiplies by a
mall bonus point:

    char_battle.cpp, CHARACTER::RewardExp:
        iExp += (iExp * to->GetPoint(POINT_MALL_EXPBONUS) / 100);   // line ~2473

so a worn item that contributes APPLY_MALL_EXPBONUS -100 makes that line subtract
the whole accumulated amount -- iExp becomes 0, and the terms after it multiply 0
by their own bonuses and stay 0. APPLY_MALL_EXPBONUS is a real item apply
(ProtoReader.cpp lists it); no item in this server used it, so -100 collides with
nothing.

THE ROW. Modelled on the Wind Shoes (39036), the one UNIQUE item confirmed to
carry a working ADDON apply (APPLY_MOV_SPEED 30 while worn). ITEM_UNIQUE in the
WEAR_SHIELD slot, exactly like the Experience Ring it is the mirror of; its one
ADDON is APPLY_MALL_EXPBONUS -100; VALUE0 is the worn-time duration in seconds,
set to ten years so it never runs out in practice. gold and shop_buy_price are
100 -- the shop charges gold*count (shop.cpp), so 100 Yang.

    ┌── SAFE TO RUN AGAIN. It appends the row only if vnum 71111 is not already
    │   present, matches the file's own column width, and never touches another
    │   row. The db core rebuilds player.item_proto from this file at every boot.
    └──

═══════════════════════════════════════════════════════════════════════════════
TWO THINGS THIS SCRIPT DOES NOT DO, AND THEY MUST BE DONE BEFORE IT SHIPS TO
PLAYERS. Both are called out here because neither could be verified from a repo
checkout, only on a running server.
═══════════════════════════════════════════════════════════════════════════════

1. THE CLIENT MUST KNOW THE ITEM, OR IT SHOWS AS "NoName" WITH A BLANK ICON.
   Item names and icons are client data, keyed by vnum, not sent by the server.
   Add vnum 71111 to each client's item list with the name and an icon (reuse the
   Experience Ring's icon):
     - Turkish name: "Anti-XP Yüzüğü"   (as requested)
     - English:      "Anti-XP Ring"
     - German:       "Anti-XP-Ring"
     - the other twelve locales: the English form is a safe fill.
   This means a browser-client DATA rebuild, and -- because the desktop and the
   original Windows clients each carry their own item data -- a republish of
   those too. Until a client is updated it renders 71111 as NoName.

2. THE -100 MECHANISM MUST BE VERIFIED ON THE TEST SERVER FIRST. Confirm that
   POINT_MALL_EXPBONUS is not clamped to >= 0 when the apply is read: equip the
   ring, kill a monster, and check that the experience gained is 0. If a negative
   mall bonus is clamped away on this build, switch the apply to a quest-driven
   affect instead. Do this on the test line before the public line is deployed.
"""
import io
import os
import sys

SHARE = os.environ.get("M2SHARE", "")
PROTO = "conf/item_proto.txt"
VNUM = "71111"

# The 33 columns of a proto row, in file order. Everything past ADDON_VALUE2 is
# left at the file's default of 0; VALUE0 (index 24) is the worn-time duration.
ROW = [
    VNUM,                                     # 0  vnum
    "Anti-XP Ring",                           # 1  name (server-side; client shows its own)
    "ITEM_UNIQUE",                            # 2  type
    "UNIQUE_NONE",                            # 3  subtype
    "1",                                      # 4  size
    "ANTI_SELL | ANTI_STACK | ANTI_MYSHOP",   # 5  anti-flag: no resale, no stack, no player-shop
    "LOG",                                    # 6  flag
    "WEAR_SHIELD",                            # 7  worn in the unique/accessory slot
    "NONE",                                   # 8  immune
    "100",                                    # 9  gold  -> the shop price, 100 Yang
    "100",                                    # 10 shop_buy_price
    "0", "0", "0",                            # 11 refine, 12 refineset, 13 magic_pct
    "LIMIT_NONE", "0", "LIMIT_NONE", "0",     # 14-17 limits
    "APPLY_MALL_EXPBONUS", "-100",            # 18-19 ADDON0: stop all experience
    "APPLY_NONE", "0", "APPLY_NONE", "0",     # 20-23 ADDON1, ADDON2
    "315360000",                              # 24 VALUE0: worn-time seconds (~10 years)
    "0", "0", "0", "0", "0",                  # 25-29 VALUE1-5
    "0", "0", "0",                            # 30 specular, 31 socket, 32 attu_addon
]


def main():
    if not SHARE:
        sys.exit("M2SHARE is not set (the server share tree, e.g. "
                 "<context>/serverfiles/share)")
    path = os.path.join(SHARE, PROTO)
    if not os.path.isfile(path):
        sys.exit("not found: %s" % path)

    s = io.open(path, encoding="latin-1", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"
    lines = s.split(nl)

    header = lines[0].split("\t")
    width = len(header)
    if width < len(ROW):
        sys.exit("%s header has %d columns, fewer than the %d this row writes"
                 % (PROTO, width, len(ROW)))

    for ln in lines[1:]:
        f = ln.split("\t")
        if f and f[0].strip() == VNUM:
            print("   already present: item %s in %s" % (VNUM, PROTO))
            return 0

    row = ROW + ["0"] * (width - len(ROW))     # pad to the file's own width
    record = "\t".join(row)

    # Append as a new last row, keeping the file's trailing-newline convention.
    if s.endswith(nl):
        s = s + record + nl
    else:
        s = s + nl + record
    io.open(path, "w", encoding="latin-1", newline="").write(s)
    print("   added item %s (Anti-XP Ring, %d columns) to %s" % (VNUM, width, PROTO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
