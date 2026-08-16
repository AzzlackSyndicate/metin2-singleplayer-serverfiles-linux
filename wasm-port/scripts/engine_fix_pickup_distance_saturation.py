#!/usr/bin/env python3
"""Pick-up finds the actually-nearest drop: no DWORD(negative float) on wasm.

WHAT WAS REPORTED: "Item/Yang Pickup is still unreliable sometimes" — after
fix_pickup_throttle.py (the throttle no longer charges silent failures) and
set_pickup_range.py (client and server agree on 600/800) had already landed.

WHAT WAS ACTUALLY WRONG. CPythonItem::GetCloseItem and GetCloseMoney — the
functions that decide WHICH drop the pick-up key reaches for — computed the
distance to every ground item like this:

    DWORD dwxDistance = DWORD(c_rPixelPosition.x - pInstance->v3EndPosition.x);
    DWORD dwyDistance = DWORD(c_rPixelPosition.y - (-pInstance->v3EndPosition.y));
    ... dwxDistance*dwxDistance + dwyDistance*dwyDistance ...

The subtraction is float, and it is NEGATIVE for any drop on the wrong side of
the player on that axis. Casting a negative float to DWORD is undefined
behaviour in C++ — and the two platforms resolve it in opposite ways:

  * x86 (the stock Windows client): the cast wraps two's-complement, and
    squaring in unsigned arithmetic wraps BACK — (2^32 - 100)^2 mod 2^32 =
    10000 = (-100)^2. Twenty years of stock clients got the RIGHT distance out
    of undefined behaviour by accident. That is why this code never looked
    broken anywhere else, and why the earlier hunt (fix_pickup_throttle.py's
    docstring: "...through the pick-up radius, the unsigned distance cast and
    the ownership map...") could walk past it.

  * WebAssembly: clang emits the SATURATING truncation (i32.trunc_sat_f32_u —
    default in this toolchain), and a negative float saturates to ZERO.
    Verified against the exact emsdk this project builds with (4.0.12):
    `(unsigned)(-100.0f)` == 0 in a compiled wasm module.

So in the browser, every drop whose delta was negative on an axis had that
axis CONTRIBUTE NOTHING to its distance, and a drop north-west of the player
(both deltas negative) measured distance 0 — closer than anything, however far
away it really was. Consequences, all matching "unreliable sometimes":

  * The pick-up key reaches for a WRONG, possibly distant drop: the packet is
    sent for it, the server's own range check (CItem::DistanceValid, 600/800
    since set_pickup_range.py) refuses it, and NOTHING happens — no sound, no
    error, while the drop at the player's feet stays untouched.
  * Whether it misfires depends on where the drops sit relative to the player,
    so walking around the pile "fixes" it — exactly the kind of sometimes the
    report describes.
  * GetCloseMoney has the same loop, so yang was hit the same way; combined
    with money often being dropped in a spray around a kill, this is the most
    plausible mechanism yet for the old "money specifically was not picked up"
    mystery that fix_pickup_throttle.py explicitly left open.

THE FIX is to compute the distance in float, which is what the inputs are, and
compare squared floats — no casts, no UB, same result on every platform and
the same result x86's accident produced. The nearest-candidate cap starts at
the caller's radius (600/800 from __GetPickableDistance) instead of the old
hard 1000, which is the same comparison the old code made at the end, folded
into the loop. Behaviour for "no drop in range" is unchanged: false.

The dead `DWORD dwDistance` shadow of the parameter inside the loop goes away
with the rewrite.

Idempotent. M2WASM points at the client tree; a second run reports
`already patched'. THE ENGINE HAS TO BE REBUILT.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

ITEM_CPP = os.path.join(ROOT, "src/PyLib/src/bindings/item/PythonItem.cpp")

MARK = "M2_PICKUP_FLOAT_DISTANCE"

MONEY_OLD = """bool CPythonItem::GetCloseMoney(const TPixelPosition & c_rPixelPosition, DWORD & rItemID, DWORD dwDistance)
{
\tDWORD dwCloseItemID = 0;
\tDWORD dwCloseItemDistance = 1000 * 1000;

\tTGroundItemInstanceMap::iterator i;
\tfor (i = m_GroundItemInstanceMap.begin(); i != m_GroundItemInstanceMap.end(); ++i)
\t{
\t\tTGroundItemInstance * pInstance = i->second;

\t\tif (pInstance->dwVirtualNumber!=VNUM_MONEY)
\t\t\tcontinue;

\t\tDWORD dwxDistance = DWORD(c_rPixelPosition.x-pInstance->v3EndPosition.x);
\t\tDWORD dwyDistance = DWORD(c_rPixelPosition.y-(-pInstance->v3EndPosition.y));
\t\tDWORD dwDistance = DWORD(dwxDistance*dwxDistance + dwyDistance*dwyDistance);

\t\tif (dwxDistance*dwxDistance + dwyDistance*dwyDistance < dwCloseItemDistance)
\t\t{
\t\t\tdwCloseItemID = i->first;
\t\t\tdwCloseItemDistance = dwDistance;
\t\t}
\t}

\tif (dwCloseItemDistance>float(dwDistance)*float(dwDistance))
\t\treturn false;

\trItemID = dwCloseItemID;

\treturn true;
}"""

MONEY_NEW = """// M2_PICKUP_FLOAT_DISTANCE — the distance is computed in float, the type the
// inputs already are. The old DWORD(float) casts were undefined for the
// negative deltas a drop behind the player produces; x86 wrapped them so that
// unsigned squaring accidentally gave the right answer, but wasm's saturating
// truncation turns a negative delta into 0 — which made a far drop in the
// north-west quadrant measure as distance 0, shadow the real nearest drop, and
// send a pick-up the server then refused by range. Silently. See the patch
// script (engine_fix_pickup_distance_saturation.py) for the whole story.
bool CPythonItem::GetCloseMoney(const TPixelPosition & c_rPixelPosition, DWORD & rItemID, DWORD dwDistance)
{
\tDWORD dwCloseItemID = 0;
\tbool bFound = false;
\tfloat fCloseItemDistanceSq = float(dwDistance) * float(dwDistance);

\tTGroundItemInstanceMap::iterator i;
\tfor (i = m_GroundItemInstanceMap.begin(); i != m_GroundItemInstanceMap.end(); ++i)
\t{
\t\tTGroundItemInstance * pInstance = i->second;

\t\tif (pInstance->dwVirtualNumber!=VNUM_MONEY)
\t\t\tcontinue;

\t\tconst float fxDistance = c_rPixelPosition.x - pInstance->v3EndPosition.x;
\t\tconst float fyDistance = c_rPixelPosition.y - (-pInstance->v3EndPosition.y);
\t\tconst float fDistanceSq = fxDistance * fxDistance + fyDistance * fyDistance;

\t\tif (fDistanceSq < fCloseItemDistanceSq)
\t\t{
\t\t\tdwCloseItemID = i->first;
\t\t\tfCloseItemDistanceSq = fDistanceSq;
\t\t\tbFound = true;
\t\t}
\t}

\tif (!bFound)
\t\treturn false;

\trItemID = dwCloseItemID;

\treturn true;
}"""

ITEM_OLD = """bool CPythonItem::GetCloseItem(const TPixelPosition & c_rPixelPosition, DWORD & rItemID, DWORD dwDistance)
{
\tDWORD dwCloseItemID = 0;
\tDWORD dwCloseItemDistance = 1000 * 1000;

\tTGroundItemInstanceMap::iterator i;
\tfor (i = m_GroundItemInstanceMap.begin(); i != m_GroundItemInstanceMap.end(); ++i)
\t{
\t\tTGroundItemInstance * pInstance = i->second;

\t\tDWORD dwxDistance = DWORD(c_rPixelPosition.x-pInstance->v3EndPosition.x);
\t\tDWORD dwyDistance = DWORD(c_rPixelPosition.y-(-pInstance->v3EndPosition.y));
\t\tDWORD dwDistance = DWORD(dwxDistance*dwxDistance + dwyDistance*dwyDistance);

\t\tif (dwxDistance*dwxDistance + dwyDistance*dwyDistance < dwCloseItemDistance)
\t\t{
\t\t\tdwCloseItemID = i->first;
\t\t\tdwCloseItemDistance = dwDistance;
\t\t}
\t}

\tif (dwCloseItemDistance>float(dwDistance)*float(dwDistance))
\t\treturn false;

\trItemID = dwCloseItemID;

\treturn true;
}"""

ITEM_NEW = """// Same fix as GetCloseMoney above: float math instead of undefined
// DWORD(negative float) casts, which saturate to 0 on wasm and made the wrong
// drop win the nearest-item search.
bool CPythonItem::GetCloseItem(const TPixelPosition & c_rPixelPosition, DWORD & rItemID, DWORD dwDistance)
{
\tDWORD dwCloseItemID = 0;
\tbool bFound = false;
\tfloat fCloseItemDistanceSq = float(dwDistance) * float(dwDistance);

\tTGroundItemInstanceMap::iterator i;
\tfor (i = m_GroundItemInstanceMap.begin(); i != m_GroundItemInstanceMap.end(); ++i)
\t{
\t\tTGroundItemInstance * pInstance = i->second;

\t\tconst float fxDistance = c_rPixelPosition.x - pInstance->v3EndPosition.x;
\t\tconst float fyDistance = c_rPixelPosition.y - (-pInstance->v3EndPosition.y);
\t\tconst float fDistanceSq = fxDistance * fxDistance + fyDistance * fyDistance;

\t\tif (fDistanceSq < fCloseItemDistanceSq)
\t\t{
\t\t\tdwCloseItemID = i->first;
\t\t\tfCloseItemDistanceSq = fDistanceSq;
\t\t\tbFound = true;
\t\t}
\t}

\tif (!bFound)
\t\treturn false;

\trItemID = dwCloseItemID;

\treturn true;
}"""

EDITS = [
    (ITEM_CPP, [(MONEY_OLD, MONEY_NEW), (ITEM_OLD, ITEM_NEW)]),
]


def read(path):
    return io.open(path, encoding="utf-8", errors="surrogateescape", newline="").read()


def write(path, text):
    io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(text)


def main():
    for path, _ in EDITS:
        if not os.path.isfile(path):
            sys.exit("not found: %s (set M2WASM to the client tree)" % path)

    sources = dict((path, read(path)) for path, _ in EDITS)

    if MARK in sources[ITEM_CPP]:
        print("already patched")
        return

    for path, pairs in EDITS:
        for old, new in pairs:
            n = sources[path].count(old)
            if n != 1:
                sys.exit("anchor not found exactly once in %s (%d matches, "
                         "wanted 1):\n---\n%s\n---\nNothing was changed."
                         % (os.path.relpath(path, ROOT), n, old[:200]))

    for path, pairs in EDITS:
        text = sources[path]
        for old, new in pairs:
            text = text.replace(old, new, 1)
        write(path, text)

    print("patched: GetCloseItem/GetCloseMoney measure distance in float")
    print("   the DWORD(negative float) casts saturated to 0 on wasm, so a far")
    print("   drop on the wrong side of the player could win the nearest-drop")
    print("   search and the server then refused the pick-up by range, silently")
    print("   THE ENGINE HAS TO BE REBUILT.")


if __name__ == "__main__":
    main()
