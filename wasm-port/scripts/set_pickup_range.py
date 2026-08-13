#!/usr/bin/env python3
"""Pick-up range: 300 -> 600 on foot, 500 -> 800 while mounted, on BOTH sides.

WHAT WAS ASKED FOR: "Pick Up Range of Items & Yang is at 300 units, should be
600 units for foot, 800 units for mounted."

WHY THIS SCRIPT TOUCHES TWO TREES: pick-up is decided twice, and the two
decisions are independent.

    CLIENT  CPythonPlayer::__GetPickableDistance()
            src/PyLib/src/bindings/player/PythonPlayerInput.cpp
            Returned 500 mounted / 300 on foot. It is what PickCloseItem and
            PickCloseMoney hand to CPythonItem::GetCloseItem / GetCloseMoney,
            i.e. it decides WHICH drop the pick-up key even considers and
            whether a packet is sent at all.

    SERVER  CItem::DistanceValid(LPCHARACTER ch)
            game/src/server/game/src/item.cpp
            Compared DISTANCE_APPROX(item, char) against a bare literal 300 and
            refused beyond it. CHARACTER::PickupItem is its only caller, so this
            function is exactly and only the pick-up range check.

Raise only the client and the player reaches for a drop the server then
silently refuses -- the item stays on the ground and pick-up reads as broken.
Raise only the server and nothing changes at all, because the client never
sends the request. Hence: one script, two files, the same two numbers.

THE MOUNTED TEST, on each side, using the predicate that already exists there:

    client  CInstanceBase::IsMountingHorse() -> SHORSE::IsMounting()
    server  CHARACTER::IsRiding() = IsHorseRiding() || GetMountVnum()

The server predicate is the broader of the two: it is true for the classic
horse AND for the newer mount vnums. The client's IsMountingHorse() is true for
whatever the client currently has mounted, new mounts included, since the same
SHORSE slot carries both. Where they can disagree the server is the authority,
and it is the more generous of the two, so a player the client thinks is on
foot is never refused a pick-up the server would have allowed.

WHAT WAS *NOT* CHANGED, deliberately:

  * The dwDistance=300 default arguments on CPythonItem::GetCloseItem and
    GetCloseMoney (EngineLib/include/EngineLib/world/PythonItem.h). Both call
    sites pass __GetPickableDistance() explicitly, so the default is dead for
    pick-up purposes; leaving it alone keeps this patch to the two places that
    actually decide.
  * c_fClickDistance (300.0f) in PythonPlayerInput.cpp. That is the "do I have
    to walk closer first" distance for a mouse click, not a refusal -- clicking
    a distant drop walks the character to it and then picks it up. Unrelated.

Idempotent. A second run prints `already patched' per file. Run it after any
re-stage of the server source, because the game core's source tree is unpacked
fresh from the archive on every rebuild and a hand edit there is silently lost.

    M2WASM=/opt/m2wasm M2SERVER=/mnt/c/Users/hatip/Metin2Server python3 set_pickup_range.py
"""
import io
import os
import sys

# Distances in world units. Kept here as names so the two sides cannot drift.
FOOT = 600
MOUNTED = 800

CLIENT_ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
CLIENT_SRC = os.path.join(
    CLIENT_ROOT, "src/PyLib/src/bindings/player/PythonPlayerInput.cpp")

SERVER_REL = "game/src/server/game/src/item.cpp"
SERVER_CANDIDATES = [
    os.environ.get("M2SERVER"),
    "/mnt/c/Users/hatip/Metin2Server",
    "C:/Users/hatip/Metin2Server",
]

CLIENT_OLD = """DWORD CPythonPlayer::__GetPickableDistance()
{
	CInstanceBase * pkInstMain = NEW_GetMainActorPtr();
	if (pkInstMain)
		if (pkInstMain->IsMountingHorse())
			return 500;

	return 300;
}
"""

CLIENT_NEW = """// How far a dropped item or a pile of Yang may be and still be reachable with
// the pick-up key, in world units: %(foot)d on foot, %(mounted)d while mounted,
// because a rider sits higher and covers ground faster, and the old 300/500
// meant stopping on top of every single drop.
// The server enforces the SAME two numbers in CItem::DistanceValid. Change one
// and you must change the other, or the client will ask for pick-ups that the
// server then refuses -- which looks exactly like pick-up being broken.
DWORD CPythonPlayer::__GetPickableDistance()
{
	CInstanceBase * pkInstMain = NEW_GetMainActorPtr();
	if (pkInstMain)
		if (pkInstMain->IsMountingHorse())
			return %(mounted)d;

	return %(foot)d;
}
""" % {"foot": FOOT, "mounted": MOUNTED}

CLIENT_MARKER = "// How far a dropped item or a pile of Yang may be and still be reachable"

SERVER_OLD = """	int iDist = DISTANCE_APPROX(GetX() - ch->GetX(), GetY() - ch->GetY());

	if (iDist > 300)
		return false;
"""

SERVER_NEW = """	int iDist = DISTANCE_APPROX(GetX() - ch->GetX(), GetY() - ch->GetY());

	// How far a dropped item or a pile of Yang may be and still be picked up,
	// in world units: %(foot)d on foot, %(mounted)d while mounted. IsRiding()
	// is the predicate that already covers both the classic horse and the
	// newer mount vnums, so no new state is introduced here.
	// The client offers pick-ups using the SAME two numbers, in
	// CPythonPlayer::__GetPickableDistance. They have to agree: if this side is
	// the stricter one, the client reaches for drops that this check refuses.
	const int iPickupRange = ch->IsRiding() ? %(mounted)d : %(foot)d;

	if (iDist > iPickupRange)
		return false;
""" % {"foot": FOOT, "mounted": MOUNTED}

SERVER_MARKER = "const int iPickupRange ="


def patch(path, marker, old, new, what):
    if not os.path.isfile(path):
        sys.exit("not found: %s (%s)" % (path, what))

    s = io.open(path, encoding="utf-8", errors="surrogateescape").read()

    if marker in s:
        print("already patched: %s" % path)
        return False

    if s.count(old) != 1:
        sys.exit("anchor found %d times (expected exactly 1) in %s -- refusing "
                 "to guess" % (s.count(old), path))

    io.open(path, "w", encoding="utf-8", errors="surrogateescape",
            newline="").write(s.replace(old, new, 1))
    print("patched: %s" % path)
    return True


def find_server_src():
    for root in SERVER_CANDIDATES:
        if not root:
            continue
        path = os.path.join(root, SERVER_REL)
        if os.path.isfile(path):
            return path
    sys.exit("server source not found: set M2SERVER to the tree that contains "
             + SERVER_REL)


def main():
    changed = 0
    changed += patch(CLIENT_SRC, CLIENT_MARKER, CLIENT_OLD, CLIENT_NEW,
                     "set M2WASM to the client tree")
    changed += patch(find_server_src(), SERVER_MARKER, SERVER_OLD, SERVER_NEW,
                     "set M2SERVER to the game core tree")

    if changed:
        print("\n%d file(s) changed. Pick-up range is now %d on foot, %d mounted."
              % (changed, FOOT, MOUNTED))
        print("The client change needs a client rebuild; the server change "
              "needs the game core rebuilt and restarted.")


if __name__ == "__main__":
    main()
