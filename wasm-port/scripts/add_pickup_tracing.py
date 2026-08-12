#!/usr/bin/env python3
"""Say why a pick-up did nothing, at every point where it silently gives up.

TEMPORARY. This is a diagnostic, not a fix: it exists because pressing the
pick-up key on a pile of yang did nothing at all -- no sound, no chat line, no
error -- while the same key worked on items and the mouse worked on both. The
Windows client picks the yang up, so the fault is in this port.

Four of the five exits from PickCloseItem -> SendClickItemPacket return without
sending and without saying anything:

    nothing within the pick-up radius
    no ground-item entry for that id (GetOwnership's map lookup)
    no item data for the vnum
    refused: not a party member, or the item is anti-flagged

...and the fifth is the 500 ms throttle, which fix_pickup_throttle.py fixes
separately. Each gets a `PICKUP: ...' line at debug level, which is the level
the browser console already shows.

Read the result with the console's filter box set to PICKUP. The line that
appears for yang and not for an item is the answer.

Remove before publishing an engine archive -- these lines are noise for a
player, and the browser console is where players look when something breaks.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
INPUT_CPP = os.path.join(ROOT, "src/PyLib/src/bindings/player/PythonPlayerInput.cpp")
PLAYER_CPP = os.path.join(ROOT, "src/PyLib/src/bindings/player/PythonPlayer.cpp")

FIND_OLD = """	DWORD dwItemID = 0;
	CPythonItem& rkItem=CPythonItem::Instance();
	if (!rkItem.GetCloseItem(kPPosMain, dwItemID, __GetPickableDistance()))
		return;

	SendClickItemPacket(dwItemID);
}"""

FIND_NEW = """	DWORD dwItemID = 0;
	CPythonItem& rkItem=CPythonItem::Instance();
	if (!rkItem.GetCloseItem(kPPosMain, dwItemID, __GetPickableDistance()))
	{
		SPDLOG_DEBUG("PICKUP: nothing within {} of ({:.0f}, {:.0f})",
		             __GetPickableDistance(), kPPosMain.x, kPPosMain.y);
		return;
	}

	SPDLOG_DEBUG("PICKUP: closest vid={} vnum={}", dwItemID,
	             CPythonItem::Instance().GetVirtualNumberOfGroundItem(dwItemID));
	SendClickItemPacket(dwItemID);
}"""

PAIRS = [
    ("""		const char * c_szOwnerName = nullptr;
		if (!CPythonItem::Instance().GetOwnership(dwIID, &c_szOwnerName))
			return;""",
     """		const char * c_szOwnerName = nullptr;
		if (!CPythonItem::Instance().GetOwnership(dwIID, &c_szOwnerName))
		{
			SPDLOG_DEBUG("PICKUP: no ownership entry for vid={}", dwIID);
			return;
		}
		SPDLOG_DEBUG("PICKUP: owner='{}' me='{}'", c_szOwnerName, GetName());"""),

    ("""			{
				SPDLOG_TRACE("CPythonPlayer::SendClickItemPacket(dwIID={}) : Non-exist item.", dwIID);
				return;
			}""",
     """			{
				SPDLOG_DEBUG("PICKUP: no item data for vnum={} (vid={})",
				             CPythonItem::Instance().GetVirtualNumberOfGroundItem(dwIID), dwIID);
				return;
			}"""),

    ("""				PyCallClassMemberFunc(m_ppyGameWindow, "OnCannotPickItem", Py_BuildValue("()"));
				return;""",
     """				SPDLOG_DEBUG("PICKUP: refused, not party or anti-flagged");
				PyCallClassMemberFunc(m_ppyGameWindow, "OnCannotPickItem", Py_BuildValue("()"));
				return;"""),

    ("""		CPythonNetworkStream& rkNetStream=CPythonNetworkStream::Instance();
		rkNetStream.SendItemPickUpPacket(dwIID);""",
     """		SPDLOG_DEBUG("PICKUP: sending pick-up for vid={}", dwIID);
		CPythonNetworkStream& rkNetStream=CPythonNetworkStream::Instance();
		rkNetStream.SendItemPickUpPacket(dwIID);"""),
]


def patch(path, pairs, marker):
    s = io.open(path, encoding="utf-8", errors="surrogateescape").read()
    if marker in s:
        return False
    for old, new in pairs:
        if s.count(old) != 1:
            sys.exit("%s: anchor not found exactly once:\n%s" % (path, old[:70]))
        s = s.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    return True


def main():
    for p in (INPUT_CPP, PLAYER_CPP):
        if not os.path.isfile(p):
            sys.exit("not found: %s (set M2WASM to the client tree)" % p)

    a = patch(INPUT_CPP, [(FIND_OLD, FIND_NEW)], "PICKUP: nothing within")
    b = patch(PLAYER_CPP, PAIRS, "PICKUP: no ownership entry")

    print("patched" if (a or b) else "already patched")


if __name__ == "__main__":
    main()
