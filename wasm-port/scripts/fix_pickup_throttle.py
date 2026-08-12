#!/usr/bin/env python3
"""Arm the pick-up throttle only when something was actually sent.

CPythonPlayer::SendClickItemPacket rate-limits pick-ups to one every 500 ms,
which is right: it is the guard against a held key flooding the server. But it
armed the timer at the TOP of the guarded block, before three checks that can
each return without sending anything:

    if (dwCurTime >= s_dwNextTCPTime)
    {
        s_dwNextTCPTime = dwCurTime + 500;   <-- armed here
        if (!GetOwnership(...))       return;
        ...
            if (!GetItemDataPointer(...)) return;
            ...                       return;   (OnCannotPickItem)
        rkNetStream.SendItemPickUpPacket(dwIID);
    }

So an attempt that was silently dropped still cost the next half second. Two
of them in a row and the player sees a key that does nothing, twice, for no
stated reason -- and the same timer is shared with the mouse, so clicking an
item and immediately pressing the pick-up key loses the key press.

MEASURED: the key arrives (browser keydown, repeat=false, clean keyup), the
player is standing on the drop, and nothing happens. That is what sent this
hunt through the pick-up radius, the unsigned distance cast and the ownership
map before landing here.

This does NOT explain why money specifically was not picked up while items
were -- that is still open, and this script does not claim it. What it fixes
is the failure being SILENT AND STICKY: after this, a dropped attempt costs
nothing and the next press is tried immediately.

Idempotent. Run it against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "src/PyLib/src/bindings/player/PythonPlayer.cpp")

ARM = "\t\ts_dwNextTCPTime=dwCurTime + 500;\n"
SEND_ANCHOR = "\t\tCPythonNetworkStream& rkNetStream=CPythonNetworkStream::Instance();\n" \
              "\t\trkNetStream.SendItemPickUpPacket(dwIID);\n"
SEND_PATCHED = ("\t\t// Armed HERE, not at the top of the block: every early return above\n"
                "\t\t// this line is an attempt that sent nothing, and charging it half a\n"
                "\t\t// second means the next press is swallowed too.\n"
                "\t\ts_dwNextTCPTime=dwCurTime + 500;\n"
                "\t\tCPythonNetworkStream& rkNetStream=CPythonNetworkStream::Instance();\n"
                "\t\trkNetStream.SendItemPickUpPacket(dwIID);\n")


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    s = io.open(SRC, encoding="utf-8", errors="surrogateescape").read()

    if SEND_PATCHED in s:
        print("already patched")
        return

    if s.count(ARM) != 1:
        sys.exit("expected exactly one `s_dwNextTCPTime=dwCurTime + 500;', found %d "
                 "-- has SendClickItemPacket changed?" % s.count(ARM))
    if s.count(SEND_ANCHOR) != 1:
        sys.exit("the send site was not found unchanged -- refusing to guess")

    s = s.replace(ARM, "", 1)
    s = s.replace(SEND_ANCHOR, SEND_PATCHED, 1)

    io.open(SRC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("patched: the pick-up throttle is armed only on an actual send")


if __name__ == "__main__":
    main()
