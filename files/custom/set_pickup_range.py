#!/usr/bin/env python3
"""Pick-up range: 300 -> 600 on foot, 500 -> 800 while mounted. Server half.

WHAT WAS ASKED FOR: "Pick Up Range of Items & Yang is at 300 units, should be
600 units for foot, 800 units for mounted."

Pick-up is decided TWICE, and the two decisions are independent:

    CLIENT  CPythonPlayer::__GetPickableDistance()
            Returns 500 mounted / 300 on foot. It is what PickCloseItem and
            PickCloseMoney hand to CPythonItem::GetCloseItem / GetCloseMoney,
            i.e. it decides WHICH drop the pick-up key even considers and
            whether a packet is sent at all.

    SERVER  CItem::DistanceValid(LPCHARACTER ch)
            game/src/server/game/src/item.cpp
            Compared DISTANCE_APPROX(item, char) against a bare literal 300 and
            refused beyond it. CHARACTER::PickupItem is its only caller, so this
            function is exactly and only the pick-up range check.

ONLY THE SERVER HALF IS HERE. The client half is compiled into the browser
client, which is built somewhere else entirely and is not this project's to
change; it already carries the same two numbers. They have to agree: if the
server is the stricter of the two, the client offers pick-ups this check then
refuses, and the item sits on the ground looking like pick-up is broken. So if
these numbers are ever changed, they must be changed on both sides.

WHAT WAS *NOT* CHANGED, deliberately: the dwDistance=300 default arguments on
CPythonItem::GetCloseItem and GetCloseMoney. Both call sites pass
__GetPickableDistance() explicitly, so the default is dead for pick-up
purposes.

Idempotent. A second run reports `already patched'.

    M2SRC=<context>/game/src python3 set_pickup_range.py
"""
import io
import os
import sys

# Distances in world units. Named rather than repeated, so the two branches of
# the ternary below cannot drift apart from the comment that explains them.
FOOT = 600
MOUNTED = 800

# The staged source tree that the game image is built from. item.cpp sits at
# server/game/src/ inside it.
SRC = os.environ.get("M2SRC", "")
ITEM_CPP = os.path.join(SRC, "server", "game", "src", "item.cpp")

MARKER = "const int iPickupRange ="

OLD = """	int iDist = DISTANCE_APPROX(GetX() - ch->GetX(), GetY() - ch->GetY());

	if (iDist > 300)
		return false;
"""

NEW = """	int iDist = DISTANCE_APPROX(GetX() - ch->GetX(), GetY() - ch->GetY());

	// How far a dropped item or a pile of Yang may be and still be picked up,
	// in world units: %(foot)d on foot, %(mounted)d while mounted, because a
	// rider sits higher and covers ground faster, and the old flat 300 meant
	// stopping on top of every single drop. IsRiding() is the predicate that
	// already covers both the classic horse and the newer mount vnums, so no
	// new state is introduced here.
	// The client offers pick-ups using the SAME two numbers, in
	// CPythonPlayer::__GetPickableDistance. They have to agree: if this side is
	// the stricter one, the client reaches for drops that this check refuses.
	const int iPickupRange = ch->IsRiding() ? %(mounted)d : %(foot)d;

	if (iDist > iPickupRange)
		return false;
""" % {"foot": FOOT, "mounted": MOUNTED}


def main():
    if not SRC or not os.path.isfile(ITEM_CPP):
        sys.exit("not found: %s (set M2SRC to the staged game/src tree)" % ITEM_CPP)

    s = io.open(ITEM_CPP, encoding="utf-8", errors="surrogateescape").read()

    if MARKER in s:
        print("already patched: server/game/src/item.cpp")
        return

    if s.count(OLD) != 1:
        sys.exit("anchor found %d times (expected exactly 1) in item.cpp -- "
                 "refusing to guess" % s.count(OLD))

    io.open(ITEM_CPP, "w", encoding="utf-8", errors="surrogateescape",
            newline="").write(s.replace(OLD, NEW, 1))
    print("patched: server/game/src/item.cpp")
    print("Items and Yang are now picked up from %d units on foot, %d mounted."
          % (FOOT, MOUNTED))


if __name__ == "__main__":
    main()
