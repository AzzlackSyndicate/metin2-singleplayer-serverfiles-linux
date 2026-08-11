#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_effect_handle_rest.py -- idempotent.

The four remaining sites of the defect fix_affect_effect_handle.py repaired for
m_adwCRCAffectEffect: an EngineLib::EffectHandle stored as a bare DWORD (its
.index alone) and rebuilt as {index, 0} to detach.

CActorInstance::DettachEffect compares whole handles
(EterBase::Handle::operator==, Handle.h:32 -- index AND generation), and
BgfxEffectManager::GetEmptyIndex never hands out generation 0, so every such
detach is a silent no-op on bgfx: the entry stays in m_AttachingEffectList and
DestroyEffectInstance is never reached. The eter manager's generation is 0, so
{index, 0} compared equal there -- this is a bgfx-only regression.

The second half of the same defect is the `if (!m_dwSomething)` test meaning
"0 == nothing attached". The original CEffectManager::GetEmptyIndex started at
1 and never recycled (EffectManager.cpp:390); bgfx's AcquireSlot hands out 0
and reuses slots, so slot 0 -- a perfectly good effect -- reads as "empty".
Handle::valid() (index != kInvalid) is the test that survives that.

SITES
-----
  1. SStoneSmoke::m_dwEftID          -- the metin-stone smoke plume.
  2. SWarrior::m_dwGeomgyeongEffect  -- the warrior's Geomgyeong weapon aura.
  3. m_swordRefineEffectRight/Left   -- the +7..+9 weapon glows.
  4. m_armorRefineEffect             -- the +7..+9 body-armour glow.

3 AND 4 ARE NOT A PURE TYPE CHANGE, and that is why they are not simply
`DWORD -> EffectHandle`. Those three members are DUAL-USE: __GetRefinedEffect
first writes an effect TYPE into them (EFFECT_REFINED + EFFECT_SWORD_REFINED7 +
refine - 7, InstanceBase.cpp:2796-2810) and then OVERWRITES the same member
with the attached instance's index (:2813-2815). One member, two meanings, and
the __Clear* functions cannot tell which one they are holding.

The armour arm is worse: :2838 DISCARDS __AttachEffect's return entirely, so
m_armorRefineEffect keeps the TYPE and __ClearArmorRefineEffect detaches
`EffectHandle{<a type id>, 0}` -- a slot index that has nothing to do with the
effect and, on a recycled pool, may well belong to SOMEONE ELSE'S effect. That
is a defect on both backends and it is why the armour glow could never be
removed.

