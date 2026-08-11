#!/usr/bin/env python3
# probe_quest_target_fix.py — repairs the marker collision probe_quest_target.py hit.
#
# M2PROBE_QUESTARROW_RESOLVER is a PREFIX of M2PROBE_QUESTARROW_RESOLVER_INCLUDE, so the
# `marker in text` test for the resolver edit matched the include edit that had just been
# written and silently skipped the real edit. Renames the include marker to something no
# other marker contains, then applies the resolver block.

import sys

CHR = "/opt/m2wasm/src/GameLib/src/PythonCharacterManager.cpp"


def read(p):
    with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(p, t):
    with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(t)


text = read(CHR)

# 1. rename the colliding marker
old_inc = "#include <set>   // M2PROBE_QUESTARROW_RESOLVER_INCLUDE — temporary, see the resolver below"
new_inc = "#include <set>   // M2PROBE_QUESTARROW_SETINCLUDE — temporary, see the resolver probe below"
if old_inc in text:
    assert text.count(old_inc) == 1
    text = text.replace(old_inc, new_inc, 1)
    print("  renamed include marker")
elif new_inc in text:
    print("  include marker already renamed")
else:
    sys.exit("include line not found in %s" % CHR)

# 2. now the resolver edit, with a marker no other marker is a prefix of
MARK = "M2PROBE_QUESTARROW_RESOLVERBLOCK"
anchor = """    CInstanceBase* pInstance = CPythonCharacterManager::Instance().GetInstancePtr(dwChrID);
    if (!pInstance)
        return false;

    TPixelPosition kPixelPosition;"""

new = """    CInstanceBase* pInstance = CPythonCharacterManager::Instance().GetInstancePtr(dwChrID);
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

    TPixelPosition kPixelPosition;"""

if MARK in text:
    print("  skip (already applied): resolver block")
else:
    n = text.count(anchor)
    if n != 1:
        sys.exit("ANCHOR NOT UNIQUE (%d hits) for resolver block" % n)
    text = text.replace(anchor, new, 1)
    print("  applied: resolver block")

write(CHR, text)
