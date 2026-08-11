#!/usr/bin/env python3
# probe_quest_target.py — ONE-RUN INSTRUMENTATION for the quest-target arrow.
#
# It answers the two questions static reading cannot:
#
#   1. WHICH targets does this r40250 server actually create for this character?
#      The server's own CTargetManager hands out ids from ++m_iID (target.cpp:216,
#      so the first id is 1, never 0) and sends HEADER_GC_TARGET_CREATE = 125 ->
#      the client's RecvTargetCreatePacketNew. A CREATE with no matching DELETE is
#      an arrow that is SUPPOSED to be standing.
#   2. WHICH NPC is the arrow glued to? The packet carries a VID; only the client
#      can turn that into a vnum. main_quest_lv1 targets vnum 20354 (City Guard);
#      "Archery Teacher" is 20303/20323/20343. If the log says 20354 the arrow is
#      the server's and correct; if it says 203x3 the VID resolution is wrong.
#
# Volume: 3 lines per target lifetime plus one line per distinct glued VID. The
# UPDATE line is one per server target_event tick (~1/s per live target) — enough
# to see the create/update/delete ordering, small enough to read.
#
# REMOVE AFTER THE RUN: re-run with --revert.
#
# Idempotent, one file-wide-unique marker per edit site.

import sys

NET = "/opt/m2wasm/src/PyLib/src/bindings/net/PythonNetworkStreamPhaseGame.cpp"
CHR = "/opt/m2wasm/src/GameLib/src/PythonCharacterManager.cpp"

REVERT = "--revert" in sys.argv
changed = []


def read(p):
    with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(p, t):
    with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(t)


def patch(path, marker, anchor, new_text, label):
    text = read(path)
    if REVERT:
        if marker not in text:
            print("  skip (not present): %s" % label)
            return
        n = text.count(new_text)
        if n != 1:
            sys.exit("REVERT BLOCK NOT UNIQUE (%d hits) for %s in %s" % (n, label, path))
        write(path, text.replace(new_text, anchor, 1))
        print("  reverted: %s" % label)
        changed.append(label)
        return
    if marker in text:
        print("  skip (already applied): %s" % label)
        return
    n = text.count(anchor)
    if n != 1:
        sys.exit("ANCHOR NOT UNIQUE (%d hits) for %s in %s\n---\n%s\n---"
                 % (n, label, path, anchor))
    write(path, text.replace(anchor, new_text, 1))
    print("  applied: %s" % label)
    changed.append(label)


# ── CREATE ───────────────────────────────────────────────────────────────────
patch(
    NET,
    "M2PROBE_QUESTARROW_CREATE",
    """	CPythonMiniMap & rkpyMiniMap = CPythonMiniMap::Instance();
	if (CREATE_TARGET_TYPE_LOCATION == kTargetCreate.byType)""",
    """	// M2PROBE_QUESTARROW_CREATE — temporary; remove with probe_quest_target.py --revert
	SPDLOG_INFO("[QUESTARROW] CREATE id={} type={} vid={} name='{}'",
	            static_cast<long>(kTargetCreate.lID),
	            static_cast<unsigned>(kTargetCreate.byType),
	            static_cast<unsigned>(kTargetCreate.dwVID),
	            kTargetCreate.szTargetName);

	CPythonMiniMap & rkpyMiniMap = CPythonMiniMap::Instance();
	if (CREATE_TARGET_TYPE_LOCATION == kTargetCreate.byType)""",
    "PythonNetworkStreamPhaseGame.cpp: log TARGET CREATE",
)

# ── UPDATE ───────────────────────────────────────────────────────────────────
patch(
    NET,
    "M2PROBE_QUESTARROW_UPDATE",
    """	rkpyMiniMap.UpdateTarget(kTargetUpdate.lID, kTargetUpdate.lX, kTargetUpdate.lY);""",
    """	// M2PROBE_QUESTARROW_UPDATE — temporary; remove with probe_quest_target.py --revert
	SPDLOG_INFO("[QUESTARROW] UPDATE id={} x={} y={}",
	            static_cast<long>(kTargetUpdate.lID),
	            static_cast<long>(kTargetUpdate.lX),
	            static_cast<long>(kTargetUpdate.lY));

	rkpyMiniMap.UpdateTarget(kTargetUpdate.lID, kTargetUpdate.lX, kTargetUpdate.lY);""",
    "PythonNetworkStreamPhaseGame.cpp: log TARGET UPDATE",
)

# ── DELETE ───────────────────────────────────────────────────────────────────
patch(
    NET,
    "M2PROBE_QUESTARROW_DELETE",
    """	rkpyMiniMap.DeleteTarget(kTargetDelete.lID);""",
    """	// M2PROBE_QUESTARROW_DELETE — temporary; remove with probe_quest_target.py --revert
	SPDLOG_INFO("[QUESTARROW] DELETE id={}", static_cast<long>(kTargetDelete.lID));

	rkpyMiniMap.DeleteTarget(kTargetDelete.lID);""",
    "PythonNetworkStreamPhaseGame.cpp: log TARGET DELETE",
)

# ── VID -> vnum, once per glued VID ──────────────────────────────────────────
# NOTE ON MARKERS, learned the hard way in this very run: the first version of this
# script used M2PROBE_QUESTARROW_RESOLVER for the block below and
# M2PROBE_QUESTARROW_RESOLVER_INCLUDE for the include above it. The include was written
# first, and `if marker in text` for the block then matched the INCLUDE's marker — the
# block was silently skipped. No marker may be a prefix of another.
patch(
    CHR,
    "M2PROBE_QUESTARROW_SETINCLUDE",
    """#include <spdlog/spdlog.h>""",
    """#include <spdlog/spdlog.h>
#include <set>   // M2PROBE_QUESTARROW_SETINCLUDE — temporary, see the resolver probe below""",
    "PythonCharacterManager.cpp: <set> for the probe",
)

patch(
    CHR,
    "M2PROBE_QUESTARROW_RESOLVERBLOCK",
    """    CInstanceBase* pInstance = CPythonCharacterManager::Instance().GetInstancePtr(dwChrID);
    if (!pInstance)
        return false;

    TPixelPosition kPixelPosition;""",
    """    CInstanceBase* pInstance = CPythonCharacterManager::Instance().GetInstancePtr(dwChrID);
    if (!pInstance)
        return false;

    // M2PROBE_QUESTARROW_RESOLVERBLOCK — temporary; remove with probe_quest_target.py --revert.
    // This resolver is the head-of-NPC arrow's ONLY source of position
    // (BgfxScene::UpdateTargetEffects -> ResolveTargetEffectPosition), so whatever it names
    // here is the NPC the player sees the arrow over. main_quest_lv1 targets vnum 20354
    // (City Guard); the Archery Teacher is 20303/20323/20343. Once per VID — this runs every
    // frame for every live target.
    {
        static std::set<unsigned int> s_kSetLoggedTargetVID;
        if (s_kSetLoggedTargetVID.insert(dwChrID).second)
            SPDLOG_INFO("[QUESTARROW] head arrow glued to vid={} vnum={}",
                        dwChrID, pInstance->GetVirtualNumber());
    }

    TPixelPosition kPixelPosition;""",
    "PythonCharacterManager.cpp: log glued VID -> vnum",
)

print("changed: %d edit(s)" % len(changed))