So the two meanings get two members: the DWORD keeps the type, and a new
EffectHandle holds the instance.
"""

import io
import os
import re
import sys

H_INST = "/opt/m2wasm/src/GameLib/include/GameLib/InstanceBase.h"
CPP_EFF = "/opt/m2wasm/src/GameLib/src/InstanceBaseEffect.cpp"
CPP_INST = "/opt/m2wasm/src/GameLib/src/InstanceBase.cpp"

# (path, marker-that-means-already-done, regex, replacement, expected_count)
EDITS = [
    # ─────────────────────────────────────────────────────────────────────
    # 1. SStoneSmoke::m_dwEftID
    # ─────────────────────────────────────────────────────────────────────
    (
        H_INST,
        "EngineLib::EffectHandle m_dwEftID;",
        re.compile(r"DWORD m_dwEftID = 0;"),
        "// THE WHOLE HANDLE. Held .index alone and rebuilt {index, 0} to detach,\n"
        "\t\t\t// which never compares equal on bgfx (generation >= 1). See\n"
        "\t\t\t// the note on m_adwCRCAffectEffect below.\n"
        "\t\t\tEngineLib::EffectHandle m_dwEftID;",
        1,
    ),
    (
        CPP_EFF,
        "m_kStoneSmoke.m_dwEftID=EngineLib::EffectHandle{};",
        re.compile(
            r"\tm_kStoneSmoke\.m_dwEftID=0;\n"
            r"\}\n"
            r"\n"
            r"void CInstanceBase::__StoneSmoke_Destroy\(\)\n"
            r"\{\n"
            r"\tif \(!m_kStoneSmoke\.m_dwEftID\)\n"
            r"\t\treturn;\n"
            r"\n"
            r"\t__DetachEffect\(EngineLib::EffectHandle\{m_kStoneSmoke\.m_dwEftID, 0\}\);\n"
            r"\tm_kStoneSmoke\.m_dwEftID=0;\n"
            r"\}\n"
            r"\n"
            r"void CInstanceBase::__StoneSmoke_Create\(DWORD eSmoke\)\n"
            r"\{\n"
            r"\tm_kStoneSmoke\.m_dwEftID=m_GraphicThingInstance\.AttachSmokeEffect\(eSmoke\)\.index;\n"
        ),
        "\tm_kStoneSmoke.m_dwEftID=EngineLib::EffectHandle{};\n"
        "}\n"
        "\n"
        "void CInstanceBase::__StoneSmoke_Destroy()\n"
        "{\n"
        "\t// valid(), not `!= 0`: 0 is a legitimate bgfx slot index.\n"
        "\tif (!m_kStoneSmoke.m_dwEftID.valid())\n"
        "\t\treturn;\n"
        "\n"
        "\t// THE HANDLE AS IT WAS HANDED OUT. Rebuilding it as {index, 0} made this\n"
        "\t// detach a silent no-op on bgfx and left the smoke plume standing.\n"
        "\t__DetachEffect(m_kStoneSmoke.m_dwEftID);\n"
        "\tm_kStoneSmoke.m_dwEftID=EngineLib::EffectHandle{};\n"
        "}\n"
        "\n"
        "void CInstanceBase::__StoneSmoke_Create(DWORD eSmoke)\n"
        "{\n"
        "\tm_kStoneSmoke.m_dwEftID=m_GraphicThingInstance.AttachSmokeEffect(eSmoke);\n",
        1,
    ),
    # ─────────────────────────────────────────────────────────────────────
    # 2. SWarrior::m_dwGeomgyeongEffect
    # ─────────────────────────────────────────────────────────────────────
    (
        H_INST,
        "EngineLib::EffectHandle m_dwGeomgyeongEffect;",
        re.compile(r"DWORD m_dwGeomgyeongEffect = 0;"),
        "// THE WHOLE HANDLE — same defect and same repair as\n"
        "\t\t\t// m_adwCRCAffectEffect above: this held .index alone, and the\n"
        "\t\t\t// {index, 0} rebuilt for the detach never compared equal against a\n"
        "\t\t\t// bgfx handle, so the weapon aura was never taken off again.\n"
        "\t\t\tEngineLib::EffectHandle m_dwGeomgyeongEffect;",
        1,
    ),
    (
        CPP_EFF,
        "__DetachEffect(m_kWarrior.m_dwGeomgyeongEffect);",
        re.compile(
            r"\t\tif \(m_kWarrior\.m_dwGeomgyeongEffect\)\n"
            r"\t\t\t__DetachEffect\(EngineLib::EffectHandle\{m_kWarrior\.m_dwGeomgyeongEffect, 0\}\);\n"
            r"\n"
            r"\t\tm_GraphicThingInstance\.SetReachScale\(1\.5f\);\n"
            r"\t\tif \(m_GraphicThingInstance\.IsTwoHandMode\(\)\)\n"
            r"\t\t\tm_kWarrior\.m_dwGeomgyeongEffect = __AttachEffect\(EFFECT_WEAPON\+WEAPON_TWOHAND\)\.index;\n"
            r"\t\telse\n"
            r"\t\t\tm_kWarrior\.m_dwGeomgyeongEffect = __AttachEffect\(EFFECT_WEAPON\+WEAPON_ONEHAND\)\.index;\n"
            r"\t\}\n"
            r"\telse\n"
            r"\t\{\n"
            r"\t\tm_GraphicThingInstance\.SetReachScale\(1\.0f\);\n"
            r"\n"
            r"\t\t__DetachEffect\(EngineLib::EffectHandle\{m_kWarrior\.m_dwGeomgyeongEffect, 0\}\);\n"
            r"\t\tm_kWarrior\.m_dwGeomgyeongEffect=0;\n"
        ),
        "\t\t// valid(), not `!= 0` — slot 0 is a real bgfx slot.\n"
        "\t\tif (m_kWarrior.m_dwGeomgyeongEffect.valid())\n"
        "\t\t\t__DetachEffect(m_kWarrior.m_dwGeomgyeongEffect);\n"
        "\n"
        "\t\tm_GraphicThingInstance.SetReachScale(1.5f);\n"
        "\t\tif (m_GraphicThingInstance.IsTwoHandMode())\n"
        "\t\t\tm_kWarrior.m_dwGeomgyeongEffect = __AttachEffect(EFFECT_WEAPON+WEAPON_TWOHAND);\n"
        "\t\telse\n"
        "\t\t\tm_kWarrior.m_dwGeomgyeongEffect = __AttachEffect(EFFECT_WEAPON+WEAPON_ONEHAND);\n"
        "\t}\n"
        "\telse\n"
        "\t{\n"
        "\t\tm_GraphicThingInstance.SetReachScale(1.0f);\n"
        "\n"
        "\t\t// The handle itself. An invalid one matches nothing in\n"
        "\t\t// m_AttachingEffectList, which is the same no-op the 0 used to be when\n"
        "\t\t// the aura was never on — but a VALID one now actually detaches.\n"
        "\t\t__DetachEffect(m_kWarrior.m_dwGeomgyeongEffect);\n"
        "\t\tm_kWarrior.m_dwGeomgyeongEffect=EngineLib::EffectHandle{};\n",
        1,
    ),
    (
        CPP_INST,
        "m_kWarrior.m_dwGeomgyeongEffect=EngineLib::EffectHandle{};",
        re.compile(r"\tm_kWarrior\.m_dwGeomgyeongEffect=0;"),
        "\tm_kWarrior.m_dwGeomgyeongEffect=EngineLib::EffectHandle{};",
        1,
    ),
    # ─────────────────────────────────────────────────────────────────────
    # 3+4. the refine glows — type and instance split into two members
    # ─────────────────────────────────────────────────────────────────────
    (
        H_INST,
        "m_hSwordRefineEffectRight",
        re.compile(r"\t\tDWORD(?P<gap>\s+)m_armorRefineEffect = 0;\n"),
        "\t\tDWORD\\g<gap>m_armorRefineEffect = 0;\n"
        "\n"
        "\t\t// >>> THE THREE ABOVE ARE EFFECT *TYPES*; THESE ARE THE INSTANCES. <<<\n"
        "\t\t//\n"
        "\t\t// __GetRefinedEffect wrote a type (EFFECT_REFINED + …) into the DWORDs and\n"
        "\t\t// then overwrote the SAME member with __AttachEffect(...).index, so one\n"
        "\t\t// member carried two meanings and __ClearWeaponRefineEffect could not tell\n"
        "\t\t// them apart. It rebuilt {index, 0} to detach, which never compares equal\n"
        "\t\t// against a bgfx handle (Handle.h:32; GetEmptyIndex never hands out\n"
        "\t\t// generation 0) — so the glow was never removed on a weapon change.\n"
        "\t\t//\n"
        "\t\t// The armour arm never even stored an index: it discarded __AttachEffect's\n"
        "\t\t// return and then detached `EffectHandle{<the type id>, 0}` — a slot number\n"
        "\t\t// unrelated to the effect, which on a recycled pool can belong to something\n"
        "\t\t// else entirely. That one was wrong on BOTH backends.\n"
        "\t\tEngineLib::EffectHandle\tm_hSwordRefineEffectRight;\n"
        "\t\tEngineLib::EffectHandle\tm_hSwordRefineEffectLeft;\n"
        "\t\tEngineLib::EffectHandle\tm_hArmorRefineEffect;\n",
        1,
    ),
    (
        CPP_INST,
        "__DetachEffect(m_hSwordRefineEffectRight);",
        re.compile(
            r"\tif \(m_swordRefineEffectRight\)\n"
            r"\t\{\n"
            r"\t\t__DetachEffect\(EngineLib::EffectHandle\{m_swordRefineEffectRight, 0\}\);\n"
            r"\t\tm_swordRefineEffectRight = 0;\n"
            r"\t\}\n"
            r"\tif \(m_swordRefineEffectLeft\)\n"
            r"\t\{\n"
            r"\t\t__DetachEffect\(EngineLib::EffectHandle\{m_swordRefineEffectLeft, 0\}\);\n"
            r"\t\tm_swordRefineEffectLeft = 0;\n"
            r"\t\}\n"
        ),
        "\t// The INSTANCE handle, not the type id, and valid() rather than `!= 0` —\n"
        "\t// see the note on m_hSwordRefineEffectRight in InstanceBase.h.\n"
        "\tif (m_hSwordRefineEffectRight.valid())\n"
        "\t{\n"
        "\t\t__DetachEffect(m_hSwordRefineEffectRight);\n"
        "\t\tm_hSwordRefineEffectRight = EngineLib::EffectHandle{};\n"
        "\t}\n"
        "\tif (m_hSwordRefineEffectLeft.valid())\n"
        "\t{\n"
        "\t\t__DetachEffect(m_hSwordRefineEffectLeft);\n"
        "\t\tm_hSwordRefineEffectLeft = EngineLib::EffectHandle{};\n"
        "\t}\n"
        "\tm_swordRefineEffectRight = 0;\n"
        "\tm_swordRefineEffectLeft = 0;\n",
        1,
    ),
    (
        CPP_INST,
        "__DetachEffect(m_hArmorRefineEffect);",
        re.compile(
            r"\tif \(m_armorRefineEffect\)\n"
            r"\t\{\n"
            r"\t\t__DetachEffect\(EngineLib::EffectHandle\{m_armorRefineEffect, 0\}\);\n"
            r"\t\tm_armorRefineEffect = 0;\n"
            r"\t\}\n"
        ),
        "\tif (m_hArmorRefineEffect.valid())\n"
        "\t{\n"
        "\t\t__DetachEffect(m_hArmorRefineEffect);\n"
        "\t\tm_hArmorRefineEffect = EngineLib::EffectHandle{};\n"
        "\t}\n"
        "\tm_armorRefineEffect = 0;\n",
        1,
    ),
    (
        CPP_INST,
        "m_hSwordRefineEffectRight = __AttachEffect(m_swordRefineEffectRight);",
        re.compile(
            r"\t\tif \(m_swordRefineEffectRight\)\n"
            r"\t\t\tm_swordRefineEffectRight = __AttachEffect\(m_swordRefineEffectRight\)\.index;\n"
            r"\t\tif \(m_swordRefineEffectLeft\)\n"
            r"\t\t\tm_swordRefineEffectLeft = __AttachEffect\(m_swordRefineEffectLeft\)\.index;\n"
        ),
        "\t\t// The type STAYS in the DWORD and the instance goes in the handle. The\n"
        "\t\t// member used to be overwritten with .index here, which destroyed the type\n"
        "\t\t// and truncated the handle in one line.\n"
        "\t\tif (m_swordRefineEffectRight)\n"
        "\t\t\tm_hSwordRefineEffectRight = __AttachEffect(m_swordRefineEffectRight);\n"
        "\t\tif (m_swordRefineEffectLeft)\n"
        "\t\t\tm_hSwordRefineEffectLeft = __AttachEffect(m_swordRefineEffectLeft);\n",
        1,
    ),
    (
        CPP_INST,
        "m_hArmorRefineEffect = __AttachEffect(m_armorRefineEffect);",
        re.compile(
            r"\t\t\tm_armorRefineEffect = EFFECT_REFINED\+EFFECT_BODYARMOR_REFINED7\+refine-7;\n"
            r"\t\t\t__AttachEffect\(m_armorRefineEffect\);\n"
        ),
        "\t\t\tm_armorRefineEffect = EFFECT_REFINED+EFFECT_BODYARMOR_REFINED7+refine-7;\n"
        "\t\t\t// KEEP THE HANDLE. Discarding it left __ClearArmorRefineEffect with\n"
        "\t\t\t// nothing but the type id to detach with.\n"
        "\t\t\tm_hArmorRefineEffect = __AttachEffect(m_armorRefineEffect);\n",
        1,
    ),
    (
        CPP_INST,
        "m_hArmorRefineEffect = EngineLib::EffectHandle{};",
        re.compile(
            r"\tm_swordRefineEffectRight = 0;\n"
            r"\tm_swordRefineEffectLeft = 0;\n"
            r"\tm_armorRefineEffect = 0;\n"
            r"\n"
            r"\tm_sAlignment = 0;\n"
        ),
        "\tm_swordRefineEffectRight = 0;\n"
        "\tm_swordRefineEffectLeft = 0;\n"
        "\tm_armorRefineEffect = 0;\n"
        "\t// NOT 0 — an EffectHandle's empty state is kInvalid, and 0 is a real slot.\n"
        "\tm_hSwordRefineEffectRight = EngineLib::EffectHandle{};\n"
        "\tm_hSwordRefineEffectLeft = EngineLib::EffectHandle{};\n"
        "\tm_hArmorRefineEffect = EngineLib::EffectHandle{};\n"
        "\n"
        "\tm_sAlignment = 0;\n",
        1,
    ),
]


def main():
    changed = 0
    already = 0
    failed = []

    for path, marker, pattern, repl, count in EDITS:
        if not os.path.isfile(path):
            failed.append("%s: missing" % path)
            continue
        with io.open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            text = f.read()

        if marker in text:
            already += 1
            print("[ok  ] already patched: %s <- %s" % (os.path.basename(path), marker[:48]))
            continue

        hits = pattern.findall(text)
        if len(hits) != count:
            failed.append("%s: pattern matched %d times (expected %d): %s"
                          % (os.path.basename(path), len(hits), count,
                             pattern.pattern[:160]))
            continue

        text = pattern.sub(repl, text, count=count)
        with io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)
        changed += 1
        print("[edit] patched: %s <- %s" % (os.path.basename(path), marker[:48]))

    print("\nchanged=%d already=%d failed=%d" % (changed, already, len(failed)))
    for f in failed:
        print("FAIL " + f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
